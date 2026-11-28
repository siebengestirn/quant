"""Point-in-time universe from effective-dated, published status history.

Identity is the canonical instrument ID established by the W1 master; a
symbol is only a caller-facing alias resolved through an injected resolver at
both the requested market date and observation time. Records published or
observed after the observation time, records not yet effective, and
conflicting or superseded revisions are excluded. Conflict, staleness and
revision decisions see only the history actually visible at that observation
time, never a later-known revision.

The calendar is a digest-verified artifact supplied by the caller; weekdays
are never inferred. All inputs are caller-supplied: this module contains no
real listing, eligibility or session data.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

try:
    from exchange_calendar import (
        CalendarDocument,
        CalendarQuery,
        SessionProvenance,
        document_from_mapping,
        load_calendar,
        parse_calendar,
        parse_instant,
        query_session,
        validate_calendar,
    )
except ModuleNotFoundError:  # pragma: no cover - standalone spec loading
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    _calendar_module = importlib.import_module("exchange_calendar")
    CalendarDocument = _calendar_module.CalendarDocument
    CalendarQuery = _calendar_module.CalendarQuery
    SessionProvenance = _calendar_module.SessionProvenance
    document_from_mapping = _calendar_module.document_from_mapping
    load_calendar = _calendar_module.load_calendar
    parse_calendar = _calendar_module.parse_calendar
    parse_instant = _calendar_module.parse_instant
    query_session = _calendar_module.query_session
    validate_calendar = _calendar_module.validate_calendar

SCHEMA_VERSION = "pit-universe-v1"

INVALID_AS_OF = "INVALID_AS_OF"
NO_CALENDAR_SESSION = "NO_CALENDAR_SESSION"
HOLIDAY_SESSION = "HOLIDAY_SESSION"
CALENDAR_NOT_OBSERVED = "CALENDAR_NOT_OBSERVED_BY_AS_OF"
INVALID_CALENDAR_ARTIFACT = "INVALID_CALENDAR_ARTIFACT"
CALENDAR_DIGEST_MISMATCH = "CALENDAR_DIGEST_MISMATCH"
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
NO_STATUS_HISTORY_FOR_ALIAS = "NO_STATUS_HISTORY"

DEFAULT_EXCHANGE = "NSE"
DEFAULT_SEGMENT = "NSE_EQ"

# A resolver accepts (identifier, *, market_date, observed_at) and returns a
# canonical ID, a W1 InstrumentState, a mapping, or None when unresolved.
Resolver = Callable[..., object]
CalendarArtifact = CalendarDocument | bytes | str | Mapping[str, object] | Path

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
    """A symbol resolved at a market date and observation time."""

    symbol: str
    canonical_instrument_id: str | None
    reasons: tuple[str, ...]


class Decision(NamedTuple):
    """Eligibility decision with reasons, governing record and input digests."""

    canonical_instrument_id: str
    eligible: bool
    reasons: tuple[str, ...]
    status_event_id: str
    revision: int
    published_at: str
    source_digest: str
    symbols: tuple[str, ...]
    universe_digest: str = ""
    calendar_digest: str = ""


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
    universe_digest: str = ""
    calendar_digest: str = ""


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _digest(value: object) -> str:
    """Return the SHA-256 digest of a canonical JSON representation."""
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _alias_symbols(
    symbols: Sequence[str] | Mapping[str, object] | str | None,
    registry: Sequence[str] | Mapping[str, object] | str | None,
) -> tuple[str, ...]:
    """Collect caller-facing alias labels; mapping values are never identity."""
    collected: set[str] = set()
    for source in (symbols, registry):
        if source is None:
            continue
        if isinstance(source, str):
            collected.add(_text(source))
        elif isinstance(source, Mapping):
            collected.update(_text(item) for item in source)
        elif isinstance(source, Sequence) and not isinstance(
            source, (bytes, bytearray)
        ):
            collected.update(_text(item) for item in source)
    return tuple(sorted(item for item in collected if item))


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
    visible: Sequence[StatusEvent],
    market_date: str,
) -> tuple[StatusEvent | None, tuple[str, ...]]:
    """Resolve covering events using only history visible at the as-of time."""
    flags = {item.eligible for item in covering}
    if len(flags) != 1:
        return None, (CONFLICTING_STATUS,)
    newest = max(item.revision for item in covering)
    current = [item for item in covering if item.revision == newest]
    if len({item.status_event_id for item in current}) != 1:
        return None, (CONFLICTING_STATUS,)
    if any(
        item.revision > newest and not _covers(item, market_date)
        for item in visible
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
    governing, reasons = _conflict_or_stale(covering, visible, market_date)
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


def _w1_resolver(
    identifier: str, *, market_date: str, observed_at: str
) -> object:
    """Resolve lazily through the W1 bitemporal resolver contract."""
    try:
        module = importlib.import_module("instrument_master")
        return module.resolve(
            identifier, market_date=market_date, observed_at=observed_at
        )
    except (ImportError, AttributeError, TypeError):
        return None


def _resolution_reasons(value: object) -> tuple[str, ...]:
    """Normalise resolver-provided reasons to a tuple of labels."""
    if isinstance(value, str):
        return (_text(value),) if _text(value) else ()
    if isinstance(value, Sequence):
        return tuple(
            _text(item)
            for item in value
            if isinstance(item, str) and _text(item)
        )
    return ()


def _resolved_identity(
    resolution: object, market_date: str
) -> tuple[str | None, tuple[str, ...]]:
    """Extract a canonical ID from a resolver result, or fail closed."""
    if resolution is None:
        return None, ()
    if isinstance(resolution, str):
        return _text(resolution) or None, ()
    if isinstance(resolution, Mapping):
        identity = _text(resolution.get("canonical_instrument_id"))
        reasons = _resolution_reasons(resolution.get("reasons"))
        start = resolution.get("valid_from") or resolution.get("effective_from")
        end = resolution.get("valid_to") or resolution.get("effective_to")
    else:
        identity = _text(getattr(resolution, "canonical_instrument_id", ""))
        reasons = _resolution_reasons(getattr(resolution, "reasons", ()))
        start = getattr(resolution, "valid_from", None) or getattr(
            resolution, "effective_from", None
        )
        end = getattr(resolution, "valid_to", None) or getattr(
            resolution, "effective_to", None
        )
    if not identity:
        return None, reasons
    if isinstance(start, str) and start > market_date:
        return None, (UNRESOLVED_SYMBOL,)
    if isinstance(end, str) and end <= market_date:
        return None, (UNRESOLVED_SYMBOL,)
    return identity, ()


def _resolution(
    symbols: tuple[str, ...],
    history: Mapping[str, tuple[StatusEvent, ...]],
    market_date: str,
    observed_at: str,
    resolver: Resolver,
) -> tuple[
    tuple[SymbolResolution, ...], dict[str, tuple[str, ...]], tuple[str, ...]
]:
    """Resolve aliases at the requested dates and group by canonical ID."""
    resolutions = []
    aliases: dict[str, list[str]] = {}
    unresolved = False
    for symbol in symbols:
        try:
            raw = resolver(
                symbol, market_date=market_date, observed_at=observed_at
            )
        except (LookupError, TypeError, ValueError, AttributeError):
            raw = None
        identity, reasons = _resolved_identity(raw, market_date)
        if identity is None:
            resolutions.append(
                SymbolResolution(symbol, None, reasons or (UNRESOLVED_SYMBOL,))
            )
            unresolved = True
            continue
        if identity not in history:
            resolutions.append(
                SymbolResolution(
                    symbol, identity, (NO_STATUS_HISTORY_FOR_ALIAS,)
                )
            )
            continue
        resolutions.append(SymbolResolution(symbol, identity, ()))
        aliases.setdefault(identity, []).append(symbol)
    grouped = {
        identity: tuple(sorted(items)) for identity, items in aliases.items()
    }
    top = (UNRESOLVED_SYMBOL,) if unresolved else ()
    return tuple(resolutions), grouped, top


def _coerce_calendar(
    calendar: CalendarArtifact, expected_digest: str | None
) -> tuple[CalendarDocument | None, str, tuple[str, ...]]:
    """Load and digest-verify a supplied calendar artifact, fail closed."""
    if isinstance(calendar, CalendarDocument):
        document = calendar
        errors = tuple(validate_calendar(document))
    elif isinstance(calendar, Mapping):
        document = document_from_mapping(calendar)
        errors = tuple(validate_calendar(document))
    else:
        try:
            if isinstance(calendar, Path):
                document = load_calendar(calendar)
            else:
                payload = (
                    calendar.encode("utf-8")
                    if isinstance(calendar, str)
                    else bytes(calendar)
                )
                document = parse_calendar(payload)
            errors = ()
        except (ValueError, OSError, TypeError):
            return None, "", (INVALID_CALENDAR_ARTIFACT,)
    if errors:
        return None, document.digest, (INVALID_CALENDAR_ARTIFACT,)
    if (
        expected_digest is not None
        and expected_digest.lower() != document.digest.lower()
    ):
        return None, document.digest, (CALENDAR_DIGEST_MISMATCH,)
    return document, document.digest, ()


def _universe_digest(
    history: Mapping[str, tuple[StatusEvent, ...]],
    symbols: tuple[str, ...],
    as_of_observation_time: str,
) -> str:
    """Digest only the visible status universe and alias labels used."""
    visible = {
        identity: sorted(
            {
                json.dumps(item._asdict(), sort_keys=True, default=str)
                for item in events
                if not _reasons_for_timing(item, as_of_observation_time)
            }
        )
        for identity, events in sorted(history.items())
    }
    material = {
        "schema_version": SCHEMA_VERSION,
        "symbols": list(symbols),
        "status_history": visible,
    }
    return _digest(material)


def _result_digest(result: UniverseResult) -> str:
    """Derive a deterministic digest over a universe result and its inputs."""
    material = {
        "as_of_market_date": result.as_of_market_date,
        "as_of_observation_time": result.as_of_observation_time,
        "eligible": [item._asdict() for item in result.eligible],
        "ineligible": [item._asdict() for item in result.ineligible],
        "symbol_resolution": [
            item._asdict() for item in result.symbol_resolution
        ],
        "calendar_provenance": (
            result.calendar_provenance._asdict()
            if result.calendar_provenance is not None
            else None
        ),
        "universe_digest": result.universe_digest,
        "calendar_digest": result.calendar_digest,
        "reasons": list(result.reasons),
    }
    return _digest(material)


def _finalize(
    result: UniverseResult, *, universe_digest: str, calendar_digest: str
) -> UniverseResult:
    """Attach input digests to every decision and result, then digest."""
    eligible = tuple(
        item._replace(
            universe_digest=universe_digest, calendar_digest=calendar_digest
        )
        for item in result.eligible
    )
    ineligible = tuple(
        item._replace(
            universe_digest=universe_digest, calendar_digest=calendar_digest
        )
        for item in result.ineligible
    )
    enriched = result._replace(
        eligible=eligible,
        ineligible=ineligible,
        universe_digest=universe_digest,
        calendar_digest=calendar_digest,
    )
    return enriched._replace(digest=_result_digest(enriched))


def point_in_time_universe(
    as_of_market_date: str,
    as_of_observation_time: str,
    status_history: Mapping[str, Sequence[StatusEvent | Mapping[str, object]]],
    calendar: CalendarArtifact,
    symbols: Sequence[str] | Mapping[str, object] | str | None = None,
    *,
    resolver: Resolver | None = None,
    registry: Sequence[str] | Mapping[str, object] | str | None = None,
    alias_resolver: Resolver | None = None,
    calendar_digest: str | None = None,
    exchange: str = DEFAULT_EXCHANGE,
    segment: str = DEFAULT_SEGMENT,
) -> UniverseResult:
    """Build the eligibility universe visible at a market date and instant.

    Aliases resolve only through the supplied resolver (or the W1 resolver
    lazily) at both requested dates. A symbol equal to a canonical ID has no
    special meaning. The calendar must be a digest-verified artifact observed
    by the observation time and the date must be an explicit session.
    """
    labels = _alias_symbols(symbols, registry)
    try:
        history = _coerce_history(status_history)
        structural: tuple[str, ...] = ()
    except (TypeError, ValueError):
        history = {}
        structural = (INVALID_STATUS_EVENT,)
    universe_digest = _universe_digest(history, labels, as_of_observation_time)
    document, digest, calendar_errors = _coerce_calendar(
        calendar, calendar_digest
    )
    if not _is_date(as_of_market_date) or (
        parse_instant(as_of_observation_time) is None
    ):
        resolutions = tuple(
            SymbolResolution(symbol, None, (INVALID_AS_OF,))
            for symbol in labels
        )
        reasons = (INVALID_AS_OF,) + structural + calendar_errors
        return _blocked_universe(
            as_of_market_date,
            as_of_observation_time,
            history,
            {},
            resolutions,
            reasons=reasons,
            universe_digest=universe_digest,
            calendar_digest=digest,
            provenance=None,
        )

    active_resolver = resolver or alias_resolver or _w1_resolver
    resolutions, grouped, unresolved = _resolution(
        labels,
        history,
        as_of_market_date,
        as_of_observation_time,
        active_resolver,
    )
    reasons = structural + calendar_errors
    if reasons or document is None:
        return _blocked_universe(
            as_of_market_date,
            as_of_observation_time,
            history,
            grouped,
            resolutions,
            reasons=(reasons + unresolved) or (INVALID_CALENDAR_ARTIFACT,),
            universe_digest=universe_digest,
            calendar_digest=digest,
            provenance=None,
        )
    query = query_session(
        document,
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
            query=query,
            unresolved=unresolved,
            universe_digest=universe_digest,
            calendar_digest=digest,
        )
    return _decide(
        as_of_market_date,
        as_of_observation_time,
        history,
        grouped,
        resolutions,
        query=query,
        unresolved=unresolved,
        universe_digest=universe_digest,
        calendar_digest=digest,
    )


def _blocked_universe(
    market_date: str,
    observation_time: str,
    history: Mapping[str, tuple[StatusEvent, ...]],
    grouped: Mapping[str, tuple[str, ...]],
    resolutions: tuple[SymbolResolution, ...],
    *,
    reasons: tuple[str, ...],
    universe_digest: str,
    calendar_digest: str,
    provenance: SessionProvenance | None,
) -> UniverseResult:
    """Return an empty eligible universe for a fail-closed precondition."""
    ineligible = tuple(
        _empty_decision(identity, grouped.get(identity, ()), reasons)
        for identity in sorted(set(history) | set(grouped))
    )
    return _finalize(
        UniverseResult(
            market_date,
            observation_time,
            (),
            ineligible,
            resolutions,
            provenance,
            reasons,
            "",
            universe_digest,
            calendar_digest,
        ),
        universe_digest=universe_digest,
        calendar_digest=calendar_digest,
    )


def _calendar_blocked(
    market_date: str,
    observation_time: str,
    history: Mapping[str, tuple[StatusEvent, ...]],
    grouped: Mapping[str, tuple[str, ...]],
    resolutions: tuple[SymbolResolution, ...],
    *,
    query: CalendarQuery,
    unresolved: tuple[str, ...],
    universe_digest: str,
    calendar_digest: str,
) -> UniverseResult:
    """Return an empty universe when the calendar cannot authorise a date."""
    mapped = {
        "SESSION_IS_HOLIDAY": HOLIDAY_SESSION,
        "CALENDAR_NOT_OBSERVED_BY_AS_OF": CALENDAR_NOT_OBSERVED,
        "NO_SESSION_FOR_DATE": NO_CALENDAR_SESSION,
        "INVALID_CALENDAR_QUERY": INVALID_AS_OF,
    }
    reasons = tuple(mapped.get(item, item) for item in query.reasons)
    reasons = (reasons or (NO_CALENDAR_SESSION,)) + unresolved
    return _blocked_universe(
        market_date,
        observation_time,
        history,
        grouped,
        resolutions,
        reasons=reasons,
        universe_digest=universe_digest,
        calendar_digest=calendar_digest,
        provenance=query.provenance,
    )


def _decide(
    market_date: str,
    observation_time: str,
    history: Mapping[str, tuple[StatusEvent, ...]],
    grouped: Mapping[str, tuple[str, ...]],
    resolutions: tuple[SymbolResolution, ...],
    *,
    query: CalendarQuery,
    unresolved: tuple[str, ...],
    universe_digest: str,
    calendar_digest: str,
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
            unresolved,
            "",
            universe_digest,
            calendar_digest,
        ),
        universe_digest=universe_digest,
        calendar_digest=calendar_digest,
    )
