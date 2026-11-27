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

Qualification status
--------------------
LIVE ENTITLEMENT AND QUALIFICATION REMAIN PENDING. No live credential is used,
no real API is contacted and no successful feed certification is claimed.
Verification is offline only, through an injected in-memory transport.
``enable_network`` defaults to ``False``; real network use requires an
explicit opt-in. Importing this module performs no network I/O.
"""

from __future__ import annotations

# Dataclass value objects carry explicit field sets for event provenance.
import hashlib
import json
import math
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

KIND_SYMBOL = "symbol"
KIND_INDEX = "index"
KINDS = (KIND_SYMBOL, KIND_INDEX)
INDEX_SUFFIX = "-INDEX"

EVENT_UPDATE = "UPDATE"
EVENT_SNAPSHOT = "SNAPSHOT"
EVENT_GAP_MARKER = "GAP_MARKER"

VALID = "VALID"
MALFORMED = "MALFORMED"
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
RECOVERABLE = "RECOVERABLE"
UNRECOVERABLE = "UNRECOVERABLE"
PARTIAL = "PARTIAL"

DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_SUBSCRIPTIONS = 64
DEFAULT_DEDUPE_WINDOW = 4096
DEFAULT_STALE_AFTER_NS = 30_000_000_000
HEARTBEAT_FRAME = '{"action":"heartbeat"}'

_OPTIONAL_NUMBERS = (
    ("open_price", "open"),
    ("high_price", "high"),
    ("low_price", "low"),
    ("prev_price", "prev_close"),
)


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
    """One desired Data Socket subscription and its activation state."""

    symbol: str
    kind: str
    active: bool
    connection_id: str


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


def _is_number(value: object) -> bool:
    """Return True only for a finite int/float that is not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


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


def _symbol_from(obj: Mapping[str, object]) -> str:
    symbol = obj.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValidationError("missing or non-string symbol")
    return symbol


def _ltp_from(obj: Mapping[str, object]) -> float:
    ltp = obj.get("ltp")
    if not _is_number(ltp) or ltp <= 0:
        raise ValidationError("missing or invalid ltp")
    return float(ltp)


