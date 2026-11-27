#!/usr/bin/env python3
"""Offline tests for the versioned verification report and native-q matrix."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
GATED = VERIFY_REPORT.GATED
NOT_APPLICABLE = VERIFY_REPORT.NOT_APPLICABLE
RELEASE_SCOPE = VERIFY_REPORT.RELEASE_SCOPE
PERSISTENCE_SCOPE = VERIFY_REPORT.PERSISTENCE_SCOPE
REQUIRED_GATES = VERIFY_REPORT.REQUIRED_GATES
NATIVE_NAME = "Native KDB-X recovery (gated tier)"
NATIVE_STEP = VERIFY_REPORT.NATIVE_TEST_STEP
REQUIRED_NAMES = (
    "core build and tests",
    "ASan/UBSan",
    "Style contract (reference guides)",
    "Release assurance (offline)",
)
ALLOWED_STATUSES = {PASS, FAIL, GATED, NOT_APPLICABLE}
COMMIT = "abc123def456"
FRESH_BINARY = b"fresh build-ci contract binary\n"
STALE_BINARY = b"stale build contract binary\n"
SKIP_ID = "tests/test_native_q_ci.py::test_skipped_case"
OTHER_SKIP_ID = "tests/test_native_q_ci.py::test_other_case"
APPROVED_REASON = "governed fixture unavailable"
RUN_ID = 12345
RUN_URL = "https://github.com/siebengestirn/quant/actions/runs/12345"
OMIT = object()
NATIVE_LOG = f"{VERIFY_REPORT.NATIVE_TEST_FILE} tests 8 passed in 0.1s"
SKIPPED_LOG = (
    f"{VERIFY_REPORT.NATIVE_TEST_FILE} tests 6 passed, 2 skipped in 0.1s"
)
ZERO_LOG = "no tests ran in 0.05s"
CONCLUSION_CASES = [
    ("skipped", GATED, FAIL),
    ("success", PASS, PASS),
    ("failure", FAIL, FAIL),
    ("cancelled", FAIL, FAIL),
    ("timed_out", FAIL, FAIL),
    ("action_required", FAIL, FAIL),
    ("unknown", FAIL, FAIL),
    (None, FAIL, FAIL),
    ({"unexpected": "payload"}, FAIL, FAIL),
    (17, FAIL, FAIL),
    ("surprise", FAIL, FAIL),
]
CONCLUSION_IDS = [
    "skipped",
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "unknown",
    "missing",
    "malformed-object",
    "malformed-number",
    "malformed-string",
]


def make_binary_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    executable: bool = True,
    fresh: bool = True,
) -> tuple[Path, Path]:
    """Build an isolated checkout with a fresh CI and a stale build binary."""
    root = tmp_path / "checkout"
    build_ci = root / "build-ci"
    build_ci.mkdir(parents=True)
    binary = build_ci / "quant_contract"
    if fresh:
        binary.write_bytes(FRESH_BINARY)
        binary.chmod(0o755 if executable else 0o644)
    stale = root / "build" / "quant_contract"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(STALE_BINARY)
    stale.chmod(0o755)
    monkeypatch.setattr(VERIFY_REPORT, "ROOT", root)
    return binary, stale


def make_jobs(
    native_conclusion: object = "success",
    required_conclusion: object = "success",
    *,
    native_steps: bool = True,
    native_enabled: bool | None = None,
) -> list[dict]:
    """Build a complete hosted job list for offline gate tests."""
    jobs = [
        {
            "name": name,
            "conclusion": required_conclusion,
            "databaseId": number,
        }
        for number, name in enumerate(REQUIRED_NAMES, 1)
    ]
    if native_conclusion is not OMIT:
        job = {
            "name": NATIVE_NAME,
            "conclusion": native_conclusion,
            "databaseId": 99,
        }
        if (
            native_steps
            and VERIFY_REPORT.conclusion_value(native_conclusion) == "success"
        ):
            job["steps"] = [{"name": NATIVE_STEP, "conclusion": "success"}]
        if native_enabled is not None:
            job["enabled"] = native_enabled
        jobs.append(job)
    return jobs


def build_gate(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[dict],
    commit: str = COMMIT,
    head_sha: str = COMMIT,
    run_conclusion: str = "success",
    expect_native: bool = True,
    scope: str = RELEASE_SCOPE,
    native_log: str = NATIVE_LOG,
) -> dict:
    """Run the hosted gate against synthetic offline gh responses."""
    run = {
        "databaseId": RUN_ID,
        "headSha": head_sha,
        "conclusion": run_conclusion,
        "url": RUN_URL,
    }

    def fake_step_result(name: str, argv: list[str], env=None) -> dict:
        del env
        if "api" in argv:
            output = native_log
        elif "list" in argv:
            output = json.dumps([run])
        else:
            output = json.dumps({"jobs": jobs})
        return {
            "name": name,
            "command": " ".join(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": output,
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", fake_step_result)
    return VERIFY_REPORT.hosted_gate(
        commit, expect_native=expect_native, scope=scope
    )


@pytest.mark.parametrize("name", [NATIVE_NAME, *REQUIRED_NAMES])
@pytest.mark.parametrize(
    ("conclusion", "native_status", "required_status"),
    CONCLUSION_CASES,
    ids=CONCLUSION_IDS,
)
def test_every_conclusion_maps_explicitly(
    name: str,
    conclusion: object,
    native_status: str,
    required_status: str,
) -> None:
    """Every conclusion state has a defined, allowed status per job type."""
    expected = native_status if name == NATIVE_NAME else required_status
    status = VERIFY_REPORT.map_job_conclusion(name, conclusion)
    assert status == expected
    assert status in ALLOWED_STATUSES


@pytest.mark.parametrize("conclusion", ["skipped", "failure", None])
def test_required_job_needs_success(conclusion: object) -> None:
    """A skipped or failed required job is never a pass."""
    for name in REQUIRED_NAMES:
        status = VERIFY_REPORT.map_job_conclusion(name, conclusion)
        assert status == FAIL


@pytest.mark.parametrize("conclusion", ["success", "skipped", None])
@pytest.mark.parametrize("name", ["", "unlisted job", "core build and tests "])
def test_unknown_job_never_passes(name: str, conclusion: object) -> None:
    """Unrecognized job names never produce a PASS status."""
    status = VERIFY_REPORT.map_job_conclusion(name, conclusion)
    assert status == FAIL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" Skipped ", "skipped"), ("SUCCESS", "success"), (None, ""), ({}, "")],
)
def test_conclusion_value(raw: object, expected: str) -> None:
    """Conclusion tokens are normalized without accepting malformed data."""
    assert VERIFY_REPORT.conclusion_value(raw) == expected


def test_map_job_records_each_field() -> None:
    """Name, raw conclusion, mapped status and database ID are retained."""
    record = VERIFY_REPORT.map_job(
        {"name": NATIVE_NAME, "conclusion": "skipped", "databaseId": 777}
    )
    assert record == {
        "name": NATIVE_NAME,
        "conclusion": "skipped",
        "result": GATED,
        "database_id": 777,
        "steps": [],
        "enabled_hint": None,
    }


def test_map_job_keeps_malformed_raw_conclusion() -> None:
    """A malformed conclusion is reported verbatim but maps to FAIL."""
    payload = {"status": "success"}
    record = VERIFY_REPORT.map_job({"name": NATIVE_NAME, "conclusion": payload})
    assert record["conclusion"] is payload
    assert record["result"] == FAIL


@pytest.mark.parametrize("output", ["[]", "{}", '{"jobs": []}', "not json"])
def test_parse_jobs_rejects_bad_response(output: str) -> None:
    """Empty or malformed job responses are rejected."""
    with pytest.raises(ValueError):
        VERIFY_REPORT.parse_jobs(output)


@pytest.mark.parametrize(
    ("conclusion", "expected_status", "expected_gate"),
    [
        ("skipped", GATED, PASS),
        ("success", PASS, PASS),
        ("failure", FAIL, FAIL),
        ("cancelled", FAIL, FAIL),
        ("timed_out", FAIL, FAIL),
        ("action_required", FAIL, FAIL),
        ("unknown", FAIL, FAIL),
        (None, FAIL, FAIL),
        ({"bad": True}, FAIL, FAIL),
        (23, FAIL, FAIL),
        ("surprise", FAIL, FAIL),
    ],
    ids=CONCLUSION_IDS,
)
def test_hosted_gate_native_conclusions(
    monkeypatch: pytest.MonkeyPatch,
    conclusion: object,
    expected_status: str,
    expected_gate: str,
) -> None:
    """Only a skipped or evidenced successful native job is accepted."""
    gate = build_gate(monkeypatch, make_jobs(conclusion))
    assert gate["counts"]["native_q_conclusion"] == conclusion
    assert gate["counts"]["native_q_result"] == expected_status
    assert gate["result"] == expected_gate


def test_skipped_native_job_is_not_counted_as_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped native job is gated and excluded from successful counts."""
    gate = build_gate(monkeypatch, make_jobs("skipped"))
    counts = gate["counts"]
    assert gate["result"] == PASS
    assert counts["native_q_result"] == GATED
    assert counts["jobs_skipped"] == 1
    assert counts["jobs_gated"] == 1
    assert counts["jobs_passed"] == 4
    assert counts["jobs_failed"] == 0
    native = gate["native_q"]
    assert native["enabled"] is False
    assert native["conclusion"] == "skipped"
    assert native["result"] == GATED
    assert native["tests_executed"] == 0
    assert native["tests_skipped"] == 0
    assert native["tests_failed"] == 0
    assert native["evidence"]["test_step_executed"] is False
    assert "not enabled" in native["reason"]
    assert native["database_id"] == 99


