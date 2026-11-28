"""Event-sourced corporate actions with versioned derived series.

The raw price series, the append-only action ledger and the adjusted series
are three separate objects. Events are never rewritten: a correction is
appended with a higher revision and a new adjustment version, so an earlier
derived dataset can always be rebuilt exactly. No real corporate action is
embedded here; every event must be supplied with source provenance.

Ratio semantics are explicit and action-specific (see ``RATIO_SEMANTICS``):

* ``SPLIT``: ``ratio`` = new_shares_per_old_share; factor = ratio.
* ``BONUS``: ``ratio`` = bonus_shares_per_held_share (incremental);
  factor = 1 + ratio.
* ``CONSOLIDATION``: ``ratio`` = old_shares_per_new_share;
  factor = 1 / ratio.
* ``DIVIDEND`` / ``CASH_DIVIDEND``: no ratio; ``cash_amount`` is cash per
  share on the share basis immediately before the event.

RIGHTS, MERGER and DEMERGER terms are retained in the ledger but are marked
``UNAPPLIED_UNSUPPORTED_TERMS``: no generic ratio is ever applied to them.

Back-adjustment divides a raw close by the product of all share-count
factors with an ex-date strictly after that session. A cash entitlement is
divided by the product of all share-count factors with an ex-date on or after
the dividend, so cash and price use one consistent share basis. An applied
event with an explicitly missing ex-date is ledgered but reported as
``UNAPPLIED_MISSING_EX_DATE``; it never changes a price. With ``known_as_of``
the ledger is read as it was knowable then: for each action the highest
revision announced, observed and ingested by that instant is selected.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import NamedTuple

SCHEMA_VERSION = "corporate-actions-v2"

SPLIT = "SPLIT"
BONUS = "BONUS"
DIVIDEND = "DIVIDEND"
CASH_DIVIDEND = "CASH_DIVIDEND"
RIGHTS = "RIGHTS"
MERGER = "MERGER"
DEMERGER = "DEMERGER"
CONSOLIDATION = "CONSOLIDATION"
DELISTING = "DELISTING"
RELISTING = "RELISTING"

ACTION_TYPES = (
    BONUS,
    CASH_DIVIDEND,
    CONSOLIDATION,
    DELISTING,
    DEMERGER,
    DIVIDEND,
    MERGER,
    RELISTING,
    RIGHTS,
    SPLIT,
)
SHARE_ACTIONS = frozenset({SPLIT, BONUS, CONSOLIDATION})
CASH_ACTIONS = frozenset({DIVIDEND, CASH_DIVIDEND})
UNSUPPORTED_TERMS_ACTIONS = frozenset({RIGHTS, MERGER, DEMERGER})
APPLIED_ACTIONS = SHARE_ACTIONS | CASH_ACTIONS
RATIO_ACTIONS = SHARE_ACTIONS  # Rights, merger and demerger are not ratios.
STATUS_ACTIONS = frozenset({DELISTING, RELISTING})
SUCCESSOR_ACTIONS = frozenset({MERGER, DEMERGER})

NEW_PER_OLD = "new_shares_per_old_share"
BONUS_PER_HELD = "bonus_shares_per_held_share"
OLD_PER_NEW = "old_shares_per_new_share"
CASH_PER_SHARE = "cash_per_share"
NOT_APPLICABLE = "not_applicable"
UNAPPLIED_UNSUPPORTED_TERMS = "UNAPPLIED_UNSUPPORTED_TERMS"
UNAPPLIED_MISSING_EX_DATE = "UNAPPLIED_MISSING_EX_DATE"
UNAPPLIED_STATUS_ONLY = "UNAPPLIED_STATUS_ONLY"

RATIO_SEMANTICS: dict[str, str] = {
    SPLIT: NEW_PER_OLD,
    BONUS: BONUS_PER_HELD,
    CONSOLIDATION: OLD_PER_NEW,
    DIVIDEND: CASH_PER_SHARE,
    CASH_DIVIDEND: CASH_PER_SHARE,
    RIGHTS: UNAPPLIED_UNSUPPORTED_TERMS,
    MERGER: UNAPPLIED_UNSUPPORTED_TERMS,
    DEMERGER: UNAPPLIED_UNSUPPORTED_TERMS,
    DELISTING: NOT_APPLICABLE,
    RELISTING: NOT_APPLICABLE,
}

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_VERSION_RE = re.compile(r"^(.*)-v(\d+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ACTION_FIELDS = (
    "corporate_action_id",
    "canonical_instrument_id",
    "action_type",
    "announcement_timestamp",
    "record_date",
    "ex_date",
    "effective_date",
    "payment_date",
    "ratio",
    "cash_amount",
    "currency",
    "successor_instrument_id",
    "source",
    "source_digest",
    "observed_at",
    "revision",
    "adjustment_version",
    "ingested_at",
)
_REVISABLE_FIELDS = frozenset(
    {
        "action_type",
        "announcement_timestamp",
        "record_date",
        "ex_date",
        "effective_date",
        "payment_date",
        "ratio",
        "cash_amount",
        "currency",
        "successor_instrument_id",
        "source",
        "source_digest",
        "observed_at",
        "ingested_at",
    }
)


class ActionEvent(NamedTuple):
    """One append-only corporate action observation with provenance."""

    corporate_action_id: str
    canonical_instrument_id: str
    action_type: str
    announcement_timestamp: str
    record_date: str | None
    ex_date: str | None
    effective_date: str | None
    payment_date: str | None
    ratio: float | None
    cash_amount: float | None
    currency: str | None
    successor_instrument_id: str | None
    source: str
    source_digest: str
    observed_at: str
    revision: int
    adjustment_version: str
    ingested_at: str | None = None

    @property
    def ratio_semantics(self) -> str:
        """Return the declared meaning of ``ratio`` for this action type."""
        return RATIO_SEMANTICS.get(self.action_type, NOT_APPLICABLE)


class PriceBar(NamedTuple):
    """One raw, unadjusted close; None close is explicit missingness."""

    session_date: str
    close: float | None


class AdjustedBar(NamedTuple):
    """Derived returns for one session under one adjustment version."""

    session_date: str
    raw_close: float | None
    adjusted_close: float | None
    price_return: float | None
    total_return: float | None
    cash_entitlement: float | None
    applied_event_ids: tuple[str, ...]
    missing_cash_event_ids: tuple[str, ...]


class AdjustedSeries(NamedTuple):
    """A deterministic derived dataset, immutable and version-tagged.

    ``ledger_digest`` binds only the events selected for this adjustment
    version, so appending a correction never changes an older dataset.
    ``unapplied_terms`` records why ledgered events were not applied.
    """

    canonical_instrument_id: str
    adjustment_version: str
    bars: tuple[AdjustedBar, ...]
    event_ids: tuple[str, ...]
    unapplied_event_ids: tuple[str, ...]
    raw_series_digest: str
    ledger_digest: str
    digest: str
    unapplied_terms: tuple[tuple[str, str], ...] = ()


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    """Return a stripped string or None for an explicitly absent value."""
    return _text(value) or None


def _is_date(value: object) -> bool:
    """Return True for a real, zero-padded ISO calendar date."""
    text = _text(value)
    if not _DATE_RE.fullmatch(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _parse_instant(value: object) -> datetime | None:
    """Parse a strict UTC `YYYY-MM-DDTHH:MM:SSZ` instant, else return None."""
    text = _text(value)
    if not _INSTANT_RE.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _knowledge_cutoff(value: object) -> datetime | None:
    """Parse an ISO8601 instant, treating a bare date as midnight UTC."""
    if _is_date(value):
        return _parse_instant(f"{_text(value)}T00:00:00Z")
    return _parse_instant(value)


def _is_number(value: object) -> bool:
    """Return True for a finite, non-boolean JSON number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def event_from_mapping(data: Mapping[str, object]) -> ActionEvent:
    """Build an action event from decoded JSON, preserving missing dates."""
    revision = data.get("revision", 0)
    return ActionEvent(
        corporate_action_id=_text(data.get("corporate_action_id")),
        canonical_instrument_id=_text(data.get("canonical_instrument_id")),
        action_type=_text(data.get("action_type")),
        announcement_timestamp=_text(data.get("announcement_timestamp")),
        record_date=_optional_text(data.get("record_date")),
        ex_date=_optional_text(data.get("ex_date")),
        effective_date=_optional_text(data.get("effective_date")),
        payment_date=_optional_text(data.get("payment_date")),
        ratio=float(data["ratio"]) if _is_number(data.get("ratio")) else None,
        cash_amount=(
            float(data["cash_amount"])
            if _is_number(data.get("cash_amount"))
            else None
        ),
        currency=_optional_text(data.get("currency")),
        successor_instrument_id=_optional_text(
            data.get("successor_instrument_id")
        ),
        source=_text(data.get("source")),
        source_digest=_text(data.get("source_digest")),
        observed_at=_text(data.get("observed_at")),
        revision=revision if isinstance(revision, int) else 0,
        adjustment_version=_text(data.get("adjustment_version")),
        ingested_at=_optional_text(data.get("ingested_at")),
    )


