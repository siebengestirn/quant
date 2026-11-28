"""Offline tests for the W3-A FYERS read-only Data Socket adapter.

All frames are SYNTHETIC GOLDEN payloads modelled on the documented FYERS
Data Socket schema (``cn``/``sub``/``sf``/``if``/``dp`` and errors). They are
not captured from a live feed. Every frame comes from an in-memory fake
transport and every timestamp from a fake clock; no network is contacted, no
credential is used and no live API is called.

LIVE ENTITLEMENT AND QUALIFICATION REMAIN PENDING: these tests verify adapter
behaviour only; they are not feed certification evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import socket
import sys
from collections import deque
from pathlib import Path
from unittest.mock import patch

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

# SYNTHETIC GOLDEN PAYLOADS — schema-shaped examples only, never live data.
GOLDEN_CN: dict[str, object] = {
    "type": "cn",
    "code": 200,
    "message": "Connected",
}
GOLDEN_SUB: dict[str, object] = {
    "type": "sub",
    "symbol": SYMBOL,
    "code": 200,
    "s": "ok",
    "message": "Subscribed",
}
GOLDEN_ERROR: dict[str, object] = {
    "type": "error",
    "code": 403,
    "message": "synthetic error",
}
GOLDEN_SF: dict[str, object] = {
    "symbol": SYMBOL,
    "ltp": 606.4,
    "prev_close_price": 620.2,
    "high_price": 610.5,
    "low_price": 605.85,
    "open_price": 609.85,
    "ch": -13.8,
    "chp": -2.23,
    "vol_traded_today": 3_045_212,
    "last_traded_time": 1_690_953_622,
    "exch_feed_time": 1_690_953_622,
    "bid_size": 2081,
    "ask_size": 903,
    "bid_price": 606.4,
    "ask_price": 606.45,
    "last_traded_qty": 5,
    "tot_buy_qty": 749_960,
    "tot_sell_qty": 1_092_063,
    "avg_trade_price": 608.2,
    "type": "sf",
}
GOLDEN_IF: dict[str, object] = {
    "symbol": INDEX,
    "ch": "-27.95",
    "high_price": "26277.35",
    "ltp": "26188.1",
    "exch_feed_time": "1727428424",
    "chp": "-0.11",
    "low_price": "26166.95",
    "open_price": "26248.25",
    "type": "if",
    "prev_close_price": "26216.05",
}
GOLDEN_DP: dict[str, object] = {
    "bid_price1": 606.25,
    "bid_price2": 606.2,
    "bid_price3": 606.15,
    "bid_price4": 606.1,
    "bid_price5": 606.05,
    "ask_price1": 606.3,
    "ask_price2": 606.35,
    "ask_price3": 606.4,
    "ask_price4": 606.45,
    "ask_price5": 606.5,
    "bid_size1": 20,
    "bid_size2": 902,
    "bid_size3": 111,
    "bid_size4": 110,
    "bid_size5": 979,
    "ask_size1": 282,
    "ask_size2": 568,
    "ask_size3": 2910,
    "ask_size4": 1676,
    "ask_size5": 2981,
    "bid_order1": 1,
    "bid_order2": 3,
    "bid_order3": 2,
    "bid_order4": 2,
    "bid_order5": 9,
    "ask_order1": 4,
    "ask_order2": 2,
    "ask_order3": 12,
    "ask_order4": 9,
    "ask_order5": 17,
    "type": "dp",
    "symbol": SYMBOL,
}


def _frame(payload: dict[str, object]) -> bytes:
    """Encode one synthetic golden payload as a raw socket frame."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


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


def _resolver(symbol: str, kind: str) -> str | None:
    """Resolve a canonical instrument id without any external lookup."""
    return f"ID::{kind}::{symbol}"


def _canonical(symbol: str, kind: str = fyers_socket.KIND_SYMBOL) -> str:
    """Return the canonical id the test resolver assigns to an instrument."""
    result = _resolver(symbol, kind)
    assert result is not None
    return result


