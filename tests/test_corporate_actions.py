"""Corporate action ledger tests using SYNTHETIC events only.

No real announcement, ratio, dividend or price is represented here. Every
value below is a small synthetic fixture created by this test module.
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
DAY_1 = "2024-01-02"
DAY_2 = "2024-01-03"


def _event(**overrides: object) -> ca.ActionEvent:
    """Build one SYNTHETIC split event with complete provenance."""
    values: dict[str, object] = {
        "corporate_action_id": "SYNTHETIC-ACTION-1",
        "canonical_instrument_id": ID,
        "action_type": ca.SPLIT,
        "announcement_timestamp": "2023-12-01T04:00:00Z",
        "record_date": "2024-01-02",
        "ex_date": DAY_2,
        "effective_date": DAY_2,
        "payment_date": None,
        "ratio": 2.0,
        "cash_amount": None,
        "currency": None,
        "successor_instrument_id": None,
        "source": SOURCE,
        "source_digest": DIGEST,
        "observed_at": "2023-12-01T05:00:00Z",
        "revision": 1,
        "adjustment_version": VERSION,
    }
    values.update(overrides)
    return ca.ActionEvent(**values)


def _dividend(cash: float = 5.0, **overrides: object) -> ca.ActionEvent:
    """Build one SYNTHETIC dividend event."""
    return _event(
        corporate_action_id="SYNTHETIC-DIVIDEND",
        action_type=ca.DIVIDEND,
        ratio=None,
        cash_amount=cash,
        currency="INR",
        payment_date="2024-01-10",
        **overrides,
    )


def _bars() -> tuple[ca.PriceBar, ...]:
    """Return a small SYNTHETIC raw price series."""
    return (
        ca.PriceBar(DAY_1, 100.0),
        ca.PriceBar(DAY_2, 120.0),
    )


def test_all_action_types_validate() -> None:
    """Each supported action has a valid synthetic representation."""
    ratio_cases = (ca.SPLIT, ca.BONUS, ca.RIGHTS, ca.CONSOLIDATION)
    events = [_event(action_type=item, ratio=2.0) for item in ratio_cases]
    events.append(
        _event(action_type=ca.MERGER, successor_instrument_id="SYNTHETIC-S")
    )
    events.append(
        _event(action_type=ca.DEMERGER, successor_instrument_id="SYNTHETIC-S")
    )
    events.append(_dividend())
    events.append(
        _event(
            action_type=ca.DELISTING,
            ex_date=None,
            ratio=None,
            effective_date=None,
            record_date=None,
        )
    )
    events.append(
        _event(
            action_type=ca.RELISTING,
            ex_date=None,
            ratio=None,
            effective_date=None,
            record_date=None,
        )
    )
    assert len(events) == 9
    assert all(ca.validate_event(item) == () for item in events)
    distinct = tuple(
        item._replace(corporate_action_id=f"SYNTHETIC-ACTION-{index}")
        for index, item in enumerate(events, start=1)
    )
    assert ca.validate_ledger(distinct) == ()
    assert ca.ActionLedger(distinct).digest == ca.ledger_digest(distinct)


def test_split_back_adjustment_is_deterministic() -> None:
    """A synthetic 1:2 split halves older prices in the derived series."""
    ledger = ca.ActionLedger((_event(),))
    first = ca.adjust_series(_bars(), ledger, VERSION, ID)
    second = ca.adjust_series(_bars(), ledger, VERSION, ID)
    assert first == second
    assert first.digest == second.digest
    assert first.bars[0].adjusted_close == pytest.approx(50.0)
    assert first.bars[1].adjusted_close == pytest.approx(120.0)
    assert first.bars[1].price_return == pytest.approx(1.4)
    assert first.event_ids == ("SYNTHETIC-ACTION-1",)
    assert first.raw_series_digest == ca.raw_series_digest(_bars())


def test_dividend_total_return_differs_from_price_return() -> None:
    """Cash entitlement belongs only in total return."""
    ledger = ca.ActionLedger((_dividend(),))
    series = ca.adjust_series(
        (
            ca.PriceBar(DAY_1, 100.0),
            ca.PriceBar(DAY_2, 95.0),
        ),
        ledger,
        VERSION,
        ID,
    )
    ex_day = series.bars[1]
    assert ex_day.adjusted_close == 95.0
    assert ex_day.price_return == pytest.approx(-0.05)
    assert ex_day.cash_entitlement == 5.0
    assert ex_day.total_return == pytest.approx(0.0)
    assert series.bars[0].total_return is None
    assert series.bars[0].cash_entitlement is None


def test_revision_creates_new_dataset_version() -> None:
    """A corrected event is appended; prior ledger and outputs stay fixed."""
    original = ca.ActionLedger((_dividend(5.0),))
    first = ca.adjust_series(
        (
            ca.PriceBar(DAY_1, 100.0),
            ca.PriceBar(DAY_2, 95.0),
        ),
        original,
        VERSION,
        ID,
    )
    revised = ca.revise_event(
        original,
        "SYNTHETIC-DIVIDEND",
        adjustment_version="synthetic-v2",
        cash_amount=2.0,
    )
    old_again = ca.adjust_series(
        (
            ca.PriceBar(DAY_1, 100.0),
            ca.PriceBar(DAY_2, 95.0),
        ),
        revised,
        VERSION,
        ID,
    )
    corrected = ca.adjust_series(
        (
            ca.PriceBar(DAY_1, 100.0),
            ca.PriceBar(DAY_2, 95.0),
        ),
        revised,
        "synthetic-v2",
        ID,
    )
    assert len(original.events) == 1
    assert len(revised.events) == 2
    assert original.digest != revised.digest
    assert old_again == first
    assert first.digest == old_again.digest
    assert corrected.bars[1].cash_entitlement == 2.0
    assert corrected.bars[1].total_return == pytest.approx(-0.03)
    assert revised.events[1].revision == 2
    assert revised.events[0].cash_amount == 5.0


def test_append_only_ledger_rejects_duplicate_revisions() -> None:
    """Append returns a new ledger and never mutates the original."""
    original = ca.ActionLedger((_event(),))
    appended = original.append(_event(corporate_action_id="SYNTHETIC-2"))
    assert len(original.events) == 1
    assert len(appended.events) == 2
    with pytest.raises(ValueError, match="duplicate event revision"):
        ca.ActionLedger((_event(), _event()))
    with pytest.raises(ValueError, match="unknown corporate_action_id"):
        ca.revise_event(original, "SYNTHETIC-UNKNOWN")


def test_missingness_and_invalid_events_fail_closed() -> None:
    """Unknown closes, undated events and malformed dividends are explicit."""
    bars = (ca.PriceBar(DAY_1, None), ca.PriceBar(DAY_2, 120.0))
    undated = _event(ex_date=None)
    ledger = ca.ActionLedger((undated,))
    series = ca.adjust_series(bars, ledger, VERSION, ID)
    assert series.bars[0].adjusted_close is None
    assert series.bars[0].price_return is None
    assert series.bars[1].price_return is None
    assert series.unapplied_event_ids == ("SYNTHETIC-ACTION-1",)

    invalid = _dividend(cash=None)
    assert any(
        "cash_amount required" in item for item in ca.validate_event(invalid)
    )
    with pytest.raises(ValueError, match="cash_amount required"):
        ca.ActionLedger((invalid,))
    with pytest.raises(ValueError, match="ratio and cash events collide"):
        ca.adjust_series(
            _bars(),
            ca.ActionLedger((_event(), _dividend())),
            VERSION,
            ID,
        )