def _validate_event_dates(event: ActionEvent, errors: list[str]) -> None:
    """Check action-specific dates; the universal date chain is not assumed."""
    values = (
        event.record_date,
        event.ex_date,
        event.effective_date,
        event.payment_date,
    )
    for name, value in zip(
        ("record_date", "ex_date", "effective_date", "payment_date"),
        values,
        strict=True,
    ):
        if value is not None and not _is_date(value):
            errors.append(f"invalid {name}")
    if any(value is not None and not _is_date(value) for value in values):
        return

    if event.action_type in SHARE_ACTIONS:
        if event.effective_date is None:
            errors.append("effective_date required for share action")
        if event.payment_date is not None:
            errors.append("payment_date not supported for share action")
        if (
            event.ex_date is not None
            and event.effective_date is not None
            and event.ex_date > event.effective_date
        ):
            errors.append("ex_date must not follow effective_date")
        return

    if event.action_type in CASH_ACTIONS:
        if (
            event.ex_date is not None
            and event.payment_date is not None
            and event.payment_date < event.ex_date
        ):
            errors.append("payment_date must not precede ex_date")
        return

    if event.action_type in STATUS_ACTIONS:
        if event.effective_date is None:
            errors.append("effective_date required for status action")
        if (
            event.ex_date is not None
            and event.effective_date is not None
            and event.ex_date > event.effective_date
        ):
            errors.append("ex_date must not follow effective_date")


