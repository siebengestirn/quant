"""Cross-language golden tests for the QUANT v2 dataset contract."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_dataset_contract", ROOT / "python" / "dataset_contract.py"
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
sys.modules["quant_dataset_contract"] = contract
SPEC.loader.exec_module(contract)

TOOL = Path(
    os.environ.get("QUANT_CONTRACT_BIN", str(ROOT / "build" / "quant_contract"))
)

CSV = (
    "date,open,high,low,close,volume\n"
    "2026-01-01,100,103,99,102,1000\n"
    "2026-01-02,102,105,101,104,1200\n"
)


def _spec(symbols: list[str], sessions: list[str]) -> dict:
    """Build a deterministic v2 expectations object."""
    return {
        "schema_version": 2,
        "snapshot_id": "SNAP-GOLDEN-TEST",
        "calendar_id": "NSE_CASH_DAILY_OBSERVED",
        "generated_at_ms": 1769000000000,
        "provenance": "GOLDEN_FIXTURE",
        "symbols": symbols,
        "sessions": sessions,
        "first_date": sessions[0],
        "last_date": sessions[-1],
        "min_rows": 1,
        "instrument_ids": {symbol: f"ID-{symbol}" for symbol in symbols},
        "storage_keys": {
            symbol: contract.storage_key(f"ID-{symbol}") for symbol in symbols
        },
    }


def _fixture(tmp_path: Path, symbols: list[str]) -> Path:
    """Write canonical CSV series under opaque storage keys."""
    for symbol in symbols:
        key = contract.storage_key(f"ID-{symbol}")
        (tmp_path / f"{key}.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


def _cpp(*args: str) -> tuple[int, dict]:
    """Invoke the C++ contract tool and parse its structured result."""
    completed = subprocess.run(
        [str(TOOL), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = {}
    if completed.stdout.strip().startswith("{"):
        payload = json.loads(completed.stdout)
    return completed.returncode, payload


def test_sha256_matches_known_vector():
    """Both languages must implement the single canonical digest algorithm."""
    assert contract.sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_python_writer_cpp_verifier(tmp_path: Path):
    """A manifest written by Python must verify in C++."""
    directory = _fixture(tmp_path, ["AAA", "BBB"])
    expected = _spec(["AAA", "BBB"], ["2026-01-01", "2026-01-02"])
    spec_path = directory / "spec.json"
    spec_path.write_text(json.dumps(expected), encoding="utf-8")
    assert contract.write_contract(directory, expected) == []
    assert contract.write_manifest(directory, expected) == []
    code, result = _cpp("verify", str(directory))
    assert code == 0
    assert result["status"] == "COMPLETE"
    assert result["contract_digest"] == contract.contract_digest(expected)
    assert len(result.get("file_digests", {})) == 2


def test_cpp_writer_python_verifier(tmp_path: Path):
    """A manifest written by C++ must verify in Python."""
    directory = _fixture(tmp_path, ["AAA", "BBB"])
    expected = _spec(["AAA", "BBB"], ["2026-01-01", "2026-01-02"])
    spec_path = directory / "spec.json"
    spec_path.write_text(json.dumps(expected), encoding="utf-8")
    code, stdout = _cpp("write", str(directory), str(spec_path))
    assert code == 0, stdout
    result = contract.verify_dataset(directory)
    assert result.status == "COMPLETE"
    assert result.contract_digest == contract.contract_digest(expected)
    assert result.file_digests == {
        symbol: contract.sha256_hex(CSV.encode()) for symbol in ("AAA", "BBB")
    }


def test_corruption_fails_in_both_languages(tmp_path: Path):
    """Byte-level tampering must be rejected by both verifiers."""
    directory = _fixture(tmp_path, ["AAA"])
    expected = _spec(["AAA"], ["2026-01-01", "2026-01-02"])
    spec_path = directory / "spec.json"
    spec_path.write_text(json.dumps(expected), encoding="utf-8")
    assert _cpp("write", str(directory), str(spec_path))[0] == 0
    _key = contract.storage_key("ID-AAA")
    (directory / f"{_key}.csv").write_bytes(
        (directory / f"{_key}.csv")
        .read_bytes()
        .replace(b"102,1000", b"999,1000")
    )
    assert contract.verify_dataset(directory).status == "FAILED"
    code, result = _cpp("verify", str(directory))
    assert code == 1
    assert result["status"] == "FAILED"


def test_zero_rows_and_missing_sessions_fail_closed(tmp_path: Path):
    """Empty or incomplete snapshots can never become COMPLETE."""
    directory = _fixture(tmp_path, ["AAA"])
    expected = _spec(["AAA"], ["2026-01-01"])
    assert contract.write_contract(directory, expected) == []
    assert contract.write_manifest(directory, expected)
    assert contract.verify_dataset(directory).status != "COMPLETE"
    _key = contract.storage_key("ID-AAA")
    (directory / f"{_key}.csv").write_text(
        "date,open,high,low,close,volume\n", encoding="utf-8"
    )
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    assert contract.verify_dataset(directory).status == "BLOCKED"
    code, _ = _cpp("verify", str(directory))
    assert code != 0


def test_csv_schema_v3_preserves_open_interest(tmp_path: Path):
    """Versioned CSV schema v3 retains validated open interest."""
    directory = tmp_path / "v3"
    directory.mkdir()
    key = contract.storage_key("ID-AAA")
    (directory / f"{key}.csv").write_text(
        "date,open,high,low,close,volume,open_interest\n"
        "2026-01-02,102,105,101,104,1200,7\n"
        "2026-01-01,100,103,99,102,1000,5\n"
    )
    expected = _spec(["AAA"], ["2026-01-01", "2026-01-02"])
    expected["csv_schema"] = "v3"
    assert contract.write_contract(directory, expected) == []
    assert contract.write_manifest(directory, expected) == []
    verified = contract.verify_dataset(directory)
    assert verified.status == "COMPLETE"
    assert any(row[-1] == "5" for row in result_rows(directory, key)[1:])


def test_csv_schema_v3_open_interest_bounds():
    """V3 open interest uses a bounded, exact integer representation."""
    header = "date,open,high,low,close,volume,open_interest\n"
    for value in ("-2", "1.5", "9223372036854775808", "1e300", "nan"):
        payload = f"2026-01-01,100,103,99,102,1000,{value}\n".encode()
        rows, errors = contract._read_csv_content(
            header.encode() + payload, "x.csv", "v3"
        )
        assert errors and not rows
    for value in ("-1", "0", "7", "9223372036854774784"):
        payload = f"2026-01-01,100,103,99,102,1000,{value}\n".encode()
        rows, errors = contract._read_csv_content(
            header.encode() + payload, "x.csv", "v3"
        )
        assert not errors and rows[0]["open_interest"] == int(value)


def result_rows(directory: Path, key: str) -> list[list[str]]:
    """Return raw CSV rows for assertion helpers."""
    return [
        line.split(",")
        for line in (directory / f"{key}.csv").read_text().splitlines()
    ]
