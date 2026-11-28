#!/usr/bin/env python3
"""Read-only Upstox V3 historical daily candle adapter.

Live provider qualification remains PENDING: no credentials are bundled and
no test in this module contacts the provider. HTTP transport is injected and
``fetch_daily`` refuses to run unless the caller explicitly sets
``enable_network=True``. Tokens are supplied per call, are never stored,
logged or written to the raw-response manifest.

The official V3 REST contract implemented here is
``GET {base_url}/historical-candle/{instrument_key}/day/{to}/{from}`` with
``Authorization: Bearer <token>`` and a JSON body of
``{"status":"success","data":{"candles":[[ts,o,h,l,c,v,oi], ...]}}``.
Timestamps are exchange-local ISO-8601 values with an explicit UTC offset.
Open interest follows the canonical sentinel contract: -1 is unavailable,
>= 0 is available, < -1 is invalid and values must be below 2**63.

Transport hardening (PR #78) is fail-closed and entirely offline: requests
are limited to two explicit host allowlists (production api.upstox.com and
the separate sandbox api-sandbox.upstox.com), HTTPS only, with no credentials,
query, fragment, traversal, unexpected port or unexpected path prefix.
Redirects must stay on the same allowlisted host over HTTPS and are bounded.
Client calls carry a bounded timeout when the injected client accepts one,
responses are size-limited (including streamed reads), and client exception
text (which may contain a token) is never propagated, logged or stored.

Transport policy (PR #84) is checked in code before the first request: an
injected client is accepted only when automatic redirects are disabled at the
client or call level. A client that signals auto-following and cannot be
pinned to manual handling is rejected without sending a token. Every redirect
hop is inspected separately and credentials are rebuilt for each approved
destination. The enforced policy is recorded in the raw manifest.

Live WebSocket Market Feed V3 is a separate authenticated transport with its
own endpoint and redirect policy. It must NOT reuse this historical HTTP
redirect policy; a dedicated Market Feed V3 adapter is future work and is not
implemented in this module.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
reject_duplicate_keys = importlib.import_module(
    "json_support"
).reject_duplicate_keys

# Official V3 hosts: the V3 candle example in the Upstox developer
# documentation uses https://api.upstox.com/v3; the official Python SDK maps
# Configuration(sandbox=True) to https://api-sandbox.upstox.com. Both lists
# are explicit: no wildcard, subdomain, IP or look-alike host is permitted.
PRODUCTION_HOSTS = frozenset({"api.upstox.com"})
SANDBOX_HOSTS = frozenset({"api-sandbox.upstox.com"})
ALLOWED_HOSTS = PRODUCTION_HOSTS | SANDBOX_HOSTS
ENVIRONMENTS = frozenset({"production", "sandbox"})
API_PATH_PREFIX = "/v3"
PRODUCTION_BASE_URL = "https://api.upstox.com/v3"
SANDBOX_BASE_URL = "https://api-sandbox.upstox.com/v3"
DEFAULT_BASE_URL = PRODUCTION_BASE_URL

HISTORICAL_PATH = "/historical-candle"
DEFAULT_INTERVAL = "day"
DEFAULT_MAX_DAYS = 365
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_MAX_REQUESTS = 1000
DEFAULT_ENVIRONMENT = "production"
# Bounds are conservative and caller-inspectable.
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
MAX_REDIRECTS = 3
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RATE_LIMIT_RETRIES = 0
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
TRANSPORT_STATUS = 0
TRANSPORT_REASON_HEADER = "X-Quant-Transport-Reason"
TRANSPORT_POLICY_REASON = "automatic redirects cannot be disabled"
REDIRECT_POLICY_KEY = "redirect_policy_enforced"
SESSION_OMISSION_KEY = "historical_sessions_omitted"
MANUAL_REDIRECT_CONTROL = "manual"
REDIRECT_CONTROL_ATTRIBUTES = ("follow_redirects", "allow_redirects")
REDIRECT_CONTROL_PARAMETERS = ("follow_redirects", "allow_redirects")
TRANSPORT_REASONS = frozenset(
    {
        "request timed out",
        "connection failed",
        "transport failure",
        "too many redirects",
        "disallowed redirect target",
        TRANSPORT_POLICY_REASON,
        "invalid response body",
        "invalid response stream",
    }
)
MAX_VOLUME = 2**53
MAX_EXACT_DOUBLE_INTEGER = 2**53
MAX_OPEN_INTEREST = 2**63
DIGEST_ALGORITHM = "sha256"
MANIFEST_VERSION = 1

VALID_INTERVALS = frozenset({"day"})
RATE_LIMIT_CODES = frozenset(
    {"UDAPI100026", "UDAPI100027", "TOO_MANY_REQUESTS", "RATE_LIMIT_EXCEEDED"}
)
# Client failures are sanitised by type; exception text is never retained.
_CLIENT_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
    AttributeError,
    KeyError,
    IndexError,
    AssertionError,
    StopIteration,
    ZeroDivisionError,
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*\|[A-Za-z0-9][A-Za-z0-9 .&/_-]*$")
_RAW_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+_[0-9]{4}_[0-9a-f]{64}\.json$")
_SIZE_REASON_RE = re.compile(r"^response exceeds \d+ bytes$")
_MISSING = object()

SessionCalendar = Callable[[str, str], Iterable[str]]
KeyResolver = Callable[[str], str]


class FailureClass(StrEnum):
    """Fail-closed classification of every rejection reason."""

    NONE = "NONE"
    RATE_LIMIT = "RATE_LIMIT"
    HTTP_ERROR = "HTTP_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_INVALID = "SCHEMA_INVALID"


class VolumeValidity(StrEnum):
    """Canonical volume validity state shared with the dataset contract."""

    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class ConfigError(ValueError):
    """Caller configuration or token failed validation."""


class InstrumentResolutionError(ConfigError):
    """A canonical identity could not be mapped to a valid provider key."""


class NetworkDisabledError(RuntimeError):
    """A fetch was attempted without explicit network opt-in."""


class TransportError(ConfigError):
    """A sanitised transport, redirect or size failure without client detail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TransportPolicyError(TransportError):
    """The injected client cannot be pinned to manual redirect handling."""