def _validate_value_terms(event: ActionEvent, errors: list[str]) -> None:
    """Check that supplied numeric terms are finite and in range."""
    ratio = event.ratio
    cash = event.cash_amount
    if ratio is not None and (ratio <= 0 or not math.isfinite(ratio)):
        errors.append("ratio must be positive")
    if cash is not None and (cash < 0 or not math.isfinite(cash)):
        errors.append("cash_amount must be non-negative")


def _validate_action_terms(event: ActionEvent, errors: list[str]) -> None:
    """Check the ratio and cash fields required by this action type."""
    ratio = event.ratio
    cash = event.cash_amount
    if event.action_type in SHARE_ACTIONS:
        if ratio is None:
            errors.append("ratio required for share action")
        elif event.action_type in {SPLIT, CONSOLIDATION} and ratio <= 1:
            errors.append(f"{event.action_type.lower()} ratio must exceed one")
        if cash is not None:
            errors.append("cash_amount not supported for share action")
    elif event.action_type in CASH_ACTIONS:
        if cash is None:
            errors.append("cash_amount required for dividend")
        if ratio is not None:
            errors.append("ratio not supported for dividend")
        if event.currency is None or not _CURRENCY_RE.fullmatch(event.currency):
            errors.append("invalid dividend currency")
    elif event.action_type in STATUS_ACTIONS and (
        ratio is not None or cash is not None
    ):
        errors.append("status action cannot carry ratio or cash")


def _validate_successor(event: ActionEvent, errors: list[str]) -> None:
    """Check successor presence only for successor actions."""
    if (
        event.action_type in SUCCESSOR_ACTIONS
        and not event.successor_instrument_id
    ):
        errors.append("successor_instrument_id required")
    if (
        event.action_type not in SUCCESSOR_ACTIONS
        and event.successor_instrument_id is not None
    ):
        errors.append("unexpected successor_instrument_id")


def _validate_knowledge_times(event: ActionEvent, errors: list[str]) -> None:
    """Check that announcement, observation and ingestion are ordered."""
    announced = _parse_instant(event.announcement_timestamp)
    observed = _parse_instant(event.observed_at)
    if announced is None:
        errors.append("invalid announcement_timestamp")
    if observed is None:
        errors.append("invalid observed_at")
    elif announced is not None and observed < announced:
        errors.append("observed_at precedes announcement_timestamp")
    if event.ingested_at is not None:
        ingested = _parse_instant(event.ingested_at)
        if ingested is None:
            errors.append("invalid ingested_at")
        elif observed is not None and ingested < observed:
            errors.append("ingested_at precedes observed_at")


