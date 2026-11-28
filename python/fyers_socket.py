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
HEARTBEAT_FRAME = '{"action":"heartbeat"}'

# Bounds are intentionally finite and enforced before any value is emitted.
MAX_TOTAL_QUANTITY = 10**18
MAX_ORDER_COUNT = 10**9
MAX_PRICE = 10**9
MAX_TIMESTAMP = 4_102_444_800
MIN_CODE = -100_000
MAX_CODE = 100_000

_FLOAT_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_INTEGER_TEXT = re.compile(r"[+-]?\d+\Z")

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


def _backfill_entry(entry: object) -> Mapping[str, object]:
    """Accept a snapshot entry or the last point of a historical sequence."""
    if isinstance(entry, Mapping):
        return entry
    if isinstance(entry, (list, tuple)) and entry:
        last = entry[-1]
        if isinstance(last, Mapping):
            return last
    return {}


class FyersDataSocket:
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
        self._feed_receive_ns: int | None = None
        self._last_heartbeat_ns: int | None = None

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
        self._feed_receive_ns = None
        self._last_heartbeat_ns = None
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
        """Send one keepalive frame and record its monotonic send time."""
        self._require_active()
        sent = self._send_with_retries(HEARTBEAT_FRAME)
        if sent:
            self._last_heartbeat_ns = self._clock.monotonic_ns()
            self._feed_receive_ns = self._last_heartbeat_ns
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

    def _is_stale(self, now_ns: int) -> bool:
        last = self._feed_receive_ns
        if last is None:
            return False
        return now_ns - last > self._options.stale_after_ns

    def is_stale(self) -> bool:
        """Report whether the whole feed has gone quiet past the threshold."""
        return self._is_stale(self._clock.monotonic_ns())

    def is_heartbeat_fresh(self) -> bool:
        """Report whether a recent heartbeat was sent or acknowledged."""
        last = self._last_heartbeat_ns
        if last is None:
            return False
        now_ns = self._clock.monotonic_ns()
        return now_ns - last <= self._options.stale_after_ns

    def feed_healthy(self) -> bool:
        """Report transport-level health; this says nothing per instrument."""
        return (
            self._connected
            and not self._closed
            and not self.is_stale()
            and self.is_heartbeat_fresh()
        )

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
        unreconciled = active_gap is not None and (
            request.event_type != EVENT_SNAPSHOT
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
        self._feed_receive_ns = mono_ns
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
        if (
            message.status == VALID
            and message.message_type == TYPE_SUBSCRIPTION
        ):
            self._record_ack(message, wall_ns, mono_ns)
        if message.message_type == TYPE_CONNECTION:
            self._last_heartbeat_ns = mono_ns
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
            self._open_gap, connection_id_after=self._connection_id
        )
        self.resubscribe()
        return self._open_gap

    def _snapshot_event(
        self,
        symbol: str,
        sub: Subscription,
        entry: Mapping[str, object],
        gap: GapRecord,
    ) -> CanonicalEvent:
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

    def _close_gap(
        self,
        snapshot: Mapping[str, object] | None = None,
        *,
        authoritative: bool = False,
        source: str = "SNAPSHOT",
        history_requested: bool = False,
    ) -> tuple[CanonicalEvent, ...]:
        """Close the open gap; this needs no active connection."""
        gap = self._require_gap()
        supplied = snapshot or {}
        covered: list[str] = []
        missing: list[str] = []
        events: list[CanonicalEvent] = []
        for symbol, sub in self._subscriptions.items():
            if symbol in supplied:
                event = self._snapshot_event(
                    symbol, sub, _backfill_entry(supplied[symbol]), gap
                )
                events.append(event)
                if event.validation_status == VALID:
                    covered.append(symbol)
                else:
                    missing.append(symbol)
            else:
                missing.append(symbol)
                events.append(self._gap_marker(symbol, sub, gap))
        closed = replace(
            gap,
            ended_wall_clock_ns=self._clock.wall_clock_ns(),
            ended_monotonic_ns=self._clock.monotonic_ns(),
            classification=_gap_classification(
                len(covered),
                len(covered) + len(missing),
                authoritative=authoritative,
                history_requested=history_requested,
            ),
            covered_symbols=tuple(covered),
            missing_symbols=tuple(missing),
            source=source,
            authoritative=authoritative,
        )
        self._apply_classification(closed, covered, missing)
        self._gaps.append(closed)
        self._open_gap = None
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

    def reconcile_snapshot(
        self, snapshot: Mapping[str, object]
    ) -> tuple[CanonicalEvent, ...]:
        """Reconcile a gap with a fresh snapshot; never recover history."""
        return self._close_gap(snapshot, authoritative=False, source="SNAPSHOT")

    def recover_history(
        self,
        backfill: Mapping[str, object],
        *,
        authoritative: bool = True,
        covered_from_ns: int | None = None,
        covered_to_ns: int | None = None,
    ) -> tuple[CanonicalEvent, ...]:
        """Apply backfill; only authoritative data recovers history."""
        gap = self._require_gap()
        interval_complete = not (
            (
                covered_from_ns is not None
                and covered_from_ns > gap.started_monotonic_ns
            )
            or (
                covered_to_ns is not None
                and covered_to_ns < gap.started_monotonic_ns
            )
        )
        return self._close_gap(
            backfill,
            authoritative=authoritative and interval_complete,
            source="BACKFILL",
            history_requested=authoritative,
        )

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
            "symbol_freshness": {
                symbol: sub.as_dict()
                for symbol, sub in self._subscriptions.items()
            },
        }

    # Readable aliases; they share the canonical implementation above.
    apply_backfill = recover_history
    reconcile_history = recover_history
    recover_gap = recover_history
    reconcile_gap = reconcile_snapshot
    freshness = symbol_freshness
    is_feed_healthy = feed_healthy
    feed_health = feed_healthy
