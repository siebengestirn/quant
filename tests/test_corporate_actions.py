"""Corporate action ledger tests using SYNTHETIC events only.

No real announcement, ratio, dividend or price is represented here. Every
value below is a small synthetic fixture created by this test module. Golden
returns are calculated independently from the formulas documented in
``python/corporate_actions.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_corporate_actions", ROOT / "python" / "corporate_actions.py"
)
assert SPEC is not None and SPEC.loader is not None
ca = importlib.util.module_from_spec(SPEC)
sys.modules["quant_corporate_actions"] = ca
SPEC.loader.exec_module(ca)

ID = "SYNTHETIC-CID"
SOURCE = "SYNTHETIC-CORPORATE-ACTION-FIXTURE"
DIGEST = "c" * 64
VERSION = "synthetic-v1"
JAN1 = "2024-01-01"
JAN2 = "2024-01-02"
JAN3 = "2024-01-03"
JAN4 = "2024-01-04"
JAN5 = "2024-01-05"
JAN10 = "2024-01-10"
ANNOUNCED = "2023-12-01T04:00:00Z"
OBSERVED = "2023-12-01T05:00:00Z"
INGESTED = "2023-12-01T06:00:00Z"


def _event(**overrides: object) -> ca.ActionEvent:
    """Build one SYNTHETIC split event with complete provenance."""
    values: dict[str, object] = {
        "corporate_action_id": "SYNTHETIC-ACTION-1",
        "canonical_instrument_id": ID,
        "action_type": ca.SPLIT,
        "announcement_timestamp": ANNOUNCED,
        "record_date": JAN2,
        "ex_date": JAN3,
        "effective_date": JAN3,
        "payment_date": None,
        "ratio": 2.0,
        "cash_amount": None,
        "currency": None,
        "successor_instrument_id": None,
        "source": SOURCE,
        "source_digest": DIGEST,
        "observed_at": OBSERVED,
        "revision": 1,
        "adjustment_version": VERSION,
        "ingested_at": INGESTED,
    }
    values.update(overrides)
    return ca.ActionEvent(**values)


def _dividend(cash: float = 5.0, **overrides: object) -> ca.ActionEvent:
    """Build one SYNTHETIC cash dividend event."""
    values: dict[str, object] = {
        "corporate_action_id": "SYNTHETIC-DIVIDEND",
        "action_type": ca.DIVIDEND,
        "ratio": None,
        "cash_amount": cash,
        "currency": "INR",
        "payment_date": JAN10,
    }
    values.update(overrides)
    return _event(**values)


def _bars() -> tuple[ca.PriceBar, ...]:
    """Return a small SYNTHETIC raw price series."""
    return (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 120.0),
        ca.PriceBar(JAN3, 120.0),
    )


def _series(
    bars: tuple[ca.PriceBar, ...],
    events: tuple[ca.ActionEvent, ...],
    version: str = VERSION,
    **kwargs: object,
) -> ca.AdjustedSeries:
    """Derive a series from a SYNTHETIC ledger."""
    return ca.adjust_series(
        bars, ca.ActionLedger(events), version, ID, **kwargs
    )


@pytest.mark.parametrize("action_type", [ca.DIVIDEND, ca.CASH_DIVIDEND])
def test_dividend_then_split_uses_one_share_basis(action_type: str) -> None:
    """Reproduce PR #77: 5 cash on 100 must be zero return, not +5%."""
    bars = (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 95.0),
        ca.PriceBar(JAN3, 47.5),
    )
    dividend = _dividend(
        cash=5.0, ex_date=JAN2, effective_date=None, action_type=action_type
    )
    split = _event(ex_date=JAN3)
    series = _series(bars, (dividend, split))

    # Future 2:1 split: 100 -> 50, 95 -> 47.5, cash 5/2 -> 2.5.
    # Golden: price = 47.5/50 - 1 = -0.05; total = -0.05 + 2.5/50 = 0.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(50.0),
        pytest.approx(47.5),
        pytest.approx(47.5),
    ]
    assert series.bars[1].price_return == pytest.approx(-0.05)
    assert series.bars[1].cash_entitlement == pytest.approx(2.5)
    assert series.bars[1].total_return == pytest.approx(0.0)
    assert series.bars[1].total_return != pytest.approx(0.05)
    assert series.bars[2].price_return == pytest.approx(0.0)
    assert series.bars[1].applied_event_ids == ("SYNTHETIC-DIVIDEND",)
    assert series.bars[2].applied_event_ids == ("SYNTHETIC-ACTION-1",)