def _digest(text: str) -> str:
    """Return a synthetic SHA-256 digest for one backfill record."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _backfill_records(
    canonical_id: str,
    start_ns: int,
    end_ns: int,
    *,
    step_ns: int = 1_000_000_000,
    source: str = "BACKFILL",
    with_sequences: bool = True,
) -> tuple[fyers_socket.BackfillRecord, ...]:
    """Build synthetic typed history spanning the interval inclusively."""
    records: list[fyers_socket.BackfillRecord] = []
    event_ns = start_ns
    sequence = 1
    while event_ns <= end_ns:
        records.append(
            fyers_socket.BackfillRecord(
                canonical_instrument_id=canonical_id,
                event_timestamp=event_ns,
                receive_timestamp=event_ns + 1,
                source=source,
                payload_digest=_digest(f"{canonical_id}:{event_ns}"),
                provider_sequence_if_available=(
                    sequence if with_sequences else None
                ),
            )
        )
        event_ns += step_ns
        sequence += 1
    return tuple(records)


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


def test_golden_symbol_update_maps_documented_fields():
    """The synthetic sf golden maps documented fields and emits cleanly."""
    clock = FakeClock()
    transport = FakeTransport()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(transport, _options(clock, events))

    assert adapter.subscribe(SYMBOL) is True
    raw = _frame(GOLDEN_SF)
    event = adapter.process_payload(raw)

    assert event.event_type == fyers_socket.TYPE_SYMBOL_UPDATE == "sf"
    assert event.kind == fyers_socket.KIND_SYMBOL
    assert event.validation_status == fyers_socket.VALID
    assert event.completeness == fyers_socket.COMPLETE
    assert event.canonical_instrument_id == f"ID::symbol::{SYMBOL}"
    assert event.fields["prev_close_price"] == 620.2
    assert event.fields["vol_traded_today"] == 3_045_212
    assert isinstance(event.fields["vol_traded_today"], int)
    assert not isinstance(event.fields["vol_traded_today"], bool)
    assert event.fields["last_traded_time"] == 1_690_953_622
    assert event.fields["bid_price"] == 606.4
    assert event.fields["ask_size"] == 903
    assert "volume" not in event.fields
    assert event.raw_payload == raw
    assert event.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert event.gap is None
    assert event.duplicate is False
    assert set(events[0].as_dict()) == CANONICAL_FIELDS

    transport.queued.append(raw)
    polled = adapter.poll(0)
    assert len(polled) == 1
    assert polled[0].fields["ltp"] == 606.4
    assert adapter.poll(0) == ()


def test_golden_index_update_maps_documented_fields():
    """The synthetic if golden is an index with no volume or bid/ask."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(INDEX)

    event = adapter.process_payload(_frame(GOLDEN_IF))

    assert event.event_type == fyers_socket.TYPE_INDEX_UPDATE == "if"
    assert event.kind == fyers_socket.KIND_INDEX
    assert event.validation_status == fyers_socket.VALID
    assert event.fields["ltp"] == 26188.1
    assert event.fields["prev_close_price"] == 26216.05
    assert "last_traded_time" not in event.fields
    assert "vol_traded_today" not in event.fields
    assert "bid_price" not in event.fields
    assert event.fields["exch_feed_time"] == 1_727_428_424


def test_golden_depth_update_maps_five_levels():
    """The synthetic dp golden carries all five documented levels."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)

    event = adapter.process_payload(_frame(GOLDEN_DP))

    assert event.event_type == fyers_socket.TYPE_DEPTH_UPDATE == "dp"
    assert event.kind == fyers_socket.KIND_SYMBOL
    assert event.validation_status == fyers_socket.VALID
    for level in range(1, 6):
        for side in ("bid", "ask"):
            price = f"{side}_price{level}"
            size = f"{side}_size{level}"
            orders = f"{side}_order{level}"
            assert event.fields[price] == GOLDEN_DP[price]
            assert event.fields[size] == GOLDEN_DP[size]
            assert isinstance(event.fields[size], int)
            assert not isinstance(event.fields[size], bool)
            assert event.fields[orders] == GOLDEN_DP[orders]
    assert "ltp" not in event.fields


def test_control_frames_do_not_require_symbol_or_ltp():
    """cn, symbol-less sub and error frames are handled without a tick."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    bare_sub = {"type": "sub", "code": 200, "s": "ok"}
    untyped_error = {"code": 403, "message": "synthetic", "s": "error"}

    connection = adapter.process_payload(_frame(GOLDEN_CN))
    subscription = adapter.process_payload(_frame(bare_sub))
    error = adapter.process_payload(_frame(GOLDEN_ERROR))
    untyped = adapter.process_payload(_frame(untyped_error))
    transport.queued.append(_frame(GOLDEN_CN))
    polled = adapter.poll(0)[0]

    assert [connection.event_type, subscription.event_type] == ["cn", "sub"]
    assert [error.event_type, untyped.event_type] == ["error", "error"]
    assert [connection.validation_status, subscription.validation_status] == [
        fyers_socket.VALID,
        fyers_socket.VALID,
    ]
    assert error.validation_status == fyers_socket.ERROR
    assert untyped.validation_status == fyers_socket.ERROR
    assert untyped.completeness == fyers_socket.INCOMPLETE
    assert all(event.symbol == "" for event in (connection, subscription))
    assert all("ltp" not in event.fields for event in (connection, error))
    assert polled.event_type == fyers_socket.TYPE_CONNECTION
    assert adapter.status()["heartbeat_fresh"] is True


