#!/usr/bin/env python3
"""Offline tests for the canonical release-state validator.

The fixture uses a fictional release identity and counts. A
validator that pins any real-world version, PR, commit or test total fails
these tests, so governance can advance without rewriting the checker.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location(
    "check_release_state", ROOT / "scripts" / "check_release_state.py"
)
assert _SPEC is not None and _SPEC.loader is not None
CHECK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECK)

COMMIT = "a" * 40
STALE_COMMIT = "b" * 40
VERSION = "R9.0.1"
PR = 77
HISTORICAL_VERSION = "R9.0.0"
HISTORICAL_PR = 76
PR_RANGE = f"47-{PR}"
RELEASE_ID = f"{VERSION}-PR{PR}"
SCOPE = "PORTABLE_RESEARCH"
NATIVE = "GATED_NOT_RUN"
W1_TESTS = 47
OLD_W1_TESTS = 14
REQUIRED_CHECKS = 4
LATEST_SOURCE_PR = PR + 1
LATEST_SOURCE_REVISION = f"PR #{LATEST_SOURCE_PR}"
RUNNER_EXECUTION = "NOT_OBSERVED"
ATTESTATION_MECHANISM = "CHECKED"
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
    "terminology_audit",
    "verify_report_tests",
    "w1",
)
GATE_COUNTS = {
    "ci_security_tests": {"passed": 13},
    "hosted_ci": {"native_q_result": NATIVE},
    "native_q_checker_tests": {"passed": 7},
    "portable_cpp": {"checks": 901},
    "portable_pytest": {"passed": 52},
    "release_bundle_tests": {"passed": 142},
    "sanitizer": {"sanitizer_checks": 901},
    "verify_report_tests": {"passed": 172},
    "w1": {"passed": W1_TESTS},
}
INVENTORY_KEYS = {
    "ci_security_tests": "ci_security_tests",
    "native_q_checker_tests": "native_q_checker_tests",
    "portable_cpp": "cpp_checks",
    "portable_pytest": "portable_pytest",
    "release_bundle_tests": "release_bundle_tests",
    "sanitizer": "sanitizer_checks",
    "verify_report_tests": "verify_report_tests",
    "w1": "w1",
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
    f"{VERSION} / PR #{PR} is COMPLETE; {W1_TESTS} W1 tests; "
    f"{REQUIRED_CHECKS} required checks; {SCOPE}; "
    f"native q {NATIVE}; runner execution {RUNNER_EXECUTION}; "
    f"attestation mechanism {ATTESTATION_MECHANISM}; "
    f"latest source revision {LATEST_SOURCE_REVISION}.\n"
)
HISTORICAL_RECORD = (
    "\n## HISTORICAL release history\n\n"
    f"{HISTORICAL_VERSION} IN PROGRESS; PR #{HISTORICAL_PR}; "
    f"W1 {OLD_W1_TESTS} tests; 12 offline tests.\n"
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
        f"CURRENT {VERSION} PR #{PR} COMPLETE {W1_TESTS} W1 tests "
        f"{REQUIRED_CHECKS} required checks {SCOPE} {NATIVE} "
        f"runner execution {RUNNER_EXECUTION} "
        f"attestation {ATTESTATION_MECHANISM} "
        f"latest source revision {LATEST_SOURCE_REVISION}"
    ] * len(header)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(row)


def make_report(commit: str = COMMIT) -> dict:
    """Build a minimal, valid assurance report with fixture counts."""
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
        "release_scope": SCOPE,
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
        "capture_context": f"Point-in-time capture at PR #{PR}",
        "captured_state_basis": f"captured fixture evidence at PR #{PR}",
        "current_workstreams": {
            name: "PARTIAL" for name in CHECK.REQUIRED_WORKSTREAMS
        },
        "gate_results": {name: "PASS" for name in GATE_NAMES},
        "historical_releases": [
            {
                "note": f"HISTORICAL fixture release at PR #{HISTORICAL_PR}",
                "pr": HISTORICAL_PR,
                "release_version": HISTORICAL_VERSION,
                "scope": SCOPE,
                "status": "COMPLETE",
            }
        ],
        "independent_acceptance": "PENDING",
        "latest_branch_protection_captured": True,
        "latest_source_revision": LATEST_SOURCE_REVISION,
        "main_captured_basis": (
            f"captured product baseline commit at PR #{PR}"
        ),
        "main_commit": STALE_COMMIT,
        "manifest_basis": "captured fixture manifest",
        "manifest_sha256": "0" * 64,
        "merged_pull_requests": PR_RANGE,
        "native_q_runner_execution": RUNNER_EXECUTION,
        "native_q_status": NATIVE,
        "release_id": RELEASE_ID,
        "release_scope": SCOPE,
        "release_status": "COMPLETE",
        "release_version": VERSION,
        "report_basis": "captured fixture report",
        "required_gate_set": list(GATE_NAMES),
        "runner_attestation_mechanism": ATTESTATION_MECHANISM,
        "source_commit": commit,
        "source_commit_basis": (
            f"captured fixture source commit at PR #{LATEST_SOURCE_PR}"
        ),
        "test_inventory": {
            inventory_key: GATE_COUNTS[gate]["passed"]
            for gate, inventory_key in INVENTORY_KEYS.items()
            if "passed" in GATE_COUNTS[gate]
        }
        | {
            "cpp_checks": GATE_COUNTS["portable_cpp"]["checks"],
            "sanitizer_checks": GATE_COUNTS["sanitizer"]["sanitizer_checks"],
        },
        "trading_authorization": {
            "decision": "NO_TRADE",
            "live_execution": "DISABLED",
        },
        "verification_report_sha256": digest,
    }


def write_branch_protection_capture(root: Path, head: str) -> None:
    """Write a fresh, HISTORICAL-labelled branch-protection capture."""
    write_json(
        root,
        CHECK.BRANCH_PROTECTION_CAPTURE_RELATIVE,
        {
            "allow_deletions": False,
            "allow_force_pushes": False,
            "api_version": "2022-11-28",
            "branch": "main",
            "captured_at": "2026-11-28T00:00:00Z",
            "endpoint": (
                "https://api.github.com/repos/fixture/quant/branches/main/"
                "protection"
            ),
            "enforce_admins": True,
            "historical_captures": [
                {
                    "captured_at": "2026-11-27T00:00:00Z",
                    "main_sha": STALE_COMMIT,
                    "note": (
                        f"HISTORICAL point-in-time capture at PR #{PR - 1}"
                    ),
                    "response_sha256": "1" * 64,
                }
            ],
            "http_protocol": "HTTP/2.0",
            "http_status": 200,
            "main_sha": head,
            "note": (
                "HISTORICAL point-in-time capture at " + LATEST_SOURCE_REVISION
            ),
            "required_approvals": 0,
            "required_checks": list(CHECK.REQUIRED_CHECKS),
            "required_conversation_resolution": True,
            "response_sha256": "2" * 64,
            "strict_checks": True,
        },
    )


def write_state(root: Path) -> None:
    """Refresh the fixture state with the current HEAD and report digest."""
    report_bytes = (root / CHECK.REPORT_RELATIVE).read_bytes()
    digest = hashlib.sha256(report_bytes).hexdigest()
    head = git(root, "rev-parse", "HEAD")
    write_branch_protection_capture(root, head)
    write_json(root, CHECK.STATE_RELATIVE, make_state(head, digest))


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
    return tmp_path


def update_state(sandbox: Path, **changes: object) -> dict:
    """Apply fixture state changes and persist canonical JSON."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    state.update(changes)
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    return state