class ReplayError(ValueError):
    """Stored raw responses could not be verified or replayed."""


class _RowError(ValueError):
    """One rejected candle row, with its session when it was readable."""

    def __init__(
        self, reason: str, session: str | None = None, detail: str = ""
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.session = session
        self.detail = detail


@dataclass(frozen=True)
class HttpResponse:
    """Minimal transport result returned by an injected HTTP client."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DateRange:
    """One inclusive request window."""

    start: str
    end: str


@dataclass(frozen=True)
class FetchConfig:
    """Non-secret configuration for a fetch or replay."""

    base_url: str = DEFAULT_BASE_URL
    interval: str = DEFAULT_INTERVAL
    max_days_per_request: int = DEFAULT_MAX_DAYS
    exchange_timezone: str = DEFAULT_TIMEZONE
    require_midnight: bool = True
    expected_sessions: Sequence[str] | SessionCalendar | None = None
    raw_dir: Path | None = None
    max_requests: int = DEFAULT_MAX_REQUESTS
    environment: str = DEFAULT_ENVIRONMENT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_rate_limit_retries: int = DEFAULT_RATE_LIMIT_RETRIES


@dataclass(frozen=True)
class BatchRecord:
    """Auditable metadata for one persisted raw response."""

    index: int
    start: str
    end: str
    status: int
    digest: str
    file_name: str
    retry_after: int | None = None


@dataclass(frozen=True)
class DailyCandle:
    """One validated daily observation with exact volume and OI."""

    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int
    volume_validity: VolumeValidity


@dataclass(frozen=True)
class Rejection:
    """A structured, non-silent rejection with no token material."""

    reason: str
    failure_class: FailureClass = FailureClass.SCHEMA_INVALID
    batch_index: int | None = None
    row_index: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class FetchOutcome:
    """Structured result of a fetch or deterministic replay."""

    status: str
    canonical_id: str
    instrument_key: str
    requested_start: str
    requested_end: str
    batches: tuple[BatchRecord, ...]
    records: tuple[DailyCandle, ...]
    rejections: tuple[Rejection, ...]
    missing_dates: tuple[str, ...]
    raw_digests: tuple[str, ...]


def sha256_hex(content: bytes) -> str:
    """Return the lowercase hex SHA-256 of exact bytes."""
    return hashlib.sha256(content).hexdigest()


def _is_date(value: object) -> bool:
    """Return True only for a real ISO calendar date."""
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value) is not None
    except ValueError:
        return False


def _validate_date(value: object, name: str) -> str:
    """Validate and return one canonical ISO date."""
    if not _is_date(value):
        raise ConfigError(f"invalid {name}")
    return value


def _allowed_hosts(environment: object) -> frozenset[str]:
    """Return the one explicit host allowlist for an environment."""
    if environment == "production":
        return PRODUCTION_HOSTS
    if environment == "sandbox":
        return SANDBOX_HOSTS
    raise ConfigError("invalid environment")


def _parse_secure_url(
    value: object, hosts: frozenset[str], label: str
) -> tuple[str, str]:
    """Validate HTTPS, host, credentials, port and URL extras; return parts."""
    if (
        not isinstance(value, str)
        or not value
        or any(
            char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F
            for char in value
        )
    ):
        raise ConfigError(f"invalid {label}")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ConfigError(f"invalid {label}") from None
    if parts.scheme != "https":
        raise ConfigError(f"non-HTTPS {label}")
    host = parts.hostname
    if host is None or host.lower() not in hosts:
        raise ConfigError(f"host not allowlisted in {label}")
    if (
        parts.username is not None
        or parts.password is not None
        or "@" in parts.netloc
    ):
        raise ConfigError(f"{label} credentials are not allowed")
    try:
        port = parts.port
    except ValueError:
        raise ConfigError(f"unexpected {label} port") from None
    if port not in (None, 443):
        raise ConfigError(f"unexpected {label} port")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ConfigError(f"unexpected {label} form")
    path = unquote(parts.path)
    if "\\" in path or ".." in path.split("/"):
        raise ConfigError(f"unsafe {label} path")
    return host.lower(), parts.path


def _validate_base_url(
    base_url: object, environment: object | None = None
) -> str:
    """Validate an HTTPS V3 base URL against the selected host allowlist.

    With no environment argument both documented allowlists are accepted;
    callers that pin an environment get only that environment's hosts.
    """
    hosts = (
        ALLOWED_HOSTS if environment is None else _allowed_hosts(environment)
    )
    host, path = _parse_secure_url(base_url, hosts, "base_url")
    if path.rstrip("/") != API_PATH_PREFIX:
        raise ConfigError("unexpected base_url path prefix")
    return f"https://{host}{API_PATH_PREFIX}"


def _header(headers: object, name: str) -> str | None:
    """Return one header value case-insensitively without echoing headers."""
    items = getattr(headers, "items", None)
    if not callable(items):
        return None
    for key, value in items():
        if (
            isinstance(key, str)
            and key.lower() == name.lower()
            and isinstance(value, str)
        ):
            return value
    return None


def _validate_redirect(
    location: object,
    current_url: str,
    hosts: frozenset[str],
    original_host: str | None = None,
) -> str:
    """Require the same allowlisted HTTPS host and an in-scope API path."""
    if not isinstance(location, str) or not location:
        raise TransportError("disallowed redirect target")
    try:
        target = urljoin(current_url, location)
        host, path = _parse_secure_url(target, hosts, "redirect target")
    except ConfigError:
        raise TransportError("disallowed redirect target") from None
    current_host = urlsplit(current_url).hostname
    if host != (current_host.lower() if current_host else None):
        raise TransportError("disallowed redirect target")
    if original_host is not None and host != original_host.lower():
        raise TransportError("disallowed redirect target")
    if path != API_PATH_PREFIX and not path.startswith(API_PATH_PREFIX + "/"):
        raise TransportError("disallowed redirect target")
    return target


def _transport_reason(exc: object) -> str:
    """Classify a client failure by type only, never by its message."""
    if isinstance(exc, TimeoutError):
        return "request timed out"
    if isinstance(exc, ConnectionError):
        return "connection failed"
    return "transport failure"


def _signature_parameters(
    callable_obj: object,
) -> Mapping[str, inspect.Parameter]:
    """Return inspectable parameters, or an empty mapping when unavailable."""
    try:
        return inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return {}


def _has_attribute(client: object, name: str) -> bool:
    """Return True only when an attribute can be read without side effects."""
    try:
        return getattr(client, name, _MISSING) is not _MISSING
    except _CLIENT_ERRORS:
        return False


def _redirect_attribute_disabled(client: object, name: str) -> bool:
    """Disable one auto-redirect flag and verify its resulting value."""
    try:
        current = getattr(client, name, _MISSING)
    except _CLIENT_ERRORS:
        return False
    if current is _MISSING:
        return False
    if current is False:
        return True
    if current is not True:
        return False
    try:
        setattr(client, name, False)
        return getattr(client, name) is False
    except _CLIENT_ERRORS:
        return False


def _max_redirects_disabled(client: object) -> bool:
    """Disable a client redirect budget, leaving zero further hops."""
    try:
        client.max_redirects = 0
        return client.max_redirects == 0
    except _CLIENT_ERRORS:
        return False


def _redirect_parameter(get: object) -> str | None:
    """Name the keyword that disables redirect following in a client call."""
    parameters = _signature_parameters(get)
    for name in REDIRECT_CONTROL_PARAMETERS:
        parameter = parameters.get(name)
        if parameter is not None and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return name
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return "allow_redirects"
    return None


def enforce_transport_policy(http_client: object) -> str:
    """Pin automatic redirects off before any token-bearing request.

    The returned value names the call keyword that must be forced to False,
    or ``MANUAL_REDIRECT_CONTROL`` when the client declares manual handling.
    A client that advertises auto-following but cannot be configured, and a
    client that exposes no redirect control at all, are rejected before a
    token is sent.
    """
    get = getattr(http_client, "get", None)
    if not callable(get):
        raise ConfigError("http_client.get(url, headers) is required")
    declared = False
    for name in REDIRECT_CONTROL_ATTRIBUTES:
        if _has_attribute(http_client, name):
            declared = True
            if not _redirect_attribute_disabled(http_client, name):
                raise TransportPolicyError(TRANSPORT_POLICY_REASON)
    if _has_attribute(http_client, "max_redirects"):
        declared = True
        if not _max_redirects_disabled(http_client):
            raise TransportPolicyError(TRANSPORT_POLICY_REASON)
    parameter = _redirect_parameter(get)
    if parameter is not None:
        return parameter
    if declared:
        return MANUAL_REDIRECT_CONTROL
    raise TransportPolicyError(TRANSPORT_POLICY_REASON)


# Explicit alias for callers that describe the step as a check.
check_transport_policy = enforce_transport_policy


def _check_no_auto_follow(requested_url: str, result: object) -> None:
    """Reject a client result that already followed to a different URL."""
    try:
        history = getattr(result, "history", ())
    except _CLIENT_ERRORS:
        history = ()
    if history:
        raise TransportError(TRANSPORT_POLICY_REASON)
    try:
        final_url = getattr(result, "url", None)
    except _CLIENT_ERRORS:
        final_url = None
    if final_url is not None and str(final_url) != requested_url:
        raise TransportError(TRANSPORT_POLICY_REASON)


def _check_declared_size(headers: object) -> None:
    """Fail early when a response declares an oversized body."""
    declared = _header(headers, "content-length")
    if declared is not None and declared.strip().isdigit():
        if int(declared.strip()) > MAX_RESPONSE_BYTES:
            raise TransportError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")


def _check_body_size(body: object) -> None:
    """Reject an already-materialised body that exceeds the byte limit."""
    if not isinstance(body, (bytes, bytearray)):
        raise TransportError("invalid response body")
    if len(body) > MAX_RESPONSE_BYTES:
        raise TransportError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")


def _read_limited(handle: object) -> bytes:
    """Stream at most MAX_RESPONSE_BYTES from an injected response handle."""
    read = getattr(handle, "read", None)
    if not callable(read):
        raise TransportError("invalid response stream")
    payload = bytearray()
    while chunk := read(READ_CHUNK_SIZE):
        if not isinstance(chunk, (bytes, bytearray)):
            raise TransportError("invalid response stream")
        if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
            raise TransportError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
        payload.extend(chunk)
    return bytes(payload)


def _coerce_response(result: object) -> HttpResponse:
    """Convert an injected result to a bounded HttpResponse."""
    if isinstance(result, HttpResponse):
        _check_declared_size(result.headers)
        _check_body_size(result.body)
        return result
    headers = getattr(result, "headers", {})
    if not callable(getattr(headers, "items", None)):
        raise ConfigError("http_client.get must return HttpResponse")
    _check_declared_size(headers)
    body = _read_limited(result)
    status = getattr(result, "status", 200)
    if isinstance(status, bool) or not isinstance(status, int):
        raise ConfigError("http_client.get must return HttpResponse")
    return HttpResponse(status, body, headers)


def _call_get(
    get: Callable[..., object],
    url: str,
    headers: dict[str, str],
    timeout: float,
    redirect_control: str | None = None,
) -> object:
    """Use bounded timeouts and force automatic redirects off when accepted."""
    parameters = _signature_parameters(get)
    accepts_timeout = "timeout" in parameters or any(
        value.kind is inspect.Parameter.VAR_KEYWORD
        for value in parameters.values()
    )
    options: dict[str, object] = {}
    if accepts_timeout:
        options["timeout"] = timeout
    if redirect_control in REDIRECT_CONTROL_PARAMETERS:
        options[redirect_control] = False
    if options:
        return get(url, headers, **options)
    return get(url, headers)


def _auth_headers(secret: str) -> dict[str, str]:
    """Build a fresh credential header set for one approved destination."""
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {secret}",
    }


def _validate_config(config: FetchConfig) -> FetchConfig:
    """Validate configuration and return its normalised copy."""
    if not isinstance(config, FetchConfig):
        raise ConfigError("config must be a FetchConfig")
    if config.environment not in ENVIRONMENTS:
        raise ConfigError("invalid environment")
    base_url = _validate_base_url(config.base_url, config.environment)
    if config.interval not in VALID_INTERVALS:
        raise ConfigError("invalid interval")
    for name in ("max_days_per_request", "max_requests"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"invalid {name}")
    if config.max_days_per_request > 366:
        raise ConfigError("max_days_per_request is too large")
    retries = config.max_rate_limit_retries
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not (0 <= retries <= MAX_RATE_LIMIT_RETRIES)
    ):
        raise ConfigError("invalid max_rate_limit_retries")
    if (
        isinstance(config.timeout_seconds, bool)
        or not isinstance(config.timeout_seconds, (int, float))
        or not math.isfinite(config.timeout_seconds)
        or not 0 < config.timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ConfigError("invalid timeout_seconds")
    if not isinstance(config.exchange_timezone, str):
        raise ConfigError("invalid exchange_timezone")
    try:
        ZoneInfo(config.exchange_timezone)
    except (ValueError, KeyError) as exc:
        raise ConfigError("invalid exchange_timezone") from exc
    if not isinstance(config.require_midnight, bool):
        raise ConfigError("invalid require_midnight")
    if config.raw_dir is not None and not isinstance(config.raw_dir, Path):
        raise ConfigError("raw_dir must be a Path")
    if config.expected_sessions is not None and not callable(
        config.expected_sessions
    ):
        if isinstance(config.expected_sessions, (str, bytes)) or not isinstance(
            config.expected_sessions, Sequence
        ):
            raise ConfigError("invalid expected_sessions")
    return FetchConfig(
        base_url=base_url,
        interval=config.interval,
        max_days_per_request=config.max_days_per_request,
        exchange_timezone=config.exchange_timezone,
        require_midnight=config.require_midnight,
        expected_sessions=config.expected_sessions,
        raw_dir=config.raw_dir,
        max_requests=config.max_requests,
        environment=config.environment,
        timeout_seconds=float(config.timeout_seconds),
        max_rate_limit_retries=retries,
    )


def iter_batches(start: str, end: str, max_days: int) -> tuple[DateRange, ...]:
    """Split an inclusive ISO date range into non-overlapping windows."""
    first = date.fromisoformat(_validate_date(start, "start"))
    last = date.fromisoformat(_validate_date(end, "end"))
    if first > last:
        raise ConfigError("start is after end")
    if (
        isinstance(max_days, bool)
        or not isinstance(max_days, int)
        or max_days < 1
    ):
        raise ConfigError("invalid max_days")
    batches: list[DateRange] = []
    cursor = first
    while cursor <= last:
        stop = min(cursor + timedelta(days=max_days - 1), last)
        batches.append(DateRange(cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return tuple(batches)


def validate_instrument_key(key: object) -> str:
    """Validate an Upstox EXCHANGE_SEGMENT|SYMBOL instrument key."""
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise InstrumentResolutionError("invalid instrument key")
    return key


def _resolve_key(resolver: KeyResolver, canonical_id: str) -> str:
    """Resolve a canonical identity through the caller-supplied callback."""
    if not callable(resolver):
        raise ConfigError("instrument key resolver must be callable")
    if (
        not isinstance(canonical_id, str)
        or not canonical_id
        or canonical_id != canonical_id.strip()
    ):
        raise ConfigError("invalid canonical id")
    return validate_instrument_key(resolver(canonical_id))


def _validate_token(token: object) -> str:
    """Validate a bearer token without ever echoing its value."""
    if not isinstance(token, str) or not token or token != token.strip():
        raise ConfigError("token must be a non-empty string")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in token):
        raise ConfigError("token contains control characters")
    return token


def _build_url(config: FetchConfig, key: str, batch: DateRange) -> str:
    """Build the official V3 historical daily candle URL."""
    encoded = quote(key, safe="")
    tail = f"{encoded}/{config.interval}/{batch.end}/{batch.start}"
    return f"{config.base_url}{HISTORICAL_PATH}/{tail}"


def _request(
    http_client: object,
    url: str,
    token: str,
    hosts: frozenset[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    redirect_control: str | None = None,
) -> HttpResponse:
    """Issue one authenticated GET with redirect and transport safeguards."""
    get = getattr(http_client, "get", None)
    if not callable(get):
        raise ConfigError("http_client.get(url, headers) is required")
    allowed = ALLOWED_HOSTS if hosts is None else hosts
    _parse_secure_url(url, allowed, "request url")
    secret = _validate_token(token)
    control = (
        enforce_transport_policy(http_client)
        if redirect_control is None
        else redirect_control
    )
    original_host = urlsplit(url).hostname or ""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            result = _call_get(
                get, current, _auth_headers(secret), timeout, control
            )
            _check_no_auto_follow(current, result)
            response = _coerce_response(result)
        except TransportError:
            raise
        except _CLIENT_ERRORS as exc:
            # Suppress the original exception: its message may contain the
            # token or the Authorization header supplied by the caller.
            raise TransportError(_transport_reason(exc)) from None
        if not 300 <= response.status < 400:
            return response
        location = _header(response.headers, "location")
        if location is None:
            return response
        current = _validate_redirect(location, current, allowed, original_host)
    raise TransportError("too many redirects")


def _safe_transport_reason(reason: object) -> str:
    """Allow only adapter-authored reasons to travel with a failure."""
    if not isinstance(reason, str):
        return "transport failure"
    if reason in TRANSPORT_REASONS or _SIZE_REASON_RE.fullmatch(reason):
        return reason
    return "transport failure"


def _transport_response(reason: str) -> HttpResponse:
    """Represent a sanitised transport failure without client-supplied data."""
    safe = _safe_transport_reason(reason)
    return HttpResponse(TRANSPORT_STATUS, b"", {TRANSPORT_REASON_HEADER: safe})


def _fetch_batch(
    http_client: object,
    url: str,
    token: str,
    hosts: frozenset[str],
    timeout: float,
    retries: int,
    redirect_control: str,
) -> HttpResponse:
    """Fetch one URL with bounded, rate-limit-only retries."""
    attempts = 0
    while True:
        try:
            response = _request(
                http_client, url, token, hosts, timeout, redirect_control
            )
        except TransportError as exc:
            return _transport_response(exc.reason)
        failure = _classify_response(response)
        if failure is None or failure[0] is not FailureClass.RATE_LIMIT:
            return response
        if attempts >= retries:
            return response
        attempts += 1


def _retry_after(headers: Mapping[str, str]) -> int | None:
    """Parse a non-negative Retry-After header when present."""
    raw = headers.get("Retry-After", headers.get("retry-after"))
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _strict_json_loads(text: str) -> object:
    """Parse JSON, rejecting duplicate object keys at every level."""
    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


def _error_fields(body: bytes) -> tuple[str, str]:
    """Extract a provider error code and message without leaking headers."""
    try:
        payload = _strict_json_loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    errors = payload.get("errors")
    if not isinstance(errors, dict):
        return "", ""
    code = errors.get("error_code")
    message = errors.get("message")
    return (
        code if isinstance(code, str) else "",
        message.lower() if isinstance(message, str) else "",
    )


def _is_rate_limit(
    status: int, code: str, message: str, retry_after: int | None
) -> bool:
    """Classify transport and provider signals as rate limiting."""
    words = ("rate limit", "too many requests", "throttl")
    return (
        status == 429
        or code in RATE_LIMIT_CODES
        or any(word in message for word in words)
        or (retry_after is not None and status >= 400)
    )


def _classify_response(
    response: HttpResponse,
) -> tuple[FailureClass, str] | None:
    """Classify a failed response; return None for a successful one."""
    if response.status == TRANSPORT_STATUS:
        detail = _header(response.headers, TRANSPORT_REASON_HEADER)
        return (
            FailureClass.TRANSPORT_ERROR,
            _safe_transport_reason(detail) if detail else "transport failure",
        )
    retry = _retry_after(response.headers)
    code, message = _error_fields(response.body)
    if _is_rate_limit(response.status, code, message, retry):
        detail = f"http {response.status}; rate limited"
        return FailureClass.RATE_LIMIT, detail
    if not 200 <= response.status < 300:
        return FailureClass.HTTP_ERROR, f"http {response.status}"
    return None


def _extract_candles(payload: object) -> tuple[list[object], str]:
    """Extract the candle array from a successful V3 payload."""
    if not isinstance(payload, dict):
        return [], "response is not an object"
    if payload.get("status") != "success":
        return [], "response status is not success"
    data = payload.get("data")
    if not isinstance(data, dict):
        return [], "missing data object"
    candles = data.get("candles")
    if not isinstance(candles, list) or not candles:
        return [], "no candles"
    return candles, ""


def _session_date(
    raw: object, config: FetchConfig, tz: ZoneInfo
) -> tuple[str | None, str]:
    """Validate an exchange-local timestamp and return its session date."""
    if not isinstance(raw, str) or not raw:
        return None, "invalid timestamp"
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None, "invalid timestamp"
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None, "naive timestamp"
    if config.require_midnight and (
        stamp.hour,
        stamp.minute,
        stamp.second,
        stamp.microsecond,
    ) != (0, 0, 0, 0):
        return None, "non-midnight timestamp"
    local = stamp.astimezone(tz)
    if stamp.date() != local.date() or stamp.utcoffset() != local.utcoffset():
        return None, "timestamp offset mismatch"
    return local.date().isoformat(), ""


def _valid_price(raw: object) -> float | None:
    """Return a finite price, rejecting booleans and non-numeric values."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


def classify_volume(raw: object) -> tuple[int | None, VolumeValidity]:
    """Validate volume as an exact non-negative count."""
    if raw is None:
        return None, VolumeValidity.MISSING
    if isinstance(raw, bool):
        return None, VolumeValidity.INVALID
    if isinstance(raw, int):
        if 0 <= raw <= MAX_VOLUME:
            return raw, VolumeValidity.VALID
        return None, VolumeValidity.INVALID
    if isinstance(raw, float):
        if (
            math.isfinite(raw)
            and 0 <= raw < MAX_EXACT_DOUBLE_INTEGER
            and raw.is_integer()
        ):
            return int(raw), VolumeValidity.VALID
    return None, VolumeValidity.INVALID


def classify_open_interest(raw: object) -> tuple[int | None, str]:
    """Validate the OI sentinel contract and return an exact integer."""
    if raw is None:
        return None, "missing open interest"
    if isinstance(raw, bool):
        return None, "invalid open interest"
    if isinstance(raw, int):
        if -1 <= raw < MAX_OPEN_INTEREST:
            return raw, ""
        return None, "invalid open interest"
    if isinstance(raw, float):
        if (
            math.isfinite(raw)
            and -1 <= raw < MAX_EXACT_DOUBLE_INTEGER
            and raw.is_integer()
        ):
            return int(raw), ""
    return None, "invalid open interest"


def _parse_prices(
    raw_prices: Sequence[object], session: str
) -> tuple[float, float, float, float]:
    """Validate the four price fields of one candle."""
    prices: list[float] = []
    for raw in raw_prices:
        price = _valid_price(raw)
        if price is None:
            raise _RowError("invalid price", session)
        prices.append(price)
    return prices[0], prices[1], prices[2], prices[3]


def _parse_row(
    row: object, config: FetchConfig, tz: ZoneInfo
) -> tuple[DailyCandle, str]:
    """Validate one seven-field candle without dropping malformed rows."""
    if not isinstance(row, (list, tuple)) or len(row) != 7:
        raise _RowError("row width mismatch")
    session, error = _session_date(row[0], config, tz)
    if error:
        raise _RowError(error)
    opening, high, low, close = _parse_prices(row[1:5], session)
    if (
        low <= 0
        or close <= 0
        or high < low
        or low > min(opening, close)
        or max(opening, close) > high
    ):
        raise _RowError("invalid OHLC relationship", session)
    volume, validity = classify_volume(row[5])
    if validity is not VolumeValidity.VALID:
        reason = (
            "volume missing"
            if validity is VolumeValidity.MISSING
            else "invalid volume"
        )
        raise _RowError(reason, session, validity.value)
    oi, error = classify_open_interest(row[6])
    if error:
        raise _RowError(error, session)
    candle = DailyCandle(
        session,
        opening,
        high,
        low,
        close,
        volume,  # type: ignore[arg-type]
        oi,  # type: ignore[arg-type]
        validity,
    )
    return candle, session


def _resolve_expected(
    source: Sequence[str] | SessionCalendar | None,
    start: str,
    end: str,
) -> tuple[tuple[str, ...], list[str]]:
    """Resolve and validate the caller's expected session calendar."""
    if source is None:
        return (), []
    values = source(start, end) if callable(source) else source
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return (), ["invalid expected sessions"]
    errors: list[str] = []
    sessions: list[str] = []
    for value in values:
        if not _is_date(value) or not start <= value <= end:
            errors.append("invalid expected session")
            continue
        sessions.append(value)
    if len(set(sessions)) != len(sessions):
        errors.append("duplicate expected sessions")
    return tuple(sorted(set(sessions))), errors


def _collect_records(
    responses: Sequence[tuple[DateRange, HttpResponse]],
    config: FetchConfig,
    tz: ZoneInfo,
) -> tuple[list[DailyCandle], list[str], list[Rejection]]:
    """Validate all raw responses and collect explicit rejections."""
    records: list[DailyCandle] = []
    sessions: list[str] = []
    rejections: list[Rejection] = []
    for batch_index, (_batch, response) in enumerate(responses):
        failure = _classify_response(response)
        if failure is not None:
            failure_class, detail = failure
            if failure_class is FailureClass.RATE_LIMIT:
                reason = "rate limited"
            elif failure_class is FailureClass.TRANSPORT_ERROR:
                reason = "transport error"
            else:
                reason = "http error"
            rejections.append(
                Rejection(
                    reason,
                    failure_class,
                    batch_index=batch_index,
                    detail=detail,
                )
            )
            continue
        try:
            payload = _strict_json_loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            rejections.append(
                Rejection(
                    "malformed response",
                    FailureClass.MALFORMED_RESPONSE,
                    batch_index=batch_index,
                )
            )
            continue
        candles, error = _extract_candles(payload)
        if error:
            rejections.append(Rejection(error, batch_index=batch_index))
            continue
        for index, row in enumerate(candles):
            try:
                candle, session = _parse_row(row, config, tz)
            except _RowError as exc:
                rejections.append(
                    Rejection(
                        exc.reason,
                        batch_index=batch_index,
                        row_index=index,
                        detail=exc.detail,
                    )
                )
                if exc.session is not None:
                    sessions.append(exc.session)
                continue
            sessions.append(session)
            records.append(candle)
    return records, sessions, rejections


def _safe_name(canonical_id: str) -> str:
    """Build a deterministic, collision-resistant file stem."""
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "_", canonical_id) or "UNKNOWN_ID"
    suffix = sha256_hex(canonical_id.encode("utf-8"))[:16]
    return f"{cleaned}_{suffix}"