def test_subscription_ack_marks_subscription_fresh():
    """A sub ack establishes subscription freshness, not event freshness."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    assert adapter.is_subscription_fresh(SYMBOL) is False

    adapter.process_payload(_frame(GOLDEN_SUB))

    freshness = adapter.symbol_freshness(SYMBOL)
    assert adapter.is_subscription_fresh(SYMBOL) is True
    assert freshness.ack_monotonic_ns == clock.monotonic_ns()
    assert freshness.last_receive_monotonic_ns == clock.monotonic_ns()
    assert freshness.last_valid_event_monotonic_ns is None
    assert adapter.is_symbol_fresh(SYMBOL) is False


@pytest.mark.parametrize(
    "invalid",
    [
        1.5,
        100.0,
        True,
        False,
        "3045212",
        "3.0",
        -1,
        2**63,
        10**30,
    ],
)
def test_exact_integer_volume_rejects_invalid_representations(invalid):
    """Volume accepts exact bounded ints only; nothing is int()-truncated."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    adapter.subscribe(SYMBOL)
    frame = _frame({**GOLDEN_SF, "vol_traded_today": invalid})

    event = adapter.process_payload(frame)

    assert event.validation_status == fyers_socket.MALFORMED
    assert event.completeness == fyers_socket.INCOMPLETE
    assert event.fields == {}
    assert event.raw_payload == frame
    assert event.raw_sha256 == hashlib.sha256(frame).hexdigest()
    assert adapter.is_symbol_fresh(SYMBOL) is False
    assert events[0].validation_status == fyers_socket.MALFORMED


def test_exact_integer_bounds_apply_to_all_quantity_fields():
    """Depth sizes, order counts and timestamps share the integer gate."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)

    valid = adapter.process_payload(
        _frame({**GOLDEN_SF, "vol_traded_today": 10**12})
    )
    assert valid.validation_status == fyers_socket.VALID
    assert valid.fields["vol_traded_today"] == 10**12

    bad_size = adapter.process_payload(_frame({**GOLDEN_DP, "bid_size1": 20.5}))
    bad_order = adapter.process_payload(
        _frame({**GOLDEN_DP, "ask_order1": "4"})
    )
    bad_bool = adapter.process_payload(
        _frame({**GOLDEN_DP, "bid_order2": True})
    )
    bad_time = adapter.process_payload(
        _frame({**GOLDEN_SF, "last_traded_time": -1})
    )
    assert all(
        event.validation_status == fyers_socket.MALFORMED
        for event in (bad_size, bad_order, bad_bool, bad_time)
    )


def test_malformed_frame_is_retained_and_attributed():
    """Bad frames stay raw and update receive time but not valid-event time."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    malformed = b'{"type":"sf","symbol":"NSE:SBIN-EQ","ltp":"not-a-number"}'

    event = adapter.process_payload(malformed)
    duplicate_keys = adapter.process_payload(
        b'{"symbol":"A","symbol":"B","ltp":1.0}'
    )
    bad_utf8 = adapter.process_payload(b"\xff\xfe")

    assert event.validation_status == fyers_socket.MALFORMED
    assert duplicate_keys.validation_status == fyers_socket.MALFORMED
    assert bad_utf8.validation_status == fyers_socket.MALFORMED
    assert event.raw_payload == malformed
    freshness = adapter.symbol_freshness(SYMBOL)
    assert freshness.last_receive_monotonic_ns == clock.monotonic_ns()
    assert freshness.last_valid_event_monotonic_ns is None


