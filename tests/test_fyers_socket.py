"""Offline tests for the W3-A FYERS read-only Data Socket adapter.

Every frame comes from an in-memory fake transport and every timestamp from a
fake clock. No network is contacted, no credential is used and no live API is
called. LIVE ENTITLEMENT AND QUALIFICATION REMAIN PENDING: these tests verify
adapter behaviour only; they are not feed certification evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_fyers_socket", ROOT / "python" / "fyers_socket.py"
)
assert SPEC is not None and SPEC.loader is not None
fyers_socket = importlib.util.module_from_spec(SPEC)
sys.modules["quant_fyers_socket"] = fyers_socket
SPEC.loader.exec_module(fyers_socket)

SYMBOL = "NSE:SBIN-EQ"
INDEX = "NSE:NIFTY50-INDEX"
TOKEN = "READ-ONLY-TEST-TOKEN"

CANONICAL_FIELDS = {
    "provider",
    "connection_id",
    "receive_wall_clock_ns",
    "receive_monotonic_ns",
    "canonical_instrument_id",
    "validation_status",
    "event_type",
    "symbol",
    "kind",
    "fields",
    "raw_sha256",
    "raw_payload",
    "duplicate",
    "stale",
    "completeness",
    "gap",
}


class FakeClock:
    """Deterministic clock; tests advance it explicitly."""

    def __init__(
        self,
        wall_ns: int = 1_700_000_000_000_000_000,
        mono_ns: int = 1_000_000_000,
    ) -> None:
        self.wall_ns = wall_ns
        self.mono_ns = mono_ns

    def wall_clock_ns(self) -> int:
        """Return the current fake wall-clock nanoseconds."""
        return self.wall_ns

    def monotonic_ns(self) -> int:
        """Return the current fake monotonic nanoseconds."""
        return self.mono_ns

    def advance(self, delta_ns: int) -> None:
        """Advance both clocks by the same deterministic delta."""
        self.wall_ns += delta_ns
        self.mono_ns += delta_ns


class FakeTransport:
    """In-memory transport with scripted failures and queued frames."""

    requires_network = False

    def __init__(
        self,
        queued: list[bytes] | None = None,
        open_failures: int = 0,
        send_failures: int = 0,
    ) -> None:
        self.queued = deque(queued or [])
        self.opens: list[tuple[str, str]] = []
        self.frames: list[str] = []
        self.closed = 0
        self.open_failures = open_failures
        self.send_failures = send_failures

    def open(self, endpoint: str, token: str) -> None:
        """Record an open attempt, honouring scripted failures."""
        self.opens.append((endpoint, token))
        if self.open_failures > 0:
            self.open_failures -= 1
            raise fyers_socket.TransportError("scripted open failure")

    def send(self, text: str) -> None:
        """Record a frame, honouring scripted failures."""
        self.frames.append(text)
        if self.send_failures > 0:
            self.send_failures -= 1
            raise fyers_socket.TransportError("scripted send failure")

    def poll(self, timeout_ns: int) -> bytes | None:
        """Pop one queued frame; the fake ignores the timeout."""
        del timeout_ns
        if not self.queued:
            return None
        return self.queued.popleft()

    def close(self) -> None:
        """Count controlled close requests."""
        self.closed += 1


class NetworkFakeTransport(FakeTransport):
    """Fake flagged as a network transport for the opt-in gate."""

    requires_network = True


def _frame(symbol: str, ltp: float) -> bytes:
    """Build one schema-valid FYERS-style tick frame."""
    payload = {
        "symbol": symbol,
        "ltp": ltp,
        "open_price": ltp - 1.0,
        "high_price": ltp + 1.0,
        "low_price": ltp - 2.0,
        "prev_price": ltp - 0.5,
        "volume": 1200,
        "timestamp": 1769000000.0,
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _resolver(symbol: str, kind: str) -> str | None:
    """Resolve a canonical instrument id without any external lookup."""
    return f"ID::{kind}::{symbol}"


def _options(
    clock: FakeClock,
    events: list[fyers_socket.CanonicalEvent] | None = None,
    stale_after_ns: int = fyers_socket.DEFAULT_STALE_AFTER_NS,
    max_subscriptions: int = fyers_socket.DEFAULT_MAX_SUBSCRIPTIONS,
    max_retries: int = fyers_socket.DEFAULT_MAX_RETRIES,
) -> fyers_socket.AdapterOptions:
    """Build deterministic adapter options for one test."""
    return fyers_socket.AdapterOptions(
        max_retries=max_retries,
        max_subscriptions=max_subscriptions,
        stale_after_ns=stale_after_ns,
        clock=clock,
        resolver=_resolver,
        event_sink=None if events is None else events.append,
    )


def _connected(
    transport: FakeTransport, options: fyers_socket.AdapterOptions
) -> fyers_socket.FyersDataSocket:
    """Connect a fresh adapter with the supplied options."""
    adapter = fyers_socket.FyersDataSocket(transport, options=options)
    adapter.connect(TOKEN)
    return adapter


def test_valid_symbol_and_index_updates():
    """Symbol and index ticks emit complete canonical events."""
    clock = FakeClock()
    transport = FakeTransport()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(transport, _options(clock, events))

    assert adapter.subscribe(SYMBOL) is True
    assert adapter.subscribe(INDEX) is True
    raw_symbol = _frame(SYMBOL, 100.5)
    raw_index = _frame(INDEX, 24000.25)
    first = adapter.process_payload(raw_symbol)
    second = adapter.process_payload(raw_index)

    assert [first.kind, second.kind] == ["symbol", "index"]
    assert first.validation_status == fyers_socket.VALID
    assert first.canonical_instrument_id == f"ID::symbol::{SYMBOL}"
    assert second.canonical_instrument_id == f"ID::index::{INDEX}"
    assert first.fields["ltp"] == 100.5
    assert "sequence" not in first.fields
    assert first.raw_sha256 == hashlib.sha256(raw_symbol).hexdigest()
    assert first.raw_payload == raw_symbol
    assert first.receive_wall_clock_ns == clock.wall_clock_ns()
    assert first.receive_monotonic_ns == clock.monotonic_ns()
    assert first.completeness == fyers_socket.COMPLETE
    assert first.gap is None
    assert first.duplicate is False

    emitted = [event.as_dict() for event in events]
    assert set(emitted[0]) == CANONICAL_FIELDS
    assert emitted[0]["provider"] == fyers_socket.PROVIDER
    assert emitted[0]["connection_id"] == adapter.status()["connection_id"]

    transport.queued.append(_frame(SYMBOL, 101.0))
    polled = adapter.poll(0)
    assert len(polled) == 1
    assert polled[0].fields["ltp"] == 101.0
    assert adapter.poll(0) == ()


def test_malformed_message_is_retained_and_incomplete():
    """Malformed bytes are retained, hashed and never treated as a tick."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    bad = b'{"symbol": "NSE:SBIN-EQ", "ltp": "not-a-number"}'

    event = adapter.process_payload(bad)

    assert event.validation_status == fyers_socket.MALFORMED
    assert event.completeness == fyers_socket.INCOMPLETE
    assert event.raw_payload == bad
    assert event.raw_sha256 == hashlib.sha256(bad).hexdigest()
    assert event.canonical_instrument_id is None
    assert event.fields == {}
    assert events[0].as_dict()["raw_payload"] == bad.decode("utf-8")

    duplicate_key = b'{"symbol":"A","symbol":"B","ltp":1.0}'
    rejected = adapter.process_payload(duplicate_key)
    assert rejected.validation_status == fyers_socket.MALFORMED