def _raw_name(canonical_id: str, index: int, digest: str) -> str:
    """Build the deterministic raw response filename."""
    return f"{_safe_name(canonical_id)}_{index:04d}_{digest}.json"


def _manifest_name(canonical_id: str) -> str:
    """Build the deterministic manifest filename."""
    return f"{_safe_name(canonical_id)}.manifest.json"


def _digest_name(file_name: str, canonical_id: str) -> str:
    """Extract the digest embedded in a raw filename."""
    prefix = f"{_safe_name(canonical_id)}_"
    if not file_name.startswith(prefix) or not file_name.endswith(".json"):
        raise ReplayError("invalid raw filename")
    return file_name[len(prefix) : -len(".json")].rsplit("_", 1)[-1]


def _assemble_outcome(
    *,
    canonical_id: str,
    instrument_key: str,
    start: str,
    end: str,
    config: FetchConfig,
    responses: Sequence[tuple[DateRange, HttpResponse]],
) -> FetchOutcome:
    """Assemble one deterministic outcome shared by fetch and replay."""
    tz = ZoneInfo(config.exchange_timezone)
    records, sessions, rejections = _collect_records(responses, config, tz)
    counts = Counter(sessions)
    for session in sorted(counts):
        if counts[session] > 1:
            rejections.append(Rejection("duplicate date", detail=session))
    accepted = tuple(
        sorted(
            (row for row in records if counts[row.session] == 1),
            key=lambda row: row.session,
        )
    )
    expected, errors = _resolve_expected(config.expected_sessions, start, end)
    rejections.extend(Rejection(error) for error in errors)
    missing = tuple(sorted(set(expected) - set(sessions)))
    if missing:
        rejections.append(
            Rejection("missing sessions", detail=",".join(missing))
        )
    batches = tuple(
        BatchRecord(
            index=index,
            start=batch.start,
            end=batch.end,
            status=response.status,
            digest=sha256_hex(response.body),
            file_name=_raw_name(canonical_id, index, sha256_hex(response.body)),
            retry_after=_retry_after(response.headers),
        )
        for index, (batch, response) in enumerate(responses)
    )
    return FetchOutcome(
        status="OK" if not rejections else "REJECTED",
        canonical_id=canonical_id,
        instrument_key=instrument_key,
        requested_start=start,
        requested_end=end,
        batches=batches,
        records=accepted,
        rejections=tuple(rejections),
        missing_dates=missing,
        raw_digests=tuple(batch.digest for batch in batches),
    )