def test_duplicate_event_detected():
    """An identical raw payload is flagged on its second arrival."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    frame = _frame(GOLDEN_SF)

    first = adapter.process_payload(frame)
    second = adapter.process_payload(frame)
    third = adapter.process_payload(_frame({**GOLDEN_SF, "ltp": 607.0}))

    assert [first.duplicate, second.duplicate, third.duplicate] == [
        False,
        True,
        False,
    ]
    assert first.raw_sha256 == second.raw_sha256
    assert [item.as_dict()["duplicate"] for item in events] == [
        False,
        True,
        False,
    ]


def test_feed_health_never_implies_instrument_freshness():
    """A healthy feed does not make quiet instruments fresh."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock, stale_after_ns=100))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    assert adapter.is_heartbeat_fresh() is False
    assert adapter.inbound_live() is False
    assert adapter.feed_healthy() is False
    assert adapter.heartbeat() is True
    assert adapter.is_heartbeat_fresh() is True
    assert adapter.inbound_live() is False
    assert adapter.feed_healthy() is False
    assert adapter.feed_health() == adapter.feed_healthy()
    assert adapter.is_symbol_fresh(SYMBOL) is False
    assert adapter.is_symbol_fresh(INDEX) is False

    adapter.process_payload(_frame(GOLDEN_CN))
    assert adapter.feed_healthy() is True
    assert adapter.market_active() is False

    adapter.process_payload(_frame(GOLDEN_SF))
    assert adapter.feed_healthy() is True
    assert adapter.market_active() is True
    assert adapter.is_symbol_fresh(SYMBOL) is True
    assert adapter.is_symbol_fresh(INDEX) is False

    adapter.process_payload(_frame({**GOLDEN_SUB, "symbol": INDEX}))
    assert adapter.is_subscription_fresh(INDEX) is True
    assert adapter.is_symbol_fresh(INDEX) is False
    adapter.process_payload(_frame(GOLDEN_IF))
    assert adapter.is_symbol_fresh(INDEX) is True

    clock.advance(101)
    assert adapter.is_stale() is True
    assert adapter.feed_healthy() is False
    assert adapter.is_heartbeat_fresh() is False
    assert adapter.is_symbol_fresh(SYMBOL) is False
    assert adapter.is_subscription_fresh(INDEX) is False


def test_subscription_freshness_resets_across_reconnect():
    """Reconnection invalidates old acks until a new ack arrives."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    adapter.process_payload(_frame(GOLDEN_SUB))
    assert adapter.is_subscription_fresh(SYMBOL) is True

    adapter.reconnect(TOKEN)

    assert adapter.is_subscription_fresh(SYMBOL) is False
    adapter.process_payload(_frame(GOLDEN_SUB))
    assert adapter.is_subscription_fresh(SYMBOL) is True


def test_failed_reconnect_attempts_preserve_gap_and_allow_retry():
    """REGRESSION: three failed attempts leave the gap open for a retry."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, max_retries=1))
    transport.open_failures = 3
    adapter.subscribe(SYMBOL)
    before = adapter.status()["connection_id"]

    for _ in range(3):
        with pytest.raises(fyers_socket.ConnectionFailedError):
            adapter.reconnect(TOKEN)
        state = adapter.status()
        assert state["open_gap"] is True
        assert state["connected"] is False
        assert state["gaps"] == ()
        assert state["unresolved_gap"].gap_id == "gap-2"
        assert state["connection_id"] == before

    gap = adapter.reconnect(TOKEN)

    assert gap.gap_id == "gap-2"
    assert gap.connection_id_before == before
    assert gap.connection_id_after == adapter.status()["connection_id"]
    assert len(transport.opens) == 5
    assert adapter.status()["subscriptions"] == {SYMBOL: True}

    live = adapter.process_payload(_frame(GOLDEN_SF))
    assert live.gap is gap
    assert live.completeness == fyers_socket.INCOMPLETE

    adapter.recover_current_state({SYMBOL: {"ltp": 610.0}})
    assert adapter.status()["gaps"][0].classification == (
        fyers_socket.STATE_RECONCILED
    )


def test_close_gap_without_active_connection():
    """Closing an open gap needs no live transport or connection."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, max_retries=1))
    transport.open_failures = 1
    adapter.subscribe(SYMBOL)
    with pytest.raises(fyers_socket.ConnectionFailedError):
        adapter.reconnect(TOKEN)
    assert adapter.status()["connected"] is False

    markers = adapter.mark_unrecoverable()

    assert [event.event_type for event in markers] == [
        fyers_socket.EVENT_GAP_MARKER
    ]
    assert markers[0].completeness == fyers_socket.INCOMPLETE
    assert adapter.status()["gaps"][0].classification == (
        fyers_socket.UNRECOVERABLE
    )
    assert adapter.status()["open_gap"] is False


def test_snapshot_cannot_claim_history_recovery():
    """A fresh snapshot establishes state reconciliation only."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.reconnect(TOKEN)
    events = adapter.recover_current_state(
        {SYMBOL: {"ltp": 610.0}, INDEX: {"ltp": 26200.0}}
    )
    closed = adapter.status()["gaps"][0]

    assert closed.classification == fyers_socket.STATE_RECONCILED
    assert closed.classification != fyers_socket.HISTORY_RECOVERED
    assert closed.source == "SNAPSHOT"
    assert closed.authoritative is False
    assert closed.backfill_records == ()
    assert all(
        event.event_type == fyers_socket.EVENT_SNAPSHOT for event in events
    )
    assert all(event.completeness == fyers_socket.COMPLETE for event in events)
    assert adapter.symbol_freshness(SYMBOL).reconciliation_status == (
        fyers_socket.STATE_RECONCILED
    )

    adapter.reconnect(TOKEN)
    adapter.recover_current_state(
        {SYMBOL: {"ltp": 611.0}, INDEX: {"ltp": 26201.0}}
    )
    assert fyers_socket.HISTORY_RECOVERED not in {
        gap.classification for gap in adapter.status()["gaps"]
    }