def test_successful_native_job_counts_as_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful job with executed, unskipped integration tests passes."""
    gate = build_gate(monkeypatch, make_jobs("success"))
    counts = gate["counts"]
    native = gate["native_q"]
    assert gate["result"] == PASS
    assert counts["native_q_result"] == PASS
    assert counts["jobs_passed"] == 5
    assert counts["jobs_skipped"] == 0
    assert counts["jobs_gated"] == 0
    assert counts["jobs_failed"] == 0
    assert native["enabled"] is True
    assert native["tests_executed"] == 8
    assert native["tests_skipped"] == 0
    assert native["tests_failed"] == 0
    assert "executed" in native["reason"]
    assert native["evidence"]["test_step_executed"] is True


@pytest.mark.parametrize(
    ("log", "skipped"),
    [(ZERO_LOG, 0), (SKIPPED_LOG, 2)],
    ids=["zero-tests", "skipped-tests"],
)
def test_native_success_needs_executed_unskipped_tests(
    monkeypatch: pytest.MonkeyPatch, log: str, skipped: int
) -> None:
    """Success without executed tests, or with skips, is a failure."""
    gate = build_gate(monkeypatch, make_jobs("success"), native_log=log)
    native = gate["native_q"]
    assert gate["result"] == FAIL
    assert native["result"] == FAIL
    assert native["tests_skipped"] == skipped
    assert native["reason"]


@pytest.mark.parametrize(
    ("enabled", "log", "expected"),
    [
        (True, NATIVE_LOG, PASS),
        (False, NATIVE_LOG, FAIL),
        (True, ZERO_LOG, FAIL),
    ],
    ids=["enabled-pass", "disabled-failure", "enabled-but-no-tests"],
)
def test_explicit_native_enablement(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    log: str,
    expected: str,
) -> None:
    """Explicit enablement is honoured but does not replace test evidence."""
    jobs = make_jobs("success", native_enabled=enabled)
    gate = build_gate(monkeypatch, jobs, native_log=log)
    assert gate["native_q"]["enabled"] is enabled
    assert gate["native_q"]["result"] == expected
    assert gate["result"] == expected


def test_explicitly_enabled_skipped_job_is_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled tier cannot hide behind a skipped hosted job."""
    jobs = make_jobs("skipped", native_enabled=True)
    gate = build_gate(monkeypatch, jobs)
    assert gate["native_q"]["result"] == FAIL
    assert gate["native_q"]["reason"]
    assert gate["result"] == FAIL


