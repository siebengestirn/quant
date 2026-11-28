#!/usr/bin/env python3
"""Read-only FYERS market-data Data Socket adapter (W3-A).

Scope and separation
--------------------
This adapter targets the FYERS *Data Socket* only. It is explicitly separate
from the FYERS *General WebSocket* (order, trade and account events): no
endpoint, frame or handler here opens, subscribes to or processes the General
Socket. The adapter has no order, trade or account capability.

The read-only access token is supplied by the caller on every ``connect`` and
``reconnect`` call. It is handed straight to the injected transport and is
never stored, rendered, logged or placed in an emitted event.

Message handling
----------------
Incoming frames are dispatched on the documented provider ``type`` field:
``cn`` (connection), ``sub`` (subscription ack), ``sf`` (symbol update),
``if`` (index update), ``dp`` (five-level depth), ``unsub`` (unsubscribe ack)
and error frames. Control frames do not require ``symbol`` or ``ltp``.

Recovery and health separation
------------------------------
Current state and event history are recovered through distinct APIs. A single
current snapshot can only establish ``STATE_RECONCILED``; ``HISTORY_RECOVERED``
requires validated typed backfill records. Transport connection, inbound
liveness, market activity and per-instrument freshness are recorded as separate
timestamps. An outgoing heartbeat proves only that a send succeeded.

Qualification status
--------------------
LIVE ENTITLEMENT AND QUALIFICATION REMAIN PENDING. No live credential is used,
no real API is contacted and no successful feed certification is claimed.
Verification is offline only, through an injected in-memory transport.
``enable_network`` defaults to ``False``; real network use requires an
explicit opt-in. Importing this module performs no network I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

PROVIDER = "fyers"

# The only endpoint this adapter may open. The General Socket is recorded as
# forbidden, so the separation is enforced in code.
DATA_SOCKET_ENDPOINT = "wss://datastream.fyers.in/socket/v3/dataSock"
GENERAL_SOCKET_ENDPOINT = "wss://api.fyers.in/socket/v3/generalSock"
FORBIDDEN_ENDPOINTS = (GENERAL_SOCKET_ENDPOINT,)
GENERAL_WEBSOCKET_ENABLED = False

# Official Data Socket message types, from the documented provider schema.
TYPE_CONNECTION = "cn"
TYPE_SUBSCRIPTION = "sub"
TYPE_UNSUBSCRIPTION = "unsub"
TYPE_SYMBOL_UPDATE = "sf"
TYPE_INDEX_UPDATE = "if"
TYPE_DEPTH_UPDATE = "dp"
TYPE_ERROR = "error"
MESSAGE_TYPES = (
    TYPE_CONNECTION,
    TYPE_SUBSCRIPTION,
    TYPE_UNSUBSCRIPTION,
    TYPE_SYMBOL_UPDATE,
    TYPE_INDEX_UPDATE,
    TYPE_DEPTH_UPDATE,
    TYPE_ERROR,
)
MARKET_TYPES = (TYPE_SYMBOL_UPDATE, TYPE_INDEX_UPDATE, TYPE_DEPTH_UPDATE)

KIND_SYMBOL = "symbol"
KIND_INDEX = "index"
KINDS = (KIND_SYMBOL, KIND_INDEX)
INDEX_SUFFIX = "-INDEX"

EVENT_UPDATE = "UPDATE"
EVENT_SNAPSHOT = "SNAPSHOT"
EVENT_BACKFILL = "BACKFILL"
EVENT_GAP_MARKER = "GAP_MARKER"
EVENT_MALFORMED = "MALFORMED"

VALID = "VALID"
MALFORMED = "MALFORMED"
ERROR = "ERROR"
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"

# Gap classifications. Only an authoritative historical backfill may
# establish HISTORY_RECOVERED; a current snapshot establishes, at most,
# STATE_RECONCILED.
STATE_RECONCILED = "STATE_RECONCILED"
HISTORY_RECOVERED = "HISTORY_RECOVERED"
HISTORY_INCOMPLETE = "HISTORY_INCOMPLETE"
UNRECOVERABLE = "UNRECOVERABLE"
PENDING = "PENDING"
GAP_STATUSES = (
    STATE_RECONCILED,
    HISTORY_RECOVERED,
    HISTORY_INCOMPLETE,
    UNRECOVERABLE,
)
STATUS_RECONCILED = STATE_RECONCILED
STATUS_HISTORY_RECOVERED = HISTORY_RECOVERED
STATUS_HISTORY_INCOMPLETE = HISTORY_INCOMPLETE
TYPE_SF = TYPE_SYMBOL_UPDATE
TYPE_IF = TYPE_INDEX_UPDATE
TYPE_DP = TYPE_DEPTH_UPDATE
# Legacy names retained so older callers keep reading the same values.
RECOVERABLE = STATE_RECONCILED
PARTIAL = HISTORY_INCOMPLETE

DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_SUBSCRIPTIONS = 64
DEFAULT_DEDUPE_WINDOW = 4096
DEFAULT_STALE_AFTER_NS = 30_000_000_000
# Maximum spacing tolerated between consecutive backfill events for one
# instrument; a larger spacing is an unexplained internal gap.
DEFAULT_MAX_BACKFILL_GAP_NS = 10_000_000_000
HEARTBEAT_FRAME = '{"action":"heartbeat"}'

# Only these sources may assert complete historical coverage. A current
# snapshot or an unspecified source can never establish HISTORY_RECOVERED.
AUTHORIZED_HISTORY_SOURCES = frozenset(
    {
        "BACKFILL",
        "HISTORY",
        "HISTORY_BACKFILL",
        "HISTORY_REPLAY",
        "PROVIDER_HISTORY",
        "PROVIDER_REPLAY",
        "REST_HISTORY",
    }
)

# Bounds are finite and enforced before any value is emitted.
MAX_TOTAL_QUANTITY = 10**18
MAX_ORDER_COUNT = 10**9
MAX_PRICE = 10**9
MAX_TIMESTAMP = 4_102_444_800
MIN_CODE = -100_000
MAX_CODE = 100_000

_FLOAT_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_INTEGER_TEXT = re.compile(r"[+-]?\d+\Z")
_DIGEST_TEXT = re.compile(r"[0-9a-f]{64}\Z")
MAX_BACKFILL_TIMESTAMP_NS = 2**63 - 1

_PRICE_FIELDS = (
    "prev_close_price",
    "high_price",
    "low_price",
    "open_price",
    "ch",
    "chp",
    "avg_trade_price",
    "bid_price",
    "ask_price",
)
_SYMBOL_INTEGER_FIELDS = (
    "vol_traded_today",
    "bid_size",
    "ask_size",
    "last_traded_qty",
    "tot_buy_qty",
    "tot_sell_qty",
)
_TIME_FIELDS = ("last_traded_time", "exch_feed_time")


class SocketError(Exception):
    """Base class for adapter-local failures."""


class NetworkDisabledError(SocketError):
    """A network transport was used without explicit opt-in."""


class ForbiddenEndpointError(SocketError):
    """A General WebSocket endpoint was requested; never allowed here."""


class NotConnectedError(SocketError):
    """The operation requires an open Data Socket connection."""


class ShutdownError(SocketError):
    """The operation was attempted after controlled shutdown."""


class SubscriptionError(SocketError):
    """The subscription request is invalid."""


class SubscriptionLimitError(SocketError):
    """The configured subscription capacity would be exceeded."""


class ConnectionFailedError(SocketError):
    """Open failed within the bounded retry budget."""


class ReconciliationError(SocketError):
    """Reconciliation was requested with no open connection gap."""


class ValidationError(SocketError):
    """A raw payload does not satisfy the accepted message schema."""


class TransportError(Exception):
    """Raised by an injected transport on I/O failure."""


class SocketTransport(Protocol):
    """The five transport primitives the adapter needs."""

    requires_network: bool

    def open(self, endpoint: str, token: str) -> None:
        """Open the endpoint with the supplied read-only token."""
        return None

    def send(self, text: str) -> None:
        """Send one frame."""
        return None

    def poll(self, timeout_ns: int) -> bytes | None:
        """Return one raw frame, or None when nothing is available."""
        return None

    def close(self) -> None:
        """Close the transport."""
        return None


class Clock(Protocol):
    """Injected clock so tests never sleep or read real time."""

    def wall_clock_ns(self) -> int:
        """Return wall-clock nanoseconds."""
        return 0

    def monotonic_ns(self) -> int:
        """Return monotonic nanoseconds."""
        return 0


class SystemClock:
    """Real clock source used outside tests."""

    def wall_clock_ns(self) -> int:
        """Return wall-clock nanoseconds."""
        return time.time_ns()

    def monotonic_ns(self) -> int:
        """Return monotonic nanoseconds."""
        return time.monotonic_ns()


@dataclass(frozen=True)
class BackfillRecord:
    """One typed historical event used to validate a connection gap.

    ``event_timestamp`` and ``receive_timestamp`` are wall-clock nanoseconds.
    ``provider_sequence_if_available`` is retained only when the provider
    supplies it; no sequence is invented. ``payload_digest`` is the SHA-256 of
    the provider payload and is carried as evidence, never recomputed from a
    synthesised frame.
    """

    canonical_instrument_id: str
    event_timestamp: int
    receive_timestamp: int
    source: str
    payload_digest: str
    provider_sequence_if_available: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the full non-secret evidence carried by this record."""
        return {
            "canonical_instrument_id": self.canonical_instrument_id,
            "event_timestamp": self.event_timestamp,
            "provider_sequence_if_available": (
                self.provider_sequence_if_available
            ),
            "receive_timestamp": self.receive_timestamp,
            "source": self.source,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True)