def test_duplicate_event_detected():
    """An identical raw payload is flagged on its second arrival."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    frame = _frame(SYMBOL, 100.5)

    first = adapter.process_payload(frame)
    second = adapter.process_payload(frame)
    third = adapter.process_payload(_frame(SYMBOL, 100.75))

    assert first.duplicate is False
    assert second.duplicate is True
    assert third.duplicate is False
    assert first.raw_sha256 == second.raw_sha256
    assert [e.as_dict()["duplicate"] for e in events] == [
        False,
        True,
        False,
    ]


def test_stale_feed_detected():
    """Quiet feeds are marked stale from the monotonic clock."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock, stale_after_ns=100))

    first = adapter.process_payload(_frame(SYMBOL, 100.0))
    assert first.stale is False
    assert adapter.is_stale() is False

    clock.advance(101)
    assert adapter.is_stale() is True
    late = adapter.process_payload(_frame(SYMBOL, 101.0))
    assert late.stale is True


def test_reconnect_with_gap_and_recovery_snapshot():
    """Reconnect records a gap and recovery reconciles a current snapshot."""
    clock = FakeClock()
    transport = FakeTransport()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(transport, _options(clock, events))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)
    before = adapter.status()["connection_id"]

    gap = adapter.reconnect(TOKEN)

    assert gap.classification == fyers_socket.UNRECOVERABLE
    assert gap.connection_id_before == before
    assert gap.connection_id_after == adapter.status()["connection_id"]
    assert before != adapter.status()["connection_id"]
    assert transport.closed == 1
    assert adapter.status()["open_gap"] is True
    assert adapter.status()["subscriptions"] == {SYMBOL: True, INDEX: True}

    live = adapter.process_payload(_frame(SYMBOL, 102.0))
    assert live.gap is gap
    assert live.completeness == fyers_socket.INCOMPLETE

    recovered = adapter.reconcile_snapshot(
        {SYMBOL: {"ltp": 103.0}, INDEX: {"ltp": 24010.0}}
    )
    assert len(recovered) == 2
    assert all(e.event_type == fyers_socket.EVENT_SNAPSHOT for e in recovered)
    assert all(e.completeness == fyers_socket.COMPLETE for e in recovered)
    assert all(e.gap is not None for e in recovered)

    closed = adapter.status()["gaps"][0]
    assert closed.classification == fyers_socket.RECOVERABLE
    assert closed.covered_symbols == (SYMBOL, INDEX)
    assert closed.missing_symbols == ()
    assert adapter.status()["open_gap"] is False