def test_split_then_dividend_keeps_cash_on_post_split_basis() -> None:
    """A later dividend needs no scaling once prices are post-split."""
    bars = (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 50.0),
        ca.PriceBar(JAN3, 47.5),
    )
    split = _event(ex_date=JAN2, record_date=JAN1)
    dividend = _dividend(cash=2.5, ex_date=JAN3)
    series = _series(bars, (split, dividend))

    # Golden: adjusted 100/2 = 50, 50, 47.5; cash 2.5; total 0.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(50.0),
        pytest.approx(50.0),
        pytest.approx(47.5),
    ]
    assert series.bars[2].price_return == pytest.approx(-0.05)
    assert series.bars[2].cash_entitlement == pytest.approx(2.5)
    assert series.bars[2].total_return == pytest.approx(0.0)


def test_dividend_before_two_splits_scales_cash_fourfold() -> None:
    """Two future 2:1 splits scale both the price and cash by four."""
    bars = (
        ca.PriceBar(JAN1, 80.0),
        ca.PriceBar(JAN2, 76.0),
        ca.PriceBar(JAN3, 38.0),
        ca.PriceBar(JAN4, 19.0),
    )
    dividend = _dividend(cash=4.0, ex_date=JAN2, effective_date=None)
    first = _event(corporate_action_id="SYNTHETIC-SPLIT-1", ex_date=JAN3)
    second = _event(
        corporate_action_id="SYNTHETIC-SPLIT-2",
        ex_date=JAN4,
        effective_date=JAN4,
    )
    series = _series(bars, (dividend, first, second))

    # Golden: adjusted 80/4=20, 76/4=19, 38/2=19, 19; cash 4/4=1.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(20.0),
        pytest.approx(19.0),
        pytest.approx(19.0),
        pytest.approx(19.0),
    ]
    assert series.bars[1].price_return == pytest.approx(-0.05)
    assert series.bars[1].cash_entitlement == pytest.approx(1.0)
    assert series.bars[1].total_return == pytest.approx(0.0)
    assert series.bars[2].price_return == pytest.approx(0.0)
    assert series.bars[3].price_return == pytest.approx(0.0)


def test_bonus_then_dividend_uses_incremental_bonus_ratio() -> None:
    """A 1:1 bonus gives two shares per held share, factor 1 + ratio."""
    bars = (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 50.0),
        ca.PriceBar(JAN3, 49.0),
    )
    bonus = _event(
        corporate_action_id="SYNTHETIC-BONUS",
        action_type=ca.BONUS,
        ratio=1.0,
        ex_date=JAN2,
    )
    dividend = _dividend(cash=1.0, ex_date=JAN3)
    series = _series(bars, (bonus, dividend))

    # Golden: factor 2; adjusted 50, 50, 49; cash 1; total 0.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(50.0),
        pytest.approx(50.0),
        pytest.approx(49.0),
    ]
    assert bonus.ratio_semantics == ca.BONUS_PER_HELD
    assert series.bars[2].price_return == pytest.approx(-0.02)
    assert series.bars[2].cash_entitlement == pytest.approx(1.0)
    assert series.bars[2].total_return == pytest.approx(0.0)


def test_consolidation_back_adjustment_and_cash_basis() -> None:
    """One-for-two consolidation uses old_shares_per_new_share = 2."""
    bars = (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 96.0),
        ca.PriceBar(JAN3, 200.0),
    )
    dividend = _dividend(cash=4.0, ex_date=JAN2, effective_date=None)
    consolidation = _event(
        corporate_action_id="SYNTHETIC-CONSOLIDATION",
        action_type=ca.CONSOLIDATION,
        ratio=2.0,
        ex_date=JAN3,
    )
    series = _series(bars, (dividend, consolidation))

    # Golden: factor 1/2; adjusted 200, 192, 200; cash 4/(1/2) = 8.
    # Total = (192/200 - 1) + 8/200 = -0.04 + 0.04 = 0.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(200.0),
        pytest.approx(192.0),
        pytest.approx(200.0),
    ]
    assert series.bars[1].price_return == pytest.approx(-0.04)
    assert series.bars[1].cash_entitlement == pytest.approx(8.0)
    assert series.bars[1].total_return == pytest.approx(0.0)
    assert consolidation.ratio_semantics == ca.OLD_PER_NEW


