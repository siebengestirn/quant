"""Pytest coverage for Python research diagnostics (SIMULATED data only)."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_research", ROOT / "python" / "research.py"
)
assert SPEC is not None and SPEC.loader is not None
research = importlib.util.module_from_spec(SPEC)
sys.modules["quant_research"] = research
SPEC.loader.exec_module(research)


def test_synthetic_history_is_deterministic():
    first = research.synthetic_history()
    second = research.synthetic_history()
    assert list(first) == list(second)
    assert first["AAA"][-1]["close"] == second["AAA"][-1]["close"]


def test_features_do_not_read_future_rows():
    rows = research.synthetic_history()["AAA"]
    cut = 100
    before = research.features(rows[: cut + 1], cut)
    extended = list(rows)
    extended[cut + 1]["close"] *= 50.0
    after = research.features(extended[: cut + 2], cut)
    assert before == after


def test_purged_walk_forward_returns_folds():
    history = research.synthetic_history()
    stats = research.walk_forward_ic(history, research.score_momentum)
    assert stats["folds"] >= 1
    assert stats["observations"] >= stats["folds"]
    assert -1.0 <= stats["mean_ic"] <= 1.0
    assert len(stats["fold_ics"]) == stats["folds"]


def test_statistical_helpers():
    ci = research.bootstrap_ci([0.01, -0.01, 0.02, -0.005], 100)
    assert ci[0] <= ci[1] <= ci[2]
    assert research.deflated(2.0, 8, 100) < 2.0
    assert math.isfinite(research.spearman([1, 2, 3, 4], [4, 3, 2, 1]))
    assert research.probability_backtest_overfitting([0.1, -0.1]) == 0.5


def test_report_labels_are_explicit():
    history = research.synthetic_history()
    report = research.analyze(history, ROOT)
    assert report["labels"]["authoritative_pnl"] == "SIMULATED"
    assert report["labels"]["live_trading"] == "NOT IMPLEMENTED (locked)"
    assert len(report["walk_forward"]) == len(research.STRATEGIES)


def test_cross_language_feature_fixture():
    """C++ ComputeFeatures and Python features share one canonical fixture."""
    import math

    rows = []
    for i in range(25):
        close = 100.0 + 0.7 * i + 2.0 * math.sin(i)
        rows.append(
            {
                "date": f"D{i:03d}",
                "open": close,
                "high": close + 3.0,
                "low": close - 2.0,
                "close": close,
                "volume": 1000.0 + 37.0 * i,
            }
        )
    result = research.features(rows, 24)
    expected = {
        "ret5": 0.012227921299313493,
        "ret20": 0.13528419355169152,
        "vol20": 0.012173429881693497,
        "volume_surprise": 0.22876667751383017,
        "vwap_dev": 0.03664852280982922,
        "trend": 1.0,
        "breakout": 0.5569266238518116,
    }
    for key, value in expected.items():
        assert abs(result[key] - value) < 1e-12, key
    assert result["valid"] is True
    assert result["validity_reason"] == "READY"


def test_warmup_and_final_availability_are_explicit():
    rows = research.synthetic_history()["AAA"]
    early = research.features(rows[:10], 9)
    assert early["valid"] is False
    assert early["validity_reason"] == "INSUFFICIENT_HISTORY"
    history = research.feature_history(rows)
    assert history[-1]["availability_index"] is None
    assert history[0]["availability_index"] == 1