def test_authoritative_backfill_establishes_history_recovery():
    """Only complete typed history can close a gap as history recovered."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, events))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    gap = _reconnect_with_gap(adapter, transport, clock)
    start_ns = gap.started_wall_clock_ns
    end_ns = gap.ended_wall_clock_ns
    symbol_records = _backfill_records(_canonical(SYMBOL), start_ns, end_ns)
    index_records = _backfill_records(
        _canonical(INDEX, fyers_socket.KIND_INDEX), start_ns, end_ns
    )
    emitted = adapter.recover_event_history(
        symbol_records + index_records, authoritative=True
    )
    recovered = adapter.status()["gaps"][0]

    assert recovered.classification == fyers_socket.HISTORY_RECOVERED
    assert recovered.source == "BACKFILL"
    assert recovered.authoritative is True
    assert recovered.covered_symbols == (SYMBOL, INDEX)
    assert recovered.missing_symbols == ()
    assert recovered.backfill_records == symbol_records + index_records
    assert recovered.validation_reasons == ()
    assert all(
        event.event_type == fyers_socket.EVENT_BACKFILL for event in emitted
    )
    assert len(emitted) == len(symbol_records) + len(index_records)
    assert {event.raw_sha256 for event in emitted} == {
        record.payload_digest for record in symbol_records + index_records
    }
    assert all(
        event.canonical_instrument_id is not None
        and event.canonical_instrument_id.startswith("ID::")
        for event in emitted
    )
    assert all("backfill" in event.fields for event in emitted)
    assert adapter.symbol_freshness(INDEX).reconciliation_status == (
        fyers_socket.HISTORY_RECOVERED
    )

    partial_gap = _reconnect_with_gap(adapter, transport, clock)
    partial = adapter.recover_event_history(
        _backfill_records(
            _canonical(SYMBOL),
            partial_gap.started_wall_clock_ns,
            partial_gap.ended_wall_clock_ns,
        ),
        authoritative=True,
    )
    incomplete = adapter.status()["gaps"][1]
    assert incomplete.classification == fyers_socket.HISTORY_INCOMPLETE
    assert incomplete.missing_symbols == (INDEX,)
    assert any(
        event.event_type == fyers_socket.EVENT_GAP_MARKER for event in partial
    )

    gap = _reconnect_with_gap(adapter, transport, clock)
    adapter.recover_event_history(
        _backfill_records(
            _canonical(SYMBOL),
            gap.started_wall_clock_ns,
            gap.ended_wall_clock_ns,
        )
        + _backfill_records(
            _canonical(INDEX, fyers_socket.KIND_INDEX),
            gap.started_wall_clock_ns,
            gap.ended_wall_clock_ns,
        ),
        authoritative=True,
        covered_from_ns=gap.started_wall_clock_ns - 1,
    )
    assert adapter.status()["gaps"][2].classification == (
        fyers_socket.HISTORY_INCOMPLETE
    )


def test_non_authoritative_backfill_is_only_state_reconciled():
    """Authority is explicit and never assumed from a backfill."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(adapter, transport, clock)
    parameters = inspect.signature(adapter.recover_event_history).parameters

    assert parameters["authoritative"].default is False
    emitted = adapter.recover_event_history(
        _backfill_records(
            _canonical(SYMBOL),
            gap.started_wall_clock_ns,
            gap.ended_wall_clock_ns,
        )
    )

    closed = adapter.status()["gaps"][0]
    assert closed.classification == fyers_socket.STATE_RECONCILED
    assert closed.authoritative is False
    assert all(
        event.event_type == fyers_socket.EVENT_BACKFILL for event in emitted
    )