def validate_event(event: ActionEvent) -> tuple[str, ...]:
    """Return all contract violations for one corporate action event."""
    errors: list[str] = []
    for name in ("corporate_action_id", "canonical_instrument_id"):
        if not _ID_RE.fullmatch(_text(getattr(event, name))):
            errors.append(f"invalid {name}")
    if event.action_type not in ACTION_TYPES:
        errors.append("unsupported action_type")
    _validate_knowledge_times(event, errors)
    _validate_event_dates(event, errors)
    _validate_value_terms(event, errors)
    _validate_action_terms(event, errors)
    _validate_successor(event, errors)
    if not event.source:
        errors.append("missing source")
    if not _DIGEST_RE.fullmatch(event.source_digest):
        errors.append("invalid source_digest")
    if isinstance(event.revision, bool) or not isinstance(event.revision, int):
        errors.append("invalid revision")
    elif event.revision < 1:
        errors.append("revision must be positive")
    if not _ID_RE.fullmatch(event.adjustment_version):
        errors.append("invalid adjustment_version")
    return tuple(errors)


def validate_ledger(events: Sequence[ActionEvent]) -> tuple[str, ...]:
    """Validate every event and the uniqueness of (ID, revision) pairs."""
    errors: list[str] = []
    seen: set[tuple[str, int]] = set()
    for index, event in enumerate(events):
        errors.extend(
            f"events[{index}]: {error}" for error in validate_event(event)
        )
        key = (event.corporate_action_id, event.revision)
        if key in seen:
            errors.append(f"duplicate event revision: {key}")
        seen.add(key)
    return tuple(errors)


def event_payload(event: ActionEvent) -> dict[str, object]:
    """Return the canonical JSON mapping for one event."""
    return dict(zip(_ACTION_FIELDS, event, strict=True))