class BackfillValidation:
    """The outcome of validating typed records against an open gap."""

    records: tuple[BackfillRecord, ...]
    covered: tuple[str, ...]
    missing: tuple[str, ...]
    reasons: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class _ValidatedBackfill:
    """A backfill record that passed field-level validation."""

    record: BackfillRecord
    symbol: str
    event_timestamp: int
    receive_timestamp: int
    sequence: int | None


@dataclass(frozen=True)
class GapRecord:
    """An explicit connection gap; never a continuity assertion."""

    gap_id: str
    connection_id_before: str
    connection_id_after: str
    started_wall_clock_ns: int
    started_monotonic_ns: int
    ended_wall_clock_ns: int = 0
    ended_monotonic_ns: int = 0
    classification: str = UNRECOVERABLE
    covered_symbols: tuple[str, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    source: str = ""
    authoritative: bool = False
    backfill_records: tuple[BackfillRecord, ...] = ()
    validation_reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """Alias for the gap classification."""
        return self.classification

    def as_dict(self) -> dict[str, object]:
        """Return a canonical shape for this gap."""
        return {
            "gap_id": self.gap_id,
            "connection_id_before": self.connection_id_before,
            "connection_id_after": self.connection_id_after,
            "started_wall_clock_ns": self.started_wall_clock_ns,
            "started_monotonic_ns": self.started_monotonic_ns,
            "ended_wall_clock_ns": self.ended_wall_clock_ns,
            "ended_monotonic_ns": self.ended_monotonic_ns,
            "classification": self.classification,
            "covered_symbols": list(self.covered_symbols),
            "missing_symbols": list(self.missing_symbols),
            "source": self.source,
            "authoritative": self.authoritative,
            "backfill_records": tuple(
                record.as_dict() for record in self.backfill_records
            ),
            "validation_reasons": list(self.validation_reasons),
        }


@dataclass(frozen=True)
class CanonicalEvent:
    """One provider-attributed market-data emission."""

    provider: str
    connection_id: str
    receive_wall_clock_ns: int
    receive_monotonic_ns: int
    canonical_instrument_id: str | None
    validation_status: str
    event_type: str
    symbol: str
    kind: str
    fields: Mapping[str, object]
    raw_sha256: str
    raw_payload: bytes
    duplicate: bool
    stale: bool
    completeness: str
    gap: GapRecord | None

    def as_dict(self) -> dict[str, object]:
        """Return the canonical JSON-shaped emission of this event."""
        return {
            "provider": self.provider,
            "connection_id": self.connection_id,
            "receive_wall_clock_ns": self.receive_wall_clock_ns,
            "receive_monotonic_ns": self.receive_monotonic_ns,
            "canonical_instrument_id": self.canonical_instrument_id,
            "validation_status": self.validation_status,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "kind": self.kind,
            "fields": dict(self.fields),
            "raw_sha256": self.raw_sha256,
            "raw_payload": self.raw_payload.decode("utf-8", "replace"),
            "duplicate": self.duplicate,
            "stale": self.stale,
            "completeness": self.completeness,
            "gap": None if self.gap is None else self.gap.as_dict(),
        }


Resolver = Callable[[str, str], str | None]
Sink = Callable[[CanonicalEvent], None]


@dataclass(frozen=True)
class Subscription:
    """One desired subscription and its per-symbol freshness evidence."""

    symbol: str
    kind: str
    active: bool
    connection_id: str
    subscribed_monotonic_ns: int
    ack_monotonic_ns: int | None = None
    last_receive_wall_clock_ns: int | None = None
    last_receive_monotonic_ns: int | None = None
    last_valid_event_wall_clock_ns: int | None = None
    last_valid_event_monotonic_ns: int | None = None
    reconciliation_status: str = PENDING

    def is_fresh(self, now_ns: int, stale_after_ns: int) -> bool:
        """Report whether this instrument has a recent valid event."""
        last = self.last_valid_event_monotonic_ns
        return last is not None and now_ns - last <= stale_after_ns

    def ack_is_fresh(self, now_ns: int, stale_after_ns: int) -> bool:
        """Report whether a recent subscription acknowledgement exists."""
        last = self.ack_monotonic_ns
        return (
            self.active and last is not None and now_ns - last <= stale_after_ns
        )

    @property
    def last_receive_ns(self) -> int | None:
        """Alias for the monotonic last-receive timestamp."""
        return self.last_receive_monotonic_ns

    @property
    def last_valid_event_ns(self) -> int | None:
        """Alias for the monotonic last-valid-event timestamp."""
        return self.last_valid_event_monotonic_ns

    def as_dict(self) -> dict[str, object]:
        """Return non-secret freshness evidence for this symbol."""
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "active": self.active,
            "connection_id": self.connection_id,
            "subscribed_monotonic_ns": self.subscribed_monotonic_ns,
            "ack_monotonic_ns": self.ack_monotonic_ns,
            "last_receive_wall_clock_ns": self.last_receive_wall_clock_ns,
            "last_receive_monotonic_ns": self.last_receive_monotonic_ns,
            "last_valid_event_wall_clock_ns": (
                self.last_valid_event_wall_clock_ns
            ),
            "last_valid_event_monotonic_ns": (
                self.last_valid_event_monotonic_ns
            ),
            "reconciliation_status": self.reconciliation_status,
        }


