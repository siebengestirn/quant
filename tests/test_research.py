"""Pytest coverage for Python research diagnostics (SIMULATED data only)."""

from __future__ import annotations

import csv
import importlib.util
import json
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
    """Enforce the QUANT style contract."""
    first = research.synthetic_history()
    second = research.synthetic_history()
    assert list(first) == list(second)
    assert first["AAA"][-1]["close"] == second["AAA"][-1]["close"]


def test_features_do_not_read_future_rows():
    """Enforce the QUANT style contract."""
    rows = research.synthetic_history()["AAA"]
    cut = 100
    before = research.features(rows[: cut + 1], cut)
    extended = list(rows)
    extended[cut + 1]["close"] *= 50.0
    after = research.features(extended[: cut + 2], cut)
    assert before == after


def test_purged_walk_forward_returns_folds():
    """Enforce the QUANT style contract."""
    history = research.synthetic_history()
    stats = research.walk_forward_ic(history, research.score_momentum)
    assert stats["folds"] >= 1
    assert stats["observations"] >= stats["folds"]
    assert -1.0 <= stats["mean_ic"] <= 1.0
    assert len(stats["fold_ics"]) == stats["folds"]


def test_statistical_helpers():
    """Enforce the QUANT style contract."""
    ci = research.bootstrap_ci([0.01, -0.01, 0.02, -0.005], 100)
    assert ci[0] <= ci[1] <= ci[2]
    assert research.deflated(2.0, 8, 100) < 2.0
    assert math.isfinite(research.spearman([1, 2, 3, 4], [4, 3, 2, 1]))
    assert research.probability_backtest_overfitting([0.1, -0.1]) == 0.5


def test_report_labels_are_explicit():
    """Enforce the QUANT style contract."""
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
    """Enforce the QUANT style contract."""
    rows = research.synthetic_history()["AAA"]
    early = research.features(rows[:10], 9)
    assert early["valid"] is False
    assert early["validity_reason"] == "INSUFFICIENT_HISTORY"
    history = research.feature_history(rows)
    assert history[-1]["availability_index"] is None
    assert history[0]["availability_index"] == 1


def test_forward_label_is_censored_not_zero():
    """Enforce the QUANT style contract."""
    rows = [{"close": float(100 + i)} for i in range(5)]
    assert research.forward_label(rows, 4, 2) is None
    assert research.forward_label(rows, 2, 2) is not None
    assert research.forward_label(rows, 0, 0) is None


def test_daily_cross_sectional_ic_reports_validity():
    """Enforce the QUANT style contract."""
    history = research.synthetic_history()
    result = research.daily_cross_sectional_ic(history, research.score_momentum)
    assert result["status"] == "CALCULATED"
    assert result["observations"] > 0
    assert len(result["daily_ics"]) == result["observations"]
    assert len(result["dates"]) == result["observations"]
    assert -1.0 <= result["mean_ic"] <= 1.0
    assert result["coverage"] >= 5


def test_cscv_pbo_is_a_probability():
    """Enforce the QUANT style contract."""
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
    """Enforce the QUANT style contract."""
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
    """Enforce the QUANT style contract."""
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


def _write_pit_fixtures(tmp_path: Path, dates: list[str]) -> tuple[Path, Path]:
    """Enforce the QUANT style contract."""
    status = tmp_path / "status.txt"
    status.write_text(
        "symbol,exchange,isin,effective_from,effective_to,eligible,"
        "published_at,revision\n"
        + "".join(f"AAA,NSE,INE001A01DD1,{dates[0]},,true,{dates[0]},1\n")
    )
    calendar = tmp_path / "sessions.txt"
    calendar.write_text("\n".join(dates) + "\n")
    return status, calendar