def test_split_and_dividend_on_same_ex_date_share_basis() -> None:
    """Same-day events use the previous bar's basis for cash."""
    bars = (
        ca.PriceBar(JAN1, 80.0),
        ca.PriceBar(JAN2, 40.0),
        ca.PriceBar(JAN3, 40.0),
    )
    split = _event(ex_date=JAN2)
    dividend = _dividend(cash=4.0, ex_date=JAN2, effective_date=None)
    series = _series(bars, (split, dividend))

    # Golden: adjusted 40, 40, 40; cash 4/2=2; total = 0 + 2/40 = 0.05.
    assert [row.adjusted_close for row in series.bars] == [
        pytest.approx(40.0),
        pytest.approx(40.0),
        pytest.approx(40.0),
    ]
    assert series.bars[1].price_return == pytest.approx(0.0)
    assert series.bars[1].cash_entitlement == pytest.approx(2.0)
    assert series.bars[1].total_return == pytest.approx(0.05)
    assert series.bars[1].applied_event_ids == (
        "SYNTHETIC-ACTION-1",
        "SYNTHETIC-DIVIDEND",
    )


def test_ratio_semantics_are_explicit_and_validated() -> None:
    """Each action type has one declared ratio meaning and its own formula."""
    assert ca.RATIO_SEMANTICS[ca.SPLIT] == ca.NEW_PER_OLD
    assert ca.RATIO_SEMANTICS[ca.BONUS] == ca.BONUS_PER_HELD
    assert ca.RATIO_SEMANTICS[ca.CONSOLIDATION] == ca.OLD_PER_NEW
    assert ca.RATIO_SEMANTICS[ca.DIVIDEND] == ca.CASH_PER_SHARE
    assert ca.RATIO_SEMANTICS[ca.CASH_DIVIDEND] == ca.CASH_PER_SHARE
    assert _event().ratio_semantics == ca.NEW_PER_OLD
    assert _dividend().ratio_semantics == ca.CASH_PER_SHARE

    # Independent golden factors: 2:1 split 2, 1:1 bonus 2, 1-for-2 0.5.
    assert ca.share_factor(_event(ratio=2.0)) == pytest.approx(2.0)
    assert ca.share_factor(_event(action_type=ca.BONUS, ratio=1.0)) == (
        pytest.approx(2.0)
    )
    assert ca.share_factor(
        _event(action_type=ca.CONSOLIDATION, ratio=2.0)
    ) == pytest.approx(0.5)

    errors = ca.validate_event(_event(ratio=1.0))
    assert "split ratio must exceed one" in errors
    errors = ca.validate_event(_event(action_type=ca.CONSOLIDATION, ratio=1.0))
    assert "consolidation ratio must exceed one" in errors
    assert "cash_amount not supported for share action" in ca.validate_event(
        _event(cash_amount=1.0)
    )
    assert "ratio not supported for dividend" in ca.validate_event(
        _dividend(ratio=2.0)
    )
    assert "successor_instrument_id required" in ca.validate_event(
        _event(action_type=ca.MERGER, ratio=3.0)
    )


def test_unsupported_terms_are_ledgered_but_not_applied() -> None:
    """Rights, merger and demerger terms never become price multipliers."""
    bars = (
        ca.PriceBar(JAN1, 100.0),
        ca.PriceBar(JAN2, 50.0),
        ca.PriceBar(JAN3, 49.0),
    )
    rights = _event(
        corporate_action_id="SYNTHETIC-RIGHTS",
        action_type=ca.RIGHTS,
        ratio=2.0,
        ex_date=JAN2,
    )
    merger = _event(
        corporate_action_id="SYNTHETIC-MERGER",
        action_type=ca.MERGER,
        ratio=3.0,
        ex_date=JAN3,
        successor_instrument_id="SYNTHETIC-SUCCESSOR",
    )
    demerger = _event(
        corporate_action_id="SYNTHETIC-DEMERGER",
        action_type=ca.DEMERGER,
        ratio=4.0,
        ex_date=JAN3,
        successor_instrument_id="SYNTHETIC-SUCCESSOR",
    )
    assert ca.validate_event(rights) == ()
    assert ca.validate_event(merger) == ()
    assert ca.validate_event(demerger) == ()
    series = _series(bars, (rights, merger, demerger))

    assert [row.adjusted_close for row in series.bars] == [100.0, 50.0, 49.0]
    assert series.bars[1].price_return == pytest.approx(-0.5)
    assert all(row.applied_event_ids == () for row in series.bars)
    assert series.unapplied_terms == (
        ("SYNTHETIC-DEMERGER", ca.UNAPPLIED_UNSUPPORTED_TERMS),
        ("SYNTHETIC-MERGER", ca.UNAPPLIED_UNSUPPORTED_TERMS),
        ("SYNTHETIC-RIGHTS", ca.UNAPPLIED_UNSUPPORTED_TERMS),
    )
    assert series.unapplied_event_ids == (
        "SYNTHETIC-DEMERGER",
        "SYNTHETIC-MERGER",
        "SYNTHETIC-RIGHTS",
    )
    assert set(series.event_ids) == set(series.unapplied_event_ids)


