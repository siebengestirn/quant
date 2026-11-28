"""Point-in-time universe tests using SYNTHETIC status and calendar data.

Nothing here claims a real listing, a real eligibility decision or provider
acceptance. All identities, aliases, digests and calendars are synthetic test
values explicitly labelled SYNTHETIC.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

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
FEB_DATE = "2024-02-05"
FEB_AS_OF = "2024-02-05T06:00:00Z"
CID_A = "SYNTHETIC-CID-A"
CID_B = "SYNTHETIC-CID-B"
ALIAS_A = "SYNTHETIC-ALIAS-A"
ALIAS_B = "SYNTHETIC-ALIAS-B"


class _State(NamedTuple):
    """A SYNTHETIC W1-like state with explicit validity bounds."""

    canonical_instrument_id: str
    effective_from: str | None = None
    effective_to: str | None = None

    def is_valid_on(self, market_date: str) -> bool:
        """Return whether this state covers a market date."""
        start = (
            self.effective_from is None or self.effective_from <= market_date
        )
        end = self.effective_to is None or market_date < self.effective_to
        return start and end


class _Resolver:
    """Offline SYNTHETIC alias resolver recording requested coordinates."""

    def __init__(self, mapping: Mapping[str, object]) -> None:
        self.mapping = dict(mapping)
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self, identifier: str, *, market_date: str, observed_at: str
    ) -> object:
        self.calls.append((identifier, market_date, observed_at))
        value = self.mapping.get(identifier)
        if callable(value):
            return value(market_date, observed_at)
        return value

    def requests(self) -> tuple[tuple[str, str, str], ...]:
        """Return the resolver requests recorded so far."""
        return tuple(self.calls)


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


def _calendar_on(
    session_date: str, *, captured_at: str = CAPTURED
) -> cal.CalendarDocument:
    """Build a SYNTHETIC calendar session on an explicitly supplied date."""
    return _calendar(
        session_date=session_date,
        captured_at=captured_at,
        market_start=f"{session_date}T04:00:00Z",
        market_end=f"{session_date}T08:30:00Z",
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
    aliases: Sequence[str] | Mapping[str, object],
    document: cal.CalendarDocument | None = None,
    *,
    as_of: str = AS_OF,
    date: str = DATE,
    resolver: _Resolver | None = None,
    **kwargs: object,
) -> pit.UniverseResult:
    """Run the point-in-time universe with SYNTHETIC inputs."""
    symbols = tuple(aliases)
    if resolver is None:
        mapping = (
            {item: aliases[item] for item in symbols}
            if isinstance(aliases, Mapping)
            else {item: CID_A for item in symbols}
        )
        resolver = _Resolver(mapping)
    return pit.point_in_time_universe(
        date,
        as_of,
        history,
        document if document is not None else _calendar(),
        symbols,
        resolver=resolver,
        exchange=EXCHANGE,
        segment=SEGMENT,
        **kwargs,
    )


def test_positive_universe_resolves_aliases_by_registry() -> None:
    """Two aliases for one canonical ID yield one deterministic decision."""
    history = {CID_A: (_event(),)}
    aliases = {ALIAS_A: CID_A, ALIAS_B: CID_A}
    resolver = _Resolver(aliases)
    result = _universe(history, aliases, resolver=resolver)
    assert len(result.eligible) == 1
    assert result.eligible[0].canonical_instrument_id == CID_A
    assert result.eligible[0].symbols == (ALIAS_A, ALIAS_B)
    assert all(item.reasons == () for item in result.eligible)
    assert all(item.reasons == () for item in result.symbol_resolution)
    assert resolver.calls == [
        (ALIAS_A, DATE, AS_OF),
        (ALIAS_B, DATE, AS_OF),
    ]
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
    """Resolver mappings, not string equality, decide canonical identity."""
    history = {
        CID_A: (_event(),),
        CID_B: (_event(CID_B, event_id="SYNTHETIC-EVENT-B", eligible=False),),
    }
    aliases = {ALIAS_A: CID_A, CID_A: CID_B}
    result = _universe(history, aliases)
    decision_a = next(
        item
        for item in result.eligible
        if item.canonical_instrument_id == CID_A
    )
    assert decision_a.symbols == (ALIAS_A,)
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
    assert pit.INVALID_AS_OF in result.reasons

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
    aliases = {ALIAS_A: CID_A}
    first = _universe(history, aliases)
    second = _universe(history, aliases)
    assert first == second
    assert first.digest == second.digest
    assert len(first.digest) == 64


def test_future_publication_never_changes_a_historical_decision() -> None:
    """A revision learned after the query cannot stale that query."""
    february = _event(
        event_id="SYNTHETIC-FEB",
        effective_from="2024-02-01",
        published_at="2024-02-01T01:00:00Z",
        observed_at="2024-02-01T02:00:00Z",
    )
    before = _universe(
        {CID_A: (february,)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    march_revision = _event(
        event_id="SYNTHETIC-MAR-REVISION",
        eligible=False,
        effective_from="2024-02-01",
        published_at="2024-03-01T01:00:00Z",
        observed_at="2024-03-01T02:00:00Z",
        revision=2,
    )
    after = _universe(
        {CID_A: (february, march_revision)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    assert before.eligible[0].reasons == ()
    assert after == before
    assert after.digest == before.digest
    assert pit.STALE_STATUS_REVISION not in after.eligible[0].reasons


def test_future_observation_never_changes_a_historical_decision() -> None:
    """Publication before the query is not enough: observation must fit."""
    february = _event(
        event_id="SYNTHETIC-FEB",
        effective_from="2024-02-01",
        published_at="2024-02-01T01:00:00Z",
        observed_at="2024-02-01T02:00:00Z",
    )
    late_observation = _event(
        event_id="SYNTHETIC-LATE-OBSERVATION",
        eligible=False,
        effective_from="2024-02-01",
        published_at="2024-02-01T03:00:00Z",
        observed_at="2024-03-01T02:00:00Z",
        revision=2,
    )
    before = _universe(
        {CID_A: (february,)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    after = _universe(
        {CID_A: (february, late_observation)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    assert after == before
    assert after.eligible[0].reasons == ()


def test_future_effective_date_is_not_applied_early() -> None:
    """A known but not-yet-effective record waits for its own start."""
    later = _event(
        CID_B,
        event_id="SYNTHETIC-LATER",
        effective_from="2024-01-03",
        published_at="2024-01-01T01:00:00Z",
        observed_at="2024-01-01T02:00:00Z",
    )
    result = _universe(
        {CID_A: (_event(),), CID_B: (later,)},
        {CID_A: CID_A, CID_B: CID_B},
    )
    assert [item.canonical_instrument_id for item in result.eligible] == [CID_A]
    assert pit.FUTURE_EFFECTIVE_DATE in result.ineligible[0].reasons


def test_retroactive_correction_learned_later_does_not_rewrite() -> None:
    """A later correction backdated into February is not known in February."""
    february = _event(
        event_id="SYNTHETIC-FEB",
        effective_from="2024-02-01",
        published_at="2024-02-01T01:00:00Z",
        observed_at="2024-02-01T02:00:00Z",
    )
    correction = _event(
        event_id="SYNTHETIC-RETRO-CORRECTION",
        eligible=False,
        effective_from="2024-02-01",
        published_at="2024-03-05T01:00:00Z",
        observed_at="2024-03-05T02:00:00Z",
        revision=3,
    )
    before = _universe(
        {CID_A: (february,)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    after = _universe(
        {CID_A: (february, correction)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    assert before.eligible[0].eligible
    assert after == before
    assert after.eligible[0].status_event_id == "SYNTHETIC-FEB"


def test_revision_closing_earlier_interval_learned_later_is_ignored() -> None:
    """A later-known revision ending before the date cannot stale it."""
    original = _event(
        event_id="SYNTHETIC-OPEN",
        effective_from="2024-01-01",
        published_at="2024-01-01T01:00:00Z",
        observed_at="2024-01-01T02:00:00Z",
    )
    closing = _event(
        event_id="SYNTHETIC-CLOSING-REVISION",
        eligible=False,
        effective_from="2024-01-01",
        effective_to=FEB_DATE,
        published_at="2024-03-01T01:00:00Z",
        observed_at="2024-03-01T02:00:00Z",
        revision=2,
    )
    before = _universe(
        {CID_A: (original,)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    after = _universe(
        {CID_A: (original, closing)},
        {ALIAS_A: CID_A},
        _calendar_on(FEB_DATE),
        as_of=FEB_AS_OF,
        date=FEB_DATE,
    )
    assert after == before
    assert after.eligible[0].eligible


def test_conflicting_same_revision_observations_fail_closed() -> None:
    """Same-revision observations cannot be silently combined."""
    disagreement = (
        _event(event_id="SYNTHETIC-SAME-YES", eligible=True),
        _event(event_id="SYNTHETIC-SAME-NO", eligible=False),
    )
    result = _universe({CID_A: disagreement}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.CONFLICTING_STATUS,)

    two_ids = (
        _event(event_id="SYNTHETIC-SAME-ID-1"),
        _event(event_id="SYNTHETIC-SAME-ID-2"),
    )
    result = _universe({CID_A: two_ids}, {CID_A: CID_A})
    assert result.ineligible[0].reasons == (pit.CONFLICTING_STATUS,)


def test_repeated_status_events_are_idempotent() -> None:
    """Repeating an identical event does not create a conflict."""
    event = _event()
    single = _universe({CID_A: (event,)}, {CID_A: CID_A})
    repeated = _universe({CID_A: (event, event)}, {CID_A: CID_A})
    assert repeated.eligible == single.eligible
    assert repeated.eligible[0].status_event_id == event.status_event_id


def test_publication_time_and_ingestion_time_disagreement() -> None:
    """Either time axis after the query hides the event and the alias."""
    event = _event()
    published_early_seen_late = _event(
        event_id="SYNTHETIC-SEEN-LATE",
        eligible=False,
        published_at="2024-01-01T03:00:00Z",
        observed_at="2024-01-02T07:00:00Z",
        revision=2,
    )
    published_late_seen_early = _event(
        event_id="SYNTHETIC-PUBLISHED-LATE",
        eligible=False,
        published_at="2024-01-02T07:00:00Z",
        observed_at="2024-01-01T02:00:00Z",
        revision=2,
    )
    baseline = _universe({CID_A: (event,)}, {ALIAS_A: CID_A})
    for extra in (published_early_seen_late, published_late_seen_early):
        result = _universe({CID_A: (event, extra)}, {ALIAS_A: CID_A})
        assert result == baseline

    ingested_late = _Resolver(
        {
            ALIAS_A: _State(
                CID_A,
                effective_from="2024-01-01",
                effective_to=None,
            )
        }
    )
    base_alias = _universe({CID_A: (event,)}, {ALIAS_A: CID_A})
    late_alias = _universe(
        {CID_A: (event,)},
        {ALIAS_A: CID_A},
        resolver=ingested_late,
    )
    assert base_alias.eligible[0].symbols == (ALIAS_A,)
    assert late_alias.symbol_resolution[0].reasons == ()
    assert ingested_late.calls == [(ALIAS_A, DATE, AS_OF)]


def test_aliases_resolve_at_requested_dates_and_expire() -> None:
    """Historical aliases are resolved at both dates; expired ones fail."""
    history = {
        CID_A: (_event(),),
        CID_B: (_event(CID_B, event_id="SYNTHETIC-EVENT-B"),),
    }
    history_later = {
        CID_A: (
            _event(effective_from="2024-01-01", effective_to="2024-01-04"),
        ),
        CID_B: (_event(CID_B, event_id="SYNTHETIC-EVENT-B"),),
    }

    def historical(market_date: str, observed_at: str) -> str:
        assert observed_at.endswith("T06:00:00Z")
        return CID_A if market_date == DATE else CID_B

    resolver = _Resolver({ALIAS_A: historical})
    first = _universe(history, {ALIAS_A: CID_A}, resolver=resolver)
    assert any(
        item.canonical_instrument_id == CID_A and item.symbols == (ALIAS_A,)
        for item in first.eligible
    )
    later = _universe(
        history,
        {ALIAS_A: CID_A},
        _calendar_on("2024-01-03"),
        resolver=resolver,
        date="2024-01-03",
        as_of="2024-01-03T06:00:00Z",
    )
    assert any(
        item.canonical_instrument_id == CID_B and item.symbols == (ALIAS_A,)
        for item in later.eligible
    )
    assert resolver.calls == [
        (ALIAS_A, DATE, AS_OF),
        (ALIAS_A, "2024-01-03", "2024-01-03T06:00:00Z"),
    ]

    expired = _Resolver({ALIAS_A: _State(CID_A, effective_to="2024-01-02")})
    result = _universe(history_later, {ALIAS_A: CID_A}, resolver=expired)
    assert result.symbol_resolution[0].canonical_instrument_id is None
    assert result.symbol_resolution[0].reasons == (pit.UNRESOLVED_SYMBOL,)
    assert pit.UNRESOLVED_SYMBOL in result.reasons
    assert result.eligible[0].symbols == ()


def test_unknown_alias_fails_closed() -> None:
    """An alias the resolver does not know grants no eligibility."""
    result = _universe(
        {CID_B: (_event(CID_B, event_id="SYNTHETIC-B", eligible=False),)},
        {ALIAS_A: CID_A},
        resolver=_Resolver({}),
    )
    assert result.eligible == ()
    assert result.symbol_resolution[0].canonical_instrument_id is None
    assert result.symbol_resolution[0].reasons == (pit.UNRESOLVED_SYMBOL,)


def test_default_resolver_calls_w1_contract_lazily() -> None:
    """Without injection, the W1 resolver gets both requested dates."""
    calls: list[tuple[str, str, str]] = []

    class FakeMaster:
        """Stand-in for the W1 resolver module during this test only."""

        @staticmethod
        def resolve(
            identifier: str, *, market_date: str, observed_at: str
        ) -> str:
            """Record the requested coordinates and return a synthetic ID."""
            calls.append((identifier, market_date, observed_at))
            return CID_A

        @staticmethod
        def requests() -> tuple[tuple[str, str, str], ...]:
            """Return coordinates seen by this stand-in module."""
            return tuple(calls)

    previous = sys.modules.get("instrument_master")
    sys.modules["instrument_master"] = FakeMaster
    try:
        result = pit.point_in_time_universe(
            DATE,
            AS_OF,
            {CID_A: (_event(),)},
            _calendar(),
            (ALIAS_A,),
            exchange=EXCHANGE,
            segment=SEGMENT,
        )
    finally:
        if previous is None:
            del sys.modules["instrument_master"]
        else:
            sys.modules["instrument_master"] = previous
    assert calls == [(ALIAS_A, DATE, AS_OF)]
    assert result.eligible[0].symbols == (ALIAS_A,)


def test_calendar_artifact_is_digest_verified() -> None:
    """Digest-verified bytes and mappings work; tampering fails closed."""
    document = _calendar()
    history = {CID_A: (_event(),)}
    baseline = _universe(history, {ALIAS_A: CID_A}, document)
    payload = cal.canonical_json(
        {
            "schema_version": document.schema_version,
            "source_reference": document.source_reference,
            "captured_at": document.captured_at,
            "digest": document.digest,
            "sessions": [
                cal.session_payload(item) for item in document.sessions
            ],
        }
    )
    from_bytes = _universe(
        history,
        {ALIAS_A: CID_A},
        payload,
        calendar_digest=document.digest,
    )
    assert from_bytes == baseline
    from_mapping = _universe(
        history,
        {ALIAS_A: CID_A},
        json.loads(payload.decode("utf-8")),
    )
    assert from_mapping == baseline

    tampered = document._replace(digest="b" * 64)
    bad = _universe(history, {ALIAS_A: CID_A}, tampered)
    assert bad.eligible == ()
    assert pit.INVALID_CALENDAR_ARTIFACT in bad.reasons

    mismatch = _universe(
        history,
        {ALIAS_A: CID_A},
        document,
        calendar_digest="c" * 64,
    )
    assert pit.CALENDAR_DIGEST_MISMATCH in mismatch.reasons
    assert mismatch.eligible == ()


def test_calendar_never_infers_weekdays() -> None:
    """Only explicit sessions authorise a date, weekend or not."""
    history = {CID_A: (_event(),)}
    saturday = "2024-01-06"
    explicit = _universe(
        history,
        {ALIAS_A: CID_A},
        _calendar_on(saturday),
        date=saturday,
        as_of=f"{saturday}T06:00:00Z",
    )
    assert explicit.eligible[0].canonical_instrument_id == CID_A

    missing = _universe(
        history,
        {ALIAS_A: CID_A},
        _calendar(),
        date=saturday,
        as_of=f"{saturday}T06:00:00Z",
    )
    assert missing.eligible == ()
    assert pit.NO_CALENDAR_SESSION in missing.reasons


def test_universe_and_calendar_digests_are_recorded() -> None:
    """Every decision carries the digests of the inputs that produced it."""
    document = _calendar()
    history = {CID_A: (_event(),)}
    result = _universe(history, {ALIAS_A: CID_A}, document)
    assert len(result.universe_digest) == 64
    assert result.calendar_digest == document.digest
    assert result.eligible[0].universe_digest == result.universe_digest
    assert result.eligible[0].calendar_digest == result.calendar_digest

    changed_history = _universe(
        {CID_A: (_event(event_id="SYNTHETIC-SECOND"),)},
        {ALIAS_A: CID_A},
        document,
    )
    assert changed_history.universe_digest != result.universe_digest
    assert changed_history.digest != result.digest

    changed_calendar = _universe(
        history,
        {ALIAS_A: CID_A},
        _calendar(captured_at="2024-01-01T05:00:00Z"),
    )
    assert changed_calendar.calendar_digest != result.calendar_digest
    assert changed_calendar.digest != result.digest