def test_unrecoverable_gap_marks_all_symbols_incomplete():
    """With no data every missed symbol is an explicit gap marker."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    adapter = _connected(FakeTransport(), _options(clock, events))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.reconnect(TOKEN)
    markers = adapter.mark_unrecoverable()

    assert [event.event_type for event in markers] == [
        fyers_socket.EVENT_GAP_MARKER,
        fyers_socket.EVENT_GAP_MARKER,
    ]
    assert all(
        event.completeness == fyers_socket.INCOMPLETE for event in markers
    )
    closed = adapter.status()["gaps"][0]
    assert closed.classification == fyers_socket.UNRECOVERABLE
    assert closed.covered_symbols == ()
    assert closed.missing_symbols == (SYMBOL, INDEX)
    assert all(
        adapter.symbol_freshness(symbol).reconciliation_status
        == fyers_socket.UNRECOVERABLE
        for symbol in (SYMBOL, INDEX)
    )


def test_consecutive_reconnects_preserve_each_gap():
    """A second reconnect cannot overwrite an unresolved earlier gap."""
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


def test_shutdown_preserves_unresolved_gap_evidence():
    """Shutdown closes the transport but retains an unresolved gap."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = adapter.reconnect(TOKEN)
    closes_before_shutdown = transport.closed

    adapter.shutdown()
    adapter.shutdown()

    state = adapter.status()
    assert transport.closed == closes_before_shutdown + 1
    assert state["closed"] is True
    assert state["connected"] is False
    assert state["open_gap"] is True
    assert state["unresolved_gap"] is gap
    assert gap in state["gap_evidence"]
    assert state["gaps"] == ()
    assert state["subscriptions"] == {SYMBOL: False}
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.heartbeat()
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.process_payload(_frame(GOLDEN_SF))
    with pytest.raises(fyers_socket.ShutdownError):
        adapter.connect(TOKEN)


def test_subscription_limits_and_tracking():
    """Subscriptions are bounded and deduplicated."""
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

    assert adapter.unsubscribe(SYMBOL) is True
    assert adapter.unsubscribe(SYMBOL) is False
    assert adapter.subscribe("NSE:RELIANCE-EQ") is True
    assert any('"unsubscribe"' in frame for frame in transport.frames)


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


def test_import_performs_no_network_io():
    """Importing the adapter never touches a socket."""
    probe_name = "quant_fyers_socket_probe"
    probe_spec = importlib.util.spec_from_file_location(
        probe_name, ROOT / "python" / "fyers_socket.py"
    )
    assert probe_spec is not None and probe_spec.loader is not None
    probe = importlib.util.module_from_spec(probe_spec)
    # Python 3.13 dataclass processing resolves annotations through
    # sys.modules; register before exec_module and remove afterwards.
    sys.modules[probe_name] = probe
    try:
        with patch.object(
            socket, "socket", side_effect=AssertionError("network")
        ):
            probe_spec.loader.exec_module(probe)
    finally:
        sys.modules.pop(probe_name, None)

    assert probe.GENERAL_WEBSOCKET_ENABLED is False
    assert probe.DATA_SOCKET_ENDPOINT.startswith("wss://datastream.fyers.in")
    assert probe_name not in sys.modules


def test_caller_token_is_not_retained():
    """The read-only token stays with the caller and never enters events."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, events))
    adapter.subscribe(SYMBOL)
    adapter.reconnect(TOKEN)
    adapter.process_payload(_frame(GOLDEN_SF))
    adapter.mark_unrecoverable()

    assert all(TOKEN not in str(value) for value in vars(adapter).values())
    assert TOKEN not in str(adapter.status())
    assert all(TOKEN not in str(event.as_dict()) for event in events)
    assert all(TOKEN not in frame for frame in transport.frames)


def _reconnect_with_gap(
    adapter: fyers_socket.FyersDataSocket,
    transport: FakeTransport,
    clock: FakeClock,
    duration_ns: int = 10_000_000_000,
) -> fyers_socket.GapRecord:
    """Open a gap, leave it unresolved, then restore connectivity later."""
    transport.open_failures = 3
    with pytest.raises(fyers_socket.ConnectionFailedError):
        adapter.reconnect(TOKEN)
    clock.advance(duration_ns)
    gap = adapter.reconnect(TOKEN)
    assert gap.connection_id_after
    return gap


def test_one_snapshot_returns_state_reconciled_only():
    """REGRESSION: one LTP snapshot cannot claim recovered history."""
    clock = FakeClock()
    events: list[fyers_socket.CanonicalEvent] = []
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock, events))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(adapter, transport, clock)

    emitted = adapter.recover_current_state({SYMBOL: {"ltp": 610.0}})
    closed = adapter.status()["gaps"][0]

    assert len(emitted) == 1
    assert emitted[0].event_type == fyers_socket.EVENT_SNAPSHOT
    assert emitted[0].fields["ltp"] == 610.0
    assert emitted[0].gap is closed
    assert closed.gap_id == gap.gap_id
    assert closed.classification == fyers_socket.STATE_RECONCILED
    assert closed.classification != fyers_socket.HISTORY_RECOVERED
    assert closed.source == "SNAPSHOT"
    assert closed.authoritative is False
    assert all(
        gap_record.classification != fyers_socket.HISTORY_RECOVERED
        for gap_record in adapter.status()["gaps"]
    )


def test_snapshot_never_qualifies_as_event_history():
    """REGRESSION: snapshots passed to history recovery are rejected."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    _reconnect_with_gap(adapter, transport, clock)

    emitted = adapter.recover_event_history(
        {SYMBOL: {"ltp": 610.0}}, authoritative=True
    )
    closed = adapter.status()["gaps"][0]

    assert closed.classification in (
        fyers_socket.HISTORY_INCOMPLETE,
        fyers_socket.UNRECOVERABLE,
    )
    assert closed.classification != fyers_socket.HISTORY_RECOVERED
    assert closed.backfill_records == ()
    assert any(
        "typed BackfillRecord" in reason for reason in closed.validation_reasons
    )
    assert any(
        event.event_type == fyers_socket.EVENT_GAP_MARKER for event in emitted
    )
    assert not any(
        event.event_type == fyers_socket.EVENT_BACKFILL for event in emitted
    )


