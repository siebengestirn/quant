"""Offline tests for post-job runner attestation via the GitHub API."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "attest_runner.py"
SPEC = importlib.util.spec_from_file_location("attest_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
attest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(attest)

RUN_ID = "45969369813"
JOB_ID = "123456789"
SHA = "a" * 40
OTHER_SHA = "b" * 40
STARTED = "2026-11-28T00:00:00Z"
COMPLETED = "2026-11-28T00:05:00Z"


def api_job(
    *, remove: tuple[str, ...] = (), **overrides: Any
) -> dict[str, Any]:
    """Build a valid workflow-job API payload with explicit overrides."""
    job: dict[str, Any] = {
        "id": int(JOB_ID),
        "run_id": int(RUN_ID),
        "name": "Native KDB-X recovery (gated tier)",
        "status": "completed",
        "conclusion": "success",
        "head_sha": SHA,
        "started_at": STARTED,
        "completed_at": COMPLETED,
        "runner_id": 98765,
        "runner_name": "quant-qualification-01",
        "runner_group_id": 42,
        "runner_group_name": "quant-native-q",
        "labels": ["self-hosted", "quant-native-q"],
    }
    job.update(overrides)
    for key in remove:
        job.pop(key, None)
    return job


def install_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    job: dict[str, Any] | None = None,
    jobs: Any = None,
    raw_job: Any = None,
) -> list[str]:
    """Stub gh_api so both API endpoints return controlled payloads."""
    calls: list[str] = []

    def fake_api(path: str, *_args: Any, **_kwargs: Any) -> Any:
        calls.append(path)
        if path.startswith("actions/runs/"):
            if jobs is not None:
                return jobs
            return {"total_count": 1, "jobs": [job if job else api_job()]}
        if path.startswith("actions/jobs/"):
            return job if raw_job is None else raw_job
        raise AssertionError(f"unexpected API path: {path}")

    monkeypatch.setattr(attest, "gh_api", fake_api)
    return calls


def run_attest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    job: dict[str, Any] | None = None,
    jobs: Any = None,
    raw_job: Any = None,
    expected_sha: str = SHA,
) -> tuple[int, Path, str, list[str]]:
    """Invoke the attestation CLI against stubbed API payloads."""
    calls = install_api(
        monkeypatch, job=job or api_job(), jobs=jobs, raw_job=raw_job
    )
    output = tmp_path / "runner-attestation.json"
    code = attest.main(
        [
            "attest",
            "--run-id",
            RUN_ID,
            "--job-id",
            JOB_ID,
            "--expected-sha",
            expected_sha,
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    return code, output, captured.err + captured.out, calls


def test_valid_api_job_is_attested_and_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An API job on the dedicated runner with the exact SHA is accepted."""
    code, output, text, calls = run_attest(tmp_path, monkeypatch, capsys)
    assert code == 0, text
    assert "runner attestation passed" in text
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run_id"] == RUN_ID
    assert payload["job_id"] == JOB_ID
    assert payload["observed"] == {
        "runner_id": 98765,
        "runner_name": "quant-qualification-01",
        "runner_labels": ["self-hosted", "quant-native-q"],
        "runner_group_id": 42,
        "runner_group_name": "quant-native-q",
        "head_sha": SHA,
    }
    lifecycle = payload["ephemeral_lifecycle"]
    assert lifecycle["job_id"] == JOB_ID
    assert lifecycle["started_at"] == STARTED
    assert lifecycle["completed_at"] == COMPLETED
    assert lifecycle["deregistration_verified"] is False
    assert lifecycle["deregistration_evidence"] == ""
    assert "external runner operator" in lifecycle["requirement"]
    assert payload["required_for_portable_research"] is False
    assert calls == [
        f"actions/runs/{RUN_ID}/jobs",
        f"actions/jobs/{JOB_ID}",
    ]


def test_attestation_is_canonical_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The evidence file is stable, sorted and newline-terminated."""
    _code, output, _text, _calls = run_attest(tmp_path, monkeypatch, capsys)
    written = output.read_text(encoding="utf-8")
    assert written == (
        json.dumps(json.loads(written), indent=2, sort_keys=True) + "\n"
    )


def test_missing_runner_group_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A job without an observed runner group cannot attest the runner."""
    code, _output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(remove=("runner_group_name",)),
    )
    assert code == 1
    assert "missing runner identity field: runner_group_name" in text


