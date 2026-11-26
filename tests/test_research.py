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


def test_forward_label_is_censored_not_zero():
    rows = [{"close": float(100 + i)} for i in range(5)]
    assert research.forward_label(rows, 4, 2) is None
    assert research.forward_label(rows, 2, 2) is not None
    assert research.forward_label(rows, 0, 0) is None


def test_daily_cross_sectional_ic_reports_validity():
    history = research.synthetic_history()
    result = research.daily_cross_sectional_ic(history, research.score_momentum)
    assert result["status"] == "CALCULATED"
    assert result["observations"] > 0
    assert len(result["daily_ics"]) == result["observations"]
    assert len(result["dates"]) == result["observations"]
    assert -1.0 <= result["mean_ic"] <= 1.0
    assert result["coverage"] >= 5


def test_cscv_pbo_is_a_probability():
    matrix = [
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
        [0.4, 0.5, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5],
    ]
    result = research.cscv_pbo(matrix)
    assert result["status"] == "CALCULATED"
    assert 0.0 <= result["pbo"] <= 1.0
    assert result["splits"] > 0
    assert research.cscv_pbo([[1.0, 2.0]])["status"] == "UNDEFINED"


def test_deflated_sharpe_accounts_for_trials():
    rng = __import__("random").Random(7)
    returns = [rng.gauss(0.0004, 0.01) for _ in range(120)]
    small = research.deflated_sharpe_ratio(returns, 2)
    large = research.deflated_sharpe_ratio(returns, 200)
    assert small["status"] == "CALCULATED"
    assert 0.0 <= large["dsr"] <= 1.0
    assert large["dsr"] <= small["dsr"]
    assert research.deflated_sharpe_ratio([1.0, 2.0], 3)["status"] == (
        "UNDEFINED"
    )


def test_experiment_registry_is_append_only(tmp_path):
    first = research.register_experiment(tmp_path, {"strategy": "x"})
    second = research.register_experiment(tmp_path, {"strategy": "y"})
    assert first != second
    lines = (
        (tmp_path / "data" / "assurance" / "experiments.jsonl")
        .read_text()
        .splitlines()
    )
    assert len(lines) == 2
    assert research.json.loads(lines[0])["id"] == first


def test_dataset_quality_reports_missing_and_malformed():
    rows = [
        {
            "date": "D1",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
        },
        {
            "date": "D2",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 0,
            "volume": 100,
        },
        {
            "date": "D2",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
        },
        {
            "date": "D3",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
        },
    ]
    result = research.dataset_quality(rows, ["D1", "D2", "D3", "D4"])
    assert result["status"] == "PARTIAL"
    assert result["missing_sessions"] == ["D4"]
    assert result["duplicate_count"] == 1
    assert result["invalid_bar_count"] == 1
    complete = research.dataset_quality(rows[:1], ["D1"])
    assert complete["status"] == "COMPLETE"


def test_point_in_time_universe_ignores_future_status():
    records = [
        {"symbol": "AAA", "eligible": True, "effective_from": "2020-01-01"},
        {"symbol": "BBB", "eligible": True, "effective_from": "2021-01-01"},
        {
            "symbol": "AAA",
            "eligible": False,
            "effective_from": "2022-01-01",
        },
    ]
    assert research.point_in_time_universe(records, "2020-06-01") == ["AAA"]
    assert research.point_in_time_universe(records, "2022-06-01") == ["BBB"]
