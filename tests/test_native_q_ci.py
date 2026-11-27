"""Offline tests for the native-q JUnit and evidence checker."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_native_q_results.py"
SPEC = importlib.util.spec_from_file_location("check_native_q_results", CHECKER)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

EVIDENCE = {
    "q_runtime_version": "4.1.0",
    "pykx_version": "4.2.0",
    "pytest_version": "9.2.0",
    "c_api": "compiled",
    "c_api_detail": "compile QUANT_HAS_KDBX=1=true; doctor c_api=available",
    "required": True,
}


def junit_xml(names, failures=(), skipped=()):
    """Build a small JUnit report with the requested test outcomes."""
    cases = []
    for name in names:
        outcome = ""
        if name in failures:
            outcome = "<failure message='failed'>traceback</failure>"
        elif name in skipped:
            outcome = "<skipped message='skipped'/>"
        cases.append(
            "<testcase classname='tests.test_kdbx' "
            f"name='{name}'>{outcome}</testcase>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<testsuites><testsuite name='pytest' tests='{len(names)}'>"
        + "".join(cases)
        + "</testsuite></testsuites>\n"
    )


def write_inputs(tmp_path, xml, evidence=None, *, write_evidence=True):
    """Write JUnit XML and evidence JSON into a temporary directory."""
    junit = tmp_path / "junit.xml"
    junit.write_text(xml, encoding="utf-8")
    evidence_path = tmp_path / "evidence.json"
    if write_evidence:
        payload = EVIDENCE if evidence is None else evidence
        evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    return junit, evidence_path


def run_checker_paths(junit, evidence_path, *, required=True):
    """Invoke the checker as a process with an explicit evidence policy."""
    env = dict(os.environ)
    env["QUANT_NATIVE_Q_REQUIRED"] = "1" if required else "0"
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--junit",
            str(junit),
            "--evidence",
            str(evidence_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def run_checker(tmp_path, xml, evidence=None, *, required=True):
    """Write inputs and run the checker against them."""
    junit, evidence_path = write_inputs(tmp_path, xml, evidence)
    return run_checker_paths(junit, evidence_path, required=required)


def test_pass_case(tmp_path):
    """A complete, executed native-q run with evidence is accepted."""
    result = run_checker(
        tmp_path, junit_xml(checker.REQUIRED_INTEGRATION_TESTS)
    )
    assert result.returncode == 0, result.stderr
    assert "native-q evidence check passed" in result.stdout
    assert f"tests={len(checker.REQUIRED_INTEGRATION_TESTS)}" in result.stdout


def test_zero_tests(tmp_path):
    """An empty JUnit report is rejected even with valid evidence."""
    result = run_checker(tmp_path, junit_xml([]))
    assert result.returncode == 1
    assert "no tests executed" in result.stderr


def test_skipped_required_test(tmp_path):
    """A skipped integration test fails the required tier."""
    names = checker.REQUIRED_INTEGRATION_TESTS
    result = run_checker(tmp_path, junit_xml(names, skipped=(names[0],)))
    assert result.returncode == 1
    assert "skipped=1" in result.stderr


def test_failures(tmp_path):
    """A failing integration test fails the checker."""
    names = checker.REQUIRED_INTEGRATION_TESTS
    result = run_checker(tmp_path, junit_xml(names, failures=(names[0],)))
    assert result.returncode == 1
    assert "failures=1" in result.stderr


def test_missing_file(tmp_path):
    """A missing JUnit report fails closed."""
    junit, evidence_path = write_inputs(tmp_path, junit_xml([]))
    junit.unlink()
    result = run_checker_paths(junit, evidence_path)
    assert result.returncode == 1
    assert "cannot read JUnit report" in result.stderr


def test_malformed_xml(tmp_path):
    """Truncated JUnit XML fails closed."""
    result = run_checker(tmp_path, "<testsuites><testsuite>")
    assert result.returncode == 1
    assert "malformed JUnit XML" in result.stderr


def test_missing_runtime_evidence(tmp_path):
    """Evidence without a q runtime version fails closed."""
    evidence = dict(EVIDENCE)
    del evidence["q_runtime_version"]
    result = run_checker(
        tmp_path, junit_xml(checker.REQUIRED_INTEGRATION_TESTS), evidence
    )
    assert result.returncode == 1
    assert "missing runtime evidence: q_runtime_version" in result.stderr


def test_skips_allowed_when_not_required(tmp_path):
    """Skips are tolerated only when the required policy is unset."""
    names = checker.REQUIRED_INTEGRATION_TESTS
    result = run_checker(
        tmp_path, junit_xml(names, skipped=(names[-1],)), required=False
    )
    assert result.returncode == 0, result.stderr