def test_unrecoverable_gap_marked_incomplete():
    """With no snapshot every missed symbol is an explicit gap marker."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.reconnect(TOKEN)
    markers = adapter.mark_unrecoverable()

    assert [e.event_type for e in markers] == [
        fyers_socket.EVENT_GAP_MARKER,
        fyers_socket.EVENT_GAP_MARKER,
    ]
    assert all(e.completeness == fyers_socket.INCOMPLETE for e in markers)
    closed = adapter.status()["gaps"][0]
    assert closed.classification == fyers_socket.UNRECOVERABLE
    assert closed.covered_symbols == ()
    assert closed.missing_symbols == (SYMBOL, INDEX)
    assert [e.event_type for e in events] == [
        fyers_socket.EVENT_GAP_MARKER,
        fyers_socket.EVENT_GAP_MARKER,
    ]


def test_consecutive_reconnects_preserve_each_gap():
    """A second reconnect cannot overwrite an unreconciled earlier gap."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    adapter.subscribe(SYMBOL)

    adapter.reconnect(TOKEN)
    adapter.reconnect(TOKEN)
    adapter.mark_unrecoverable()

    gaps = adapter.status()["gaps"]
    assert len(gaps) == 2
    assert gaps[0].gap_id != gaps[1].gap_id
    assert all(gap.classification == fyers_socket.UNRECOVERABLE for gap in gaps)
    assert [event.event_type for event in events] == [
        fyers_socket.EVENT_GAP_MARKER,
        fyers_socket.EVENT_GAP_MARKER,
    ]


def test_partial_recovery_marks_missing_symbol_incomplete():
    """A partial snapshot only recovers the symbols it actually covers."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.reconnect(TOKEN)
    recovered = adapter.reconcile_snapshot({SYMBOL: {"ltp": 103.0}})

    by_symbol = {event.symbol: event for event in recovered}
    assert by_symbol[SYMBOL].completeness == fyers_socket.COMPLETE
    assert by_symbol[INDEX].completeness == fyers_socket.INCOMPLETE
    assert by_symbol[INDEX].event_type == fyers_socket.EVENT_GAP_MARKER
    assert adapter.status()["gaps"][0].classification == fyers_socket.PARTIAL


def test_invalid_snapshot_entry_stays_incomplete():
    """An invalid recovery snapshot cannot convert a gap into continuity."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.reconnect(TOKEN)
    recovered = adapter.reconcile_snapshot({SYMBOL: {}})

    assert recovered[0].validation_status == fyers_socket.MALFORMED
    assert recovered[0].completeness == fyers_socket.INCOMPLETE
    closed = adapter.status()["gaps"][0]
    assert closed.classification == fyers_socket.UNRECOVERABLE
    assert closed.missing_symbols == (SYMBOL, INDEX)


