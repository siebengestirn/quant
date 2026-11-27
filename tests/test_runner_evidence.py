"""Offline tests for dedicated-runner evidence capture and validation."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_runner_evidence.py"
SPEC = importlib.util.spec_from_file_location("capture_runner_evidence", SCRIPT)
runner_evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner_evidence)

SHA = "a" * 40
OTHER_SHA = "b" * 40
LABELS = "self-hosted,quant-native-q"


def runner_env(**overrides: str) -> dict[str, str]:
    """Build a complete GitHub runner environment with explicit overrides."""
    env = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_SHA": SHA,
        "QUANT_EXPECTED_SHA": SHA,
        "RUNNER_NAME": "quant-qualification-01",
        "RUNNER_OS": "Linux",
        "RUNNER_LABELS": LABELS,
        "GITHUB_REPOSITORY": "siebengestirn/quant",
        "GITHUB_WORKFLOW": "QUANT CI",
        "GITHUB_JOB": "native-q",
        "QHOME": "/opt/kx/q",
    }
    env.update(overrides)
    return env


def run_capture(
    tmp_path: Path, env: dict[str, str], *, head_sha: str = SHA
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Invoke the capture script with an injected runner environment."""
    output = tmp_path / "runner-evidence.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(output),
            "--head-sha",
            head_sha,
            "--q-version",
            "4.1.0",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def read_payload(path: Path) -> dict[str, Any]:
    """Decode a written evidence file for assertions."""
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def test_valid_evidence_is_captured_and_accepted(tmp_path: Path) -> None:
    """A dedicated runner on the exact push SHA produces valid evidence."""
    result, output = run_capture(tmp_path, runner_env())
    assert result.returncode == 0, result.stderr
    assert "runner evidence check passed" in result.stdout
    payload = read_payload(output)
    assert payload["event_name"] == "push"
    assert payload["head_sha"] == SHA
    assert payload["expected_sha"] == SHA
    assert payload["runner_labels"] == ["self-hosted", "quant-native-q"]
    assert payload["runner_labels_source"] == (
        "workflow-declared; not API-observed"
    )
    assert payload["runner_group_expected"] == "quant-native-q"
    assert "runner_group" not in payload
    assert payload["identity_attestation_required"] is True
    assert payload["identity_attestation_path"] == (
        "data/assurance/runner-attestation.json"
    )
    assert payload["qhome"] == "/opt/kx/q"
    assert payload["q_version"] == "4.1.0"
    assert runner_evidence.validate(payload) == []


def test_evidence_records_runner_identity_and_scope() -> None:
    """All mandated identity fields are present and portable scope is false."""
    evidence = runner_evidence.capture(
        runner_env(), q_version="4.1.0", head_sha=SHA
    )
    for field in runner_evidence.EVIDENCE_FIELDS:
        assert str(evidence[field]).strip(), field
    assert evidence["runner_name"] == "quant-qualification-01"
    assert evidence["runner_os"] == "Linux"
    assert evidence["repository"] == "siebengestirn/quant"
    assert evidence["workflow"] == "QUANT CI"
    assert evidence["job"] == "native-q"
    assert evidence["scope"] == "Q_PERSISTENCE"
    assert evidence["required_for_portable_research"] is False
    lifecycle = evidence["ephemeral_lifecycle"]
    assert lifecycle["deregistration_verified"] is False
    assert lifecycle["deregistration_evidence"] == ""
    assert "external runner operator" in lifecycle["requirement"]


def test_missing_dedicated_label_fails_closed(tmp_path: Path) -> None:
    """A generic self-hosted runner is rejected as evidence."""
    result, output = run_capture(
        tmp_path, runner_env(RUNNER_LABELS="self-hosted")
    )
    assert result.returncode == 1
    assert "quant-native-q" in result.stderr
    assert read_payload(output)["runner_labels"] == ["self-hosted"]


def test_pull_request_event_fails_closed(tmp_path: Path) -> None:
    """A pull_request event cannot qualify for the licensed runner."""
    result, _ = run_capture(
        tmp_path, runner_env(GITHUB_EVENT_NAME="pull_request")
    )
    assert result.returncode == 1
    assert "pull_request" in result.stderr


def test_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    """A checked-out HEAD that differs from the event SHA is rejected."""
    result, _ = run_capture(tmp_path, runner_env(), head_sha=OTHER_SHA)
    assert result.returncode == 1
    assert "head SHA mismatch" in result.stderr


def test_expected_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    """An event SHA different from the recorded HEAD is rejected."""
    result, _ = run_capture(tmp_path, runner_env(QUANT_EXPECTED_SHA=OTHER_SHA))
    assert result.returncode == 1
    assert "head SHA mismatch" in result.stderr


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    """Malformed evidence JSON fails closed in offline verification."""
    broken = tmp_path / "runner-evidence.json"
    broken.write_text("{not json", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(broken)],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "malformed runner evidence JSON" in result.stderr


def test_non_object_json_is_rejected(tmp_path: Path) -> None:
    """A JSON array is not a valid evidence document."""
    broken = tmp_path / "runner-evidence.json"
    broken.write_text("[]", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(broken)],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "must contain a JSON object" in result.stderr


def test_json_array_labels_are_accepted() -> None:
    """Runner labels may also arrive as a JSON array string."""
    labels = runner_evidence.parse_labels('["self-hosted", "quant-native-q"]')
    assert labels == ["self-hosted", "quant-native-q"]


def test_missing_environment_group_is_not_claimed_as_observed(
    tmp_path: Path,
) -> None:
    """An absent runner group env value is not reported as observed proof."""
    result, output = run_capture(tmp_path, runner_env())
    assert result.returncode == 0, result.stderr
    assert read_payload(output)["runner_group_observed"] == ""


def test_observed_group_mismatch_fails_closed(tmp_path: Path) -> None:
    """An observed group outside the dedicated group is rejected."""
    result, _ = run_capture(tmp_path, runner_env(RUNNER_GROUP="default"))
    assert result.returncode == 1
    assert "runner group differs" in result.stderr


def test_deregistration_cannot_be_claimed_without_evidence() -> None:
    """Lifecycle evidence cannot assert deregistration without API proof."""
    evidence = runner_evidence.capture(
        runner_env(), q_version="4.1.0", head_sha=SHA
    )
    lifecycle = evidence["ephemeral_lifecycle"]
    assert isinstance(lifecycle, dict)
    lifecycle["deregistration_verified"] = True
    assert (
        "deregistration claimed without evidence"
        in runner_evidence.validate(evidence)
    )
    lifecycle["deregistration_evidence"] = "api-deactivated-at-2026-11-28"
    assert (
        "deregistration claimed without evidence"
        not in runner_evidence.validate(evidence)
    )