@pytest.mark.parametrize(
    ("scope", "expected"),
    [(RELEASE_SCOPE, GATED), (PERSISTENCE_SCOPE, FAIL)],
)
def test_gated_native_requires_permissive_scope(
    monkeypatch: pytest.MonkeyPatch, scope: str, expected: str
) -> None:
    """A gated native tier is only valid in the portable research scope."""
    gate = build_gate(monkeypatch, make_jobs("skipped"), scope=scope)
    assert gate["counts"]["native_q_result"] == expected
    assert gate["result"] == (PASS if expected == GATED else FAIL)


def test_persistence_scope_requires_executed_native_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persistence scope accepts a native pass with full evidence."""
    gate = build_gate(
        monkeypatch, make_jobs("success"), scope=PERSISTENCE_SCOPE
    )
    assert gate["result"] == PASS
    assert gate["native_q"]["result"] == PASS
    gated = build_gate(
        monkeypatch, make_jobs("skipped"), scope=PERSISTENCE_SCOPE
    )
    assert gated["result"] == FAIL


def test_success_without_test_step_is_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful job with no integration test step ran no tests."""
    gate = build_gate(monkeypatch, make_jobs("success", native_steps=False))
    assert gate["native_q"]["result"] == GATED
    assert gate["native_q"]["tests_executed"] == 0
    assert gate["result"] == PASS