SymbolFreshness = Subscription


@dataclass(frozen=True)
class AdapterOptions:
    """Non-secret policy and injected collaborators for the adapter."""

    max_retries: int = DEFAULT_MAX_RETRIES
    max_subscriptions: int = DEFAULT_MAX_SUBSCRIPTIONS
    stale_after_ns: int = DEFAULT_STALE_AFTER_NS
    max_backfill_gap_ns: int = DEFAULT_MAX_BACKFILL_GAP_NS
    dedupe_window: int = DEFAULT_DEDUPE_WINDOW
    clock: Clock | None = None
    resolver: Resolver | None = None
    event_sink: Sink | None = None


@dataclass(frozen=True)
class ParsedMessage:
    """One decoded provider frame, before canonical emission."""

    message_type: str
    symbol: str
    kind: str
    fields: Mapping[str, object]
    status: str = VALID
    market: bool = False


@dataclass(frozen=True)
class EventRequest:
    """The inputs used to build one canonical event."""

    symbol: str
    kind: str
    fields: Mapping[str, object]
    status: str
    payload: bytes
    event_type: str
    complete: bool
    dedupe: bool = True
    gap: GapRecord | None = None


def _exact_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = MAX_TOTAL_QUANTITY,
    allow_text: bool = False,
) -> int:
    """Return an exact bounded integer; never truncate or coerce."""
    if isinstance(value, bool) or not isinstance(value, int):
        if not allow_text or not isinstance(value, str):
            raise ValidationError("value is not an exact integer")
        if not _INTEGER_TEXT.fullmatch(value.strip()):
            raise ValidationError("invalid integer representation")
        value = int(value.strip())
    if value < minimum or value > maximum:
        raise ValidationError("integer outside accepted bounds")
    return value


def _exact_float(value: object, *, allow_text: bool = True) -> float:
    """Return a finite bounded price, accepting documented numeric text."""
    if isinstance(value, bool):
        raise ValidationError("boolean is not a price")
    if isinstance(value, str):
        if not allow_text or not _FLOAT_TEXT.fullmatch(value.strip()):
            raise ValidationError("invalid numeric representation")
        value = float(value)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError("value is not a finite number")
    number = float(value)
    if abs(number) > MAX_PRICE:
        raise ValidationError("price outside accepted bounds")
    return number


def _strict_json(raw: bytes) -> object:
    """Decode UTF-8 JSON, rejecting duplicate object keys."""

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("payload is not valid UTF-8 JSON") from exc


def _infer_kind(symbol: str) -> str:
    """Infer symbol vs index from the documented FYERS symbol suffix."""
    if symbol.upper().endswith(INDEX_SUFFIX):
        return KIND_INDEX
    return KIND_SYMBOL


def _optional_text(obj: Mapping[str, object], key: str) -> str | None:
    """Return an optional non-empty provider text field."""
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"invalid {key}")
    if not value.strip():
        raise ValidationError(f"empty {key}")
    return value


def _message_type(obj: Mapping[str, object]) -> str:
    """Resolve the documented provider message type."""
    raw = obj.get("type")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in MESSAGE_TYPES:
            return normalized
        if normalized in ("e", "err", "exception"):
            return TYPE_ERROR
    if obj.get("s") == "error" or "error" in obj:
        return TYPE_ERROR
    raise ValidationError("unsupported or missing message type")


def _validate_control(
    obj: Mapping[str, object], message_type: str
) -> ParsedMessage:
    """Validate cn/sub/unsub/error frames without requiring symbol or ltp."""
    fields: dict[str, object] = {}
    code = obj.get("code")
    if code is not None:
        fields["code"] = _exact_int(
            code, minimum=MIN_CODE, maximum=MAX_CODE, allow_text=True
        )
    for key in ("message", "s"):
        text = _optional_text(obj, key)
        if text is not None:
            fields[key] = text
    symbol = _optional_text(obj, "symbol")
    if symbol is not None:
        fields["symbol"] = symbol
    status = VALID
    if message_type == TYPE_ERROR or fields.get("s") == "error":
        status = ERROR
    elif isinstance(fields.get("code"), int) and fields["code"] >= 400:
        status = ERROR
    return ParsedMessage(message_type, symbol or "", "", fields, status)


def _quote_fields(
    obj: Mapping[str, object], message_type: str
) -> dict[str, object]:
    """Map the documented sf/if scalar fields to canonical fields."""
    symbol = _optional_text(obj, "symbol")
    if symbol is None:
        raise ValidationError("missing or non-string symbol")
    ltp = obj.get("ltp")
    if ltp is None:
        raise ValidationError("missing ltp")
    fields: dict[str, object] = {
        "symbol": symbol,
        "ltp": _exact_float(ltp),
        "type": message_type,
    }
    for key in _PRICE_FIELDS:
        value = obj.get(key)
        if value is not None:
            fields[key] = _exact_float(value)
    if message_type == TYPE_SYMBOL_UPDATE:
        for key in _SYMBOL_INTEGER_FIELDS:
            value = obj.get(key)
            if value is not None:
                fields[key] = _exact_int(value)
    for key in _TIME_FIELDS:
        value = obj.get(key)
        if value is not None:
            fields[key] = _exact_int(
                value,
                minimum=0,
                maximum=MAX_TIMESTAMP,
                allow_text=True,
            )
    return fields


def _depth_fields(obj: Mapping[str, object]) -> dict[str, object]:
    """Map the five documented bid/ask price, size and order levels."""
    symbol = _optional_text(obj, "symbol")
    if symbol is None:
        raise ValidationError("missing or non-string symbol")
    fields: dict[str, object] = {"symbol": symbol, "type": TYPE_DEPTH_UPDATE}
    levels = 0
    ltp = obj.get("ltp")
    if ltp is not None:
        fields["ltp"] = _exact_float(ltp)
    for side in ("bid", "ask"):
        for level in range(1, 6):
            price_key = f"{side}_price{level}"
            size_key = f"{side}_size{level}"
            order_key = f"{side}_order{level}"
            if obj.get(price_key) is not None:
                fields[price_key] = _exact_float(obj[price_key])
                levels += 1
            if obj.get(size_key) is not None:
                fields[size_key] = _exact_int(obj[size_key])
                levels += 1
            if obj.get(order_key) is not None:
                fields[order_key] = _exact_int(
                    obj[order_key], maximum=MAX_ORDER_COUNT
                )
                levels += 1
    if levels == 0:
        raise ValidationError("depth frame has no levels")
    return fields


def _validate_message(raw: bytes, kind_hint: str | None) -> ParsedMessage:
    """Dispatch one raw frame by its documented provider message type."""
    obj = _strict_json(raw)
    if not isinstance(obj, Mapping):
        raise ValidationError("payload must be a JSON object")
    message_type = _message_type(obj)
    if message_type in (TYPE_SYMBOL_UPDATE, TYPE_INDEX_UPDATE):
        kind = KIND_INDEX if message_type == TYPE_INDEX_UPDATE else KIND_SYMBOL
        if kind_hint is not None and kind_hint != kind:
            raise ValidationError(f"kind disagrees with {message_type}")
        return ParsedMessage(
            message_type,
            str(obj["symbol"]),
            kind,
            _quote_fields(obj, message_type),
            VALID,
            True,
        )
    if message_type == TYPE_DEPTH_UPDATE:
        if kind_hint is not None and kind_hint != KIND_SYMBOL:
            raise ValidationError("depth updates are symbol updates")
        return ParsedMessage(
            message_type,
            str(obj["symbol"]),
            KIND_SYMBOL,
            _depth_fields(obj),
            VALID,
            True,
        )
    if kind_hint is not None:
        raise ValidationError("control frames have no market kind")
    return _validate_control(obj, message_type)