def test_valid_state_passes(sandbox: Path) -> None:
    """A clean canonical state and reconciled documents pass every check."""
    assert CHECK.check(sandbox) == []


def test_identity_is_derived_not_hardcoded(sandbox: Path) -> None:
    """A fictional release identity passes, proving no pinned real release."""
    assert VERSION != "R8.4.2" and PR != 66
    assert LATEST_SOURCE_PR != 70
    assert CHECK.check(sandbox) == []


def test_bound_identity_fails(sandbox: Path) -> None:
    """A release id that disagrees with version and PR fails closed."""
    update_state(sandbox, release_id="R9.9.9-PR99")
    errors = CHECK.check(sandbox)
    assert any("release_id is not bound to version and PR" in e for e in errors)


def test_stale_source_commit_fails(sandbox: Path) -> None:
    """A source commit that no longer matches git HEAD fails closed."""
    update_state(sandbox, source_commit=STALE_COMMIT)
    errors = CHECK.check(sandbox)
    assert any("source_commit does not match git HEAD" in e for e in errors)


def test_baseline_commit_may_differ_from_latest_source(sandbox: Path) -> None:
    """The captured product baseline pin is not the latest source commit."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    assert state["main_commit"] != state["source_commit"]
    assert CHECK.check(sandbox) == []


def test_source_revision_must_follow_product_baseline(sandbox: Path) -> None:
    """A source revision older than the product release baseline fails."""
    update_state(sandbox, latest_source_revision=f"PR #{PR - 1}")
    errors = CHECK.check(sandbox)
    assert any("latest_source_revision predates" in e for e in errors)


def test_source_revision_grammar_fails_closed(sandbox: Path) -> None:
    """The source revision must be an explicit PR token."""
    update_state(sandbox, latest_source_revision="v78")
    errors = CHECK.check(sandbox)
    assert any("latest_source_revision must be" in e for e in errors)


def test_runner_execution_must_match_native_q_result(sandbox: Path) -> None:
    """A skipped native-q result cannot claim observed runner execution."""
    update_state(sandbox, native_q_runner_execution="OBSERVED")
    errors = CHECK.check(sandbox)
    assert any("native_q_runner_execution does not match" in e for e in errors)


def test_attestation_mechanism_must_be_checked(sandbox: Path) -> None:
    """An unverified attestation mechanism fails closed."""
    update_state(sandbox, runner_attestation_mechanism="ASSUMED")
    errors = CHECK.check(sandbox)
    assert any(
        "runner_attestation_mechanism is not CHECKED" in e for e in errors
    )


def test_branch_protection_capture_flag_is_required(sandbox: Path) -> None:
    """The fresh branch-protection capture must be declared present."""
    update_state(sandbox, latest_branch_protection_captured=False)
    errors = CHECK.check(sandbox)
    assert any(
        "latest_branch_protection_captured is not true" in e for e in errors
    )


def test_branch_protection_capture_digest_must_be_sha256(
    sandbox: Path,
) -> None:
    """A malformed response digest in the fresh capture fails closed."""
    path = sandbox / CHECK.BRANCH_PROTECTION_CAPTURE_RELATIVE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["response_sha256"] = "not-a-digest"
    write_json(sandbox, CHECK.BRANCH_PROTECTION_CAPTURE_RELATIVE, payload)
    errors = CHECK.check(sandbox)
    assert any("response_sha256 is not a SHA-256 digest" in e for e in errors)


def test_pr_range_binding_fails(sandbox: Path) -> None:
    """A changed PR range breaks the derived release-id binding."""
    update_state(sandbox, merged_pull_requests=f"47-{HISTORICAL_PR}")
    errors = CHECK.check(sandbox)
    assert any("release_id is not bound to version and PR" in e for e in errors)


def test_test_inventory_must_match_report(sandbox: Path) -> None:
    """A recorded count that disagrees with the generated report fails."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    state["test_inventory"]["w1"] = W1_TESTS + 1
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    errors = CHECK.check(sandbox)
    assert any("w1 test count differs" in e for e in errors)


