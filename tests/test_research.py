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