def ledger_digest(events: Sequence[ActionEvent]) -> str:
    """Digest the exact ordered event ledger."""
    payload = json.dumps(
        [event_payload(item) for item in events],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def knowledge_time(event: ActionEvent) -> datetime:
    """Return the first instant at which all knowledge gates are satisfied."""
    instants = [
        _parse_instant(event.announcement_timestamp),
        _parse_instant(event.observed_at),
    ]
    if event.ingested_at is not None:
        instants.append(_parse_instant(event.ingested_at))
    known = [item for item in instants if item is not None]
    if not known:
        raise ValueError("event has no knowledge timestamp")
    return max(known)


def _event_sort_key(event: ActionEvent) -> tuple[str, str, int]:
    """Order events deterministically for adjustment."""
    return (event.ex_date or "", event.corporate_action_id, event.revision)


class ActionLedger:
    """Immutable append-only ledger; append returns a new ledger."""

    def __init__(self, events: Sequence[ActionEvent] = ()) -> None:
        errors = validate_ledger(events)
        if errors:
            raise ValueError("; ".join(errors))
        self._events = tuple(events)

    @property
    def events(self) -> tuple[ActionEvent, ...]:
        """Return the immutable event tuple."""
        return self._events

    @property
    def digest(self) -> str:
        """Return the digest binding the exact event sequence."""
        return ledger_digest(self._events)

    def append(self, event: ActionEvent) -> ActionLedger:
        """Return a new ledger with one validated event appended."""
        return ActionLedger(self._events + (event,))

    def select(
        self,
        canonical_instrument_id: str,
        adjustment_version: str | None = None,
    ) -> tuple[ActionEvent, ...]:
        """Select events by canonical ID and optional adjustment version."""
        return tuple(
            item
            for item in self._events
            if item.canonical_instrument_id == canonical_instrument_id
            and (
                adjustment_version is None
                or item.adjustment_version == adjustment_version
            )
        )

    def as_known(
        self, canonical_instrument_id: str, known_as_of: object
    ) -> tuple[ActionEvent, ...]:
        """Select the latest revisions knowable at ``known_as_of``."""
        cutoff = _knowledge_cutoff(known_as_of)
        if cutoff is None:
            raise ValueError("invalid known_as_of")
        latest: dict[str, ActionEvent] = {}
        for item in self._events:
            if item.canonical_instrument_id != canonical_instrument_id:
                continue
            if knowledge_time(item) > cutoff:
                continue
            current = latest.get(item.corporate_action_id)
            if current is None or item.revision > current.revision:
                latest[item.corporate_action_id] = item
        return tuple(sorted(latest.values(), key=_event_sort_key))

    def versions(self, canonical_instrument_id: str) -> tuple[str, ...]:
        """Return sorted distinct adjustment versions for an instrument."""
        return tuple(
            sorted(
                {
                    item.adjustment_version
                    for item in self._events
                    if item.canonical_instrument_id == canonical_instrument_id
                }
            )
        )


def _next_version(value: str) -> str:
    """Derive the next deterministic adjustment version suffix."""
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        return f"{value}-v2"
    return f"{match.group(1)}-v{int(match.group(2)) + 1}"


def revise_event(
    ledger: ActionLedger,
    corporate_action_id: str,
    *,
    adjustment_version: str | None = None,
    **changes: object,
) -> ActionLedger:
    """Append a higher-revision correction without changing prior events."""
    unknown = set(changes) - _REVISABLE_FIELDS
    if unknown:
        raise ValueError(f"unrevisable fields: {','.join(sorted(unknown))}")
    existing = [
        item
        for item in ledger.events
        if item.corporate_action_id == corporate_action_id
    ]
    if not existing:
        raise ValueError("unknown corporate_action_id")
    previous = max(existing, key=lambda item: item.revision)
    version = adjustment_version or _next_version(previous.adjustment_version)
    if version in {item.adjustment_version for item in existing}:
        raise ValueError("adjustment_version already used for this event")
    revised = previous._replace(
        **changes, revision=previous.revision + 1, adjustment_version=version
    )
    errors = validate_event(revised)
    if errors:
        raise ValueError("; ".join(errors))
    return ledger.append(revised)


def validate_series(bars: Sequence[PriceBar]) -> tuple[str, ...]:
    """Validate raw series ordering, dates and explicit missing closes."""
    errors: list[str] = []
    dates = [item.session_date for item in bars]
    if dates != sorted(dates):
        errors.append("raw series is not ordered")
    if len(set(dates)) != len(dates):
        errors.append("duplicate raw session date")
    for index, row in enumerate(bars):
        if not _is_date(row.session_date):
            errors.append(f"bars[{index}]: invalid session date")
        if row.close is not None and (
            not _is_number(row.close) or row.close <= 0
        ):
            errors.append(f"bars[{index}]: invalid close")
    return tuple(errors)


def raw_series_digest(bars: Sequence[PriceBar]) -> str:
    """Digest the exact unadjusted raw series."""
    payload = json.dumps(
        [item._asdict() for item in bars],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def share_factor(event: ActionEvent) -> float:
    """Return the back-adjustment factor for one share-count action.

    The action type fixes the ratio meaning; no generic ratio multiplier is
    used for rights, mergers or demergers.
    """
    ratio = event.ratio
    if ratio is None:
        raise ValueError("share action without ratio")
    if event.action_type == SPLIT:
        return ratio
    if event.action_type == BONUS:
        return 1.0 + ratio
    if event.action_type == CONSOLIDATION:
        return 1.0 / ratio
    raise ValueError("not a share-count action")


def _price_factor(date: str, share_events: Sequence[ActionEvent]) -> float:
    """Return the cumulative factor for all splits after ``date``."""
    return math.prod(
        share_factor(event)
        for event in share_events
        if event.ex_date is not None and event.ex_date > date
    )


def _cash_factor(date: str, share_events: Sequence[ActionEvent]) -> float:
    """Return the factor that puts cash on the previous bar's share basis."""
    return math.prod(
        share_factor(event)
        for event in share_events
        if event.ex_date is not None and event.ex_date >= date
    )


def _cash_by_date(
    cash_events: Sequence[ActionEvent], share_events: Sequence[ActionEvent]
) -> dict[str, tuple[float | None, tuple[str, ...]]]:
    """Aggregate basis-consistent dividend entitlement per ex-date."""
    result: dict[str, tuple[float | None, tuple[str, ...]]] = {}
    for event in cash_events:
        if event.ex_date is None:
            continue
        factor = _cash_factor(event.ex_date, share_events)
        amount = (
            None if event.cash_amount is None else event.cash_amount / factor
        )
        total, missing = result.get(event.ex_date, (0.0, ()))
        if amount is None:
            result[event.ex_date] = (
                None,
                missing + (event.corporate_action_id,),
            )
        elif total is not None:
            result[event.ex_date] = (total + amount, missing)
    return result


def _bar_returns(
    adjusted: float | None,
    previous: float | None,
    cash: float | None,
    missing_cash: tuple[str, ...],
) -> tuple[float | None, float | None]:
    """Separate price-adjusted return from total return."""
    if adjusted is None or previous is None or previous <= 0:
        return None, None
    price_return = adjusted / previous - 1.0
    if cash is None or missing_cash:
        return price_return, None
    return price_return, price_return + cash / previous


def _build_adjusted_bars(
    bars: Sequence[PriceBar],
    share_events: Sequence[ActionEvent],
    cash_events: Sequence[ActionEvent],
) -> tuple[AdjustedBar, ...]:
    """Build back-adjusted bars from raw closes and validated events."""
    cash = _cash_by_date(cash_events, share_events)
    result = []
    previous = None
    all_applied = (*share_events, *cash_events)
    for row in bars:
        factor = _price_factor(row.session_date, share_events)
        adjusted = row.close / factor if row.close is not None else None
        entitlement, missing = cash.get(row.session_date, (None, ()))
        price_return, total_return = _bar_returns(
            adjusted, previous, entitlement, missing
        )
        applied = tuple(
            event.corporate_action_id
            for event in all_applied
            if event.ex_date == row.session_date
        )
        result.append(
            AdjustedBar(
                row.session_date,
                row.close,
                adjusted,
                price_return,
                total_return,
                entitlement,
                tuple(sorted(applied)),
                missing,
            )
        )
        previous = adjusted
    return tuple(result)


def _unapplied_terms(
    events: Sequence[ActionEvent],
) -> tuple[tuple[str, str], ...]:
    """Return (event id, reason) for every ledgered but unused event."""
    terms: list[tuple[str, str]] = []
    for event in events:
        if event.action_type in UNSUPPORTED_TERMS_ACTIONS:
            reason = UNAPPLIED_UNSUPPORTED_TERMS
        elif event.action_type in STATUS_ACTIONS:
            reason = UNAPPLIED_STATUS_ONLY
        elif event.ex_date is None and event.action_type in APPLIED_ACTIONS:
            reason = UNAPPLIED_MISSING_EX_DATE
        else:
            continue
        terms.append((event.corporate_action_id, reason))
    return tuple(sorted(terms))


def _series_digest(result: AdjustedSeries) -> str:
    """Derive the digest of a derived dataset, excluding the digest itself."""
    payload = {
        "canonical_instrument_id": result.canonical_instrument_id,
        "adjustment_version": result.adjustment_version,
        "bars": [item._asdict() for item in result.bars],
        "event_ids": list(result.event_ids),
        "unapplied_event_ids": list(result.unapplied_event_ids),
        "unapplied_terms": [list(pair) for pair in result.unapplied_terms],
        "raw_series_digest": result.raw_series_digest,
        "ledger_digest": result.ledger_digest,
    }
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def adjust_series(
    raw_bars: Sequence[PriceBar],
    ledger: ActionLedger,
    adjustment_version: str,
    canonical_instrument_id: str,
    *,
    known_as_of: object = None,
) -> AdjustedSeries:
    """Derive one deterministic adjusted series for one event version.

    Without ``known_as_of`` events are selected by exact adjustment version
    (the latest corrected dataset). With an ISO8601 ``known_as_of`` the ledger
    is read point-in-time: the latest revision announced, observed and
    ingested by then is used for each action, regardless of version label.
    """
    errors = validate_series(raw_bars)
    if errors:
        raise ValueError("; ".join(errors))
    if known_as_of is None:
        events = tuple(
            sorted(
                ledger.select(canonical_instrument_id, adjustment_version),
                key=_event_sort_key,
            )
        )
        ids = [item.corporate_action_id for item in events]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate action in one adjustment version")
    else:
        events = ledger.as_known(canonical_instrument_id, known_as_of)

    share_events = [
        item
        for item in events
        if item.action_type in SHARE_ACTIONS and item.ex_date is not None
    ]
    cash_events = [
        item
        for item in events
        if item.action_type in CASH_ACTIONS and item.ex_date is not None
    ]
    terms = _unapplied_terms(events)
    result = AdjustedSeries(
        canonical_instrument_id,
        adjustment_version,
        _build_adjusted_bars(tuple(raw_bars), share_events, cash_events),
        tuple(item.corporate_action_id for item in events),
        tuple(sorted(event_id for event_id, _ in terms)),
        raw_series_digest(raw_bars),
        ledger_digest(events),
        "",
        terms,
    )
    return result._replace(digest=_series_digest(result))