def test_gate_result_must_match_report(sandbox: Path) -> None:
    """A changed gate result fails against the current report."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    state["gate_results"]["w1"] = "FAIL"
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    errors = CHECK.check(sandbox)
    assert any("gate w1 result differs" in e for e in errors)


def test_stale_test_count_in_current_doc_fails(sandbox: Path) -> None:
    """An old W1 test count in a current-state section fails."""
    path = sandbox / DOC_PATHS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            CURRENT_RECORD, CURRENT_RECORD + f"W1 {OLD_W1_TESTS} tests.\n"
        ),
        encoding="utf-8",
    )
    errors = CHECK.check(sandbox)
    assert any(f"stale W1 {OLD_W1_TESTS} tests" in e for e in errors)


def test_stale_pr_claim_in_current_doc_fails(sandbox: Path) -> None:
    """A historical PR claimed as a current release fails."""
    path = sandbox / DOC_PATHS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            CURRENT_RECORD,
            CURRENT_RECORD + f"PR #{HISTORICAL_PR} is the current release.\n",
        ),
        encoding="utf-8",
    )
    errors = CHECK.check(sandbox)
    assert any(f"stale PR #{HISTORICAL_PR} current claim" in e for e in errors)


def test_stale_version_claim_in_current_doc_fails(sandbox: Path) -> None:
    """A historical version claimed as a current release fails."""
    path = sandbox / DOC_PATHS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            CURRENT_RECORD,
            CURRENT_RECORD + f"{HISTORICAL_VERSION} is the current release.\n",
        ),
        encoding="utf-8",
    )
    errors = CHECK.check(sandbox)
    assert any(f"stale {HISTORICAL_VERSION} current claim" in e for e in errors)


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


def test_missing_source_commit_fails(sandbox: Path) -> None:
    """A state without the latest source commit fails required-field checks."""
    state = json.loads((sandbox / CHECK.STATE_RELATIVE).read_text())
    del state["source_commit"]
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    errors = CHECK.check(sandbox)
    assert any("missing required field source_commit" in e for e in errors)


def test_show_reports_the_canonical_state(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The show command prints both release identities and evidence status."""
    assert CHECK.show(sandbox) == 0
    printed = capsys.readouterr().out
    for token in (
        RELEASE_ID,
        LATEST_SOURCE_REVISION,
        RUNNER_EXECUTION,
        ATTESTATION_MECHANISM,
    ):
        assert token in printed


