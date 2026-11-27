#!/usr/bin/env python3
"""Offline tests for the canonical release-state validator."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_release_state", ROOT / "scripts" / "check_release_state.py"
)
assert _SPEC is not None and _SPEC.loader is not None
CHECK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECK)

COMMIT = "a" * 40
STALE_COMMIT = "b" * 40
DOC_PATHS = CHECK.DOC_PATHS
GATE_NAMES = (
    "ci_security_tests",
    "hosted_ci",
    "native_q_checker_tests",
    "portable_cpp",
    "portable_pytest",
    "release_bundle_tests",
    "sanitizer",
    "state_freshness",
    "style",
    "terminology_audit",
    "verify_report_tests",
    "w1",
)
GATE_COUNTS = {
    "ci_security_tests": {"passed": 11},
    "hosted_ci": {"native_q_result": "GATED_NOT_RUN"},
    "native_q_checker_tests": {"passed": 8},
    "portable_cpp": {"checks": 14114},
    "portable_pytest": {"passed": 51},
    "release_bundle_tests": {"passed": 121},
    "sanitizer": {"sanitizer_checks": 14114},
    "verify_report_tests": {"passed": 161},
    "w1": {"passed": 36},
}
CSV_HEADERS = {
    "governance/requirements-traceability.csv": (
        "requirement_id",
        "workstream",
        "requirement",
        "current_status",
        "evidence",
        "verification",
        "gated_by",
        "acceptance_evidence",
    ),
    "governance/technical-debt-register.csv": (
        "id",
        "priority",
        "area",
        "description",
        "evidence",
        "status",
        "blocked_by",
        "exit_evidence",
    ),
    "governance/security-findings.csv": (
        "id",
        "severity",
        "area",
        "finding",
        "evidence",
        "status",
        "mitigation",
        "residual_risk",
    ),
    "governance/research-report-registry.csv": (
        "report_id",
        "snapshot",
        "trades",
        "net_pnl",
        "sharpe",
        "holdout_status",
        "dataset_digest",
        "research_sha",
        "strategy_version",
        "universe_version",
        "cost_model",
        "report_digest",
        "provenance_status",
    ),
}
CURRENT_RECORD = (
    "R8.4.2 / PR #66 is COMPLETE; 36 W1 tests; 4 required checks; "
    "PORTABLE_RESEARCH; native q GATED_NOT_RUN.\n"
)
HISTORICAL_RECORD = (
    "\n## HISTORICAL release history\n\n"
    "R8.4.2 IN PROGRESS; PR #64; W1 14 tests; 28 offline tests.\n"
)


def git(root: Path, *args: str) -> str:
    """Run Git in a temporary repository and require success."""
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def write_json(root: Path, relative: str, payload: dict) -> None:
    """Write canonical JSON to a path under the temporary repository."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CHECK.canonical_text(payload), encoding="utf-8")