def test_known_as_of_distinguishes_latest_from_historically_knowable() -> None:
    """A later-ingested correction changes the as-known dataset only."""
    bars = (ca.PriceBar(JAN1, 100.0), ca.PriceBar(JAN2, 95.0))
    original = _dividend(cash=5.0, ex_date=JAN2, effective_date=None)
    original_ledger = ca.ActionLedger((original,))
    first = ca.adjust_series(bars, original_ledger, VERSION, ID)
    revised = ca.revise_event(
        original_ledger,
        "SYNTHETIC-DIVIDEND",
        adjustment_version="synthetic-v2",
        cash_amount=2.0,
        ingested_at="2024-01-05T06:00:00Z",
    )
    latest = ca.adjust_series(bars, revised, "synthetic-v2", ID)
    historical = ca.adjust_series(
        bars, revised, "synthetic-v2", ID, known_as_of="2024-01-03T00:00:00Z"
    )

    assert latest.bars[1].cash_entitlement == pytest.approx(2.0)
    assert latest.bars[1].total_return == pytest.approx(-0.03)
    assert historical.bars[1].cash_entitlement == pytest.approx(5.0)
    assert historical.bars[1].total_return == pytest.approx(0.0)
    assert latest.digest != historical.digest
    assert historical.event_ids == ("SYNTHETIC-DIVIDEND",)
    assert historical.adjustment_version == "synthetic-v2"
    assert ca.adjust_series(bars, revised, VERSION, ID) == first
    assert first.digest == ca.adjust_series(bars, revised, VERSION, ID).digest
    assert historical == ca.adjust_series(
        bars, revised, "synthetic-v2", ID, known_as_of=JAN3
    )


def test_knowledge_gates_use_announcement_observation_and_ingestion() -> None:
    """Every knowledge timestamp must be at or before the cutoff."""
    bars = (ca.PriceBar(JAN1, 100.0), ca.PriceBar(JAN2, 95.0))
    late_ingest = _event(
        corporate_action_id="SYNTHETIC-LATE-INGEST",
        ingested_at="2024-01-05T06:00:00Z",
    )
    ledger = ca.ActionLedger((late_ingest,))
    before = ca.adjust_series(
        bars, ledger, VERSION, ID, known_as_of="2024-01-04T00:00:00Z"
    )
    after = ca.adjust_series(
        bars, ledger, VERSION, ID, known_as_of="2024-01-06T00:00:00Z"
    )
    assert before.event_ids == ()
    assert before.bars[0].adjusted_close == 100.0
    assert after.event_ids == ("SYNTHETIC-LATE-INGEST",)
    assert after.bars[0].adjusted_close == pytest.approx(50.0)

    late_announcement = _event(
        corporate_action_id="SYNTHETIC-LATE-ANNOUNCEMENT",
        announcement_timestamp="2024-01-06T04:00:00Z",
        observed_at="2024-01-06T05:00:00Z",
        ingested_at="2024-01-06T06:00:00Z",
    )
    unseen = ca.adjust_series(
        bars,
        ca.ActionLedger((late_announcement,)),
        VERSION,
        ID,
        known_as_of="2024-01-05T00:00:00Z",
    )
    assert unseen.event_ids == ()
    with pytest.raises(ValueError, match="invalid known_as_of"):
        ca.adjust_series(
            bars, ledger, VERSION, ID, known_as_of="not-a-timestamp"
        )


def test_dates_are_validated_per_action_not_by_one_chain() -> None:
    """T+1 conventions and action-specific missingness are supported."""
    t_plus_one = _event(record_date=JAN4, ex_date=JAN3, effective_date=JAN3)
    assert ca.validate_event(t_plus_one) == ()

    # Ex before record and effective before ex are both outside the old
    # universal chain, yet valid for a cash dividend.
    unusual_dividend = _dividend(
        record_date=JAN4, ex_date=JAN3, effective_date=JAN1
    )
    assert ca.validate_event(unusual_dividend) == ()
    assert ca.validate_event(_dividend(ex_date=None, effective_date=None)) == ()
    missing_rights = _event(
        action_type=ca.RIGHTS,
        ratio=None,
        ex_date=None,
        record_date=None,
        effective_date=None,
    )
    assert ca.validate_event(missing_rights) == ()

    assert "payment_date must not precede ex_date" in ca.validate_event(
        _dividend(ex_date=JAN3, payment_date=JAN2)
    )
    assert "payment_date not supported for share action" in ca.validate_event(
        _event(payment_date=JAN10)
    )
    assert "effective_date required for share action" in ca.validate_event(
        _event(effective_date=None)
    )
    assert "effective_date required for status action" in ca.validate_event(
        _event(
            action_type=ca.DELISTING,
            ex_date=None,
            effective_date=None,
            ratio=None,
        )
    )
    assert "invalid ex_date" in ca.validate_event(
        _dividend(ex_date="2024-02-30")
    )