def test_partial_backfill_is_history_incomplete():
    """REGRESSION: history for only one instrument is incomplete."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)
    gap = _reconnect_with_gap(adapter, transport, clock)
    records = _backfill_records(
        _canonical(SYMBOL),
        gap.started_wall_clock_ns,
        gap.ended_wall_clock_ns,
    )

    emitted = adapter.recover_event_history(records, authoritative=True)
    closed = adapter.status()["gaps"][0]

    assert closed.classification == fyers_socket.HISTORY_INCOMPLETE
    assert closed.covered_symbols == (SYMBOL,)
    assert closed.missing_symbols == (INDEX,)
    assert closed.authoritative is False
    assert any(
        event.event_type == fyers_socket.EVENT_GAP_MARKER
        and event.symbol == INDEX
        for event in emitted
    )
    assert any(
        event.event_type == fyers_socket.EVENT_BACKFILL
        and event.symbol == SYMBOL
        and event.completeness == fyers_socket.INCOMPLETE
        for event in emitted
    )


def test_full_backfill_preserves_every_record():
    """REGRESSION: full backfill evidence is never collapsed to one point."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(adapter, transport, clock)
    records = _backfill_records(
        _canonical(SYMBOL),
        gap.started_wall_clock_ns,
        gap.ended_wall_clock_ns,
    )

    emitted = adapter.recover_event_history(records, authoritative=True)
    closed = adapter.status()["gaps"][0]

    assert closed.classification == fyers_socket.HISTORY_RECOVERED
    assert closed.backfill_records == records
    assert len(emitted) == len(records)
    assert [
        event.fields["backfill"]["event_timestamp"] for event in emitted
    ] == [record.event_timestamp for record in records]
    assert [event.raw_sha256 for event in emitted] == [
        record.payload_digest for record in records
    ]
    assert all(
        event.event_type == fyers_socket.EVENT_BACKFILL
        and event.completeness == fyers_socket.COMPLETE
        for event in emitted
    )


def test_missing_middle_interval_is_history_incomplete():
    """REGRESSION: an unexplained internal interval blocks recovery."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(
        adapter, transport, clock, duration_ns=30_000_000_000
    )
    records = _backfill_records(
        _canonical(SYMBOL),
        gap.started_wall_clock_ns,
        gap.ended_wall_clock_ns,
        with_sequences=False,
    )
    assert len(records) >= 5

    gapped = adapter.recover_event_history(
        records[:2] + records[-2:], authoritative=True
    )
    closed = adapter.status()["gaps"][0]

    assert closed.classification == fyers_socket.HISTORY_INCOMPLETE
    assert any("internal gap" in reason for reason in closed.validation_reasons)
    assert any(
        event.event_type == fyers_socket.EVENT_BACKFILL for event in gapped
    )

    next_gap = _reconnect_with_gap(adapter, transport, clock)
    current = _backfill_records(
        _canonical(SYMBOL),
        next_gap.started_wall_clock_ns,
        next_gap.ended_wall_clock_ns,
    )
    adapter.recover_event_history(current[:3] + current[4:], authoritative=True)
    sequence_gap = adapter.status()["gaps"][1]
    assert sequence_gap.classification == fyers_socket.HISTORY_INCOMPLETE
    assert any(
        "sequence gap" in reason for reason in sequence_gap.validation_reasons
    )


def test_backfill_span_order_and_duplicates_are_validated():
    """Incomplete, out-of-order and duplicated records cannot recover."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(adapter, transport, clock)
    records = _backfill_records(
        _canonical(SYMBOL),
        gap.started_wall_clock_ns,
        gap.ended_wall_clock_ns,
    )

    adapter.recover_event_history((records[0],), authoritative=True)
    assert any(
        "ends before" in reason
        for reason in adapter.status()["gaps"][0].validation_reasons
    )

    _reconnect_with_gap(adapter, transport, clock)
    adapter.recover_event_history(
        (records[0], records[2], records[1], *records[3:]), authoritative=True
    )
    assert any(
        "out of event order" in reason
        for reason in adapter.status()["gaps"][1].validation_reasons
    )

    _reconnect_with_gap(adapter, transport, clock)
    adapter.recover_event_history(
        records[:2] + (records[1],) + records[2:], authoritative=True
    )
    assert any(
        "duplicate payload digest" in reason
        for reason in adapter.status()["gaps"][2].validation_reasons
    )
    assert all(
        gap_record.classification == fyers_socket.HISTORY_INCOMPLETE
        for gap_record in adapter.status()["gaps"]
    )