def write_csv(root: Path, relative: str, header: tuple[str, ...]) -> None:
    """Write a header and one current-state row with the expected fields."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    row = [
        "CURRENT R8.4.2 PR #66 COMPLETE 36 W1 tests "
        "4 required checks PORTABLE_RESEARCH GATED_NOT_RUN"
    ] * len(header)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(row)


def make_report(commit: str = COMMIT) -> dict:
    """Build a minimal, valid assurance report."""
    gates = []
    for name in GATE_NAMES:
        gates.append(
            {
                "name": name,
                "command": "offline fixture",
                "exit_code": 0,
                "counts": GATE_COUNTS.get(name, {}),
                "result": "PASS",
                "steps": [],
            }
        )
    return {
        "schema_version": 1,
        "release_scope": "PORTABLE_RESEARCH",
        "commit": commit,
        "overall": "PASS",
        "gates": gates,
    }


def make_state(commit: str, digest: str) -> dict:
    """Build a canonical release state for the fixture repository."""
    return {
        "branch_protection": {
            "native_q_gated": True,
            "required_approvals": 0,
            "required_checks": list(CHECK.REQUIRED_CHECKS),
        },
        "captured_state_basis": "captured fixture evidence",
        "current_workstreams": {
            name: "PARTIAL" for name in CHECK.REQUIRED_WORKSTREAMS
        },
        "gate_results": {name: "PASS" for name in GATE_NAMES},
        "historical_releases": [
            {
                "note": "HISTORICAL fixture release",
                "pr": 63,
                "release_version": "R8.4.1",
                "scope": "PORTABLE_RESEARCH",
                "status": "COMPLETE",
            }
        ],
        "main_captured_basis": "captured fixture commit",
        "main_commit": commit,
        "manifest_basis": "captured fixture manifest",
        "manifest_sha256": "0" * 64,
        "merged_pull_requests": "47-66",
        "native_q_status": "GATED_NOT_RUN",
        "release_id": "R8.4.2-PR66",
        "release_scope": "PORTABLE_RESEARCH",
        "release_status": "COMPLETE",
        "release_version": "R8.4.2",
        "report_basis": "captured fixture report",
        "required_gate_set": list(GATE_NAMES),
        "test_inventory": {
            "ci_security_tests": 11,
            "cpp_checks": 14114,
            "native_q_checker_tests": 8,
            "portable_pytest": 51,
            "release_bundle_tests": 121,
            "sanitizer_checks": 14114,
            "verify_report_tests": 161,
            "w1": 36,
        },
        "trading_authorization": {
            "decision": "NO_TRADE",
            "live_execution": "DISABLED",
        },
        "verification_report_sha256": digest,
    }


def write_state(root: Path) -> None:
    """Refresh the fixture state with the current HEAD and report digest."""
    report_bytes = (root / CHECK.REPORT_RELATIVE).read_bytes()
    digest = hashlib.sha256(report_bytes).hexdigest()
    write_json(
        root,
        CHECK.STATE_RELATIVE,
        make_state(git(root, "rev-parse", "HEAD"), digest),
    )


@pytest.fixture(name="sandbox")
def _sandbox(tmp_path: Path) -> Path:
    """Build a clean repository with a valid canonical release state."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Release State Test")
    report = make_report()
    write_json(tmp_path, CHECK.REPORT_RELATIVE, report)
    for relative in DOC_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Fixture\n\n## CURRENT RELEASE RECORD (authoritative)\n\n"
            f"{CURRENT_RECORD}{HISTORICAL_RECORD}",
            encoding="utf-8",
        )
    for relative, header in CSV_HEADERS.items():
        write_csv(tmp_path, relative, header)
    write_json(
        tmp_path,
        CHECK.INVENTORY_RELATIVE,
        {
            "entries": [
                {
                    "class": "policy",
                    "owner": "maintainer",
                    "path": CHECK.STATE_RELATIVE,
                    "purpose": "Canonical release state",
                    "retention": "current",
                }
            ]
        },
    )
    tracked = [
        CHECK.REPORT_RELATIVE,
        CHECK.INVENTORY_RELATIVE,
        *DOC_PATHS,
        *CSV_HEADERS,
    ]
    git(tmp_path, "add", *tracked)
    git(tmp_path, "commit", "-q", "-m", "fixture evidence")
    head = git(tmp_path, "rev-parse", "HEAD")
    write_json(tmp_path, CHECK.REPORT_RELATIVE, make_report(head))
    write_state(tmp_path)
    git(tmp_path, "add", CHECK.STATE_RELATIVE)
    return tmp_path


def test_valid_state_passes(sandbox: Path) -> None:
    """A clean canonical state and reconciled documents pass every check."""
    assert CHECK.check(sandbox) == []


def test_stale_commit_fails(sandbox: Path) -> None:
    """A commit that no longer matches git HEAD fails closed."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    state["main_commit"] = STALE_COMMIT
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    errors = CHECK.check(sandbox)
    assert any("main_commit does not match git HEAD" in e for e in errors)


def test_stale_test_count_in_current_doc_fails(sandbox: Path) -> None:
    """An old W1 test count in a current-state section fails."""
    path = sandbox / DOC_PATHS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(CURRENT_RECORD, CURRENT_RECORD + "W1 14 tests.\n"),
        encoding="utf-8",
    )
    errors = CHECK.check(sandbox)
    assert any("stale W1 14 tests" in e for e in errors)


def test_historical_section_with_old_values_passes(sandbox: Path) -> None:
    """Historical-labelled records may retain superseded values."""
    assert CHECK.check(sandbox) == []


def test_missing_required_field_fails(sandbox: Path) -> None:
    """A canonical state with a missing required field fails."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    del state["manifest_sha256"]
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    errors = CHECK.check(sandbox)
    assert any("missing required field manifest_sha256" in e for e in errors)


def test_bad_csv_field_count_fails(sandbox: Path) -> None:
    """A register row with the wrong field count fails."""
    path = sandbox / "governance/requirements-traceability.csv"
    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(["short", "row"])
    errors = CHECK.check(sandbox)
    assert any("expected 8" in e for e in errors)


def test_noncanonical_json_fails(sandbox: Path) -> None:
    """Reformatted JSON fails the canonical-form requirement."""
    path = sandbox / CHECK.STATE_RELATIVE
    path.write_text(json.dumps(json.loads(path.read_text())), encoding="utf-8")
    errors = CHECK.check(sandbox)
    assert any("not canonical JSON" in e for e in errors)


def test_show_reports_the_canonical_state(sandbox: Path) -> None:
    """The show command prints the authoritative release identity."""
    assert CHECK.show(sandbox) == 0
