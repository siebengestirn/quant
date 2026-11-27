"""Point-in-time universe from effective-dated, published status history.

Identity is always the canonical instrument ID supplied by the W1 master; a
symbol is only a caller-facing alias resolved through an explicit registry.
Records published after the observation time, records not yet effective, and
conflicting or superseded revisions are excluded. All inputs are supplied by
the caller: this module contains no real listing or eligibility data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import NamedTuple

try:
    from exchange_calendar import (
        CalendarDocument,
        CalendarQuery,
        SessionProvenance,
        parse_instant,
        query_session,
    )
except ModuleNotFoundError:  # pragma: no cover - standalone spec loading
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exchange_calendar import (  # pylint: disable=import-outside-toplevel
        CalendarDocument,
        CalendarQuery,
        SessionProvenance,
        parse_instant,
        query_session,
    )

SCHEMA_VERSION = "pit-universe-v1"

INVALID_AS_OF = "INVALID_AS_OF"
NO_CALENDAR_SESSION = "NO_CALENDAR_SESSION"
HOLIDAY_SESSION = "HOLIDAY_SESSION"
CALENDAR_NOT_OBSERVED = "CALENDAR_NOT_OBSERVED_BY_AS_OF"
NO_STATUS_HISTORY = "NO_STATUS_HISTORY"
INVALID_STATUS_EVENT = "INVALID_STATUS_EVENT"
PUBLISHED_AFTER_AS_OF = "PUBLISHED_AFTER_AS_OF"
OBSERVED_AFTER_AS_OF = "OBSERVED_AFTER_AS_OF"
FUTURE_EFFECTIVE_DATE = "FUTURE_EFFECTIVE_DATE"
NO_STATUS_IN_EFFECT = "NO_STATUS_IN_EFFECT"
CONFLICTING_STATUS = "CONFLICTING_STATUS"
STALE_STATUS_REVISION = "STALE_STATUS_REVISION"
STATUS_NOT_ELIGIBLE = "STATUS_NOT_ELIGIBLE"
UNRESOLVED_SYMBOL = "UNRESOLVED_SYMBOL"
NO_SYMBOL_REGISTRATION = "NO_SYMBOL_REGISTRATION"

DEFAULT_EXCHANGE = "NSE"
DEFAULT_SEGMENT = "NSE_EQ"

_STATUS_FIELDS = (
    "canonical_instrument_id",
    "status_event_id",
    "eligible",
    "effective_from",
    "effective_to",
    "published_at",
    "observed_at",
    "revision",
    "source_digest",
)


class StatusEvent(NamedTuple):
    """One published, effective-dated eligibility observation."""

    canonical_instrument_id: str
    status_event_id: str
    eligible: bool
    effective_from: str
    effective_to: str | None
    published_at: str
    observed_at: str
    revision: int
    source_digest: str


class SymbolResolution(NamedTuple):
    """A symbol resolved through the registry, never by string identity."""

    symbol: str
    canonical_instrument_id: str | None
    reasons: tuple[str, ...]


class Decision(NamedTuple):
    """Eligibility decision with all reasons and the governing record."""

    canonical_instrument_id: str
    eligible: bool
    reasons: tuple[str, ...]
    status_event_id: str
    revision: int
    published_at: str
    source_digest: str
    symbols: tuple[str, ...]


class UniverseResult(NamedTuple):
    """Deterministic point-in-time universe and its provenance."""

    as_of_market_date: str
    as_of_observation_time: str
    eligible: tuple[Decision, ...]
    ineligible: tuple[Decision, ...]
    symbol_resolution: tuple[SymbolResolution, ...]
    calendar_provenance: SessionProvenance | None
    reasons: tuple[str, ...]
    digest: str


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _is_date(value: object) -> bool:
    """Return True for a canonical ISO date."""
    return (
        _text(value) != ""
        and parse_instant(f"{_text(value)}T00:00:00Z") is not None
    )


def _valid_event(event: StatusEvent) -> tuple[str, ...]:
    """Return the grammar violations of one status event."""
    errors = []
    for name in ("canonical_instrument_id", "status_event_id"):
        if not _text(getattr(event, name)):
            errors.append(f"missing {name}")
    if not isinstance(event.eligible, bool):
        errors.append("eligible must be a boolean")
    if not _is_date(event.effective_from):
        errors.append("invalid effective_from")
    if event.effective_to is not None and not _is_date(event.effective_to):
        errors.append("invalid effective_to")
    if (
        event.effective_to is not None
        and event.effective_from >= event.effective_to
    ):
        errors.append("effective interval is empty")
    if parse_instant(event.published_at) is None:
        errors.append("invalid published_at")
    if parse_instant(event.observed_at) is None:
        errors.append("invalid observed_at")
    if isinstance(event.revision, bool) or not isinstance(event.revision, int):
        errors.append("invalid revision")
    elif event.revision < 1:
        errors.append("revision must be positive")
    if event.source_digest == "" or len(event.source_digest) != 64:
        errors.append("invalid source_digest")
    return tuple(errors)


def event_from_mapping(data: Mapping[str, object]) -> StatusEvent:
    """Coerce a mapping into a status event with explicit missing values."""
    effective_to = _text(data.get("effective_to")) or None
    revision = data.get("revision", 0)
    return StatusEvent(
        canonical_instrument_id=_text(data.get("canonical_instrument_id")),
        status_event_id=_text(data.get("status_event_id")),
        eligible=(
            data.get("eligible")
            if isinstance(data.get("eligible"), bool)
            else False
        ),
        effective_from=_text(data.get("effective_from")),
        effective_to=effective_to,
        published_at=_text(data.get("published_at")),
        observed_at=_text(data.get("observed_at")),
        revision=revision if isinstance(revision, int) else 0,
        source_digest=_text(data.get("source_digest")),
    )


def coerce_event(value: StatusEvent | Mapping[str, object]) -> StatusEvent:
    """Accept an already typed event or a decoded JSON mapping."""
    if isinstance(value, StatusEvent):
        return value
    if isinstance(value, Mapping):
        return event_from_mapping(value)
    raise ValueError("status history contains an unsupported event type")


def validate_status_history(
    status_history: Mapping[str, Sequence[StatusEvent | Mapping[str, object]]],
) -> tuple[str, ...]:
    """Return structural violations of a canonical-ID keyed history."""
    if not isinstance(status_history, Mapping):
        return ("STATUS_HISTORY_NOT_A_MAPPING",)
    errors = []
    seen_events: set[tuple[str, str]] = set()
    for identity, values in status_history.items():
        if _text(identity) != identity or not identity:
            errors.append(f"invalid history key: {identity!r}")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            errors.append(f"history is not a sequence: {identity!r}")
            continue
        for value in values:
            event = coerce_event(value)
            if event.canonical_instrument_id != identity:
                errors.append(f"history key mismatch: {identity!r}")
            key = (event.canonical_instrument_id, event.status_event_id)
            if key in seen_events:
                errors.append(
                    f"duplicate status_event_id: {event.status_event_id}"
                )
            seen_events.add(key)
            errors.extend(_valid_event(event))
    return tuple(errors)


def _coerce_history(
    status_history: Mapping[str, Sequence[StatusEvent | Mapping[str, object]]],
) -> dict[str, tuple[StatusEvent, ...]]:
    """Normalise the supplied history into immutable event tuples."""
    if not isinstance(status_history, Mapping):
        raise ValueError("status_history must be a mapping")
    return {
        _text(identity): tuple(coerce_event(item) for item in values)
        for identity, values in status_history.items()
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes))
    }


def _coerce_registry(
    registry: Mapping[str, str],
) -> dict[str, str]:
    """Normalise alias mappings; invalid entries are dropped, not guessed."""
    if not isinstance(registry, Mapping):
        raise ValueError("registry must be a mapping")
    return {
        _text(symbol): _text(identity)
        for symbol, identity in registry.items()
        if _text(symbol) and _text(identity)
    }


def _reasons_for_timing(
    event: StatusEvent, as_of_observation_time: str
) -> tuple[str, ...]:
    """Return publication-time violations for one event."""
    reasons = []
    if event.published_at > as_of_observation_time:
        reasons.append(PUBLISHED_AFTER_AS_OF)
    if event.observed_at > as_of_observation_time:
        reasons.append(OBSERVED_AFTER_AS_OF)
    return tuple(reasons)


def _covers(event: StatusEvent, market_date: str) -> bool:
    """Test a half-open effective interval for a market date."""
    if event.effective_from > market_date:
        return False
    return event.effective_to is None or market_date < event.effective_to


def _empty_decision(
    identity: str, symbols: tuple[str, ...], reasons: tuple[str, ...]
) -> Decision:
    """Build a fail-closed decision with no governing status event."""
    return Decision(identity, False, reasons, "", 0, "", "", symbols)


def _conflict_or_stale(
    covering: Sequence[StatusEvent],
    all_events: Sequence[StatusEvent],
    market_date: str,
) -> tuple[StatusEvent | None, tuple[str, ...]]:
    """Resolve covering events, failing closed on conflict or staleness."""
    flags = {item.eligible for item in covering}
    if len(flags) != 1:
        return None, (CONFLICTING_STATUS,)
    newest = max(item.revision for item in covering)
    current = [item for item in covering if item.revision == newest]
    if len({item.status_event_id for item in current}) != 1:
        return None, (CONFLICTING_STATUS,)
    if any(
        item.revision > newest and not _covers(item, market_date)
        for item in all_events
    ):
        return None, (STALE_STATUS_REVISION,)
    return current[0], ()


def _evaluate_events(
    identity: str,
    events: Sequence[StatusEvent],
    market_date: str,
    as_of_observation_time: str,
    symbols: tuple[str, ...],
) -> Decision:
    """Evaluate one canonical ID at one market date and observation time."""
    if not events:
        return _empty_decision(identity, symbols, (NO_STATUS_HISTORY,))
    if any(_valid_event(item) for item in events):
        return _empty_decision(identity, symbols, (INVALID_STATUS_EVENT,))
    timed_out = [
        item
        for item in events
        if _reasons_for_timing(item, as_of_observation_time)
    ]
    visible = [item for item in events if item not in timed_out]
    covering = [item for item in visible if _covers(item, market_date)]
    if not covering:
        late_covering = next(
            (item for item in timed_out if _covers(item, market_date)), None
        )
        if late_covering is not None:
            reasons = _reasons_for_timing(late_covering, as_of_observation_time)
        elif any(item.effective_from > market_date for item in visible):
            reasons = (FUTURE_EFFECTIVE_DATE,)
        else:
            reasons = (NO_STATUS_IN_EFFECT,)
        return _empty_decision(identity, symbols, reasons)
    governing, reasons = _conflict_or_stale(covering, events, market_date)
    if reasons:
        return _empty_decision(identity, symbols, reasons)
    assert governing is not None
    if governing.eligible:
        return Decision(
            identity,
            True,
            (),
            governing.status_event_id,
            governing.revision,
            governing.published_at,
            governing.source_digest,
            symbols,
        )
    return Decision(
        identity,
        False,
        (STATUS_NOT_ELIGIBLE,),
        governing.status_event_id,
        governing.revision,
        governing.published_at,
        governing.source_digest,
        symbols,
    )


def _resolution(
    registry: Mapping[str, str],
    history: Mapping[str, tuple[StatusEvent, ...]],
) -> tuple[tuple[SymbolResolution, ...], dict[str, tuple[str, ...]]]:
    """Resolve aliases and group them by canonical ID deterministically."""
    resolutions = []
    aliases: dict[str, list[str]] = {}
    for symbol in sorted(registry):
        identity = registry[symbol]
        if identity not in history:
            resolutions.append(
                SymbolResolution(symbol, identity, (NO_STATUS_HISTORY,))
            )
            continue
        resolutions.append(SymbolResolution(symbol, identity, ()))
        aliases.setdefault(identity, []).append(symbol)
    grouped = {
        identity: tuple(sorted(symbols))
        for identity, symbols in aliases.items()
    }
    return tuple(resolutions), grouped


def _result_digest(result: UniverseResult) -> str:
    """Derive a deterministic digest over a universe result."""
    material = {
        "as_of_market_date": result.as_of_market_date,
        "as_of_observation_time": result.as_of_observation_time,
        "eligible": [item._asdict() for item in result.eligible],
        "ineligible": [item._asdict() for item in result.ineligible],
        "symbol_resolution": [
            item._asdict() for item in result.symbol_resolution
        ],
        "reasons": list(result.reasons),
    }
    payload = json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finalize(
    result: UniverseResult,
) -> UniverseResult:
    """Attach the deterministic digest to an assembled result."""
    return result._replace(digest=_result_digest(result))


def point_in_time_universe(
    as_of_market_date: str,
    as_of_observation_time: str,
    status_history: Mapping[str, Sequence[StatusEvent | Mapping[str, object]]],
    calendar: CalendarDocument,
    registry: Mapping[str, str],
    *,
    exchange: str = DEFAULT_EXCHANGE,
    segment: str = DEFAULT_SEGMENT,
) -> UniverseResult:
    """Build the eligibility universe visible at a market date and instant.

    The calendar must be observed by the observation time and the date must
    be a trading session. Symbol aliases resolve only through the supplied
    registry; a symbol equal to a canonical ID has no special meaning.
    """
    if not _is_date(as_of_market_date) or (
        parse_instant(as_of_observation_time) is None
    ):
        return _invalid_as_of(
            as_of_market_date, as_of_observation_time, registry
        )
    history = _coerce_history(status_history)
    aliases = _coerce_registry(registry)
    resolutions, grouped = _resolution(aliases, history)
    query = query_session(
        calendar,
        as_of_market_date,
        exchange,
        segment,
        as_of_observation_time,
    )
    if not query.found:
        return _calendar_blocked(
            as_of_market_date,
            as_of_observation_time,
            history,
            grouped,
            resolutions,
            query,
        )
    return _decide(
        as_of_market_date,
        as_of_observation_time,
        history,
        grouped,
        resolutions,
        query,
    )


def _invalid_as_of(
    market_date: str,
    observation_time: str,
    registry: Mapping[str, str],
) -> UniverseResult:
    """Fail closed when the requested as-of coordinates are malformed."""
    resolutions = tuple(
        SymbolResolution(symbol, identity, (INVALID_AS_OF,))
        for symbol, identity in sorted(_coerce_registry(registry).items())
    )
    return _finalize(
        UniverseResult(
            market_date,
            observation_time,
            (),
            (),
            resolutions,
            None,
            (INVALID_AS_OF,),
            "",
        )
    )


def _calendar_blocked(
    market_date: str,
    observation_time: str,
    history: Mapping[str, tuple[StatusEvent, ...]],
    grouped: Mapping[str, tuple[str, ...]],
    resolutions: tuple[SymbolResolution, ...],
    query: CalendarQuery,
) -> UniverseResult:
    """Return an empty universe when the calendar cannot authorise a date."""
    mapped = {
        "SESSION_IS_HOLIDAY": HOLIDAY_SESSION,
        "CALENDAR_NOT_OBSERVED_BY_AS_OF": CALENDAR_NOT_OBSERVED,
        "NO_SESSION_FOR_DATE": NO_CALENDAR_SESSION,
        "INVALID_CALENDAR_QUERY": INVALID_AS_OF,
    }
    reasons = tuple(mapped.get(item, item) for item in query.reasons)
    reasons = reasons or (NO_CALENDAR_SESSION,)
    ineligible = tuple(
        _empty_decision(identity, grouped.get(identity, ()), reasons)
        for identity in sorted(history)
    )
    return _finalize(
        UniverseResult(
            market_date,
            observation_time,
            (),
            ineligible,
            resolutions,
            query.provenance,
            reasons,
            "",
        )
    )


def _decide(
    market_date: str,
    observation_time: str,
    history: Mapping[str, tuple[StatusEvent, ...]],
    grouped: Mapping[str, tuple[str, ...]],
    resolutions: tuple[SymbolResolution, ...],
    query: CalendarQuery,
) -> UniverseResult:
    """Evaluate every known identity against one authorised session."""
    decisions = tuple(
        _evaluate_events(
            identity,
            history.get(identity, ()),
            market_date,
            observation_time,
            grouped.get(identity, ()),
        )
        for identity in sorted(set(history) | set(grouped))
    )
    eligible = tuple(item for item in decisions if item.eligible)
    ineligible = tuple(item for item in decisions if not item.eligible)
    return _finalize(
        UniverseResult(
            market_date,
            observation_time,
            eligible,
            ineligible,
            resolutions,
            query.provenance,
            (),
            "",
        )
    )
