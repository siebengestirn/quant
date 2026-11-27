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
"""

from __future__ import annotations

import hashlib
import importlib
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
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
reject_duplicate_keys = importlib.import_module(
    "json_support"
).reject_duplicate_keys

DEFAULT_BASE_URL = "https://api.upstox.com/v3"
HISTORICAL_PATH = "/historical-candle"
DEFAULT_INTERVAL = "day"
DEFAULT_MAX_DAYS = 365
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_MAX_REQUESTS = 1000
MAX_VOLUME = 2**53
MAX_EXACT_DOUBLE_INTEGER = 2**53
MAX_OPEN_INTEREST = 2**63
DIGEST_ALGORITHM = "sha256"
MANIFEST_VERSION = 1

VALID_INTERVALS = frozenset({"day"})
RATE_LIMIT_CODES = frozenset(
    {"UDAPI100026", "UDAPI100027", "TOO_MANY_REQUESTS", "RATE_LIMIT_EXCEEDED"}
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*\|[A-Za-z0-9][A-Za-z0-9 .&/_-]*$")
_RAW_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+_[0-9]{4}_[0-9a-f]{64}\.json$")

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


def _validate_base_url(base_url: object) -> str:
    """Validate an HTTPS V3 base URL without embedded credentials."""
    if not isinstance(base_url, str) or any(
        char.isspace() for char in base_url
    ):
        raise ConfigError("invalid base_url")
    url = urlsplit(base_url)
    if url.scheme != "https" or not url.netloc:
        raise ConfigError("invalid base_url")
    if (
        url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ConfigError("invalid base_url")
    if ".." in url.path:
        raise ConfigError("invalid base_url")
    return base_url.rstrip("/")


def _validate_config(config: FetchConfig) -> FetchConfig:
    """Validate configuration and return its normalised copy."""
    if not isinstance(config, FetchConfig):
        raise ConfigError("config must be a FetchConfig")
    base_url = _validate_base_url(config.base_url)
    if config.interval not in VALID_INTERVALS:
        raise ConfigError("invalid interval")
    for name in ("max_days_per_request", "max_requests"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"invalid {name}")
    if config.max_days_per_request > 366:
        raise ConfigError("max_days_per_request is too large")
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


def _request(http_client: object, url: str, token: str) -> HttpResponse:
    """Issue one authenticated GET using a transient header mapping."""
    get = getattr(http_client, "get", None)
    if not callable(get):
        raise ConfigError("http_client.get(url, headers) is required")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    response = get(url, headers)
    if not isinstance(response, HttpResponse):
        raise ConfigError("http_client.get must return HttpResponse")
    return response


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
            reason = (
                "rate limited"
                if failure_class is FailureClass.RATE_LIMIT
                else "http error"
            )
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
        chunks = list(iter(lambda: os.read(fd, 16384), b""))
        return b"".join(chunks)
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
        "max_requests": config.max_requests,
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
    batches = iter_batches(start, end, cfg.max_days_per_request)
    if len(batches) > cfg.max_requests:
        raise ConfigError("too many batches requested")
    key = _resolve_key(resolve_instrument_key, canonical_id)
    responses: list[tuple[DateRange, HttpResponse]] = []
    for index, batch in enumerate(batches):
        response = _request(http_client, _build_url(cfg, key, batch), secret)
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
        resolved = None
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