def test_wrong_runner_group_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A generic runner group is rejected even with the dedicated label."""
    code, _output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(runner_group_name="default"),
    )
    assert code == 1
    assert "runner group is default" in text


def test_missing_dedicated_label_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An API job without the dedicated label cannot attest the runner."""
    code, _output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(labels=["self-hosted"]),
    )
    assert code == 1
    assert "runner labels lack required label quant-native-q" in text


def test_sha_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An API job on a different commit cannot attest the expected SHA."""
    code, _output, text, _calls = run_attest(
        tmp_path, monkeypatch, capsys, expected_sha=OTHER_SHA
    )
    assert code == 1
    assert "head SHA mismatch" in text
    assert OTHER_SHA in text


def test_empty_jobs_response_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty jobs list leaves no job to attest."""
    code, output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        jobs={"total_count": 0, "jobs": []},
    )
    assert code == 1
    assert "not uniquely found" in text
    assert not output.exists()


def test_malformed_jobs_response_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A jobs response without a jobs array fails closed."""
    code, output, text, _calls = run_attest(
        tmp_path, monkeypatch, capsys, jobs=["not-a-jobs-object"]
    )
    assert code == 1
    assert "must contain a jobs array" in text
    assert not output.exists()


def test_non_object_job_response_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The workflow-job endpoint must return a JSON object."""
    code, output, text, _calls = run_attest(
        tmp_path, monkeypatch, capsys, raw_job="unexpected-string"
    )
    assert code == 1
    assert "must be a JSON object" in text
    assert not output.exists()


def test_job_from_other_run_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A job that belongs to another workflow run is rejected."""
    code, _output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(run_id=int(OTHER_SHA[:8], 16)),
    )
    assert code == 1
    assert "job run id does not match" in text


def test_missing_lifecycle_timestamps_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An incomplete job lifecycle cannot support ephemeral claims."""
    code, _output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(remove=("completed_at",)),
    )
    assert code == 1
    assert "missing lifecycle evidence: completed_at" in text


def test_deregistration_requires_explicit_api_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only an API deactivation field may mark deregistration as verified."""
    code, output, text, _calls = run_attest(
        tmp_path,
        monkeypatch,
        capsys,
        job=api_job(runner_deactivated_at=COMPLETED),
    )
    assert code == 0, text
    lifecycle = json.loads(output.read_text(encoding="utf-8"))[
        "ephemeral_lifecycle"
    ]
    assert lifecycle["deregistration_verified"] is True
    assert lifecycle["deregistration_evidence"] == COMPLETED


def test_gh_api_rejects_command_failure_and_malformed_json() -> None:
    """The gh wrapper fails closed on a bad exit code or broken JSON."""
    failed = subprocess.CompletedProcess(["gh"], 1, "", "auth required")
    malformed = subprocess.CompletedProcess(["gh"], 0, "{not json", "")

    def fake_runner(result: subprocess.CompletedProcess[str]):
        def run(*_args: Any, **_kwargs: Any):
            return result

        return run

    with pytest.raises(attest.AttestationError, match="failed: auth required"):
        attest.gh_api("actions/jobs/1", runner=fake_runner(failed))
    with pytest.raises(attest.AttestationError, match="malformed gh api JSON"):
        attest.gh_api("actions/jobs/1", runner=fake_runner(malformed))


def test_cli_invokes_gh_for_both_endpoints(tmp_path: Path) -> None:
    """The real CLI queries the gh executable for both API endpoints."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    jobs_file = tmp_path / "jobs.json"
    job_file = tmp_path / "job.json"
    jobs_file.write_text(
        json.dumps({"total_count": 1, "jobs": [api_job()]}), encoding="utf-8"
    )
    job_file.write_text(json.dumps(api_job()), encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"actions/runs/"*) cat "$QUANT_ATTEST_JOBS_FILE" ;;\n'
        '  *"actions/jobs/"*) cat "$QUANT_ATTEST_JOB_FILE" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    output = tmp_path / "attestation.json"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "GH_TOKEN": "stub-token",
        "QUANT_ATTEST_JOBS_FILE": str(jobs_file),
        "QUANT_ATTEST_JOB_FILE": str(job_file),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "attest",
            "--run-id",
            RUN_ID,
            "--job-id",
            JOB_ID,
            "--expected-sha",
            SHA,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["observed"]["runner_group_name"] == "quant-native-q"
    assert payload["observed"]["runner_id"] == 98765
