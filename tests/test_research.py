"""Pytest coverage for Python research diagnostics (SIMULATED data only)."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_loader_support import load_test_module

ROOT = Path(__file__).resolve().parent.parent
research = load_test_module("quant_research", ROOT / "python" / "research.py")
provenance = load_test_module(
    "quant_data_provenance_for_research",
    ROOT / "python" / "data_provenance.py",
)


def _authorization() -> research.ExperimentAuthorization:
    """Authorized offline registration for simulated test fixtures."""
    return research.ExperimentAuthorization(
        classification=provenance.DataClassification.SIMULATED_MARKET_DATA,
        approved_by="test",
        reason="offline pytest fixture on synthetic data",
    )


def _registry(tmp_path: Path) -> Path:
    """Explicit registry file path used by the sink and direct registration."""
    return tmp_path / "data" / "assurance" / "experiments.jsonl"


def _synthetic_dataset(**overrides: object) -> research.ValidatedDataset:
    """Wrap simulated history as a COMPLETE dataset without PIT filtering."""
    provenance_fields: dict = {
        "data_classification": (
            provenance.DataClassification.SIMULATED_MARKET_DATA
        ),
        "source_artifact_digest": "e" * 64,
    }
    for key in (
        "data_classification",
        "source_artifact_digest",
        "corporate_action_digest",
        "feature_version_digest",
        "instrument_registry_digest",
    ):
        if key in overrides:
            provenance_fields[key] = overrides.pop(key)
    fields: dict = {
        "history": research.synthetic_history(),
        "quality": {},
        "eligible_dates": {},
        "status": "COMPLETE",
        "source": "SYNTHETIC_TEST_FIXTURE",
        "vintage": "TEST",
        "invalid_rows": 0,
        "pit_enforced": False,
        "dataset_digest": "a" * 64,
        "universe_digest": "b" * 64,
        "calendar_digest": "c" * 64,
        "code_version": "d" * 64,
        "provenance": research.DatasetProvenance(**provenance_fields),
    }
    fields.update(overrides)
    return research.ValidatedDataset(**fields)


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
    auth = _authorization()
    first = research.register_experiment(
        _registry(tmp_path), {"strategy": "x"}, authorization=auth
    )
    second = research.register_experiment(
        _registry(tmp_path), {"strategy": "y"}, authorization=auth
    )
    assert first != second
    lines = _registry(tmp_path).read_text().splitlines()
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


DATASET_CONTRACT = load_test_module(
    "quant_dataset_contract_test", ROOT / "python" / "dataset_contract.py"
)


def _write_manifest(directory: Path) -> None:
    """Write a v2 contract and manifest for every CSV in the directory."""
    storage = {path.stem: path for path in directory.glob("*.csv")}
    expectations_path = directory / "contract.json"
    if expectations_path.is_file():
        prior = research.json.loads(expectations_path.read_text())
        keys = prior["storage_keys"]
    else:
        keys = {stem: stem for stem in storage}
    symbols = sorted(keys)
    sessions = sorted(
        {
            line.split(",", 1)[0]
            for path in storage.values()
            for line in path.read_text(encoding="utf-8").splitlines()[1:]
            if line
        }
    )
    expectations = {
        "schema_version": 2,
        "snapshot_id": "SNAP-TEST-FIXTURE",
        "calendar_id": "NSE_CASH_DAILY_OBSERVED",
        "generated_at_ms": 1769000000000,
        "provenance": "PYTEST_FIXTURE",
        "symbols": symbols,
        "sessions": sessions,
        "first_date": sessions[0],
        "last_date": sessions[-1],
        "min_rows": 1,
        "instrument_ids": {symbol: f"ID-{symbol}" for symbol in symbols},
        "storage_keys": {
            symbol: keys.get(
                symbol, DATASET_CONTRACT.storage_key(f"ID-{symbol}")
            )
            for symbol in symbols
        },
    }
    DATASET_CONTRACT.write_contract(directory, expectations)
    errors = DATASET_CONTRACT.write_manifest(directory, expectations)
    if not errors:
        return
    # Malformed fixtures cannot be published by the strict writer. Build a
    # structurally valid manifest so the verifier can fail closed on content.
    files = {}
    for symbol in symbols:
        content = storage[expectations["storage_keys"][symbol]].read_bytes()
        rows = len(content.decode("utf-8").splitlines()) - 1
        files[symbol] = {
            "digest": DATASET_CONTRACT.sha256_hex(content),
            "rows": rows,
            "first_date": sessions[0],
            "last_date": sessions[-1],
            "instrument_id": expectations["instrument_ids"][symbol],
            "raw_digest": "",
        }
    manifest = {
        "schema_version": 2,
        "digest_algorithm": "sha256",
        "contract_digest": DATASET_CONTRACT.contract_digest(expectations),
        "contract_sha256": DATASET_CONTRACT.sha256_hex(
            (directory / "contract.json").read_bytes()
        ),
        "expectations": expectations,
        "files": files,
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
    assert dataset.status == "BLOCKED"
    assert not dataset.history
    report = research.analyze(dataset.history, ROOT, dataset)
    assert report["conclusion"].startswith("BLOCKED")


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
    assert code == 2
    assert report["conclusion"].startswith("BLOCKED")

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
    identifier = research.register_experiment(
        _registry(tmp_path), {"strategy": "x"}, authorization=_authorization()
    )
    line = _registry(tmp_path).read_text().splitlines()[0]
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
    nested = report["methodology"]["nested_validation"]
    assert stats["folds"] == sum(
        1 for fold in nested["folds"] if fold["selected_strategy"] == best
    )
    assert stats["folds"] <= len(nested["folds"])
    assert stats["folds"] != stats["observations"]
    assert stats["cost_model"] == "GROSS_ONLY"


def test_oos_fold_metrics_drive_report():
    report = research.analyze(research.synthetic_history(), ROOT)
    best = report["best_strategy"]
    stats = report["walk_forward"][best]
    nested = report["methodology"]["nested_validation"]
    folds = [
        fold for fold in nested["folds"] if fold["selected_strategy"] == best
    ]
    values = [value for fold in folds for value in fold["test_ic"]]
    expected = research.statistics.fmean(values) if values else None
    assert stats["scope"] == "OUT_OF_SAMPLE_FOLDS"
    assert stats["mean_ic"] == expected
    assert stats["folds"] == len(folds)
    assert report["methodology"]["holdout_status"] == "SEALED_NOT_EVALUATED"
    assert report["methodology"]["oos_metric"]


def test_future_fold_mutation_does_not_alter_earlier_folds():
    history = research.synthetic_history()
    baseline = research.purged_walk_forward(
        history, research.score_momentum, final_holdout=0
    )
    dates = sorted({row["date"] for rows in history.values() for row in rows})
    mutated = {
        symbol: [
            (
                {**row, "close": row["close"] * 10.0}
                if row["date"] in dates[-10:]
                else dict(row)
            )
            for row in rows
        ]
        for symbol, rows in history.items()
    }
    changed = research.purged_walk_forward(
        mutated, research.score_momentum, final_holdout=0
    )
    assert baseline["folds"][0] == changed["folds"][0]


def test_nested_selection_ignores_outer_test_data():
    history = research.synthetic_history()
    dates = sorted({row["date"] for rows in history.values() for row in rows})
    baseline = research.nested_walk_forward(history)
    mutated = {
        symbol: [
            (
                {**row, "close": row["close"] * 20.0}
                if row["date"] in dates[-25:]
                else dict(row)
            )
            for row in rows
        ]
        for symbol, rows in history.items()
    }
    changed = research.nested_walk_forward(mutated)
    assert baseline["folds"][0]["selected_strategy"] == (
        changed["folds"][0]["selected_strategy"]
    )
    assert baseline["folds"][0]["train_mean_ic"] == (
        changed["folds"][0]["train_mean_ic"]
    )


def test_nested_registers_full_trial_universe():
    history = research.synthetic_history()
    nested = research.nested_walk_forward(history)
    strategies = len(research.STRATEGIES)
    folds = len(nested["folds"])
    assert len(nested["full_trial_matrix"]) == strategies * folds
    assert all(
        0 <= count <= folds for count in nested["selection_counts"].values()
    )
    assert nested["outer_test_observations"] > 0
    report = research.analyze(history, ROOT)
    analysis_nested = report["methodology"]["nested_validation"]
    assert report["multiple_testing"]["registered_trials"] == (
        strategies * len(analysis_nested["folds"])
    )
    assert report["methodology"]["holdout_status"] == "SEALED_NOT_EVALUATED"


def test_report_separates_development_and_outer_test_scopes():
    report = research.analyze(research.synthetic_history(), ROOT)
    best = report["best_strategy"]
    stats = report["walk_forward"][best]
    assert "development_descriptive" in stats
    assert stats["development_descriptive"]["scope"] == (
        "DEVELOPMENT_DESCRIPTIVE"
    )
    oos = stats["outer_test"]
    assert oos["scope"] == "OUTER_TEST_ONLY"
    assert oos["folds_selected"] == stats["folds"]
    assert stats["mean_ic"] == oos["mean_ic"]
    assert stats["ic_std"] == oos["ic_std"]
    assert stats["positive_folds"] == oos["positive_folds"]
    for field in ("dsr", "gross_spread_mean", "gross_spread_observations"):
        assert field in stats["development_descriptive"]
        assert field not in oos
    assert report["methodology"]["holdout_status"] == "SEALED_NOT_EVALUATED"


RESEARCH_OBSERVED_AT = "2026-01-03T00:00:00Z"


def _identity_resolution(
    identity: str = "CID-AAA",
    status: str = "RESOLVED",
    validity: str = "KNOWN_INTERVAL",
) -> dict:
    """Return a resolver-shaped canonical identity result."""
    return {
        "status": status,
        "canonical_instrument_id": identity,
        "validity": validity,
        "eligibility": "ELIGIBLE",
    }


def _resolver_for(status: str, validity: str):
    """Return a resolver producing one fixed, explicit resolution outcome."""

    def resolver(*_args):
        """Return the configured identity outcome for any query."""
        return _identity_resolution(status=status, validity=validity)

    return resolver


def _fixed_resolution(*_args):
    """Return a stable canonical identity for any resolver query."""
    return _identity_resolution()


def _attribute_resolver(*_args):
    """Return an attribute-bearing canonical identity result."""
    return SimpleNamespace(**_identity_resolution())


def _expired_attribute_resolver(*_args):
    """Return an attribute-bearing result with a past validity bound."""
    result = _identity_resolution()
    result["valid_to"] = "2026-01-01"
    return SimpleNamespace(**result)


def _missing_resolver(*_args):
    """Return no resolution at all."""
    return None


def _write_research_dataset(tmp_path: Path) -> tuple[Path, Path, list, list]:
    """Write a minimal manifest-backed dataset for resolver tests."""
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
    return status, calendar, records, sessions


def test_dataset_resolver_records_identity_and_default_is_unchanged(tmp_path):
    """A supplied resolver annotates bars; symbol-keyed default stays as-is."""
    _status, _calendar, records, sessions = _write_research_dataset(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def resolver(identifier, market_date, observed_at):
        """Resolve every bar to one canonical identity."""
        calls.append((identifier, market_date, observed_at))
        return _identity_resolution()

    dataset = research.load_history_validated(
        tmp_path,
        records,
        "TEST",
        sessions,
        [],
        [],
        resolver=resolver,
        observed_at=RESEARCH_OBSERVED_AT,
    )
    assert dataset.status == "COMPLETE"
    assert not dataset.coverage_gaps
    assert len(dataset.history["AAA"]) == 2
    assert all(
        row["canonical_instrument_id"] == "CID-AAA" and row["symbol"] == "AAA"
        for row in dataset.history["AAA"]
    )
    assert calls == [
        ("AAA", "2026-01-01", RESEARCH_OBSERVED_AT),
        ("AAA", "2026-01-02", RESEARCH_OBSERVED_AT),
    ]
    default = research.load_history_validated(
        tmp_path, records, "TEST", sessions, [], []
    )
    assert all(
        "canonical_instrument_id" not in row for row in default.history["AAA"]
    )


def test_dataset_resolver_refuses_records_without_established_validity(
    tmp_path,
):
    """Unknown validity and expired aliases never enter accepted history."""
    _status, _calendar, records, sessions = _write_research_dataset(tmp_path)
    for validity, status in (
        ("UNKNOWN_INTERVAL", "UNKNOWN_INTERVAL"),
        ("KNOWN_INTERVAL", "EXPIRED"),
    ):
        candidate = tmp_path / "candidate"
        candidate.mkdir(exist_ok=True)
        (candidate / "AAA.csv").write_text((tmp_path / "AAA.csv").read_text())
        for name in ("contract.json", "manifest.json"):
            (candidate / name).write_text((tmp_path / name).read_text())
        dataset = research.load_history_validated(
            candidate,
            records,
            "TEST",
            sessions,
            [],
            [],
            resolver=_resolver_for(status, validity),
            observed_at=RESEARCH_OBSERVED_AT,
        )
        assert not dataset.history
        assert len(dataset.coverage_gaps) == 2
        assert all(
            "unresolved identity" in gap for gap in dataset.coverage_gaps
        )


def test_feature_path_records_identity_without_changing_math():
    """Feature values stay identical while canonical metadata is attached."""
    rows = research.synthetic_history()["AAA"]
    plain = research.features(rows, 100)
    history = research.feature_history(
        rows,
        resolver=_fixed_resolution,
        symbol="AAA",
        observed_at=RESEARCH_OBSERVED_AT,
    )
    enriched = history[100]
    assert enriched["symbol"] == "AAA"
    assert enriched["canonical_instrument_id"] == "CID-AAA"
    assert enriched["resolution_status"] == "RESOLVED"
    for key, value in plain.items():
        assert enriched[key] == value
    assert "canonical_instrument_id" not in research.feature_history(rows)[0]
    assert research.score_momentum(enriched) == research.score_momentum(plain)


def test_feature_path_refuses_unresolved_identity():
    """A refused resolution leaves no canonical ID on any feature record."""
    rows = research.synthetic_history()["AAA"]
    history = research.feature_history(
        rows,
        resolver=_resolver_for("EXPIRED", "KNOWN_INTERVAL"),
        symbol="AAA",
        observed_at=RESEARCH_OBSERVED_AT,
    )
    assert all(item["canonical_instrument_id"] is None for item in history)
    assert all(item["resolution_status"] == "EXPIRED" for item in history)


def test_feature_path_accepts_attribute_results_and_rejects_missing():
    """Mapping and attribute results work; unknown axis bounds are refused."""
    rows = research.synthetic_history()["AAA"][:1]
    accepted = research.feature_history(
        rows,
        resolver=_attribute_resolver,
        symbol="AAA",
        observed_at=RESEARCH_OBSERVED_AT,
    )
    assert accepted[0]["canonical_instrument_id"] == "CID-AAA"
    for resolver in (_expired_attribute_resolver, _missing_resolver):
        refused = research.feature_history(
            rows,
            resolver=resolver,
            symbol="AAA",
            observed_at=RESEARCH_OBSERVED_AT,
        )
        assert refused[0]["canonical_instrument_id"] is None


def test_service_bound_resolver_integrates_with_dataset_path(tmp_path):
    """The optional resolver accepts a provider-bound registry service."""
    im = load_test_module(
        "quant_instrument_master_research_test",
        ROOT / "python" / "instrument_master.py",
    )
    svc = load_test_module(
        "quant_registry_service_research_test",
        ROOT / "python" / "instrument_registry_service.py",
    )
    identity = "NSE-CASH-RESEARCH-TEST"
    observed_at = "2025-12-31T09:00:00Z"

    def row(name, value, *, provider=""):
        """Build one observation with an attested interval."""
        attribute = im.provider_attribute(provider, name) if provider else name
        return im.observation(
            identity,
            attribute,
            value,
            observed_at=observed_at,
            valid_from="2026-01-01",
            valid_to="2026-02-01",
            source_digest="a" * 64,
        )

    rows = [
        row(im.ATTR_IDENTITY_STATUS, im.VERIFIED),
        row(im.ATTR_CONFORMANCE_STATUS, im.PASS),
        row(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE),
        row(im.ATTR_EXCHANGE_STATUS, im.OPEN),
        row(im.ATTR_EXCHANGE_ELIGIBILITY, im.ALLOWED),
        row(im.ATTR_DATA_FRESHNESS, im.FRESH),
        row(
            im.PROVIDER_KEY_ATTRIBUTE,
            im.provider_alias("upstox", "AAA"),
            provider="upstox",
        ),
    ]
    resolver = svc.InstrumentRegistryService(
        im.build_registry(rows)
    ).for_provider("upstox")
    _status, _calendar, records, sessions = _write_research_dataset(tmp_path)
    dataset = research.load_history_validated(
        tmp_path,
        records,
        "TEST",
        sessions,
        [],
        [],
        resolver=resolver,
        observed_at=observed_at,
    )
    assert dataset.status == "COMPLETE"
    assert not dataset.coverage_gaps
    assert all(
        entry["canonical_instrument_id"] == identity
        for entry in dataset.history["AAA"]
    )


def test_analyze_default_sink_never_registers(tmp_path):
    """The default sink must not create or modify any registry file."""
    persistent = ROOT / "data" / "assurance" / "experiments.jsonl"
    before = persistent.read_bytes() if persistent.is_file() else None
    report = research.analyze(research.synthetic_history(), ROOT)
    assert report["experiment_registration"]["sink"] == "NullExperimentSink"
    assert report["experiment_registration"]["records"] == 4
    research.analyze(research.synthetic_history(), tmp_path)
    assert not _registry(tmp_path).exists()
    after = persistent.read_bytes() if persistent.is_file() else None
    assert after == before


def test_synthetic_sink_records_are_never_real_data_research(tmp_path):
    """Synthetic analysis can only emit SYNTHETIC_OR_UNVERIFIED records."""
    sink = research.InMemoryExperimentSink()
    report = research.analyze(
        research.synthetic_history(), ROOT, experiment_sink=sink
    )
    assert len(sink.records()) == 4
    assert all(
        record["experiment_classification"] == "SYNTHETIC_OR_UNVERIFIED"
        for record in sink.records()
    )
    assert all(
        record["experiment_classification"] != "REAL_DATA_RESEARCH"
        for record in sink.records()
    )
    assert report["experiment_registration"]["classification"] == (
        "SYNTHETIC_OR_UNVERIFIED"
    )

    path = _registry(tmp_path)
    file_sink = research.FileExperimentSink(path, _authorization())
    research.analyze(
        research.synthetic_history(), ROOT, experiment_sink=file_sink
    )
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(entries) == 4
    assert all(
        entry["experiment_classification"] == "SYNTHETIC_OR_UNVERIFIED"
        for entry in entries
    )
    assert all(entry["approved_by"] == "test" for entry in entries)
    assert path.stat().st_mode & 0o777 == 0o600


def test_complete_real_dataset_registers_real_research(tmp_path):
    """A complete, correctly classified real dataset must be registrable."""
    lineage = {
        "corporate_action_digest": "1" * 64,
        "feature_version_digest": "2" * 64,
        "instrument_registry_digest": "3" * 64,
    }
    dataset = _synthetic_dataset(
        data_classification=provenance.DataClassification.PROVIDER_API_CAPTURE,
        source_artifact_digest="e" * 64,
        **lineage,
    )
    assert dataset.status == "COMPLETE"
    assert dataset.provenance.data_classification == "PROVIDER_API_CAPTURE"
    authorization = research.ExperimentAuthorization(
        classification=provenance.DataClassification.PROVIDER_API_CAPTURE,
        approved_by="test",
        reason="offline positive case for real-source research",
    )
    path = _registry(tmp_path)
    sink = research.FileExperimentSink(path, authorization)
    report = research.analyze(
        dataset.history, ROOT, dataset, experiment_sink=sink
    )

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert report["experiment_registration"]["classification"] == (
        "REAL_DATA_RESEARCH"
    )
    assert len(entries) == 4
    assert all(
        entry["experiment_classification"] == "REAL_DATA_RESEARCH"
        and entry["data_classification"] == "PROVIDER_API_CAPTURE"
        and entry["source_artifact_digest"] == "e" * 64
        and entry["dataset_digest"] == "a" * 64
        and entry["universe_digest"] == "b" * 64
        and entry["calendar_digest"] == "c" * 64
        and all(entry[key] == value for key, value in lineage.items())
        for entry in entries
    )


def test_real_classification_refused_without_digest_or_classification(
    tmp_path,
):
    """A real-source claim without a digest cannot become real research."""
    dataset = _synthetic_dataset(
        data_classification=provenance.DataClassification.PROVIDER_API_CAPTURE,
        source_artifact_digest="",
    )
    assert dataset.provenance.data_classification == "UNVERIFIED"
    assert any(
        "real-source classification refused" in error
        for error in dataset.status_errors
    )
    sink = research.InMemoryExperimentSink()
    research.analyze(dataset.history, ROOT, dataset, experiment_sink=sink)
    assert len(sink.records()) == 4
    assert all(
        record["experiment_classification"] == "SYNTHETIC_OR_UNVERIFIED"
        and record["data_classification"] == "UNVERIFIED"
        for record in sink.records()
    )

    unclassified = _synthetic_dataset(data_classification="UNVERIFIED")
    refused = research.InMemoryExperimentSink()
    research.analyze(
        unclassified.history, ROOT, unclassified, experiment_sink=refused
    )
    assert all(
        record["experiment_classification"] == "SYNTHETIC_OR_UNVERIFIED"
        for record in refused.records()
    )

    real_auth = research.ExperimentAuthorization(
        classification=provenance.DataClassification.PROVIDER_API_CAPTURE,
        approved_by="test",
        reason="offline negative case",
    )
    real_sink = research.FileExperimentSink(_registry(tmp_path), real_auth)
    with pytest.raises(ValueError):
        real_sink.append(
            {"experiment_classification": "SYNTHETIC_OR_UNVERIFIED"}
        )
    assert not _registry(tmp_path).exists()


def test_lineage_digests_and_configuration_digest_are_recorded(tmp_path):
    """Lineage fields flow through loaders and into experiment records."""
    _status, _calendar, records, sessions = _write_research_dataset(tmp_path)
    lineage = {
        "corporate_action_digest": "1" * 64,
        "feature_version_digest": "2" * 64,
        "instrument_registry_digest": "3" * 64,
    }
    dataset = research.load_history_validated(
        tmp_path,
        records,
        "TEST",
        sessions,
        [],
        [],
        provenance=research.DatasetProvenance(
            data_classification=(
                provenance.DataClassification.SIMULATED_MARKET_DATA
            ),
            **lineage,
        ),
    )
    assert dataset.provenance.data_classification == ("SIMULATED_MARKET_DATA")
    for key, value in lineage.items():
        assert getattr(dataset.provenance, key) == value
    missing = research.load_history_validated(
        tmp_path, records, "TEST", sessions, [], []
    )
    assert missing.provenance.corporate_action_digest == "UNKNOWN"
    assert missing.provenance.feature_version_digest == "UNKNOWN"
    assert missing.provenance.instrument_registry_digest == "UNKNOWN"

    analyzed = _synthetic_dataset(**lineage)
    sink = research.InMemoryExperimentSink()
    research.analyze(analyzed.history, ROOT, analyzed, experiment_sink=sink)
    assert len(sink.records()) == 4
    assert all(
        all(record[key] == value for key, value in lineage.items())
        and provenance.is_sha256_hex(record["configuration_digest"])
        for record in sink.records()
    )


def test_experiment_record_reconstructable_from_digests():
    """The configuration digest is reproducible from recorded fields."""
    dataset = _synthetic_dataset()
    sink = research.InMemoryExperimentSink()
    research.analyze(dataset.history, ROOT, dataset, experiment_sink=sink)
    record = sink.records()[0]
    config = research.experiment_configuration(record)
    assert research.configuration_digest(config) == (
        record["configuration_digest"]
    )
    rebuilt = {
        "strategy": record["strategy"],
        "dataset_digest": record["dataset_digest"],
        "universe_digest": record["universe_digest"],
        "calendar_digest": record["calendar_digest"],
        "source_artifact_digest": record["source_artifact_digest"],
        "corporate_action_digest": record["corporate_action_digest"],
        "feature_version_digest": record["feature_version_digest"],
        "instrument_registry_digest": record["instrument_registry_digest"],
        "code_commit": record["code_commit"],
        "data_classification": record["data_classification"],
        "experiment_classification": record["experiment_classification"],
        "random_seed": record["random_seed"],
        "trial_count": record["trial_count"],
        "holdout_status": record["holdout_status"],
    }
    assert config == rebuilt
    assert research.configuration_digest(rebuilt) == (
        record["configuration_digest"]
    )
    assert record["status"] == "INCONCLUSIVE"
    assert record["holdout_status"] == "SEALED_NOT_EVALUATED"
    assert record["random_seed"] == research.SEED
    assert record["trial_count"] == research.TRIALS