@pytest.mark.parametrize("name", REQUIRED_NAMES)
@pytest.mark.parametrize(
    "conclusion",
    ["skipped", "failure", "cancelled", "timed_out", "unknown", None],
)
def test_required_job_failure_rejects_run(
    monkeypatch: pytest.MonkeyPatch, name: str, conclusion: object
) -> None:
    """Any non-success required job rejects the hosted run."""
    jobs = make_jobs()
    for job in jobs:
        if job["name"] == name:
            job["conclusion"] = conclusion
    gate = build_gate(monkeypatch, jobs)
    assert gate["result"] == FAIL
    assert gate["counts"]["jobs_failed"] >= 1


def test_missing_native_job_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expected native-q job that is absent is a failure."""
    gate = build_gate(monkeypatch, make_jobs(OMIT))
    assert gate["result"] == FAIL
    assert gate["counts"]["missing_jobs"] == ["native-q"]
    assert gate["counts"]["native_q_result"] == FAIL
    assert gate["counts"]["native_q_conclusion"] is None
    assert gate["native_q"]["result"] == FAIL
    assert "missing" in gate["native_q"]["reason"]


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_missing_required_job_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Every required job must be present in the hosted run."""
    jobs = [job for job in make_jobs() if job["name"] != name]
    gate = build_gate(monkeypatch, jobs)
    assert gate["result"] == FAIL
    assert name in gate["counts"]["missing_jobs"]


def test_native_not_expected_is_not_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent tier is NOT_APPLICABLE when native-q is not expected."""
    gate = build_gate(monkeypatch, make_jobs(OMIT), expect_native=False)
    assert gate["result"] == PASS
    assert gate["counts"]["native_q_result"] == NOT_APPLICABLE
    assert gate["counts"]["missing_jobs"] == []


def test_head_sha_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hosted run on a different commit cannot verify the report."""
    gate = build_gate(monkeypatch, make_jobs("skipped"), head_sha="deadbeef")
    assert gate["counts"]["head_sha_match"] is False
    assert gate["result"] == FAIL


def test_empty_commit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty report commit cannot match a hosted run."""
    gate = build_gate(monkeypatch, make_jobs("skipped"), commit="")
    assert gate["counts"]["head_sha_match"] is False
    assert gate["result"] == FAIL


def test_run_id_url_and_jobs_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report records the run identity and every job individually."""
    gate = build_gate(monkeypatch, make_jobs("skipped"))
    assert gate["counts"]["run_id"] == RUN_ID
    assert gate["counts"]["run_url"] == RUN_URL
    assert gate["run"] == {
        "id": RUN_ID,
        "url": RUN_URL,
        "head_sha": COMMIT,
        "conclusion": "success",
    }
    assert [
        (
            job["name"],
            job["conclusion"],
            job["result"],
            job["database_id"],
        )
        for job in gate["jobs"]
    ] == [
        ("core build and tests", "success", PASS, 1),
        ("ASan/UBSan", "success", PASS, 2),
        ("Style contract (reference guides)", "success", PASS, 3),
        ("Release assurance (offline)", "success", PASS, 4),
        (NATIVE_NAME, "skipped", GATED, 99),
    ]


