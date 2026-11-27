#!/usr/bin/env python3
"""Offline clean-room proof of canonical cross-language binary routing.

Each test copies the two portable cross-language suites and the shared
Python contract module into a temporary checkout that starts with no
``build/`` or ``build-ci/`` directory. A simulated portable build installs a
fresh executable at ``build-ci/quant_contract`` and a stale, poisoned one at
``build/quant_contract``. Both wrappers log their invocations. The tests
prove that ``QUANT_CONTRACT_BIN`` selects the fresh binary, that an absent or
wrongly routed environment never silently uses the stale default, and that
``verify_report`` records the fresh binary's path and digest in its gate.

No network, hosted CI or real CMake build is needed; the fresh wrapper
delegates to the repository's Python contract CLI so the cross-language
fixtures remain deterministic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location(
    "verify_report", ROOT / "scripts" / "verify_report.py"
)
assert _SPEC is not None and _SPEC.loader is not None
VERIFY_REPORT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(VERIFY_REPORT)

PASS = VERIFY_REPORT.PASS
FAIL = VERIFY_REPORT.FAIL
COMMIT = "c" * 40
PYTHON_SUITES = (
    "tests/test_dataset_contract.py",
    "tests/test_dataset_adversarial.py",
)
FRESH_WRAPPER = """#!/bin/sh
echo "$*" >> "$QUANT_CLEAN_ROOM_FRESH_LOG"
exec python3 "$(dirname "$0")/../python/dataset_contract.py" "$@"
"""
STALE_WRAPPER = """#!/bin/sh
echo "$*" >> "$QUANT_CLEAN_ROOM_STALE_LOG"
echo '{"status": "STALE"}'
exit 9
"""


def clean_checkout(tmp_path: Path) -> Path:
    """Copy only the portable contract sources into an empty checkout."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "python").mkdir()
    (checkout / "tests").mkdir()
    (checkout / "data" / "review-fixtures").mkdir(parents=True)
    for relative in PYTHON_SUITES:
        shutil.copy2(ROOT / relative, checkout / relative)
    shutil.copy2(
        ROOT / "python" / "dataset_contract.py",
        checkout / "python" / "dataset_contract.py",
    )
    assert not (checkout / "build").exists()
    assert not (checkout / "build-ci").exists()
    return checkout


def simulate_portable_build(
    checkout: Path,
) -> tuple[Path, Path, Path, Path]:
    """Install a fresh CI binary and a poisoned stale build binary."""
    build_ci = checkout / "build-ci"
    build_ci.mkdir()
    fresh = build_ci / "quant_contract"
    fresh.write_text(FRESH_WRAPPER, encoding="utf-8")
    fresh.chmod(0o755)
    build = checkout / "build"
    build.mkdir()
    stale = build / "quant_contract"
    stale.write_text(STALE_WRAPPER, encoding="utf-8")
    stale.chmod(0o755)
    fresh_log = checkout / "fresh-invocations.log"
    stale_log = checkout / "stale-invocations.log"
    return fresh, stale, fresh_log, stale_log


def pytest_env(
    fresh_log: Path, stale_log: Path, *, binary: Path | None = None
) -> dict[str, str]:
    """Build an offline pytest environment with explicit invocation logs."""
    env = {
        **os.environ,
        "QUANT_CLEAN_ROOM_FRESH_LOG": str(fresh_log),
        "QUANT_CLEAN_ROOM_STALE_LOG": str(stale_log),
        "PYTEST_ADDOPTS": "",
    }
    env.pop(VERIFY_REPORT.BINARY_ENV, None)
    if binary is not None:
        env[VERIFY_REPORT.BINARY_ENV] = str(binary)
    return env