def mark_historical(sandbox: Path) -> dict:
    """Label the fixture state as a historical capture and return it."""
    return update_state(
        sandbox,
        capture_context=f"HISTORICAL capture at PR #{HISTORICAL_PR}",
    )


def test_historical_state_without_generated_report_passes(
    sandbox: Path,
) -> None:
    """A generated report absent from a clean historical checkout is N/A."""
    mark_historical(sandbox)
    (sandbox / CHECK.REPORT_RELATIVE).unlink()
    assert CHECK.check(sandbox) == []


def test_current_state_without_generated_report_fails(sandbox: Path) -> None:
    """A current-state record must still provide the generated report."""
    (sandbox / CHECK.REPORT_RELATIVE).unlink()
    errors = CHECK.check(sandbox)
    assert any(
        f"{CHECK.REPORT_RELATIVE}: missing or unreadable" in error
        for error in errors
    )


def test_clean_checkout_still_validates_runner_fields(sandbox: Path) -> None:
    """A missing generated report does not waive runner evidence fields."""
    mark_historical(sandbox)
    (sandbox / CHECK.REPORT_RELATIVE).unlink()
    update_state(sandbox, runner_attestation_mechanism="ASSUMED")
    errors = CHECK.check(sandbox)
    assert any(
        "runner_attestation_mechanism is not CHECKED" in e for e in errors
    )


def test_historical_capture_tolerates_unsynced_generated_gates(
    sandbox: Path,
) -> None:
    """A point-in-time capture does not claim volatile generated gates."""
    mark_historical(sandbox)
    report_path = sandbox / CHECK.REPORT_RELATIVE
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["overall"] = "FAIL"
    write_json(sandbox, CHECK.REPORT_RELATIVE, report)
    assert CHECK.check(sandbox) == []


def test_historical_state_with_invalid_report_fails(sandbox: Path) -> None:
    """A present but malformed report is never treated as not applicable."""
    mark_historical(sandbox)
    (sandbox / CHECK.REPORT_RELATIVE).write_text(
        "{not valid json", encoding="utf-8"
    )
    errors = CHECK.check(sandbox)
    assert any("invalid JSON" in error for error in errors)


def test_current_state_with_invalid_report_fails(sandbox: Path) -> None:
    """A malformed generated report fails closed for a current record."""
    (sandbox / CHECK.REPORT_RELATIVE).write_text(
        "{not valid json", encoding="utf-8"
    )
    errors = CHECK.check(sandbox)
    assert any("invalid JSON" in error for error in errors)


def test_historical_state_keeps_checking_current_doc_values(
    sandbox: Path,
) -> None:
    """A historical label never excuses a stale current-state document."""
    mark_historical(sandbox)
    path = sandbox / DOC_PATHS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            CURRENT_RECORD, CURRENT_RECORD + f"W1 {OLD_W1_TESTS} tests.\n"
        ),
        encoding="utf-8",
    )
    errors = CHECK.check(sandbox)
    assert any(f"stale W1 {OLD_W1_TESTS} tests" in e for e in errors)


def test_historical_state_keeps_checking_required_fields(
    sandbox: Path,
) -> None:
    """Missing required fields fail even without a generated report."""
    state = mark_historical(sandbox)
    del state["manifest_sha256"]
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    (sandbox / CHECK.REPORT_RELATIVE).unlink()
    errors = CHECK.check(sandbox)
    assert any(
        "missing required field manifest_sha256" in error for error in errors
    )


def test_historical_state_tolerates_captured_commit(sandbox: Path) -> None:
    """A historical capture does not claim to match the live HEAD commit."""
    state = mark_historical(sandbox)
    state["main_commit"] = STALE_COMMIT
    write_json(sandbox, CHECK.STATE_RELATIVE, state)
    (sandbox / CHECK.REPORT_RELATIVE).unlink()
    assert CHECK.check(sandbox) == []
