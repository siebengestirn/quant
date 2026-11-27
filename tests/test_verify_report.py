#!/usr/bin/env python3
"""Offline tests for hosted job conclusion mapping in verify_report."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
NATIVE_NAME = "Native KDB-X recovery (gated tier)"
REQUIRED_NAMES = (
    "core build and tests",
    "ASan/UBSan",
    "Style contract (reference guides)",
)
ALLOWED_STATUSES = {PASS, FAIL, GATED, NOT_APPLICABLE}
COMMIT = "abc123def456"
RUN_ID = 12345
RUN_URL = "https://github.com/siebengestirn/quant/actions/runs/12345"
OMIT = object()
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


def make_jobs(
    native_conclusion: object = "success",
    required_conclusion: object = "success",
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
        jobs.append(
            {
                "name": NATIVE_NAME,
                "conclusion": native_conclusion,
                "databaseId": 99,
            }
        )
    return jobs


def build_gate(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[dict],
    commit: str = COMMIT,
    head_sha: str = COMMIT,
    run_conclusion: str = "success",
    expect_native: bool = True,
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
        output = (
            json.dumps([run]) if "list" in argv else json.dumps({"jobs": jobs})
        )
        return {
            "name": name,
            "command": " ".join(argv),
            "exit_code": 0,
            "counts": {},
            "result": PASS,
            "output": output,
        }

    monkeypatch.setattr(VERIFY_REPORT, "step_result", fake_step_result)
    return VERIFY_REPORT.hosted_gate(commit, expect_native=expect_native)


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
    """The gate accepts only a skipped or successful native-q job."""
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
    assert counts["jobs_passed"] == 3
    assert counts["jobs_failed"] == 0
    assert gate["native_q"] == {
        "name": NATIVE_NAME,
        "conclusion": "skipped",
        "result": GATED,
        "database_id": 99,
    }


def test_successful_native_job_counts_as_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful native job is a pass and is counted as one."""
    gate = build_gate(monkeypatch, make_jobs("success"))
    counts = gate["counts"]
    assert gate["result"] == PASS
    assert counts["native_q_result"] == PASS
    assert counts["jobs_passed"] == 4
    assert counts["jobs_skipped"] == 0
    assert counts["jobs_gated"] == 0
    assert counts["jobs_failed"] == 0


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
        (NATIVE_NAME, "skipped", GATED, 99),
    ]


def test_malformed_jobs_response_rejects_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed hosted response is a recorded gate failure."""
    gate = build_gate(monkeypatch, [])
    assert gate["result"] == FAIL
    assert "error" in gate["counts"]