def run_suites(checkout: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run the cross-language suites inside the clean checkout."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *PYTHON_SUITES,
            "-o",
            "addopts=",
            "-q",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


def capture_gate_steps(
    monkeypatch: pytest.MonkeyPatch, output: str = "9 passed in 0.1s"
) -> list[dict[str, str]]:
    """Capture the environment handed to the portable pytest step."""
    seen: list[dict[str, str]] = []

    def step(name: str, argv: list[str], env=None) -> dict:
        del name, argv
        seen.append(dict(env or {}))
        return {
            "name": "portable pytest",
            "command": "python3 -m pytest",
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": output,
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", step)
    return seen


def test_clean_room_uses_fresh_binary_from_canonical_env(
    tmp_path: Path,
) -> None:
    """A clean checkout passes only when the fresh binary is named."""
    checkout = clean_checkout(tmp_path)
    fresh, _stale, fresh_log, stale_log = simulate_portable_build(checkout)
    code, output = run_suites(
        checkout, pytest_env(fresh_log, stale_log, binary=fresh)
    )
    counts = VERIFY_REPORT.parse_pytest(output)
    assert code == 0, output
    assert counts["passed"] > 0
    assert counts["failed"] == 0 and counts["errors"] == 0
    assert counts["skipped"] == 0
    assert fresh_log.read_text(encoding="utf-8").strip()
    assert not stale_log.exists()


def test_clean_room_wrong_env_path_uses_stale_binary_and_fails(
    tmp_path: Path,
) -> None:
    """A wrong QUANT_CONTRACT_BIN cannot silently pass the suites."""
    checkout = clean_checkout(tmp_path)
    _fresh, stale, fresh_log, stale_log = simulate_portable_build(checkout)
    code, output = run_suites(
        checkout, pytest_env(fresh_log, stale_log, binary=stale)
    )
    assert code != 0, output
    assert stale_log.read_text(encoding="utf-8").strip()
    assert not fresh_log.exists()


def test_clean_room_default_binary_is_stale_and_fails_closed(
    tmp_path: Path,
) -> None:
    """Without the canonical env, the stale build/ default fails closed."""
    checkout = clean_checkout(tmp_path)
    _fresh, _stale, fresh_log, stale_log = simulate_portable_build(checkout)
    code, output = run_suites(checkout, pytest_env(fresh_log, stale_log))
    assert code != 0, output
    assert stale_log.read_text(encoding="utf-8").strip()
    assert not fresh_log.exists()


def test_verify_report_gate_routes_clean_checkout_to_fresh_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The report gate overrides ambient env and records the fresh digest."""
    checkout = clean_checkout(tmp_path)
    fresh, stale, fresh_log, stale_log = simulate_portable_build(checkout)
    monkeypatch.setattr(VERIFY_REPORT, "ROOT", checkout)
    monkeypatch.setenv(VERIFY_REPORT.BINARY_ENV, str(stale))
    seen = capture_gate_steps(monkeypatch)
    gate = VERIFY_REPORT.run_pytest([], COMMIT)
    assert gate["result"] == PASS
    assert len(seen) == 1
    assert seen[0][VERIFY_REPORT.BINARY_ENV] == str(fresh)
    assert gate["counts"][VERIFY_REPORT.BINARY_PATH_KEY] == (
        VERIFY_REPORT.CANONICAL_BINARY_PATH
    )
    assert (
        gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY]
        == hashlib.sha256(fresh.read_bytes()).hexdigest()
    )
    assert (
        gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY]
        != hashlib.sha256(stale.read_bytes()).hexdigest()
    )
    assert gate["counts"][VERIFY_REPORT.BINARY_COMMIT_KEY] == COMMIT
    assert shlex.quote(str(stale)) not in gate["command"]
    assert not fresh_log.exists()
    assert not stale_log.exists()


def test_verify_report_gate_rejects_clean_checkout_without_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A checkout whose portable build produced no binary fails the gate."""
    checkout = clean_checkout(tmp_path)
    monkeypatch.setattr(VERIFY_REPORT, "ROOT", checkout)
    capture_gate_steps(monkeypatch)
    gate = VERIFY_REPORT.run_pytest([], COMMIT)
    assert gate["result"] == FAIL
    assert gate["steps"][0]["result"] == FAIL
    assert gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY] == ""
