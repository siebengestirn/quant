"""Event-sourced corporate actions with versioned derived series.

The raw price series, the append-only action ledger and the adjusted series
are three separate objects. Events are never rewritten: a correction is
appended with a higher revision and a new adjustment version, so an earlier
derived dataset can always be rebuilt exactly. No real corporate action is
embedded here; every event must be supplied with source provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import NamedTuple

try:
    from exchange_calendar import parse_instant
except ModuleNotFoundError:  # pragma: no cover - standalone spec loading
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exchange_calendar import (  # pylint: disable=import-outside-toplevel
        parse_instant,
    )

SCHEMA_VERSION = "corporate-actions-v1"

SPLIT = "SPLIT"
BONUS = "BONUS"
DIVIDEND = "DIVIDEND"
RIGHTS = "RIGHTS"
MERGER = "MERGER"
DEMERGER = "DEMERGER"
CONSOLIDATION = "CONSOLIDATION"
DELISTING = "DELISTING"
RELISTING = "RELISTING"

ACTION_TYPES = (
    BONUS,
    CONSOLIDATION,
    DELISTING,
    DEMERGER,
    DIVIDEND,
    MERGER,
    RELISTING,
    RIGHTS,
    SPLIT,
)
RATIO_ACTIONS = frozenset(
    {
        SPLIT,
        BONUS,
        RIGHTS,
        MERGER,
        DEMERGER,
        CONSOLIDATION,
    }
)
CASH_ACTIONS = frozenset({DIVIDEND})
STATUS_ACTIONS = frozenset({DELISTING, RELISTING})
SUCCESSOR_ACTIONS = frozenset({MERGER, DEMERGER})

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_VERSION_RE = re.compile(r"^(.*)-v(\d+)$")

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

    ledger_digest binds only the events selected for this adjustment
    version, so appending a correction never changes an older dataset.
    """

    canonical_instrument_id: str
    adjustment_version: str
    bars: tuple[AdjustedBar, ...]
    event_ids: tuple[str, ...]
    unapplied_event_ids: tuple[str, ...]
    raw_series_digest: str
    ledger_digest: str
    digest: str


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    """Return a stripped string or None for an explicitly absent value."""
    return _text(value) or None


def _is_date(value: object) -> bool:
    """Return True for a real ISO calendar date, not a merely shaped one."""
    text = _text(value)
    if not text:
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


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
    )


def _validate_dates(event: ActionEvent, errors: list[str]) -> None:
    """Check optional dates and their chronological relationships."""
    dates = {
        "record_date": event.record_date,
        "ex_date": event.ex_date,
        "effective_date": event.effective_date,
        "payment_date": event.payment_date,
    }
    for name, value in dates.items():
        if value is not None and not _is_date(value):
            errors.append(f"invalid {name}")
    ordered = ("record_date", "ex_date", "effective_date", "payment_date")
    present = [dates[name] for name in ordered if dates[name] is not None]
    if present != sorted(present):
        errors.append("dates are not chronologically ordered")


def _validate_terms(event: ActionEvent, errors: list[str]) -> None:
    """Check action-specific ratio and cash terms without inventing values."""
    if event.ratio is not None and (
        event.ratio <= 0 or not math.isfinite(event.ratio)
    ):
        errors.append("ratio must be positive")
    if event.cash_amount is not None and (
        event.cash_amount < 0 or not math.isfinite(event.cash_amount)
    ):
        errors.append("cash_amount must be non-negative")
    if event.action_type in RATIO_ACTIONS:
        if event.ratio is None:
            errors.append("ratio required for price action")
        if event.cash_amount is not None:
            errors.append("cash_amount not supported for price action")
    if event.action_type in CASH_ACTIONS:
        if event.cash_amount is None:
            errors.append("cash_amount required for dividend")
        if event.ratio is not None:
            errors.append("ratio not supported for dividend")
        if event.currency is None or not _CURRENCY_RE.fullmatch(event.currency):
            errors.append("invalid dividend currency")
    if event.action_type in STATUS_ACTIONS and (
        event.ratio is not None or event.cash_amount is not None
    ):
        errors.append("status action cannot carry ratio or cash")
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