def _open_root(directory: Path) -> int:
    """Open a raw directory as a pinned descriptor without symlinks."""
    try:
        return os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as exc:
        raise ReplayError("raw directory unavailable") from exc


def _write_object(root_fd: int, name: str, content: bytes) -> None:
    """Write and sync one raw object inside the pinned directory."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, 0o600, dir_fd=root_fd)
    try:
        view = memoryview(content)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_object(root_fd: int, name: str) -> bytes:
    """Read one regular raw object through the pinned descriptor."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=root_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReplayError("raw object is not a regular file")
        payload = bytearray()
        while chunk := os.read(fd, READ_CHUNK_SIZE):
            if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ReplayError("raw object is too large")
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(fd)


def _manifest_payload(
    canonical_id: str,
    instrument_key: str,
    config: FetchConfig,
    start: str,
    end: str,
    outcome: FetchOutcome,
) -> dict:
    """Build the non-secret manifest used for deterministic replay."""
    sessions = config.expected_sessions
    if callable(sessions):
        stored_sessions: object = "CALLER_SUPPLIED"
    else:
        stored_sessions = list(sessions) if sessions is not None else None
    return {
        "version": MANIFEST_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "canonical_id": canonical_id,
        "instrument_key": instrument_key,
        "base_url": config.base_url,
        "interval": config.interval,
        "max_days_per_request": config.max_days_per_request,
        "exchange_timezone": config.exchange_timezone,
        "require_midnight": config.require_midnight,
        "expected_sessions": stored_sessions,
        SESSION_OMISSION_KEY: sessions is None,
        "max_requests": config.max_requests,
        "environment": config.environment,
        REDIRECT_POLICY_KEY: True,
        "timeout_seconds": config.timeout_seconds,
        "max_rate_limit_retries": config.max_rate_limit_retries,
        "requested_start": start,
        "requested_end": end,
        "batches": [asdict(batch) for batch in outcome.batches],
    }