def test_subscription_limits_and_tracking():
    """Subscriptions are bounded, deduplicated and explicitly tracked."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, max_subscriptions=2))

    assert adapter.subscribe(SYMBOL) is True
    assert adapter.subscribe(INDEX) is True
    assert adapter.subscribe(SYMBOL) is False
    with pytest.raises(fyers_socket.SubscriptionLimitError):
        adapter.subscribe("NSE:RELIANCE-EQ")
    with pytest.raises(fyers_socket.SubscriptionError):
        adapter.subscribe("NSE:TEST-EQ", kind="future")
    assert adapter.status()["subscriptions"] == {SYMBOL: True, INDEX: True}

    assert adapter.unsubscribe(SYMBOL) is True
    assert adapter.unsubscribe(SYMBOL) is False
    assert adapter.subscribe("NSE:RELIANCE-EQ") is True
    assert any('"unsubscribe"' in frame for frame in transport.frames)


def test_controlled_shutdown_is_idempotent():
    """Shutdown closes once and makes all later operations fail."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)

    adapter.shutdown()
    adapter.shutdown()

    assert transport.closed == 1
    assert adapter.status()["closed"] is True
    assert adapter.status()["connected"] is False
    assert adapter.status()["subscriptions"] == {SYMBOL: False}
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.heartbeat()
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.process_payload(_frame(SYMBOL, 1.0))
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.connect(TOKEN)


def test_network_disabled_unless_opted_in():
    """Network-marked transports require an explicit opt-in."""
    clock = FakeClock()
    guarded = NetworkFakeTransport()
    blocked = fyers_socket.FyersDataSocket(guarded, options=_options(clock))
    with pytest.raises(fyers_socket.NetworkDisabledError):
        blocked.connect(TOKEN)
    assert not guarded.opens

    enabled = NetworkFakeTransport()
    opted_in = fyers_socket.FyersDataSocket(
        enabled, enable_network=True, options=_options(clock)
    )
    opted_in.connect(TOKEN)
    assert enabled.opens == [(fyers_socket.DATA_SOCKET_ENDPOINT, TOKEN)]


def test_bounded_retries_and_open_failure():
    """Open and send failures stop at the configured retry budget."""
    clock = FakeClock()
    transport = FakeTransport(open_failures=2, send_failures=1)
    adapter = fyers_socket.FyersDataSocket(
        transport, options=_options(clock, max_retries=3)
    )

    adapter.connect(TOKEN)
    assert len(transport.opens) == 3
    assert adapter.heartbeat() is True
    assert len(transport.frames) == 2

    failed = FakeTransport(open_failures=5)
    doomed = fyers_socket.FyersDataSocket(
        failed, options=_options(clock, max_retries=2)
    )
    with pytest.raises(fyers_socket.ConnectionFailedError):
        doomed.connect(TOKEN)
    assert len(failed.opens) == 2


def test_general_websocket_is_forbidden():
    """The order/trade/account General Socket is never a valid endpoint."""
    clock = FakeClock()
    adapter = fyers_socket.FyersDataSocket(
        FakeTransport(), options=_options(clock)
    )

    assert fyers_socket.GENERAL_WEBSOCKET_ENABLED is False
    with pytest.raises(fyers_socket.ForbiddenEndpointError):
        adapter.validate_endpoint(fyers_socket.GENERAL_SOCKET_ENDPOINT)
    adapter.validate_endpoint(fyers_socket.DATA_SOCKET_ENDPOINT)


def test_caller_token_is_not_retained():
    """The read-only token stays with the caller and never enters events."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    adapter.subscribe(SYMBOL)

    adapter.reconnect(TOKEN)
    adapter.process_payload(_frame(SYMBOL, 104.0))
    adapter.mark_unrecoverable()

    assert all(TOKEN not in str(value) for value in vars(adapter).values())
    assert TOKEN not in str(adapter.status())
    assert all(TOKEN not in str(event.as_dict()) for event in events)