def _write_manifest(directory: Path) -> None:
    import hashlib

    manifest: dict[str, object] = {"schema_version": 1}
    for path in sorted(directory.glob("*.csv")):
        content = path.read_bytes()
        manifest[path.stem] = {
            "digest": hashlib.sha256(content).hexdigest(),
            "rows": len(content.decode("utf-8").splitlines()) - 1,
        }
    (directory / "manifest.json").write_text(
        research.json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


def test_dataset_quality_reports_missing_and_malformed():
    """Enforce the QUANT style contract."""
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
    for row, value in zip(
        rows,
        ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"],
        strict=True,
    ):
        row["date"] = value
    result = research.dataset_quality(
        rows, ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    )
    assert result["status"] == "PARTIAL"
    assert result["missing_sessions"] == ["2026-01-04"]
    assert result["duplicate_count"] == 1
    assert result["invalid_bar_count"] == 1
    complete = research.dataset_quality(rows[:1], ["2026-01-01"])
    assert complete["status"] == "COMPLETE"
    impossible = research.dataset_quality(
        [
            {
                "date": "2026-02-31",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
        ],
        ["2026-02-31"],
    )
    assert impossible["invalid_bar_count"] == 1


def test_point_in_time_universe_ignores_future_status():
    """Enforce the QUANT style contract."""
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


def test_validated_dataset_rejects_blocked_input(tmp_path):
    """Enforce the QUANT style contract."""
    (tmp_path / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,102,99,101,1000\n"
        "2026-01-02,101,103,100,102,1000\n"
    )
    _write_manifest(tmp_path)
    status, calendar = _write_pit_fixtures(
        tmp_path, ["2026-01-01", "2026-01-02"]
    )
    records, errors = research.load_universe_status(status)
    sessions, calendar_errors = research.load_session_calendar(calendar)
    assert not errors and not calendar_errors
    dataset = research.load_history_validated(
        tmp_path, records, "TEST", sessions, errors, calendar_errors
    )
    assert dataset.status == "COMPLETE"
    assert dataset.invalid_rows == 0
    report = research.analyze(dataset.history, ROOT, dataset)
    assert report["data_contract"]["status"] == "COMPLETE"
    assert report["data_contract"]["digests"]["universe"]
    assert report["data_contract"]["digests"]["calendar"]
    assert report["data_contract"]["session_eligibility"] == "ENFORCED"


def test_validated_dataset_flags_malformed_ohlc(tmp_path):
    """Enforce the QUANT style contract."""
    (tmp_path / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,102,99,101,1000\n"
        "2026-01-02,50,103,100,102,1000\n"
    )
    _write_manifest(tmp_path)
    status, calendar = _write_pit_fixtures(
        tmp_path, ["2026-01-01", "2026-01-02"]
    )
    records, errors = research.load_universe_status(status)
    sessions, calendar_errors = research.load_session_calendar(calendar)
    dataset = research.load_history_validated(
        tmp_path, records, "TEST", sessions, errors, calendar_errors
    )
    assert dataset.status == "PARTIAL"
    assert dataset.invalid_rows == 1
    report = research.analyze(dataset.history, ROOT, dataset)
    assert report["conclusion"].startswith("PARTIAL")


def test_point_in_time_universe_uses_revision_precedence():
    """Enforce the QUANT style contract."""
    records = [
        {
            "symbol": "AAA",
            "eligible": True,
            "effective_from": "2020-01-01",
            "revision": 1,
        },
        {
            "symbol": "AAA",
            "eligible": False,
            "effective_from": "2020-01-01",
            "revision": 2,
        },
    ]
    assert research.point_in_time_universe(records, "2020-06-01") == []


def test_eligibility_map_filters_daily_cross_section():
    """Enforce the QUANT style contract."""
    history = research.synthetic_history()
    all_symbols = sorted(k for k in history if k != "NIFTY 50")
    eligible = {row["date"]: all_symbols for row in history["AAA"]}
    full = research.daily_cross_sectional_ic(
        history,
        research.score_momentum,
        eligible=eligible,
        min_instruments=4,
    )
    restricted_map = {
        date: [symbol for symbol in symbols if symbol != "AAA"]
        for date, symbols in eligible.items()
    }
    restricted = research.daily_cross_sectional_ic(
        history,
        research.score_momentum,
        eligible=restricted_map,
        min_instruments=4,
    )
    assert full["status"] == "CALCULATED"
    assert restricted["status"] == "CALCULATED"
    assert restricted["coverage"] == full["coverage"] - 1
    assert restricted["effective_sample_size"] < full["effective_sample_size"]


def _run_cli(data: Path, out: Path, *extra: str) -> tuple[int, dict]:
    """Enforce the QUANT style contract."""
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "python" / "research.py"),
            "--data",
            str(data),
            "--out",
            str(out),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, json.loads((out / "research.json").read_text())


def test_cli_exit_codes_reflect_dataset_status(tmp_path):
    """Enforce the QUANT style contract."""
    data = tmp_path / "history"
    data.mkdir()
    out = tmp_path / "reports"
    status, calendar = _write_pit_fixtures(
        tmp_path, ["2026-01-01", "2026-01-02"]
    )
    (data / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,102,99,101,1000\n"
        "2026-01-02,101,103,100,102,1000\n"
    )
    _write_manifest(data)
    code, report = _run_cli(
        data, out, "--universe-status", str(status), "--sessions", str(calendar)
    )
    assert code == 0
    assert report["data_contract"]["status"] == "COMPLETE"

    (data / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,102,99,101,1000\n"
        "2026-01-02,50,103,100,102,1000\n"
    )
    _write_manifest(data)
    code, report = _run_cli(
        data, out, "--universe-status", str(status), "--sessions", str(calendar)
    )
    assert code == 3
    assert report["conclusion"].startswith("PARTIAL")

    (data / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,102,not-a-number,101,1000\n"
    )
    _write_manifest(data)
    code, report = _run_cli(
        data, out, "--universe-status", str(status), "--sessions", str(calendar)
    )
    assert code == 2
    assert report["conclusion"].startswith("BLOCKED")
    assert report["data_contract"]["invalid_rows"] >= 1


def test_cli_fails_closed_without_pit_or_calendar(tmp_path):
    """Enforce the QUANT style contract."""
    data = tmp_path / "history"
    data.mkdir()
    out = tmp_path / "reports"
    (data / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n" "2026-01-01,100,102,99,101,1000\n"
    )
    code, report = _run_cli(data, out)
    assert code == 2
    assert report["data_contract"]["status"] == "BLOCKED"
    assert "universe status not supplied" in (
        report["data_contract"]["status_errors"]
    )

    status, _calendar = _write_pit_fixtures(tmp_path, ["2026-01-01"])
    code, report = _run_cli(data, out, "--universe-status", str(status))
    assert code == 2
    assert "exchange calendar not supplied" in (
        report["data_contract"]["status_errors"]
    )

    code, report = _run_cli(data, out, "--diagnostic-observed-only")
    assert code == 3
    assert report["data_contract"]["status"] == "OBSERVED_ONLY"
    assert report["data_contract"]["session_eligibility"] != "ENFORCED"


def test_malformed_pit_records_fail_closed(tmp_path):
    """Enforce the QUANT style contract."""
    data = tmp_path / "history"
    data.mkdir()
    out = tmp_path / "reports"
    (data / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n" "2026-01-01,100,102,99,101,1000\n"
    )
    calendar = tmp_path / "sessions.txt"
    calendar.write_text("2026-01-01\n")
    status = tmp_path / "status.txt"
    status.write_text(
        "symbol,exchange,isin,effective_from,effective_to,eligible,"
        "published_at,revision\n"
        "AAA,NSE,INE001A01DD1,2026-01-01,,maybe,2026-01-01,1\n"
    )
    code, report = _run_cli(
        data, out, "--universe-status", str(status), "--sessions", str(calendar)
    )
    assert code == 2
    assert report["data_contract"]["status"] == "BLOCKED"
    assert any(
        "eligible" in item for item in report["data_contract"]["status_errors"]
    )


def test_duplicate_csv_headers_rejected(tmp_path):
    """Enforce the QUANT style contract."""
    (tmp_path / "AAA.csv").write_text(
        "date,date,open,high,low,close,volume\n"
        "2026-01-01,2026-01-01,100,102,99,101,1000\n"
    )
    history, _count, detail = research.load_history_with_errors(tmp_path)
    assert history == {}
    assert any("duplicate header" in item for item in detail)


def test_late_published_status_cannot_alter_earlier_decision():
    """Enforce the QUANT style contract."""
    records = [
        {
            "symbol": "AAA",
            "effective_from": "2020-01-01",
            "eligible": False,
            "published_at": "2023-01-01",
            "revision": 2,
        }
    ]
    assert research.point_in_time_universe(records, "2021-06-01") == []


def test_shared_canonical_date_fixture():
    """Enforce the QUANT style contract."""
    fixture = ROOT / "tests" / "fixtures" / "canonical_dates.csv"
    with fixture.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 14
    for row in rows:
        parsed = research.parse_iso_date(row["date"])
        assert (parsed is not None) == (row["valid"] == "true"), row["date"]


def test_dependence_aware_uncertainty():
    """Enforce the QUANT style contract."""
    rng = __import__("random").Random(3)
    values = [0.0]
    for _ in range(119):
        values.append(0.8 * values[-1] + rng.gauss(0, 0.01))
    ci = research.block_bootstrap_ci(values, block_length=5, draws=500)
    assert ci[0] <= ci[1] <= ci[2]
    assert ci[2] > ci[0]
    assert research.hac_standard_error(values, lags=5) > 0
    assert research.block_bootstrap_ci([1.0, 2.0]) == [0.0, 0.0, 0.0]


def test_purged_walk_forward_protocol():
    """Enforce the QUANT style contract."""
    history = research.synthetic_history()
    result = research.purged_walk_forward(history, research.score_momentum)
    assert result["status"] == "CALCULATED"
    assert len(result["folds"]) > 0
    assert result["holdout_used_for_selection"] is False
    assert result["holdout_sessions"] == 20
    for fold in result["folds"]:
        assert fold["purged"] == research.HOLDING_SESSIONS
        assert fold["embargoed"] == research.HOLDING_SESSIONS


def test_selection_tests_are_not_overclaimed():
    """Enforce the QUANT style contract."""
    assert research.selection_test_status(None)["status"] == "UNAVAILABLE"
    report = research.analyze(research.synthetic_history(), ROOT)
    assert report["methodology"]["signal_diagnostics_scope"].startswith(
        "DESCRIPTIVE"
    )
    assert report["methodology"]["formal_selection_scope"].startswith(
        "NOT ESTABLISHED"
    )
    assert report["methodology"]["selection_tests"]["status"] == ("UNAVAILABLE")


def test_experiment_registry_records_reproducibility_inputs(tmp_path):
    """Enforce the QUANT style contract."""
    identifier = research.register_experiment(tmp_path, {"strategy": "x"})
    line = (
        (tmp_path / "data" / "assurance" / "experiments.jsonl")
        .read_text()
        .splitlines()[0]
    )
    entry = research.json.loads(line)
    assert entry["id"] == identifier
    assert entry["random_seed"] == research.SEED
    assert entry["trial_count"] == research.TRIALS
    assert entry["code_commit"]


def test_holdout_mutation_cannot_change_selection():
    """Enforce the QUANT style contract."""
    history = research.synthetic_history()
    baseline = research.analyze(history, ROOT)
    mutated = {
        symbol: [
            (
                dict(row)
                if i < len(rows) - 20
                else {**row, "close": row["close"] * 100.0}
            )
            for i, row in enumerate(rows)
        ]
        for symbol, rows in history.items()
    }
    changed = research.analyze(mutated, ROOT)
    assert changed["best_strategy"] == baseline["best_strategy"]
    assert changed["methodology"]["holdout_dates"] == (
        baseline["methodology"]["holdout_dates"]
    )
    for name in research.STRATEGIES:
        assert changed["walk_forward"][name]["mean_ic"] == (
            baseline["walk_forward"][name]["mean_ic"]
        )
        assert changed["walk_forward"][name]["dates"] == (
            baseline["walk_forward"][name]["dates"]
        )


def test_fold_count_is_not_observation_count():
    """Enforce the QUANT style contract."""
    report = research.analyze(research.synthetic_history(), ROOT)
    best = report["best_strategy"]
    stats = report["walk_forward"][best]
    assert stats["folds"] == len(
        report["methodology"]["purged_walk_forward"]["folds"]
    )
    assert stats["folds"] != stats["observations"]
    assert stats["cost_model"] == "GROSS_ONLY"