def _persist_response(
    raw_dir: Path, canonical_id: str, index: int, body: bytes
) -> None:
    """Persist exact response bytes with a digest-bearing filename."""
    digest = sha256_hex(body)
    root_fd = _open_root(raw_dir)
    try:
        _write_object(root_fd, _raw_name(canonical_id, index, digest), body)
    finally:
        os.close(root_fd)


def fetch_daily(
    canonical_id: str,
    start: str,
    end: str,
    *,
    token: str,
    http_client: object,
    resolve_instrument_key: KeyResolver,
    config: FetchConfig | None = None,
    enable_network: bool = False,
) -> FetchOutcome:
    """Fetch and validate daily candles through an injected HTTP client."""
    if enable_network is not True:
        raise NetworkDisabledError(
            "network access is disabled; set enable_network=True"
        )
    cfg = _validate_config(config if config is not None else FetchConfig())
    secret = _validate_token(token)
    control = enforce_transport_policy(http_client)
    batches = iter_batches(start, end, cfg.max_days_per_request)
    if len(batches) > cfg.max_requests:
        raise ConfigError("too many batches requested")
    key = _resolve_key(resolve_instrument_key, canonical_id)
    hosts = _allowed_hosts(cfg.environment)
    responses: list[tuple[DateRange, HttpResponse]] = []
    for index, batch in enumerate(batches):
        response = _fetch_batch(
            http_client,
            _build_url(cfg, key, batch),
            secret,
            hosts,
            cfg.timeout_seconds,
            cfg.max_rate_limit_retries,
            control,
        )
        if cfg.raw_dir is not None:
            cfg.raw_dir.mkdir(parents=True, exist_ok=True)
            _persist_response(cfg.raw_dir, canonical_id, index, response.body)
        responses.append((batch, response))
    outcome = _assemble_outcome(
        canonical_id=canonical_id,
        instrument_key=key,
        start=start,
        end=end,
        config=cfg,
        responses=responses,
    )
    if cfg.raw_dir is not None:
        manifest = _manifest_payload(
            canonical_id, key, cfg, start, end, outcome
        )
        content = (
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        root_fd = _open_root(cfg.raw_dir)
        try:
            _write_object(root_fd, _manifest_name(canonical_id), content)
        finally:
            os.close(root_fd)
    return outcome


def _config_from_manifest(
    entry: object, raw_dir: Path, expected_sessions: object
) -> FetchConfig:
    """Reconstruct validation parameters from a verified manifest."""
    if not isinstance(entry, dict):
        raise ReplayError("invalid manifest")
    stored = entry.get("expected_sessions")
    if stored == "CALLER_SUPPLIED":
        if expected_sessions is None:
            raise ReplayError("expected_sessions is required for this replay")
        resolved = expected_sessions
    elif stored is None:
        # Omission is recorded as evidence; a caller-supplied calendar is
        # honoured instead of silently disabling gap detection.
        resolved = expected_sessions
    else:
        resolved = tuple(stored) if isinstance(stored, list) else None
    try:
        config = FetchConfig(
            base_url=entry["base_url"],
            interval=entry["interval"],
            max_days_per_request=entry["max_days_per_request"],
            exchange_timezone=entry["exchange_timezone"],
            require_midnight=entry["require_midnight"],
            expected_sessions=resolved,
            raw_dir=raw_dir,
            max_requests=entry["max_requests"],
            environment=entry.get("environment", DEFAULT_ENVIRONMENT),
            timeout_seconds=entry.get(
                "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
            ),
            max_rate_limit_retries=entry.get(
                "max_rate_limit_retries", DEFAULT_RATE_LIMIT_RETRIES
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ReplayError("incomplete manifest") from exc
    return _validate_config(config)


def replay_daily(
    raw_dir: Path,
    *,
    canonical_id: str,
    expected_sessions: Sequence[str] | SessionCalendar | None = None,
) -> FetchOutcome:
    """Replay validated raw responses with digest verification."""
    if not isinstance(raw_dir, Path):
        raise ReplayError("raw_dir must be a Path")
    if not isinstance(canonical_id, str) or not canonical_id:
        raise ReplayError("invalid canonical id")
    root_fd = _open_root(raw_dir)
    try:
        try:
            manifest = _strict_json_loads(
                _read_object(root_fd, _manifest_name(canonical_id)).decode(
                    "utf-8"
                )
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ReplayError("manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise ReplayError("invalid manifest")
        if (
            manifest.get("version") != MANIFEST_VERSION
            or manifest.get("digest_algorithm") != DIGEST_ALGORITHM
            or manifest.get("canonical_id") != canonical_id
        ):
            raise ReplayError("manifest identity mismatch")
        config = _config_from_manifest(manifest, raw_dir, expected_sessions)
        entries = manifest.get("batches")
        if not isinstance(entries, list):
            raise ReplayError("invalid manifest batches")
        responses: list[tuple[DateRange, HttpResponse]] = []
        for index, entry in enumerate(entries):
            responses.append(_replay_batch(root_fd, canonical_id, index, entry))
    finally:
        os.close(root_fd)
    return _assemble_outcome(
        canonical_id=canonical_id,
        instrument_key=str(manifest.get("instrument_key", "")),
        start=str(manifest.get("requested_start", "")),
        end=str(manifest.get("requested_end", "")),
        config=config,
        responses=responses,
    )


def _replay_batch(
    root_fd: int, canonical_id: str, index: int, entry: object
) -> tuple[DateRange, HttpResponse]:
    """Verify and reconstruct one persisted raw response."""
    if not isinstance(entry, dict):
        raise ReplayError("invalid batch entry")
    name = entry.get("file_name")
    if (
        not isinstance(name, str)
        or _RAW_NAME_RE.fullmatch(name) is None
        or not name.startswith(f"{_safe_name(canonical_id)}_")
    ):
        raise ReplayError("invalid raw filename")
    try:
        body = _read_object(root_fd, name)
    except OSError as exc:
        raise ReplayError("raw response is unreadable") from exc
    digest = sha256_hex(body)
    if (
        digest != entry.get("digest")
        or _digest_name(name, canonical_id) != digest
        or entry.get("index") != index
    ):
        raise ReplayError("raw digest mismatch")
    retry = entry.get("retry_after")
    headers = {"Retry-After": str(retry)} if retry is not None else {}
    try:
        batch = DateRange(
            _validate_date(entry["start"], "batch start"),
            _validate_date(entry["end"], "batch end"),
        )
        status = int(entry["status"])
    except (KeyError, TypeError, ValueError, ConfigError) as exc:
        raise ReplayError("invalid batch metadata") from exc
    return batch, HttpResponse(status, body, headers)