def _optional_fields(obj: Mapping[str, object]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for source, target in _OPTIONAL_NUMBERS:
        value = obj.get(source)
        if value is not None:
            if not _is_number(value):
                raise ValidationError(f"invalid {source}")
            fields[target] = float(value)
    volume = obj.get("volume")
    if volume is not None:
        if not _is_number(volume) or volume < 0:
            raise ValidationError("invalid volume")
        fields["volume"] = int(volume)
    timestamp = obj.get("timestamp")
    if timestamp is not None:
        if not _is_number(timestamp):
            raise ValidationError("invalid provider timestamp")
        fields["provider_timestamp"] = float(timestamp)
    depth = obj.get("depth")
    if depth is not None:
        if not isinstance(depth, Mapping):
            raise ValidationError("invalid depth")
        fields["depth"] = dict(depth)
    return fields


def _validate_tick(
    raw: bytes, kind_hint: str | None
) -> tuple[str, str, dict[str, object]]:
    obj = _strict_json(raw)
    if not isinstance(obj, Mapping):
        raise ValidationError("payload must be a JSON object")
    symbol = _symbol_from(obj)
    ltp = _ltp_from(obj)
    kind = kind_hint or _infer_kind(symbol)
    if kind not in KINDS:
        raise ValidationError(f"unknown update kind: {kind}")
    fields: dict[str, object] = {"symbol": symbol, "ltp": ltp}
    fields.update(_optional_fields(obj))
    return symbol, kind, fields


def _subscribe_frame(symbol: str, kind: str) -> str:
    return json.dumps(
        {"action": "subscribe", "args": {"symbol": symbol, "kind": kind}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _unsubscribe_frame(symbol: str) -> str:
    return json.dumps(
        {"action": "unsubscribe", "args": {"symbol": symbol}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _gap_classification(covered: int, total: int) -> str:
    if total in (0, covered):
        return RECOVERABLE
    if covered == 0:
        return UNRECOVERABLE
    return PARTIAL


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
        self._last_event_ns: int | None = None
        self._last_heartbeat_ns: int | None = None

    def _require_active(self) -> None:
        if self._closed:
            raise ShutdownError("adapter has been shut down")
        if not self._connected:
            raise NotConnectedError("Data Socket is not connected")

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

    def connect(self, token: str) -> None:
        """Open the Data Socket; the caller-supplied token is not retained."""
        if self._closed:
            raise ShutdownError("adapter has been shut down")
        if not token:
            raise ValueError("read-only token must be supplied by caller")
        self._check_network()
        self.validate_endpoint(DATA_SOCKET_ENDPOINT)
        if not self._open_with_retries(token):
            raise ConnectionFailedError("Data Socket open failed")
        self._connection_seq += 1
        self._connection_id = f"fyers-data-{self._connection_seq}"
        self._connected = True
        self._last_event_ns = None
        for symbol, sub in tuple(self._subscriptions.items()):
            self._subscriptions[symbol] = replace(
                sub, active=False, connection_id=self._connection_id
            )

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
            symbol, resolved, True, self._connection_id
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
                    sub, active=True, connection_id=self._connection_id
                )
                restored += 1
        return restored

    def heartbeat(self) -> bool:
        """Send one keepalive frame and record its monotonic send time."""
        self._require_active()
        sent = self._send_with_retries(HEARTBEAT_FRAME)
        if sent:
            self._last_heartbeat_ns = self._clock.monotonic_ns()
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
        last = self._last_event_ns
        if last is None:
            return False
        return now_ns - last > self._options.stale_after_ns

    def is_stale(self) -> bool:
        """Report whether the live feed has gone quiet past the threshold."""
        return self._is_stale(self._clock.monotonic_ns())

    def _emit(self, event: CanonicalEvent) -> None:
        sink = self._options.event_sink
        if sink is not None:
            sink(event)

    # pylint: disable=too-many-arguments,too-many-locals
    def _build_event(
        self,
        *,
        symbol: str,
        kind: str,
        fields: Mapping[str, object],
        status: str,
        payload: bytes,
        event_type: str,
        complete: bool,
        dedupe: bool = True,
        gap: GapRecord | None = None,
    ) -> CanonicalEvent:
        wall_ns = self._clock.wall_clock_ns()
        mono_ns = self._clock.monotonic_ns()
        digest = hashlib.sha256(payload).hexdigest()
        duplicate = self._remember(digest) if dedupe else False
        stale = self._is_stale(mono_ns)
        active_gap = gap if gap is not None else self._open_gap
        if active_gap is not None and event_type != EVENT_SNAPSHOT:
            complete = False
        completeness = COMPLETE if complete else INCOMPLETE
        canonical_id = (
            self._resolve(symbol, kind) if status == VALID and symbol else None
        )
        event = CanonicalEvent(
            provider=PROVIDER,
            connection_id=self._connection_id,
            receive_wall_clock_ns=wall_ns,
            receive_monotonic_ns=mono_ns,
            canonical_instrument_id=canonical_id,
            validation_status=status,
            event_type=event_type,
            symbol=symbol,
            kind=kind,
            fields=dict(fields),
            raw_sha256=digest,
            raw_payload=payload,
            duplicate=duplicate,
            stale=stale,
            completeness=completeness,
            gap=active_gap,
        )
        if status == VALID and event_type == EVENT_UPDATE:
            self._last_event_ns = mono_ns
        self._emit(event)
        return event

    def process_payload(
        self, raw: bytes | str, *, kind: str | None = None
    ) -> CanonicalEvent:
        """Validate a raw payload and emit one canonical market event."""
        self._require_active()
        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        try:
            symbol, resolved, fields = _validate_tick(payload, kind)
        except ValidationError:
            return self._build_event(
                symbol="",
                kind=kind or KIND_SYMBOL,
                fields={},
                status=MALFORMED,
                payload=payload,
                event_type=EVENT_UPDATE,
                complete=False,
                dedupe=False,
            )
        return self._build_event(
            symbol=symbol,
            kind=resolved,
            fields=fields,
            status=VALID,
            payload=payload,
            event_type=EVENT_UPDATE,
            complete=True,
        )

    def reconnect(self, token: str) -> GapRecord:
        """Open a new connection, recording an explicit gap first.

        The gap is never continuity: until it is closed, live events carry it
        and are marked incomplete. Missing updates are never synthesized.
        """
        if self._closed:
            raise ShutdownError("adapter has been shut down")
        if not token:
            raise ValueError("read-only token must be supplied by caller")
        self._check_network()
        self.validate_endpoint(DATA_SOCKET_ENDPOINT)
        if self._open_gap is not None:
            self._close_gap({})
        self._transport.close()
        self._connected = False
        before = self._connection_id
        self._open_gap = GapRecord(
            gap_id=f"gap-{self._connection_seq + 1}",
            connection_id_before=before,
            connection_id_after="",
            started_wall_clock_ns=self._clock.wall_clock_ns(),
            started_monotonic_ns=self._clock.monotonic_ns(),
        )
        if not self._open_with_retries(token):
            raise ConnectionFailedError("Data Socket reconnect failed")
        self._connection_seq += 1
        self._connection_id = f"fyers-data-{self._connection_seq}"
        self._connected = True
        self._last_event_ns = None
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
        payload = json.dumps(
            prepared, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        try:
            _, kind, fields = _validate_tick(payload, sub.kind)
        except ValidationError:
            return self._build_event(
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
        return self._build_event(
            symbol=symbol,
            kind=kind,
            fields=fields,
            status=VALID,
            payload=payload,
            event_type=EVENT_SNAPSHOT,
            complete=True,
            dedupe=False,
            gap=gap,
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

    def _close_gap(
        self, snapshot: Mapping[str, Mapping[str, object]] | None
    ) -> tuple[CanonicalEvent, ...]:
        gap = self._open_gap
        if gap is None:
            raise ReconciliationError("no open connection gap")
        self._require_active()
        supplied = snapshot or {}
        covered: list[str] = []
        missing: list[str] = []
        events: list[CanonicalEvent] = []
        for symbol, sub in tuple(self._subscriptions.items()):
            if symbol in supplied:
                event = self._snapshot_event(symbol, sub, supplied[symbol], gap)
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
                len(covered), len(covered) + len(missing)
            ),
            covered_symbols=tuple(covered),
            missing_symbols=tuple(missing),
        )
        self._gaps.append(closed)
        self._open_gap = None
        return tuple(events)

    def reconcile_snapshot(
        self, snapshot: Mapping[str, Mapping[str, object]]
    ) -> tuple[CanonicalEvent, ...]:
        """Reconcile an open gap with a caller-supplied current snapshot."""
        return self._close_gap(snapshot)

    def mark_unrecoverable(self) -> tuple[CanonicalEvent, ...]:
        """Close an open gap with no snapshot; mark every symbol incomplete."""
        return self._close_gap({})

    def shutdown(self) -> None:
        """Close the transport once and make all further operations fail."""
        if self._closed:
            return
        try:
            self._transport.close()
        finally:
            self._closed = True
            self._connected = False
            self._open_gap = None
            self._subscriptions = {
                symbol: replace(sub, active=False)
                for symbol, sub in self._subscriptions.items()
            }

    def status(self) -> dict[str, object]:
        """Report non-secret lifecycle state for diagnostics and tests."""
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
            "gaps": tuple(self._gaps),
            "stale": self.is_stale(),
        }