def validate_event(event: ActionEvent) -> tuple[str, ...]:
    """Return all contract violations for one corporate action event."""
    errors: list[str] = []
    for name in ("corporate_action_id", "canonical_instrument_id"):
        if not _ID_RE.fullmatch(_text(getattr(event, name))):
            errors.append(f"invalid {name}")
    if event.action_type not in ACTION_TYPES:
        errors.append("unsupported action_type")
    if parse_instant(event.announcement_timestamp) is None:
        errors.append("invalid announcement_timestamp")
    if parse_instant(event.observed_at) is None:
        errors.append("invalid observed_at")
    _validate_dates(event, errors)
    _validate_terms(event, errors)
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


def _event_sort_key(event: ActionEvent) -> tuple[str, str, int]:
    """Order events deterministically for adjustment."""
    return (event.ex_date or "", event.corporate_action_id, event.revision)


def _cash_by_date(
    events: Sequence[ActionEvent],
) -> dict[str, tuple[float | None, tuple[str, ...]]]:
    """Aggregate dividend cash entitlement per ex-date."""
    result: dict[str, tuple[float | None, tuple[str, ...]]] = {}
    for event in events:
        assert event.ex_date is not None
        total, missing = result.get(event.ex_date, (0.0, ()))
        if event.cash_amount is None:
            result[event.ex_date] = (
                None,
                missing + (event.corporate_action_id,),
            )
        elif total is not None:
            result[event.ex_date] = (total + event.cash_amount, missing)
    return result


def _factor_for(date: str, ratio_events: Sequence[ActionEvent]) -> float:
    """Return the cumulative back-adjustment factor after a date."""
    return math.prod(
        event.ratio
        for event in ratio_events
        if event.ex_date is not None
        and event.ex_date > date
        and event.ratio is not None
    )


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
    ratio_events: Sequence[ActionEvent],
    cash_events: Sequence[ActionEvent],
) -> tuple[AdjustedBar, ...]:
    """Build back-adjusted bars from raw closes and validated events."""
    cash = _cash_by_date(cash_events)
    result = []
    previous = None
    for row in bars:
        factor = _factor_for(row.session_date, ratio_events)
        adjusted = row.close / factor if row.close is not None else None
        entitlement, missing = cash.get(row.session_date, (None, ()))
        price_return, total_return = _bar_returns(
            adjusted, previous, entitlement, missing
        )
        applied = tuple(
            event.corporate_action_id
            for event in (*ratio_events, *cash_events)
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


def _series_digest(result: AdjustedSeries) -> str:
    """Derive the digest of a derived dataset, excluding the digest itself."""
    payload = {
        "canonical_instrument_id": result.canonical_instrument_id,
        "adjustment_version": result.adjustment_version,
        "bars": [item._asdict() for item in result.bars],
        "event_ids": list(result.event_ids),
        "unapplied_event_ids": list(result.unapplied_event_ids),
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
) -> AdjustedSeries:
    """Derive one deterministic adjusted series for one event version."""
    errors = validate_series(raw_bars)
    if errors:
        raise ValueError("; ".join(errors))
    events = tuple(
        sorted(
            ledger.select(canonical_instrument_id, adjustment_version),
            key=_event_sort_key,
        )
    )
    ids = [item.corporate_action_id for item in events]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate action in one adjustment version")
    dated = [item for item in events if item.ex_date is not None]
    ratio_events = [item for item in dated if item.action_type in RATIO_ACTIONS]
    cash_events = [item for item in dated if item.action_type in CASH_ACTIONS]
    ratio_dates = {item.ex_date for item in ratio_events}
    cash_dates = {item.ex_date for item in cash_events}
    if ratio_dates & cash_dates:
        raise ValueError("ratio and cash events collide on an ex-date")
    result = AdjustedSeries(
        canonical_instrument_id,
        adjustment_version,
        _build_adjusted_bars(tuple(raw_bars), ratio_events, cash_events),
        tuple(ids),
        tuple(
            sorted(
                item.corporate_action_id for item in events if item not in dated
            )
        ),
        raw_series_digest(raw_bars),
        ledger_digest(events),
        "",
    )
    return result._replace(digest=_series_digest(result))