def test_malformed_jobs_response_rejects_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed hosted response is a recorded gate failure."""
    gate = build_gate(monkeypatch, [])
    assert gate["result"] == FAIL
    assert "error" in gate["counts"]


def test_report_has_versioned_schema_and_all_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run writes the schema version, scope and every required gate."""
    gates = [
        {
            "name": name,
            "command": "recorded command",
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "steps": [],
        }
        for name in sorted(REQUIRED_GATES)
    ]
    monkeypatch.setattr(VERIFY_REPORT, "run_gates", lambda commit, scope: gates)
    monkeypatch.setattr(VERIFY_REPORT, "git_commit", lambda: COMMIT)
    monkeypatch.setattr(VERIFY_REPORT, "toolchain", lambda: {"python": "3"})
    output = tmp_path / "report.json"
    assert VERIFY_REPORT.run_report(output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == VERIFY_REPORT.REPORT_SCHEMA_VERSION
    assert report["release_scope"] == RELEASE_SCOPE
    assert {gate["name"] for gate in report["gates"]} == set(REQUIRED_GATES)
    assert report["overall"] == PASS


@pytest.mark.parametrize(
    ("factory", "name", "filename"),
    [
        (VERIFY_REPORT.run_w1_tests, "w1", "tests/test_instrument_master.py"),
        (
            VERIFY_REPORT.run_release_bundle_tests,
            "release_bundle_tests",
            "tests/test_release_bundle.py",
        ),
        (
            VERIFY_REPORT.run_verify_report_tests,
            "verify_report_tests",
            "tests/test_verify_report.py",
        ),
        (
            VERIFY_REPORT.run_native_q_checker_tests,
            "native_q_checker_tests",
            "tests/test_native_q_ci.py",
        ),
        (
            VERIFY_REPORT.run_ci_security_tests,
            "ci_security_tests",
            "tests/test_ci_security.py",
        ),
    ],
)
def test_new_pytest_gates_reset_addopts(
    monkeypatch: pytest.MonkeyPatch, factory, name: str, filename: str
) -> None:
    """New suites run with empty addopts and report their test counts."""
    calls: list[list[str]] = []

    def fake_step_result(gate_name: str, argv: list[str], env=None) -> dict:
        del env, gate_name
        calls.append(list(argv))
        return {
            "name": "pytest",
            "command": VERIFY_REPORT.command_text(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": "11 passed in 0.1s",
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", fake_step_result)
    gate = factory()
    assert gate["name"] == name
    assert gate["result"] == PASS
    assert gate["counts"]["passed"] == 11
    argv = calls[-1]
    assert filename in argv
    assert argv[argv.index("-o") + 1] == "addopts="
    assert "-q" in argv
    assert "-o addopts='' -q" in gate["command"]


def test_verify_report_gate_does_not_recurse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the report test gate never invokes another report run."""

    def reject_run(*args, **kwargs) -> None:
        raise AssertionError("verify_report run must not be called")

    monkeypatch.setattr(VERIFY_REPORT, "run_report", reject_run)
    monkeypatch.setattr(
        VERIFY_REPORT,
        "step_result",
        lambda name, argv, env=None: {
            "name": name,
            "command": " ".join(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": "80 passed in 1.0s",
        },
    )
    gate = VERIFY_REPORT.run_verify_report_tests()
    assert gate["name"] == "verify_report_tests"
    assert gate["counts"]["passed"] == 80


def test_show_report_renders_recorded_native_reason(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The summary prints the report's explanation, not a fixed claim."""
    report = {
        "commit": COMMIT,
        "branch": "main",
        "schema_version": 1,
        "release_scope": RELEASE_SCOPE,
        "overall": PASS,
        "gates": [],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        VERIFY_REPORT,
        "gate_named",
        lambda rep, name: {
            "native_q": {"result": PASS, "reason": "recorded explanation"},
        },
    )
    try:
        assert VERIFY_REPORT.show_report(path) == 0
    finally:
        monkeypatch.undo()
    output = capsys.readouterr().out
    assert "Native q tier: PASS (recorded explanation)" in output
    assert "GATED / NOT RUN" not in output


def fake_pytest_step(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    """Capture pytest steps while returning a fixed synthetic output."""

    def step(name: str, argv: list[str], env=None) -> dict:
        del env
        return {
            "name": name,
            "command": VERIFY_REPORT.command_text(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": output,
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", step)


def test_parse_pytest_records_every_outcome() -> None:
    """All five counts are parsed, with singular errors normalized."""
    output = (
        f"SKIPPED [1] {SKIP_ID}:42: {APPROVED_REASON}\n"
        "3 passed, 1 failed, 2 errors, 1 skipped in 0.1s"
    )
    assert VERIFY_REPORT.parse_pytest(output) == {
        "passed": 3,
        "failed": 1,
        "errors": 2,
        "skipped": 1,
        "total": 7,
    }
    assert VERIFY_REPORT.parse_pytest("2 passed, 1 error in 0.1s") == {
        "passed": 2,
        "failed": 0,
        "errors": 1,
        "skipped": 0,
        "total": 3,
    }


def test_native_q_checker_gate_is_required_and_command_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checker suite is mandatory and runs the exact pinned command."""
    calls: list[list[str]] = []
    fake_pytest_step(monkeypatch, "8 passed in 0.1s")
    original = VERIFY_REPORT.step_result

    def recording_step(name: str, argv: list[str], env=None) -> dict:
        calls.append(list(argv))
        return original(name, argv, env)

    monkeypatch.setattr(VERIFY_REPORT, "step_result", recording_step)
    gate = VERIFY_REPORT.run_native_q_checker_tests()
    assert "native_q_checker_tests" in REQUIRED_GATES
    assert gate["name"] == "native_q_checker_tests"
    assert gate["result"] == PASS
    assert calls[0] == [
        "python3",
        "-m",
        "pytest",
        "tests/test_native_q_ci.py",
        "-o",
        "addopts=",
        "-q",
    ]
    assert gate["command"] == (
        "python3 -m pytest tests/test_native_q_ci.py -o addopts='' -q"
    )
    assert gate["counts"] == {
        "passed": 8,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "total": 8,
    }


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("8 passed in 0.1s", PASS),
        ("7 passed, 1 skipped in 0.1s", FAIL),
        ("7 passed, 1 failed in 0.1s", FAIL),
        ("7 passed, 1 error in 0.1s", FAIL),
        ("no tests ran in 0.05s", FAIL),
    ],
    ids=["clean", "hidden-skip", "failure", "error", "zero-tests"],
)
def test_python_gate_policy_requires_clean_executed_tests(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: str
) -> None:
    """A Python gate passes only on executed, clean, unskipped tests."""
    fake_pytest_step(monkeypatch, output)
    gate = VERIFY_REPORT.run_native_q_checker_tests()
    assert gate["result"] == expected
    assert set(VERIFY_REPORT.PYTEST_COUNT_KEYS) <= gate["counts"].keys()


def test_allowlisted_skip_is_accepted_with_test_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A documented skip with a matching test ID does not fail the gate."""
    allowed = [{"test_id": SKIP_ID, "reason": APPROVED_REASON}]
    output = (
        f"SKIPPED [1] {SKIP_ID}:42: {APPROVED_REASON}\n"
        "7 passed, 1 skipped in 0.1s"
    )
    fake_pytest_step(monkeypatch, output)
    gate = VERIFY_REPORT.run_native_q_checker_tests(allowed)
    assert gate["result"] == PASS
    assert gate["counts"]["skipped"] == 1
    assert gate["allowed_skips"] == allowed


def test_unapproved_hidden_skip_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skip whose ID is absent from the allowlist fails the gate."""
    allowed = [{"test_id": SKIP_ID, "reason": APPROVED_REASON}]
    output = (
        f"SKIPPED [1] {OTHER_SKIP_ID}:42: {APPROVED_REASON}\n"
        "7 passed, 1 skipped in 0.1s"
    )
    fake_pytest_step(monkeypatch, output)
    gate = VERIFY_REPORT.run_native_q_checker_tests(allowed)
    assert gate["result"] == FAIL
    assert gate["counts"]["skipped"] == 1


def test_hidden_skip_outside_suite_is_not_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlist entry for another suite cannot approve a hidden skip."""
    allowed = [
        {"test_id": "tests/test_research.py::test_other", "reason": "other"}
    ]
    fake_pytest_step(monkeypatch, "7 passed, 1 skipped in 0.1s")
    gate = VERIFY_REPORT.run_native_q_checker_tests(allowed)
    assert gate["result"] == FAIL


def test_skip_environment_requests_ids_without_changing_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip IDs are captured via the environment, not the pinned argv."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p no:cacheprovider")
    env = VERIFY_REPORT.pytest_environment()
    assert "-rs" in env["PYTEST_ADDOPTS"]
    assert "-p no:cacheprovider" in env["PYTEST_ADDOPTS"]


@pytest.mark.parametrize(
    "entry",
    [
        {"test_id": SKIP_ID},
        {"reason": APPROVED_REASON},
        {"test_id": SKIP_ID, "reason": 7},
        {"test_id": SKIP_ID, "reason": ""},
        {"test_id": SKIP_ID, "reason": APPROVED_REASON, "extra": 1},
        "not-a-record",
    ],
)
def test_malformed_allowlist_records_are_rejected(entry: object) -> None:
    """Allowlist entries must carry a test ID and a non-empty reason."""
    with pytest.raises(ValueError):
        VERIFY_REPORT.allowed_skip_entries([entry])


def test_duplicate_allowlist_entries_are_rejected() -> None:
    """The same skip cannot be approved twice."""
    entry = {"test_id": SKIP_ID, "reason": APPROVED_REASON}
    with pytest.raises(ValueError, match="duplicate"):
        VERIFY_REPORT.allowed_skip_entries([entry, dict(entry)])


def test_report_config_is_embedded_and_passed_to_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A JSON config is validated, embedded and shared with every gate."""
    allowed = [{"test_id": SKIP_ID, "reason": APPROVED_REASON}]
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"allowed_skips": allowed}), encoding="utf-8"
    )
    seen: dict = {}

    def fake_gates(commit: str, scope: str, config: dict) -> list:
        del commit, scope
        seen["config"] = config
        return []

    monkeypatch.setattr(VERIFY_REPORT, "run_gates", fake_gates)
    monkeypatch.setattr(VERIFY_REPORT, "git_commit", lambda: COMMIT)
    monkeypatch.setattr(VERIFY_REPORT, "toolchain", lambda: {"python": "3"})
    output = tmp_path / "report.json"
    assert VERIFY_REPORT.run_report(output, config=config_path) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["config"] == {"allowed_skips": allowed}
    assert seen["config"] == report["config"]


def test_run_gates_includes_every_required_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report always contains the native-q checker gate."""
    mapping = {
        "run_portable": "portable_cpp",
        "run_sanitizer": "sanitizer",
        "run_pytest": "portable_pytest",
        "run_w1_tests": "w1",
        "run_release_bundle_tests": "release_bundle_tests",
        "run_verify_report_tests": "verify_report_tests",
        "run_native_q_checker_tests": "native_q_checker_tests",
        "run_ci_security_tests": "ci_security_tests",
        "run_style": "style",
        "run_freshness": "state_freshness",
        "run_audit": "terminology_audit",
        "hosted_gate": "hosted_ci",
    }

    def make_fake(name: str):
        def fake(*args, **kwargs) -> dict:
            del args, kwargs
            return {"name": name, "result": PASS, "counts": {}}

        return fake

    for runner, gate_name in mapping.items():
        monkeypatch.setattr(VERIFY_REPORT, runner, make_fake(gate_name))
    gates = VERIFY_REPORT.run_gates(COMMIT)
    assert {gate["name"] for gate in gates} == set(REQUIRED_GATES)
    assert len(gates) == len(REQUIRED_GATES)


def capture_pytest_steps(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> list[tuple[list[str], dict[str, str]]]:
    """Capture pytest argv and environment while returning fixed output."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def step(name: str, argv: list[str], env=None) -> dict:
        del name
        calls.append((list(argv), dict(env or {})))
        return {
            "name": "pytest",
            "command": VERIFY_REPORT.command_text(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": output,
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", step)
    return calls


def test_portable_pytest_gate_sets_canonical_binary_env_and_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The portable gate routes tests to the fresh build-ci binary."""
    binary, stale = make_binary_checkout(monkeypatch, tmp_path)
    monkeypatch.setenv(VERIFY_REPORT.BINARY_ENV, str(stale))
    calls = capture_pytest_steps(monkeypatch, "12 passed in 0.1s")
    gate = VERIFY_REPORT.run_pytest([], COMMIT)
    assert gate["result"] == PASS
    assert gate["counts"]["passed"] == 12
    assert gate["counts"][VERIFY_REPORT.BINARY_PATH_KEY] == (
        VERIFY_REPORT.CANONICAL_BINARY_PATH
    )
    assert (
        gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY]
        == hashlib.sha256(FRESH_BINARY).hexdigest()
    )
    assert gate["counts"][VERIFY_REPORT.BINARY_BUILD_DIR_KEY] == (
        VERIFY_REPORT.CANONICAL_BUILD_DIR
    )
    assert gate["counts"][VERIFY_REPORT.BINARY_COMMIT_KEY] == COMMIT
    assert calls[0][1][VERIFY_REPORT.BINARY_ENV] == str(binary)
    assert f"{VERIFY_REPORT.BINARY_ENV}=" in gate["command"]
    assert str(stale) not in gate["command"]
    assert gate["steps"][0]["result"] == PASS


@pytest.mark.parametrize(
    ("fresh", "executable", "expected"),
    [(False, True, FAIL), (True, False, FAIL), (True, True, PASS)],
    ids=["missing", "not-executable", "executable"],
)
def test_portable_pytest_gate_validates_binary_presence_and_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fresh: bool,
    executable: bool,
    expected: str,
) -> None:
    """A missing or non-executable canonical binary fails the gate."""
    make_binary_checkout(
        monkeypatch, tmp_path, fresh=fresh, executable=executable
    )
    capture_pytest_steps(monkeypatch, "12 passed in 0.1s")
    gate = VERIFY_REPORT.run_pytest([], COMMIT)
    assert gate["result"] == expected
    assert gate["steps"][0]["result"] == expected
    if expected == FAIL:
        assert gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY] == ""
    else:
        assert gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY]


def test_stale_binary_digest_is_never_recorded_as_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded digest comes from build-ci, never from build/."""
    _binary, stale = make_binary_checkout(monkeypatch, tmp_path)
    monkeypatch.setenv(VERIFY_REPORT.BINARY_ENV, str(stale))
    capture_pytest_steps(monkeypatch, "12 passed in 0.1s")
    gate = VERIFY_REPORT.run_pytest([], COMMIT)
    stale_digest = hashlib.sha256(stale.read_bytes()).hexdigest()
    assert gate["counts"][VERIFY_REPORT.BINARY_DIGEST_KEY] != stale_digest


def test_ci_security_gate_is_required_and_command_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CI security suite is mandatory and runs the pinned command."""
    calls = capture_pytest_steps(monkeypatch, "11 passed in 0.1s")
    gate = VERIFY_REPORT.run_ci_security_tests()
    assert "ci_security_tests" in REQUIRED_GATES
    assert gate["name"] == "ci_security_tests"
    assert gate["result"] == PASS
    assert calls[0][0] == [
        "python3",
        "-m",
        "pytest",
        "tests/test_ci_security.py",
        "-o",
        "addopts=",
        "-q",
    ]
    assert gate["command"] == (
        "python3 -m pytest tests/test_ci_security.py -o addopts='' -q"
    )
    assert gate["counts"] == {
        "passed": 11,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "total": 11,
    }


@pytest.mark.parametrize(
    ("output", "allowed", "expected"),
    [
        ("11 passed in 0.1s", (), PASS),
        (
            "SKIPPED [1] tests/test_ci_security.py::test_x:42: reason\n"
            "10 passed, 1 skipped in 0.1s",
            (),
            FAIL,
        ),
        (
            "SKIPPED [1] tests/test_ci_security.py::test_x:42: reason\n"
            "10 passed, 1 skipped in 0.1s",
            ({"test_id": "tests/test_ci_security.py::test_x", "reason": "ok"},),
            PASS,
        ),
    ],
    ids=["clean", "unapproved-skip", "approved-skip"],
)
def test_ci_security_gate_enforces_skip_policy(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    allowed: tuple[dict[str, str], ...],
    expected: str,
) -> None:
    """An unapproved skip in the CI security suite fails its gate."""
    capture_pytest_steps(monkeypatch, output)
    gate = VERIFY_REPORT.run_ci_security_tests(allowed)
    assert gate["result"] == expected