def test_append_only_versions_and_deterministic_rebuild() -> None:
    """Corrections append new versions; old datasets rebuild identically."""
    original = ca.ActionLedger((_event(),))
    appended = original.append(_event(corporate_action_id="SYNTHETIC-2"))
    assert len(original.events) == 1
    assert len(appended.events) == 2
    assert original.digest != appended.digest

    revised = ca.revise_event(
        original,
        "SYNTHETIC-ACTION-1",
        adjustment_version="synthetic-v2",
        ratio=3.0,
    )
    assert len(revised.events) == 2
    assert revised.events[0] == original.events[0]
    assert revised.events[1].revision == 2
    assert revised.versions(ID) == (VERSION, "synthetic-v2")
    first = ca.adjust_series(_bars(), original, VERSION, ID)
    old_again = ca.adjust_series(_bars(), revised, VERSION, ID)
    corrected = ca.adjust_series(_bars(), revised, "synthetic-v2", ID)
    assert first == old_again
    assert first.digest == old_again.digest
    assert corrected.bars[0].adjusted_close == pytest.approx(100.0 / 3)
    with pytest.raises(ValueError, match="duplicate event revision"):
        ca.ActionLedger((_event(), _event()))
    with pytest.raises(ValueError, match="unknown corporate_action_id"):
        ca.revise_event(original, "SYNTHETIC-UNKNOWN")
    with pytest.raises(ValueError, match="unrevisable fields: revision"):
        ca.revise_event(original, "SYNTHETIC-ACTION-1", revision=3)


def test_all_action_types_and_mapping_round_trip_validate() -> None:
    """Every supported action has a valid, provenanced synthetic form."""
    events = [
        _event(action_type=item, ratio=2.0)
        for item in (ca.SPLIT, ca.BONUS, ca.CONSOLIDATION)
    ]
    events.append(_event(action_type=ca.RIGHTS, ratio=2.0))
    events.extend(
        _event(
            action_type=item,
            ratio=2.0,
            successor_instrument_id="SYNTHETIC-SUCCESSOR",
        )
        for item in (ca.MERGER, ca.DEMERGER)
    )
    events.append(_dividend())
    events.append(_dividend(action_type=ca.CASH_DIVIDEND))
    events.append(
        _event(
            action_type=ca.DELISTING,
            ex_date=None,
            ratio=None,
            payment_date=None,
        )
    )
    events.append(
        _event(
            action_type=ca.RELISTING,
            ex_date=None,
            ratio=None,
            payment_date=None,
        )
    )
    distinct = tuple(
        item._replace(corporate_action_id=f"SYNTHETIC-ACTION-{index}")
        for index, item in enumerate(events, start=1)
    )
    assert len(distinct) == 10
    assert all(ca.validate_event(item) == () for item in distinct)
    assert ca.validate_ledger(distinct) == ()
    assert ca.ActionLedger(distinct).digest == ca.ledger_digest(distinct)

    data = ca.event_payload(_dividend())
    assert data["ingested_at"] == INGESTED
    assert ca.event_from_mapping(data) == _dividend()
    assert ca.validate_event(ca.event_from_mapping(data)) == ()


def test_missingness_and_duplicate_versions_fail_closed() -> None:
    """Unknown closes, undated events and duplicate actions are explicit."""
    bars = (ca.PriceBar(JAN1, None), ca.PriceBar(JAN2, 120.0))
    undated = _event(ex_date=None)
    series = _series(bars, (undated,))
    assert series.bars[0].adjusted_close is None
    assert series.bars[0].price_return is None
    assert series.bars[1].price_return is None
    assert series.unapplied_terms == (
        ("SYNTHETIC-ACTION-1", ca.UNAPPLIED_MISSING_EX_DATE),
    )

    invalid = _dividend(cash=None)
    assert any(
        "cash_amount required" in item for item in ca.validate_event(invalid)
    )
    with pytest.raises(ValueError, match="cash_amount required"):
        ca.ActionLedger((invalid,))
    with pytest.raises(ValueError, match="duplicate action in one adjustment"):
        _series(
            _bars(),
            (_event(), _event(revision=2)),
        )