def test_backfill_source_must_be_authorised():
    """An unauthorised source cannot establish historical completeness."""
    clock = FakeClock()
    transport = FakeTransport()
    adapter = _connected(transport, _options(clock))
    adapter.subscribe(SYMBOL)
    gap = _reconnect_with_gap(adapter, transport, clock)
    records = _backfill_records(
        _canonical(SYMBOL),
        gap.started_wall_clock_ns,
        gap.ended_wall_clock_ns,
        source="SNAPSHOT",
    )

    adapter.recover_event_history(records, authoritative=True)

    closed = adapter.status()["gaps"][0]
    assert closed.classification in (
        fyers_socket.HISTORY_INCOMPLETE,
        fyers_socket.UNRECOVERABLE,
    )
    assert closed.classification != fyers_socket.HISTORY_RECOVERED
    assert closed.authoritative is False
    assert any(
        "not authorised" in reason for reason in closed.validation_reasons
    )

    next_gap = _reconnect_with_gap(adapter, transport, clock)
    unsubscribed = _backfill_records(
        "ID::symbol::NSE:UNKNOWN-EQ",
        next_gap.started_wall_clock_ns,
        next_gap.ended_wall_clock_ns,
    )
    adapter.recover_event_history(unsubscribed, authoritative=True)
    assert adapter.status()["gaps"][1].classification == (
        fyers_socket.UNRECOVERABLE
    )


def test_heartbeat_send_does_not_establish_inbound_liveness():
    """REGRESSION: an outbound heartbeat never proves inbound traffic."""
    clock = FakeClock()
    adapter = _connected(
        FakeTransport(), _options(clock, stale_after_ns=5_000_000_000)
    )
    adapter.subscribe(SYMBOL)
    clock.advance(10_000_000_000)

    assert adapter.inbound_live() is False
    assert adapter.feed_healthy() is False
    assert adapter.heartbeat() is True

    state = adapter.status()
    assert state["last_successful_send_ns"] == clock.monotonic_ns()
    assert state["last_inbound_frame_ns"] is None
    assert state["last_server_heartbeat_or_pong_ns"] is None
    assert state["last_valid_symbol_event_ns"] is None
    assert state["last_valid_index_event_ns"] is None
    assert adapter.is_heartbeat_fresh() is True
    assert adapter.inbound_live() is False
    assert adapter.is_stale() is True
    assert adapter.feed_healthy() is False

    adapter.process_payload(_frame(GOLDEN_CN))
    state = adapter.status()
    assert state["last_inbound_frame_ns"] == clock.monotonic_ns()
    assert state["last_server_heartbeat_or_pong_ns"] == clock.monotonic_ns()
    assert adapter.inbound_live() is True
    assert adapter.feed_healthy() is True
    assert adapter.market_active() is False

    clock.advance(6_000_000_000)
    assert adapter.inbound_live() is False
    assert adapter.is_heartbeat_fresh() is False
    assert adapter.feed_healthy() is False


def test_per_instrument_freshness_is_independent():
    """REGRESSION: one symbol's ticks do not make another symbol fresh."""
    clock = FakeClock()
    adapter = _connected(FakeTransport(), _options(clock, stale_after_ns=100))
    adapter.subscribe(SYMBOL)
    adapter.subscribe(INDEX)

    adapter.process_payload(_frame(GOLDEN_SF))
    assert adapter.is_symbol_fresh(SYMBOL) is True
    assert adapter.is_symbol_fresh(INDEX) is False
    assert adapter.last_valid_symbol_event_ns == clock.monotonic_ns()
    assert adapter.last_valid_index_event_ns is None
    assert adapter.market_active() is True

    clock.advance(101)
    assert adapter.is_symbol_fresh(SYMBOL) is False
    adapter.process_payload(_frame(GOLDEN_IF))
    assert adapter.is_symbol_fresh(SYMBOL) is False
    assert adapter.is_symbol_fresh(INDEX) is True
    assert adapter.last_valid_symbol_event_ns < (
        adapter.last_valid_index_event_ns or 0
    )
    assert adapter.symbol_freshness(SYMBOL).last_valid_event_monotonic_ns != (
        adapter.symbol_freshness(INDEX).last_valid_event_monotonic_ns
    )
