"""Point-in-time universe tests using SYNTHETIC status and calendar data.

Nothing here claims a real listing, a real eligibility decision or provider
acceptance. All identities, aliases and digests are synthetic test values.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "exchange_calendar", ROOT / "python" / "exchange_calendar.py"
)
assert SPEC is not None and SPEC.loader is not None
cal = importlib.util.module_from_spec(SPEC)
sys.modules["exchange_calendar"] = cal
SPEC.loader.exec_module(cal)

PIT_SPEC = importlib.util.spec_from_file_location(
    "quant_pit_universe", ROOT / "python" / "pit_universe.py"
)
assert PIT_SPEC is not None and PIT_SPEC.loader is not None
pit = importlib.util.module_from_spec(PIT_SPEC)
sys.modules["quant_pit_universe"] = pit
PIT_SPEC.loader.exec_module(pit)

SOURCE = "SYNTHETIC-PIT-FIXTURE-NOT-NSE"
CAPTURED = "2024-01-01T00:00:00Z"
EXCHANGE = "SYNTHETIC-EXCHANGE"
SEGMENT = "SYNTHETIC-EQ"
DATE = "2024-01-02"
AS_OF = "2024-01-02T06:00:00Z"
CID_A = "SYNTHETIC-CID-A"
CID_B = "SYNTHETIC-CID-B"


def _calendar(**overrides: object) -> cal.CalendarDocument:
    """Build a one-session SYNTHETIC calendar document."""
    names = tuple(cal.session_fields(cal.session_from_mapping({})))
    values = (
        EXCHANGE,
        SEGMENT,
        DATE,
        None,
        None,
        f"{DATE}T04:00:00Z",
        f"{DATE}T08:30:00Z",
        None,
        [],
        False,
        False,
        False,
        [],
        SOURCE,
        CAPTURED,
    )
    data = dict(zip(names, values, strict=True))
    data.update(overrides)
    session = cal.session_from_mapping(data)
    document_data = {
        "schema_version": cal.SCHEMA_VERSION,
        "source_reference": SOURCE,
        "captured_at": CAPTURED,
        "sessions": [cal.session_payload(session)],
    }
    document = cal.document_from_mapping(document_data)
    document_data["digest"] = document.digest
    return cal.parse_calendar(
        json.dumps(document_data, sort_keys=True).encode("utf-8")
    )


def _event(
    identity: str = CID_A,
    *,
    event_id: str = "SYNTHETIC-EVENT-1",
    eligible: bool = True,
    effective_from: str = "2024-01-01",
    effective_to: str | None = None,
    published_at: str = "2024-01-01T01:00:00Z",
    observed_at: str = "2024-01-01T02:00:00Z",
    revision: int = 1,
) -> pit.StatusEvent:
    """Build one SYNTHETIC status event with an explicit digest."""
    return pit.StatusEvent(
        identity,
        event_id,
        eligible,
        effective_from,
        effective_to,
        published_at,
        observed_at,
        revision,
        "a" * 64,
    )


def _universe(
    history: Mapping[str, Sequence[pit.StatusEvent]],
    registry: Mapping[str, str],
    document: cal.CalendarDocument | None = None,
    *,
    as_of: str = AS_OF,
    date: str = DATE,
) -> pit.UniverseResult:
    """Run the point-in-time universe with SYNTHETIC inputs."""
    return pit.point_in_time_universe(
        date,
        as_of,
        history,
        document if document is not None else _calendar(),
        registry,
        exchange=EXCHANGE,
        segment=SEGMENT,
    )


def test_positive_universe_resolves_aliases_by_registry() -> None:
    """Two aliases for one canonical ID yield one deterministic decision."""
    history = {CID_A: (_event(),)}
    registry = {"SYNTHETIC-ALIAS-1": CID_A, "SYNTHETIC-ALIAS-2": CID_A}
    result = _universe(history, registry)
    assert len(result.eligible) == 1
    assert result.eligible[0].canonical_instrument_id == CID_A
    assert result.eligible[0].symbols == tuple(sorted(registry))
    assert all(item.reasons == () for item in result.eligible)
    assert all(item.reasons == () for item in result.symbol_resolution)
    assert result.calendar_provenance is not None
    assert result.calendar_provenance.session_digest != ""


def test_future_listings_are_excluded() -> None:
    """An event effective after the market date never enters the universe."""
    history = {
        CID_A: (_event(),),
        CID_B: (
            _event(
                CID_B, event_id="SYNTHETIC-EVENT-B", effective_from="2024-01-03"
            ),
        ),
    }
    result = _universe(history, {CID_A: CID_A, CID_B: CID_B})
    assert [item.canonical_instrument_id for item in result.eligible] == [CID_A]
    excluded = result.ineligible[0]
    assert excluded.canonical_instrument_id == CID_B
    assert pit.FUTURE_EFFECTIVE_DATE in excluded.reasons


def test_publication_time_excludes_late_records() -> None:
    """A record published or observed after the observation is invisible."""
    late = _event(
        event_id="SYNTHETIC-LATE",
        published_at="2024-01-02T07:00:00Z",
    )
    result = _universe({CID_A: (late,)}, {CID_A: CID_A})
    assert result.eligible == ()
    assert result.ineligible[0].reasons == (pit.PUBLISHED_AFTER_AS_OF,)

    observed_late = _event(
        event_id="SYNTHETIC-OBS-LATE",
        observed_at="2024-01-02T07:00:00Z",
    )
    result = _universe({CID_A: (observed_late,)}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.OBSERVED_AFTER_AS_OF,)


def test_symbol_equality_is_not_identity() -> None:
    """A symbol that happens to equal another ID does not grant eligibility."""
    history = {
        CID_A: (_event(),),
        CID_B: (_event(CID_B, event_id="SYNTHETIC-EVENT-B", eligible=False),),
    }
    registry = {"SYNTHETIC-ALIAS": CID_A, CID_A: CID_B}
    result = _universe(history, registry)
    decision_a = next(
        item
        for item in result.eligible
        if item.canonical_instrument_id == CID_A
    )
    assert decision_a.symbols == ("SYNTHETIC-ALIAS",)
    decision_b = next(
        item
        for item in result.ineligible
        if item.canonical_instrument_id == CID_B
    )
    assert decision_b.symbols == (CID_A,)
    assert decision_b.reasons == (pit.STATUS_NOT_ELIGIBLE,)


def test_conflicting_and_superseded_statuses_fail_closed() -> None:
    """Overlapping disagreements and newer non-covering revisions block."""
    conflicting = (
        _event(event_id="SYNTHETIC-YES", eligible=True),
        _event(event_id="SYNTHETIC-NO", eligible=False),
    )
    result = _universe({CID_A: conflicting}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.CONFLICTING_STATUS,)

    superseded = (
        _event(event_id="SYNTHETIC-OLD", revision=1),
        _event(
            CID_A,
            event_id="SYNTHETIC-NEW",
            effective_from="2024-01-03",
            revision=2,
        ),
    )
    result = _universe({CID_A: superseded}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.STALE_STATUS_REVISION,)


def test_half_open_interval_boundaries() -> None:
    """effective_from is inclusive and effective_to is exclusive."""
    starts_today = _event(effective_from=DATE)
    assert _universe({CID_A: (starts_today,)}, {CID_A: CID_A}).eligible
    ends_today = _event(effective_to=DATE)
    result = _universe({CID_A: (ends_today,)}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.NO_STATUS_IN_EFFECT,)


def test_calendar_blocks_holiday_and_unobserved_session() -> None:
    """No status record can make a blocked calendar date tradable."""
    history = {CID_A: (_event(),)}
    holiday = _calendar(holiday=True, market_start=None, market_end=None)
    result = _universe(history, {CID_A: CID_A}, holiday)
    assert result.eligible == ()
    assert result.ineligible[0].reasons == (pit.HOLIDAY_SESSION,)

    future_calendar = _calendar(captured_at="2024-01-03T00:00:00Z")
    result = _universe(history, {CID_A: CID_A}, future_calendar)
    assert result.eligible == ()
    assert result.ineligible[0].reasons == (pit.CALENDAR_NOT_OBSERVED,)


def test_invalid_inputs_and_history_structure_fail_closed() -> None:
    """Malformed coordinates and history keys never yield eligibility."""
    result = _universe({CID_A: (_event(),)}, {CID_A: CID_A}, date="2024-02-30")
    assert result.eligible == ()
    assert result.reasons == (pit.INVALID_AS_OF,)

    errors = pit.validate_status_history({CID_A: (_event("WRONG-KEY"),)})
    assert any("history key mismatch" in item for item in errors)
    duplicate = (_event(), _event())
    assert any(
        "duplicate status_event_id" in item
        for item in pit.validate_status_history({CID_A: duplicate})
    )


def test_deterministic_rebuild() -> None:
    """Identical inputs produce identical decisions and result digests."""
    history = {CID_A: (_event(),)}
    registry = {"SYNTHETIC-ALIAS": CID_A}
    first = _universe(history, registry)
    second = _universe(history, registry)
    assert first == second
    assert first.digest == second.digest
    assert len(first.digest) == 64