def _safe_symbol(raw: bytes) -> str:
    """Best-effort symbol attribution for a frame that failed validation."""
    try:
        obj = _strict_json(raw)
    except ValidationError:
        return ""
    if not isinstance(obj, Mapping):
        return ""
    symbol = obj.get("symbol")
    return symbol if isinstance(symbol, str) and symbol.strip() else ""


def _subscribe_frame(symbol: str, kind: str) -> str:
    """Build the SDK-shaped subscribe request."""
    return json.dumps(
        {"action": "subscribe", "args": {"symbol": symbol, "kind": kind}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _unsubscribe_frame(symbol: str) -> str:
    """Build the SDK-shaped unsubscribe request."""
    return json.dumps(
        {"action": "unsubscribe", "args": {"symbol": symbol}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _gap_classification(
    covered: int,
    total: int,
    *,
    authoritative: bool,
    history_requested: bool = False,
) -> str:
    """Classify a closed gap; snapshots can never recover history."""
    if total == 0:
        return STATE_RECONCILED
    if covered == 0:
        return UNRECOVERABLE
    if covered < total or (history_requested and not authoritative):
        return HISTORY_INCOMPLETE
    if authoritative:
        return HISTORY_RECOVERED
    return STATE_RECONCILED


def _backfill_candidates(backfill: object) -> tuple[object, ...]:
    """Flatten backfill inputs without collapsing a sequence to one point."""
    if isinstance(backfill, BackfillRecord):
        return (backfill,)
    if isinstance(backfill, Mapping):
        entries: list[object] = []
        for value in backfill.values():
            if isinstance(value, (list, tuple)):
                entries.extend(value)
            else:
                entries.append(value)
        return tuple(entries)
    if isinstance(backfill, (list, tuple)):
        return tuple(backfill)
    return () if backfill is None else (backfill,)


class FyersDataSocket:  # pylint: disable=too-many-public-methods
    """Read-only Data Socket lifecycle and canonical event adapter.

    ``connection_id`` is an adapter-local correlation identifier; it is not a
    provider sequence. No provider sequence is invented, inferred or emitted.
    """

    def __init__(
        self,
        transport: SocketTransport,
        *,
        enable_network: bool = False,
        options: AdapterOptions | None = None,
    ) -> None:
        self._transport = transport
        self._options = options or AdapterOptions()
        self._enable_network = enable_network
        self._clock: Clock = self._options.clock or SystemClock()
        self._connected = False
        self._closed = False
        self._connection_id = ""
        self._connection_seq = 0
        self._subscriptions: dict[str, Subscription] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._gaps: list[GapRecord] = []
        self._open_gap: GapRecord | None = None
        # Each timestamp has one meaning; none is inferred from another.
        self._last_successful_send_ns: int | None = None
        self._last_inbound_frame_ns: int | None = None
        self._last_server_heartbeat_or_pong_ns: int | None = None
        self._last_valid_symbol_event_ns: int | None = None
        self._last_valid_index_event_ns: int | None = None

    def _require_active(self) -> None:
        if self._closed:
            raise ShutdownError("adapter has been shut down")
        if not self._connected:
            raise NotConnectedError("Data Socket is not connected")

    def _require_not_closed(self) -> None:
        if self._closed:
            raise ShutdownError("adapter has been shut down")

    def _check_network(self) -> None:
        needs_network = getattr(self._transport, "requires_network", True)
        if needs_network and not self._enable_network:
            raise NetworkDisabledError(
                "network use requires enable_network=True"
            )

    def validate_endpoint(self, endpoint: str) -> None:
        """Reject any endpoint other than the read-only Data Socket."""
        if GENERAL_WEBSOCKET_ENABLED or endpoint in FORBIDDEN_ENDPOINTS:
            raise ForbiddenEndpointError(
                "General WebSocket is out of scope for this adapter"
            )
        if endpoint != DATA_SOCKET_ENDPOINT:
            raise ForbiddenEndpointError("endpoint is not the Data Socket")

    def _open_with_retries(self, token: str) -> bool:
        for _ in range(self._options.max_retries):
            try:
                self._transport.open(DATA_SOCKET_ENDPOINT, token)
                return True
            except TransportError:
                continue
        return False

    def _send_with_retries(self, text: str) -> bool:
        for _ in range(self._options.max_retries):
            try:
                self._transport.send(text)
                return True
            except TransportError:
                continue
        return False

    def _reset_connection_state(self) -> None:
        """Drop freshness claims that belonged to the previous connection."""
        self._last_successful_send_ns = None
        self._last_inbound_frame_ns = None
        self._last_server_heartbeat_or_pong_ns = None
        self._last_valid_symbol_event_ns = None
        self._last_valid_index_event_ns = None
        self._subscriptions = {
            symbol: replace(
                sub,
                active=False,
                connection_id=self._connection_id,
                ack_monotonic_ns=None,
                last_receive_wall_clock_ns=None,
                last_receive_monotonic_ns=None,
                last_valid_event_wall_clock_ns=None,
                last_valid_event_monotonic_ns=None,
                reconciliation_status=PENDING,
            )
            for symbol, sub in self._subscriptions.items()
        }

    def connect(self, token: str) -> None:
        """Open the Data Socket; the caller-supplied token is not retained."""
        self._require_not_closed()
        if not token:
            raise ValueError("read-only token must be supplied by caller")
        self._check_network()
        self.validate_endpoint(DATA_SOCKET_ENDPOINT)
        if not self._open_with_retries(token):
            raise ConnectionFailedError("Data Socket open failed")
        self._connection_seq += 1
        self._connection_id = f"fyers-data-{self._connection_seq}"
        self._connected = True
        self._reset_connection_state()

    def subscribe(self, symbol: str, kind: str | None = None) -> bool:
        """Subscribe to one symbol or index; return True when newly added."""
        self._require_active()
        if not isinstance(symbol, str) or not symbol.strip():
            raise SubscriptionError("symbol must be a non-empty string")
        resolved = kind or _infer_kind(symbol)
        if resolved not in KINDS:
            raise SubscriptionError(f"unknown kind: {resolved}")
        if symbol in self._subscriptions:
            return False
        if len(self._subscriptions) >= self._options.max_subscriptions:
            raise SubscriptionLimitError("subscription limit reached")
        if not self._send_with_retries(_subscribe_frame(symbol, resolved)):
            raise TransportError("subscribe frame was not accepted")
        self._subscriptions[symbol] = Subscription(
            symbol=symbol,
            kind=resolved,
            active=True,
            connection_id=self._connection_id,
            subscribed_monotonic_ns=self._clock.monotonic_ns(),
        )
        return True

    def unsubscribe(self, symbol: str) -> bool:
        """Remove one subscription; return True when it existed."""
        self._require_active()
        if symbol not in self._subscriptions:
            return False
        if not self._send_with_retries(_unsubscribe_frame(symbol)):
            raise TransportError("unsubscribe frame was not accepted")
        del self._subscriptions[symbol]
        return True

    def resubscribe(self) -> int:
        """Re-send subscriptions on the current connection; count restores."""
        self._require_active()
        restored = 0
        for symbol, sub in tuple(self._subscriptions.items()):
            if sub.active:
                continue
            if self._send_with_retries(_subscribe_frame(symbol, sub.kind)):
                self._subscriptions[symbol] = replace(
                    sub,
                    active=True,
                    connection_id=self._connection_id,
                    ack_monotonic_ns=None,
                )
                restored += 1
        return restored

    def heartbeat(self) -> bool:
        """Send one keepalive frame; this never proves inbound liveness."""
        self._require_active()
        sent = self._send_with_retries(HEARTBEAT_FRAME)
        if sent:
            self._last_successful_send_ns = self._clock.monotonic_ns()
        return sent

    def poll(self, timeout_ns: int = 0) -> tuple[CanonicalEvent, ...]:
        """Receive at most one raw payload and emit its canonical event."""
        self._require_active()
        raw = self._transport.poll(timeout_ns)
        if raw is None:
            return ()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return (self.process_payload(raw),)

    def _remember(self, key: str) -> bool:
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        if len(self._seen) > self._options.dedupe_window:
            self._seen.popitem(last=False)
        return False

    def _resolve(self, symbol: str, kind: str) -> str | None:
        resolver = self._options.resolver
        if resolver is None:
            return None
        try:
            return resolver(symbol, kind)
        except ValueError:
            return None

    @property
    def last_successful_send_ns(self) -> int | None:
        """Monotonic time of the last frame accepted by the transport."""
        return self._last_successful_send_ns

    @property
    def last_inbound_frame_ns(self) -> int | None:
        """Monotonic time of the last raw frame received from the server."""
        return self._last_inbound_frame_ns

    @property
    def last_server_heartbeat_or_pong_ns(self) -> int | None:
        """Monotonic time of the last documented ``cn`` server frame."""
        return self._last_server_heartbeat_or_pong_ns

    @property
    def last_valid_symbol_event_ns(self) -> int | None:
        """Monotonic time of the last valid symbol market event."""
        return self._last_valid_symbol_event_ns

    @property
    def last_valid_index_event_ns(self) -> int | None:
        """Monotonic time of the last valid index market event."""
        return self._last_valid_index_event_ns

    def _is_fresh(self, last_ns: int | None, now_ns: int) -> bool:
        return (
            last_ns is not None
            and now_ns - last_ns <= self._options.stale_after_ns
        )

    def _is_stale(self, now_ns: int) -> bool:
        """Inbound staleness never uses an outbound send timestamp."""
        return not self._is_fresh(self._last_inbound_frame_ns, now_ns)

    def is_stale(self) -> bool:
        """Report whether the inbound feed has gone quiet or never arrived."""
        return self._is_stale(self._clock.monotonic_ns())

    def inbound_live(self) -> bool:
        """Report recent inbound traffic independently of heartbeats."""
        return not self.is_stale()

    def market_active(self) -> bool:
        """Report recent valid market activity of either market kind."""
        now_ns = self._clock.monotonic_ns()
        return self._is_fresh(
            self._last_valid_symbol_event_ns, now_ns
        ) or self._is_fresh(self._last_valid_index_event_ns, now_ns)

    def transport_connected(self) -> bool:
        """Report only Data Socket connection state, not data liveness."""
        return self._connected and not self._closed

    def is_heartbeat_fresh(self) -> bool:
        """Report a recent successful send or server heartbeat/pong."""
        now_ns = self._clock.monotonic_ns()
        return self._is_fresh(
            self._last_successful_send_ns, now_ns
        ) or self._is_fresh(self._last_server_heartbeat_or_pong_ns, now_ns)

    def feed_healthy(self) -> bool:
        """Require a connection and live inbound frames, nothing more."""
        return self.transport_connected() and self.inbound_live()

    def is_symbol_fresh(self, symbol: str) -> bool:
        """Report whether one instrument has a recent valid event."""
        sub = self._subscriptions.get(symbol)
        if sub is None:
            return False
        return sub.is_fresh(
            self._clock.monotonic_ns(), self._options.stale_after_ns
        )

    def is_subscription_fresh(self, symbol: str) -> bool:
        """Report whether one instrument has a recent subscription ack."""
        sub = self._subscriptions.get(symbol)
        if sub is None:
            return False
        return sub.ack_is_fresh(
            self._clock.monotonic_ns(), self._options.stale_after_ns
        )

    def symbol_freshness(self, symbol: str) -> Subscription | None:
        """Return immutable freshness evidence for one instrument."""
        return self._subscriptions.get(symbol)

    def _touch_receive(self, symbol: str, wall_ns: int, mono_ns: int) -> None:
        sub = self._subscriptions.get(symbol)
        if sub is None:
            return
        self._subscriptions[symbol] = replace(
            sub,
            last_receive_wall_clock_ns=wall_ns,
            last_receive_monotonic_ns=mono_ns,
        )

    def _touch_valid(self, symbol: str, wall_ns: int, mono_ns: int) -> None:
        sub = self._subscriptions.get(symbol)
        if sub is None:
            return
        self._subscriptions[symbol] = replace(
            sub,
            last_valid_event_wall_clock_ns=wall_ns,
            last_valid_event_monotonic_ns=mono_ns,
        )

    def _record_ack(
        self, message: ParsedMessage, wall_ns: int, mono_ns: int
    ) -> None:
        sub = self._subscriptions.get(message.symbol)
        if sub is None:
            return
        self._subscriptions[message.symbol] = replace(
            sub,
            active=True,
            connection_id=self._connection_id,
            ack_monotonic_ns=mono_ns,
            last_receive_wall_clock_ns=wall_ns,
            last_receive_monotonic_ns=mono_ns,
        )

    def _emit(self, event: CanonicalEvent) -> None:
        sink = self._options.event_sink
        if sink is not None:
            sink(event)

    def _build_event(self, request: EventRequest) -> CanonicalEvent:
        """Hash, stamp and emit one canonical event."""
        wall_ns = self._clock.wall_clock_ns()
        mono_ns = self._clock.monotonic_ns()
        digest = hashlib.sha256(request.payload).hexdigest()
        duplicate = self._remember(digest) if request.dedupe else False
        stale = self._is_stale(mono_ns)
        active_gap = request.gap if request.gap is not None else self._open_gap
        unreconciled = active_gap is not None and request.event_type not in (
            EVENT_SNAPSHOT,
            EVENT_BACKFILL,
        )
        complete = request.complete and not unreconciled
        canonical_id = (
            self._resolve(request.symbol, request.kind)
            if request.status == VALID and request.symbol
            else None
        )
        event = CanonicalEvent(
            provider=PROVIDER,
            connection_id=self._connection_id,
            receive_wall_clock_ns=wall_ns,
            receive_monotonic_ns=mono_ns,
            canonical_instrument_id=canonical_id,
            validation_status=request.status,
            event_type=request.event_type,
            symbol=request.symbol,
            kind=request.kind,
            fields=dict(request.fields),
            raw_sha256=digest,
            raw_payload=request.payload,
            duplicate=duplicate,
            stale=stale,
            completeness=COMPLETE if complete else INCOMPLETE,
            gap=active_gap,
        )
        self._emit(event)
        return event

    def process_payload(
        self, raw: bytes | str, *, kind: str | None = None
    ) -> CanonicalEvent:
        """Validate one raw frame and emit its canonical market event."""
        self._require_active()
        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        wall_ns = self._clock.wall_clock_ns()
        mono_ns = self._clock.monotonic_ns()
        self._last_inbound_frame_ns = mono_ns
        try:
            message = _validate_message(payload, kind)
        except ValidationError:
            self._touch_receive(_safe_symbol(payload), wall_ns, mono_ns)
            return self._build_event(
                EventRequest(
                    symbol="",
                    kind=kind or KIND_SYMBOL,
                    fields={},
                    status=MALFORMED,
                    payload=payload,
                    event_type=EVENT_MALFORMED,
                    complete=False,
                    dedupe=False,
                )
            )
        self._touch_receive(message.symbol, wall_ns, mono_ns)
        if message.status == VALID and message.market:
            self._touch_valid(message.symbol, wall_ns, mono_ns)
            if message.kind == KIND_SYMBOL:
                self._last_valid_symbol_event_ns = mono_ns
            elif message.kind == KIND_INDEX:
                self._last_valid_index_event_ns = mono_ns
        if (
            message.status == VALID
            and message.message_type == TYPE_SUBSCRIPTION
        ):
            self._record_ack(message, wall_ns, mono_ns)
        if message.status == VALID and message.message_type == TYPE_CONNECTION:
            self._last_server_heartbeat_or_pong_ns = mono_ns
        return self._build_event(
            EventRequest(
                symbol=message.symbol,
                kind=message.kind,
                fields=message.fields,
                status=message.status,
                payload=payload,
                event_type=message.message_type,
                complete=message.status == VALID,
                dedupe=message.market,
            )
        )

    def reconnect(self, token: str) -> GapRecord:
        """Open a new connection, recording an explicit gap first.

        Failed attempts never close the gap: a later retry reuses it. Until
        the gap is reconciled, live events carry it and are marked incomplete.
        Missing updates are never synthesized.
        """
        self._require_not_closed()
        if not token:
            raise ValueError("read-only token must be supplied by caller")
        self._check_network()
        self.validate_endpoint(DATA_SOCKET_ENDPOINT)
        if self._connected and self._open_gap is not None:
            self._close_gap({})
        if self._open_gap is None:
            self._open_gap = GapRecord(
                gap_id=f"gap-{self._connection_seq + 1}",
                connection_id_before=self._connection_id,
                connection_id_after="",
                started_wall_clock_ns=self._clock.wall_clock_ns(),
                started_monotonic_ns=self._clock.monotonic_ns(),
            )
            self._reset_connection_state()
        self._transport.close()
        self._connected = False
        if not self._open_with_retries(token):
            raise ConnectionFailedError("Data Socket reconnect failed")
        self._connection_seq += 1
        self._connection_id = f"fyers-data-{self._connection_seq}"
        self._connected = True
        self._open_gap = replace(
            self._open_gap,
            connection_id_after=self._connection_id,
            ended_wall_clock_ns=(
                self._open_gap.ended_wall_clock_ns
                or self._clock.wall_clock_ns()
            ),
            ended_monotonic_ns=(
                self._open_gap.ended_monotonic_ns or self._clock.monotonic_ns()
            ),
        )
        self.resubscribe()
        return self._open_gap

    def _prepare_snapshot(
        self, symbol: str, sub: Subscription, entry: object
    ) -> tuple[bytes, ParsedMessage | None]:
        """Validate one current snapshot without emitting or closing a gap."""
        if not isinstance(entry, Mapping):
            entry = {}
        prepared = dict(entry)
        prepared["symbol"] = symbol
        snapshot_type = (
            TYPE_INDEX_UPDATE if sub.kind == KIND_INDEX else TYPE_SYMBOL_UPDATE
        )
        prepared.setdefault("type", snapshot_type)
        payload = json.dumps(
            prepared, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        try:
            message = _validate_message(payload, sub.kind)
        except ValidationError:
            return payload, None
        return payload, message if message.status == VALID else None

    def _snapshot_event(
        self,
        symbol: str,
        sub: Subscription,
        payload: bytes,
        message: ParsedMessage | None,
        gap: GapRecord,
    ) -> CanonicalEvent:
        if message is None:
            return self._build_event(
                EventRequest(
                    symbol=symbol,
                    kind=sub.kind,
                    fields={},
                    status=MALFORMED,
                    payload=payload,
                    event_type=EVENT_SNAPSHOT,
                    complete=False,
                    dedupe=False,
                    gap=gap,
                )
            )
        return self._build_event(
            EventRequest(
                symbol=symbol,
                kind=message.kind,
                fields=message.fields,
                status=message.status,
                payload=payload,
                event_type=EVENT_SNAPSHOT,
                complete=True,
                dedupe=False,
                gap=gap,
            )
        )

    def _gap_marker(
        self, symbol: str, sub: Subscription, gap: GapRecord
    ) -> CanonicalEvent:
        payload = json.dumps(
            {"gap": gap.gap_id, "status": "MISSING", "symbol": symbol},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._build_event(
            EventRequest(
                symbol=symbol,
                kind=sub.kind,
                fields={},
                status=VALID,
                payload=payload,
                event_type=EVENT_GAP_MARKER,
                complete=False,
                dedupe=False,
                gap=gap,
            )
        )

    def _require_gap(self) -> GapRecord:
        if self._open_gap is None:
            raise ReconciliationError("no open connection gap")
        return self._open_gap

    def _finalize_gap(
        self,
        *,
        classification: str,
        covered: list[str],
        missing: list[str],
        source: str,
        authoritative: bool,
        records: tuple[BackfillRecord, ...] = (),
        reasons: tuple[str, ...] = (),
    ) -> GapRecord:
        """Close the open gap and retain all supplied evidence."""
        gap = self._require_gap()
        closed = replace(
            gap,
            ended_wall_clock_ns=(
                gap.ended_wall_clock_ns or self._clock.wall_clock_ns()
            ),
            ended_monotonic_ns=(
                gap.ended_monotonic_ns or self._clock.monotonic_ns()
            ),
            classification=classification,
            covered_symbols=tuple(covered),
            missing_symbols=tuple(missing),
            source=source,
            authoritative=authoritative,
            backfill_records=records,
            validation_reasons=reasons,
        )
        self._apply_classification(closed, covered, missing)
        self._gaps.append(closed)
        self._open_gap = None
        return closed

    def _close_gap(
        self,
        snapshot: Mapping[str, object] | None = None,
        *,
        authoritative: bool = False,
        source: str = "SNAPSHOT",
        history_requested: bool = False,
    ) -> tuple[CanonicalEvent, ...]:
        """Close a gap from current snapshots; this needs no connection."""
        self._require_gap()
        supplied = snapshot or {}
        prepared: list[
            tuple[str, Subscription, bytes, ParsedMessage | None]
        ] = []
        covered: list[str] = []
        missing: list[str] = []
        for symbol, sub in self._subscriptions.items():
            if symbol not in supplied:
                missing.append(symbol)
                continue
            payload, message = self._prepare_snapshot(
                symbol, sub, supplied[symbol]
            )
            prepared.append((symbol, sub, payload, message))
            if message is None:
                missing.append(symbol)
            else:
                covered.append(symbol)
        closed = self._finalize_gap(
            classification=_gap_classification(
                len(covered),
                len(covered) + len(missing),
                authoritative=authoritative,
                history_requested=history_requested,
            ),
            covered=covered,
            missing=missing,
            source=source,
            authoritative=authoritative,
        )
        events = [
            self._snapshot_event(symbol, sub, payload, message, closed)
            for symbol, sub, payload, message in prepared
        ]
        events.extend(
            self._gap_marker(symbol, self._subscriptions[symbol], closed)
            for symbol in missing
        )
        return tuple(events)

    def _apply_classification(
        self,
        closed: GapRecord,
        covered: list[str],
        missing: list[str],
    ) -> None:
        """Record per-symbol reconciliation evidence from a closed gap."""
        for symbol in covered:
            sub = self._subscriptions.get(symbol)
            if sub is not None:
                self._subscriptions[symbol] = replace(
                    sub, reconciliation_status=closed.classification
                )
        for symbol in missing:
            sub = self._subscriptions.get(symbol)
            if sub is not None:
                self._subscriptions[symbol] = replace(
                    sub, reconciliation_status=UNRECOVERABLE
                )

    def recover_current_state(
        self, snapshot: Mapping[str, object]
    ) -> tuple[CanonicalEvent, ...]:
        """Reconcile the current state; a snapshot never recovers history."""
        return self._close_gap(
            snapshot,
            authoritative=False,
            source="SNAPSHOT",
            history_requested=False,
        )

    def _instrument_map(self) -> dict[str, str]:
        """Map canonical instrument ids to subscribed provider symbols."""
        mapping: dict[str, str] = {}
        for symbol, sub in self._subscriptions.items():
            mapping[self._resolve(symbol, sub.kind) or symbol] = symbol
        return mapping

    def _reject_record_identity(
        self,
        record: BackfillRecord,
        instruments: Mapping[str, str],
        source: str,
    ) -> str | None:
        """Check attribution and source authority of one typed record."""
        canonical = record.canonical_instrument_id
        if not isinstance(canonical, str) or not canonical.strip():
            return "missing canonical_instrument_id"
        if canonical not in instruments:
            return f"canonical instrument is not subscribed: {canonical}"
        if not isinstance(source, str) or source.strip().upper() not in (
            AUTHORIZED_HISTORY_SOURCES
        ):
            return "source is not authorised for historical completeness"
        if not isinstance(record.source, str) or (
            record.source.strip().upper() not in AUTHORIZED_HISTORY_SOURCES
        ):
            return "record source is not authorised for historical completeness"
        return None

    def _reject_record_values(self, record: BackfillRecord) -> str | None:
        """Check evidence fields of one typed record."""
        for label, value in (
            ("event_timestamp", record.event_timestamp),
            ("receive_timestamp", record.receive_timestamp),
        ):
            try:
                _exact_int(value, minimum=1, maximum=MAX_BACKFILL_TIMESTAMP_NS)
            except ValidationError:
                return f"invalid {label}"
        if int(record.receive_timestamp) < int(record.event_timestamp):
            return "receive_timestamp precedes event_timestamp"
        if not isinstance(record.payload_digest, str) or not (
            _DIGEST_TEXT.fullmatch(record.payload_digest)
        ):
            return "invalid payload_digest"
        sequence = record.provider_sequence_if_available
        if sequence is not None:
            try:
                _exact_int(
                    sequence, minimum=0, maximum=MAX_BACKFILL_TIMESTAMP_NS
                )
            except ValidationError:
                return "invalid provider_sequence_if_available"
        return None

    def _record_rejection(
        self,
        record: BackfillRecord,
        instruments: Mapping[str, str],
        source: str,
    ) -> str | None:
        """Reject a typed record that cannot support history recovery."""
        return self._reject_record_identity(
            record, instruments, source
        ) or self._reject_record_values(record)

    def _reject_record_duplicates(
        self,
        symbol: str,
        ordered: list[_ValidatedBackfill],
        timestamps: list[int],
    ) -> str | None:
        """Reject duplicate evidence and out-of-order event times."""
        if timestamps != sorted(timestamps):
            return f"{symbol}: backfill records are out of event order"
        digests = [item.record.payload_digest for item in ordered]
        if len(set(digests)) != len(digests):
            return f"{symbol}: duplicate payload digest"
        sequences = [
            item.sequence for item in ordered if item.sequence is not None
        ]
        if len(set(sequences)) != len(sequences):
            return f"{symbol}: duplicate provider sequence"
        return None

    def _reject_sequence_and_receipts(
        self, symbol: str, ordered: list[_ValidatedBackfill]
    ) -> str | None:
        """Reject sequence discontinuities and reversed receive times."""
        sequences = [
            item.sequence for item in ordered if item.sequence is not None
        ]
        if len(sequences) == len(ordered):
            if any(
                b != a + 1
                for a, b in zip(sequences, sequences[1:], strict=False)
            ):
                return f"{symbol}: unexplained provider sequence gap"
        elif any(
            b <= a for a, b in zip(sequences, sequences[1:], strict=False)
        ):
            return f"{symbol}: provider sequence is not increasing"
        receives = [item.receive_timestamp for item in ordered]
        if any(b < a for a, b in zip(receives, receives[1:], strict=False)):
            return f"{symbol}: receive timestamps are out of order"
        return None

    def _reject_record_span(
        self,
        symbol: str,
        timestamps: list[int],
        *,
        start_ns: int,
        end_ns: int,
        max_gap_ns: int,
    ) -> str | None:
        """Reject coverage that does not span the whole gap continuously."""
        if timestamps[0] > start_ns:
            return f"{symbol}: backfill starts after the gap start"
        if timestamps[-1] < end_ns:
            return f"{symbol}: backfill ends before the gap end"
        if timestamps[-1] == timestamps[0]:
            return f"{symbol}: events do not span the gap interval"
        if any(
            b - a > max_gap_ns
            for a, b in zip(timestamps, timestamps[1:], strict=False)
        ):
            return f"{symbol}: unexplained internal gap"
        return None

    def _check_instrument_coverage(
        self,
        symbol: str,
        items: list[_ValidatedBackfill],
        *,
        start_ns: int,
        end_ns: int,
        max_gap_ns: int,
    ) -> str | None:
        """Validate ordering, duplicates, span and internal gaps."""
        timestamps = [item.event_timestamp for item in items]
        ordered = sorted(items, key=lambda item: item.event_timestamp)
        for rejection in (
            self._reject_record_duplicates(symbol, ordered, timestamps),
            self._reject_sequence_and_receipts(symbol, ordered),
            self._reject_record_span(
                symbol,
                timestamps,
                start_ns=start_ns,
                end_ns=end_ns,
                max_gap_ns=max_gap_ns,
            ),
        ):
            if rejection is not None:
                return rejection
        return None

    def _validate_backfill(
        self,
        backfill: object,
        *,
        source: str,
        covered_from_ns: int | None,
        covered_to_ns: int | None,
        max_internal_gap_ns: int | None,
    ) -> BackfillValidation:
        """Validate typed records against every subscribed instrument."""
        gap = self._require_gap()
        instruments = self._instrument_map()
        reasons: list[str] = []
        if not gap.connection_id_after:
            reasons.append("gap was not closed by a successful reconnect")
        valid: list[_ValidatedBackfill] = []
        for index, candidate in enumerate(_backfill_candidates(backfill)):
            if not isinstance(candidate, BackfillRecord):
                reasons.append(f"record {index} is not a typed BackfillRecord")
                continue
            rejection = self._record_rejection(candidate, instruments, source)
            if rejection is not None:
                reasons.append(f"record {index}: {rejection}")
                continue
            valid.append(
                _ValidatedBackfill(
                    record=candidate,
                    symbol=instruments[candidate.canonical_instrument_id],
                    event_timestamp=int(candidate.event_timestamp),
                    receive_timestamp=int(candidate.receive_timestamp),
                    sequence=candidate.provider_sequence_if_available,
                )
            )
        grouped: dict[str, list[_ValidatedBackfill]] = {}
        for item in valid:
            grouped.setdefault(item.symbol, []).append(item)
        start_ns = (
            gap.started_wall_clock_ns
            if covered_from_ns is None
            else covered_from_ns
        )
        end_ns = (
            gap.ended_wall_clock_ns if covered_to_ns is None else covered_to_ns
        )
        max_gap_ns = (
            self._options.max_backfill_gap_ns
            if max_internal_gap_ns is None
            else max_internal_gap_ns
        )
        covered: list[str] = []
        for symbol, items in grouped.items():
            failure = self._check_instrument_coverage(
                symbol,
                items,
                start_ns=start_ns,
                end_ns=end_ns,
                max_gap_ns=max_gap_ns,
            )
            if failure is not None:
                reasons.append(failure)
            else:
                covered.append(symbol)
        missing = [
            symbol for symbol in self._subscriptions if symbol not in covered
        ]
        complete = (
            bool(covered)
            and not reasons
            and len(covered) == len(self._subscriptions)
        )
        return BackfillValidation(
            tuple(item.record for item in valid),
            tuple(covered),
            tuple(missing),
            tuple(reasons),
            complete,
        )

    def _backfill_event(
        self,
        record: BackfillRecord,
        symbol: str,
        sub: Subscription,
        gap: GapRecord,
        complete: bool,
    ) -> CanonicalEvent:
        """Emit one canonical event per typed record, with full evidence."""
        evidence = record.as_dict()
        snapshot_type = (
            TYPE_INDEX_UPDATE if sub.kind == KIND_INDEX else TYPE_SYMBOL_UPDATE
        )
        payload = json.dumps(
            {
                "type": snapshot_type,
                "symbol": symbol,
                "event": EVENT_BACKFILL,
                "backfill": evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        mono_ns = self._clock.monotonic_ns()
        event = CanonicalEvent(
            provider=PROVIDER,
            connection_id=gap.connection_id_after or self._connection_id,
            receive_wall_clock_ns=int(record.receive_timestamp),
            receive_monotonic_ns=mono_ns,
            canonical_instrument_id=self._resolve(symbol, sub.kind) or symbol,
            validation_status=VALID,
            event_type=EVENT_BACKFILL,
            symbol=symbol,
            kind=sub.kind,
            fields={
                "symbol": symbol,
                "type": snapshot_type,
                "backfill": evidence,
            },
            raw_sha256=record.payload_digest,
            raw_payload=payload,
            duplicate=self._remember(f"backfill:{record.payload_digest}"),
            stale=self._is_stale(mono_ns),
            completeness=COMPLETE if complete else INCOMPLETE,
            gap=gap,
        )
        self._emit(event)
        return event

    def recover_event_history(
        self,
        backfill: object,
        *,
        authoritative: bool = False,
        source: str = "BACKFILL",
        covered_from_ns: int | None = None,
        covered_to_ns: int | None = None,
        max_internal_gap_ns: int | None = None,
    ) -> tuple[CanonicalEvent, ...]:
        """Recover history only from complete, validated typed records."""
        self._require_gap()
        validation = self._validate_backfill(
            backfill,
            source=source,
            covered_from_ns=covered_from_ns,
            covered_to_ns=covered_to_ns,
            max_internal_gap_ns=max_internal_gap_ns,
        )
        if not validation.records:
            classification = UNRECOVERABLE
        elif not validation.complete:
            classification = HISTORY_INCOMPLETE
        elif authoritative:
            classification = HISTORY_RECOVERED
        else:
            classification = STATE_RECONCILED
        closed = self._finalize_gap(
            classification=classification,
            covered=list(validation.covered),
            missing=list(validation.missing),
            source=source,
            authoritative=classification == HISTORY_RECOVERED,
            records=validation.records,
            reasons=validation.reasons,
        )
        grouped: dict[str, list[BackfillRecord]] = {}
        for record in validation.records:
            grouped.setdefault(record.canonical_instrument_id, []).append(
                record
            )
        complete = classification in (HISTORY_RECOVERED, STATE_RECONCILED)
        events: list[CanonicalEvent] = []
        for symbol, sub in self._subscriptions.items():
            if symbol not in validation.covered:
                events.append(self._gap_marker(symbol, sub, closed))
            canonical = self._resolve(symbol, sub.kind) or symbol
            # Invalid or incomplete history is still emitted as evidence; it
            # is never collapsed to a single point or silently discarded.
            for record in sorted(
                grouped.get(canonical, ()),
                key=lambda item: item.event_timestamp,
            ):
                events.append(
                    self._backfill_event(record, symbol, sub, closed, complete)
                )
        return tuple(events)

    def mark_unrecoverable(self) -> tuple[CanonicalEvent, ...]:
        """Close an open gap with no snapshot; mark every symbol incomplete."""
        return self._close_gap({}, authoritative=False, source="UNRECOVERABLE")

    def shutdown(self) -> None:
        """Close the transport once; keep unresolved gap evidence intact."""
        if self._closed:
            return
        try:
            self._transport.close()
        finally:
            self._closed = True
            self._connected = False
            self._subscriptions = {
                symbol: replace(sub, active=False)
                for symbol, sub in self._subscriptions.items()
            }

    def status(self) -> dict[str, object]:
        """Report non-secret lifecycle, freshness and gap evidence."""
        evidence = tuple(self._gaps)
        if self._open_gap is not None:
            evidence += (self._open_gap,)
        return {
            "provider": PROVIDER,
            "connected": self._connected,
            "closed": self._closed,
            "connection_id": self._connection_id,
            "subscriptions": {
                symbol: sub.active
                for symbol, sub in self._subscriptions.items()
            },
            "open_gap": self._open_gap is not None,
            "unresolved_gap": self._open_gap,
            "gaps": tuple(self._gaps),
            "gap_evidence": evidence,
            "stale": self.is_stale(),
            "heartbeat_fresh": self.is_heartbeat_fresh(),
            "feed_healthy": self.feed_healthy(),
            "transport_connected": self.transport_connected(),
            "inbound_live": self.inbound_live(),
            "market_active": self.market_active(),
            "last_successful_send_ns": self.last_successful_send_ns,
            "last_inbound_frame_ns": self.last_inbound_frame_ns,
            "last_server_heartbeat_or_pong_ns": (
                self.last_server_heartbeat_or_pong_ns
            ),
            "last_valid_symbol_event_ns": self.last_valid_symbol_event_ns,
            "last_valid_index_event_ns": self.last_valid_index_event_ns,
            "symbol_freshness": {
                symbol: sub.as_dict()
                for symbol, sub in self._subscriptions.items()
            },
        }

    # Readable aliases; they share the canonical implementations above.
    reconcile_snapshot = recover_current_state
    reconcile_gap = recover_current_state
    recover_history = recover_event_history
    apply_backfill = recover_event_history
    reconcile_history = recover_event_history
    recover_gap = recover_event_history
    freshness = symbol_freshness
    is_feed_healthy = feed_healthy
    feed_health = feed_healthy
