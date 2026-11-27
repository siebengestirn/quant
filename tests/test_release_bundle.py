"""Offline tests for release bundle provenance, scope and report evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "release_bundle", ROOT / "scripts" / "release_bundle.py"
)
assert SPEC is not None and SPEC.loader is not None
LOADER = SPEC.loader
RELEASE_BUNDLE = importlib.util.module_from_spec(SPEC)
sys.modules["release_bundle"] = RELEASE_BUNDLE
LOADER.exec_module(RELEASE_BUNDLE)
AUDIT_DISPOSITION = sys.modules["audit_disposition"]

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
CI_COMMIT = "c" * 40
SCHEMA_VERSION = RELEASE_BUNDLE.REPORT_SCHEMA_VERSION
RELEASE_SCOPE = RELEASE_BUNDLE.RELEASE_SCOPE
PERSISTENCE_SCOPE = RELEASE_BUNDLE.PERSISTENCE_SCOPE
REQUIRED_GATES = RELEASE_BUNDLE.REQUIRED_GATES
ORIGINAL_BYTES = b"pinned original snapshot bytes\n"
DECODED_BYTES = b"pinned decoded snapshot bytes\n"
GATED_REASON = "native-q feature is not enabled; hosted job skipped"
GATE_TEST_FILES = {
    "portable_pytest": "tests/test_dataset_contract.py",
    "w1": "tests/test_instrument_master.py",
    "release_bundle_tests": "tests/test_release_bundle.py",
    "verify_report_tests": "tests/test_verify_report.py",
    "native_q_checker_tests": "tests/test_native_q_ci.py",
    "runner_attestation_tests": "tests/test_runner_attestation.py",
    "ci_security_tests": "tests/test_ci_security.py",
    "audit_disposition_tests": "tests/test_audit_disposition.py",
}
EVIDENCE_DIGEST = "d" * 64
COUNTS_BY_CLASS = {
    "CORRECT_WORDING": 60,
    "ACCEPTED_DOMAIN_TERMINOLOGY": 20,
    "HISTORICAL_STATEMENT": 10,
    "AMBIGUOUS_REQUIREMENT": 7,
    "POTENTIALLY_MISLEADING_CLAIM": 7,
    "DOCUMENTATION_DEFECT": 3,
}
OWNER_STATUS = {
    "CORRECT_WORDING": "resolved",
    "ACCEPTED_DOMAIN_TERMINOLOGY": "resolved",
    "HISTORICAL_STATEMENT": "resolved",
    "AMBIGUOUS_REQUIREMENT": "OPEN",
    "POTENTIALLY_MISLEADING_CLAIM": "OPEN",
    "DOCUMENTATION_DEFECT": "OPEN",
}
EVIDENCE_REASON = (
    "exact audit digest, finding coverage and owner status verified"
)
BINARY_DIGEST = hashlib.sha256(b"fresh build-ci contract binary\n").hexdigest()
STALE_DIGEST = hashlib.sha256(b"stale build contract binary\n").hexdigest()
PASS_REASON = (
    "native-q integration tests executed: 8 passed, 0 skipped, 0 failed"
)


@dataclass(frozen=True)
class Sandbox:
    """A temporary repository with a local bare origin."""

    base: Path
    repo: Path
    remote: Path


@dataclass(frozen=True)
class SnapshotFixture:
    """A pinned provenance record with its two snapshot byte files."""

    provenance: Path
    data_root: Path
    payloads: dict[str, bytes]


def git(args: list[str], cwd: Path) -> str:
    """Run Git in a temporary repository and require success."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def commit_identity(repo: Path) -> None:
    """Configure a local-only commit identity."""
    git(["config", "user.email", "test@example.invalid"], repo)
    git(["config", "user.name", "Release Bundle Test"], repo)


@pytest.fixture(name="sandbox")
def _sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sandbox]:
    """Build a clean main with a local origin and patch the module root."""
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        base = Path(name)
        remote = base / "origin.git"
        git(["init", "--bare", "--initial-branch=main", str(remote)], base)
        repo = base / "repo"
        repo.mkdir()
        git(["init", "-b", "main"], repo)
        commit_identity(repo)
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        git(["add", "tracked.txt"], repo)
        git(["commit", "-m", "initial"], repo)
        git(["remote", "add", "origin", str(remote)], repo)
        git(["push", "-u", "origin", "main"], repo)
        monkeypatch.setattr(RELEASE_BUNDLE, "ROOT", repo)
        yield Sandbox(base=base, repo=repo, remote=remote)


@pytest.fixture(name="workspace")
def _workspace() -> Iterator[Path]:
    """Provide an approved temporary directory for staging and ZIP output."""
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        yield Path(name)


@pytest.fixture(name="snapshots")
def _snapshots() -> Iterator[SnapshotFixture]:
    """Write a tiny, valid pinned snapshot pair and its provenance."""
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        base = Path(name)
        data_root = base / "data" / "instrument-master"
        entry = {
            "provider": "fixture",
            "market_date": "2026-11-26",
            "original_filename": "NSE.json",
            "original_bytes": len(ORIGINAL_BYTES),
            "original_sha256": hashlib.sha256(ORIGINAL_BYTES).hexdigest(),
            "decoded_bytes": len(DECODED_BYTES),
            "decoded_sha256": hashlib.sha256(DECODED_BYTES).hexdigest(),
        }
        payloads = dict(
            zip(
                (
                    RELEASE_BUNDLE.snapshot_files(entry)[0][0],
                    RELEASE_BUNDLE.snapshot_files(entry)[1][0],
                ),
                (ORIGINAL_BYTES, DECODED_BYTES),
                strict=True,
            )
        )
        for relative, payload in payloads.items():
            path = data_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        provenance = base / "governance" / "instrument-master-snapshots.json"
        provenance.parent.mkdir(parents=True, exist_ok=True)
        provenance.write_text(
            json.dumps({"snapshots": [entry]}, sort_keys=True),
            encoding="utf-8",
        )
        yield SnapshotFixture(
            provenance=provenance,
            data_root=data_root,
            payloads=payloads,
        )


def test_clean_repo_passes_provenance(sandbox: Sandbox) -> None:
    """A clean, published main passes every provenance check."""
    expected = git(["rev-parse", "HEAD"], sandbox.repo)
    actual = RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))
    assert actual == expected


def test_stale_remote_reference_fails_after_fetch(
    sandbox: Sandbox,
) -> None:
    """A fetched remote ref ahead of HEAD fails closed."""
    other = sandbox.base / "other"
    git(["clone", str(sandbox.remote), str(other)], sandbox.base)
    commit_identity(other)
    (other / "tracked.txt").write_text("two\n", encoding="utf-8")
    git(["add", "tracked.txt"], other)
    git(["commit", "-m", "advance main"], other)
    git(["push", "origin", "main"], other)
    with pytest.raises(RuntimeError, match="differs from origin/main"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_missing_remote_branch_fails(sandbox: Sandbox) -> None:
    """A remote without main fails after fetch and prune."""
    git(
        [
            "--git-dir",
            str(sandbox.remote),
            "update-ref",
            "-d",
            "refs/heads/main",
        ],
        sandbox.base,
    )
    with pytest.raises(RuntimeError, match="origin/main is missing"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_dirty_tracked_file_fails(sandbox: Sandbox) -> None:
    """An unstaged tracked edit fails closed."""
    (sandbox.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="working tree is not clean"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_staged_change_fails(sandbox: Sandbox) -> None:
    """A staged edit fails before any staging of bundle files."""
    (sandbox.repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git(["add", "tracked.txt"], sandbox.repo)
    with pytest.raises(RuntimeError, match="staged changes are present"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_untracked_file_fails(sandbox: Sandbox) -> None:
    """An untracked file makes the porcelain status non-empty."""
    (sandbox.repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="working tree is not clean"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_disconnected_remote_fails(sandbox: Sandbox) -> None:
    """A fetch failure aborts before any other check or staging."""
    git(
        ["remote", "set-url", "origin", str(sandbox.base / "missing.git")],
        sandbox.repo,
    )
    with pytest.raises(RuntimeError, match="git fetch --prune origin"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.base / "missing.git"))


def test_wrong_branch_fails(sandbox: Sandbox) -> None:
    """A published commit on another branch fails closed."""
    git(["checkout", "-b", "dev"], sandbox.repo)
    with pytest.raises(RuntimeError, match="expected branch"):
        RELEASE_BUNDLE.verify_provenance(str(sandbox.remote))


def test_wrong_remote_url_fails(sandbox: Sandbox) -> None:
    """An origin pointing at another repository fails closed."""
    assert sandbox.remote.is_dir()
    with pytest.raises(RuntimeError, match="origin URL"):
        RELEASE_BUNDLE.verify_provenance("siebengestirn/other")


def test_commit_chain_requires_matching_attestations() -> None:
    """Report, CI and release commits must name the same verified HEAD."""
    report = sample_report(COMMIT)
    RELEASE_BUNDLE.validate_commit_chain(report, COMMIT)
    report["commit"] = OTHER_COMMIT
    with pytest.raises(ValueError, match="report commit"):
        RELEASE_BUNDLE.validate_commit_chain(report, COMMIT)
    report["commit"] = COMMIT
    host_gate(report)["counts"]["head_sha"] = CI_COMMIT
    with pytest.raises(ValueError, match="ci commit"):
        RELEASE_BUNDLE.validate_commit_chain(report, COMMIT)


def pytest_counts(
    passed: int, *, failed: int = 0, errors: int = 0, skipped: int = 0
) -> dict:
    """Return a complete five-count record for one Python gate."""
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "total": passed + failed + errors + skipped,
    }


def make_gate(name: str, counts: dict, result: str = "PASS") -> dict:
    """Build a gate with command, execution metadata and typed counts."""
    return {
        "name": name,
        "command": f"python3 -m verify {name}",
        "exit_code": 0,
        "counts": dict(counts),
        "result": result,
        "steps": [
            {
                "name": f"{name} step",
                "command": f"python3 -m verify {name} --step",
                "exit_code": 0,
                "counts": {},
                "result": "PASS",
                "output": "ok",
            }
        ],
    }


def host_gate(report: dict) -> dict:
    """Return the hosted CI gate of a sample report."""
    return next(gate for gate in report["gates"] if gate["name"] == "hosted_ci")


def gated_native() -> dict:
    """Return a gated native-q record for the portable research scope."""
    return {
        "name": "native-q",
        "enabled": False,
        "conclusion": "skipped",
        "result": "GATED_NOT_RUN",
        "tests_executed": 0,
        "tests_skipped": 0,
        "tests_failed": 0,
        "evidence": {
            "source": "hosted job step",
            "test_step_executed": False,
        },
        "reason": GATED_REASON,
        "database_id": None,
    }


def passed_native() -> dict:
    """Return a fully evidenced native-q PASS record."""
    return {
        "name": "native-q",
        "enabled": True,
        "conclusion": "success",
        "result": "PASS",
        "tests_executed": 8,
        "tests_skipped": 0,
        "tests_failed": 0,
        "evidence": {
            "source": "hosted job log",
            "test_step_executed": True,
            "pytest_counts": {"passed": 8},
        },
        "reason": PASS_REASON,
        "database_id": 99,
    }


def sample_report(commit: str, *, native: dict | None = None) -> dict:
    """Return a report that passes every bundle validation check."""
    counts = {
        "portable_cpp": {
            "checks": 100,
            "failures": 0,
            "ctest_passed": 3,
            "ctest_percent": 100,
            "ctest_total": 3,
        },
        "sanitizer": {
            "sanitizer_checks": 100,
            "sanitizer_failures": 0,
            "sanitizer_ctest_passed": 3,
            "sanitizer_ctest_percent": 100,
            "sanitizer_ctest_total": 3,
        },
        "portable_pytest": {
            **pytest_counts(51),
            RELEASE_BUNDLE.BINARY_PATH_KEY: RELEASE_BUNDLE.CANONICAL_BINARY_PATH,
            RELEASE_BUNDLE.BINARY_DIGEST_KEY: BINARY_DIGEST,
            RELEASE_BUNDLE.BINARY_BUILD_DIR_KEY: (
                RELEASE_BUNDLE.CANONICAL_BUILD_DIR
            ),
            RELEASE_BUNDLE.BINARY_COMMIT_KEY: commit,
        },
        "w1": pytest_counts(14),
        "release_bundle_tests": pytest_counts(74),
        "verify_report_tests": pytest_counts(122),
        "native_q_checker_tests": pytest_counts(8),
        "runner_attestation_tests": pytest_counts(15),
        "ci_security_tests": pytest_counts(11),
        "audit_disposition_tests": pytest_counts(31),
        "style": {"pass_checks": 8, "checks_total": 8},
        "state_freshness": {"self_test": "PASS", "findings": 0},
        "terminology_audit": {"findings": 0, "binding": 0, "advisory": 0},
        "hosted_ci": {
            "run_id": 12345,
            "run_url": "https://github.com/siebengestirn/quant/actions/runs/1",
            "head_sha": commit,
            "head_sha_match": True,
            "conclusion": "success",
            "commit": commit,
            "native_q_conclusion": "skipped",
            "native_q_result": "GATED_NOT_RUN",
            "native_q_tests_executed": 0,
            "native_q_tests_skipped": 0,
            "native_q_tests_failed": 0,
            "native_q_runtime": "GATED_NOT_RUN",
            "native_q_runner_execution": "NOT_OBSERVED",
            "runner_attestation_mechanism": "CHECKED",
            "jobs_total": 4,
            "jobs_passed": 3,
            "jobs_failed": 0,
            "jobs_gated": 1,
        },
    }
    gates = [
        make_gate(name, gate_counts) for name, gate_counts in counts.items()
    ]
    for name, test_file in GATE_TEST_FILES.items():
        gate = next(item for item in gates if item["name"] == name)
        gate["command"] = f"python3 -m pytest {test_file} -o addopts='' -q"
    evidence = make_gate(
        "audit_disposition_evidence",
        {
            "applicable": True,
            "source_digest": EVIDENCE_DIGEST,
            "finding_count": 107,
            "disposition_count": 107,
            "counts_by_class": dict(COUNTS_BY_CLASS),
            "owner_status": dict(OWNER_STATUS),
        },
    )
    evidence["command"] = RELEASE_BUNDLE.AUDIT_EVIDENCE_COMMAND
    evidence["reason"] = EVIDENCE_REASON
    evidence["steps"][0]["output"] = "verify: PASS"
    gates.append(evidence)
    hosted = host_gate({"gates": gates})
    hosted["native_q"] = native if native is not None else gated_native()
    return {
        "schema_version": SCHEMA_VERSION,
        "release_scope": RELEASE_SCOPE,
        "commit": commit,
        "branch": "main",
        "generated_at": "2026-11-27T00:00:00Z",
        "started_at": "2026-11-27T00:00:00Z",
        "completed_at": "2026-11-27T00:01:00Z",
        "toolchain": {"python": "Python 3.12"},
        "presets": ["portable-ci", "sanitizers"],
        "config": {"allowed_skips": []},
        "overall": "PASS",
        "gates": gates,
    }


def passed_report(commit: str) -> dict:
    """Return a valid report whose native tier PASSed with full evidence."""
    report = sample_report(commit, native=passed_native())
    hosted = host_gate(report)
    hosted["counts"].update(
        {
            "native_q_conclusion": "success",
            "native_q_result": "PASS",
            "native_q_runtime": "PASS",
            "native_q_tests_executed": 8,
            "jobs_passed": 4,
            "jobs_gated": 0,
        }
    )
    return report


def test_validate_report_accepts_gated_portable_report() -> None:
    """A gated native tier is valid for the default release scope."""
    report = sample_report(COMMIT)
    assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report
    assert len(report["gates"]) == len(REQUIRED_GATES)


def test_validate_report_accepts_native_pass() -> None:
    """A fully evidenced native PASS is accepted for either scope."""
    report = passed_report(COMMIT)
    assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report
    report["release_scope"] = PERSISTENCE_SCOPE
    assert (
        RELEASE_BUNDLE.validate_report(report, COMMIT, PERSISTENCE_SCOPE)
        is report
    )


@pytest.mark.parametrize("name", sorted(REQUIRED_GATES - {"hosted_ci"}))
def test_missing_required_gate_is_rejected(name: str) -> None:
    """Every required gate must be present in the report."""
    report = sample_report(COMMIT)
    report["gates"] = [gate for gate in report["gates"] if gate["name"] != name]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_duplicate_gate_names_are_rejected() -> None:
    """A repeated gate name cannot inflate coverage."""
    report = sample_report(COMMIT)
    report["gates"].append(dict(report["gates"][0]))
    with pytest.raises(ValueError, match="duplicate gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_unknown_gate_names_are_rejected() -> None:
    """A gate outside the versioned schema cannot replace a required one."""
    report = sample_report(COMMIT)
    report["gates"][0]["name"] = "unlisted-gate"
    with pytest.raises(ValueError, match="unknown gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize("name", ["portable_cpp", "w1", "hosted_ci"])
def test_missing_gate_count_is_rejected(name: str) -> None:
    """A required count key cannot be omitted."""
    report = sample_report(COMMIT)
    gate = next(item for item in report["gates"] if item["name"] == name)
    del gate["counts"][RELEASE_BUNDLE.REQUIRED_COUNTS[name][0]]
    with pytest.raises(ValueError, match="missing counts|invalid count"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize("value", ["51", True, -1])
def test_invalid_gate_count_is_rejected(value: object) -> None:
    """Counts must be non-negative integers, not strings or booleans."""
    report = sample_report(COMMIT)
    report["gates"][2]["counts"]["passed"] = value
    with pytest.raises(ValueError, match="invalid count"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_empty_gate_counts_are_rejected() -> None:
    """A gate with no parsed counts is not evidence."""
    report = sample_report(COMMIT)
    report["gates"][2]["counts"] = {}
    with pytest.raises(ValueError, match="missing counts"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_missing_gate_command_is_rejected() -> None:
    """Every gate must record the command it executed."""
    report = sample_report(COMMIT)
    del report["gates"][0]["command"]
    with pytest.raises(ValueError, match="missing command"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_missing_step_metadata_is_rejected() -> None:
    """Execution metadata must include at least one recorded step."""
    report = sample_report(COMMIT)
    report["gates"][0]["steps"] = []
    with pytest.raises(ValueError, match="missing execution metadata"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize("key", ["generated_at", "started_at", "toolchain"])
def test_missing_report_metadata_is_rejected(key: str) -> None:
    """Top-level execution metadata cannot be absent."""
    report = sample_report(COMMIT)
    del report[key]
    with pytest.raises(ValueError, match="missing execution metadata"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_nonzero_gate_exit_code_is_rejected() -> None:
    """A gate that exited nonzero cannot support a release."""
    report = sample_report(COMMIT)
    report["gates"][0]["exit_code"] = 1
    with pytest.raises(ValueError, match="nonzero exit code"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize("version", [2, "1", True, None])
def test_unexpected_schema_version_is_rejected(version: object) -> None:
    """Only the pinned schema version is accepted."""
    report = sample_report(COMMIT)
    report["schema_version"] = version
    with pytest.raises(ValueError, match="schema version"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_wrong_branch_is_rejected() -> None:
    """A report from another branch cannot qualify main."""
    report = sample_report(COMMIT)
    report["branch"] = "dev"
    with pytest.raises(ValueError, match="report branch"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_wrong_release_scope_is_rejected() -> None:
    """A report's scope must equal the requested build scope."""
    report = sample_report(COMMIT)
    report["release_scope"] = PERSISTENCE_SCOPE
    with pytest.raises(ValueError, match="report release scope"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    with pytest.raises(ValueError, match="report release scope"):
        RELEASE_BUNDLE.validate_report(
            sample_report(COMMIT), COMMIT, PERSISTENCE_SCOPE
        )


def test_unknown_release_scope_is_rejected() -> None:
    """An undefined scope cannot be used to build a bundle."""
    with pytest.raises(ValueError, match="unknown release scope"):
        RELEASE_BUNDLE.validate_report(sample_report(COMMIT), COMMIT, "OTHER")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("report", "report commit"),
        ("ci", "ci commit"),
        ("match", "head SHA does not match"),
    ],
)
def test_inconsistent_commits_are_rejected(mutate: str, match: str) -> None:
    """Report, CI and release commits must name one verified HEAD."""
    report = sample_report(COMMIT)
    if mutate == "report":
        report["commit"] = OTHER_COMMIT
    elif mutate == "ci":
        host_gate(report)["counts"]["head_sha"] = CI_COMMIT
    else:
        host_gate(report)["counts"]["head_sha_match"] = False
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_overall_status_must_match_gate_results() -> None:
    """A FAIL overall cannot accompany all-PASS gates."""
    report = sample_report(COMMIT)
    report["overall"] = "FAIL"
    with pytest.raises(ValueError, match="overall status"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_gate_result_must_be_pass() -> None:
    """Every gate must carry a PASS verdict."""
    report = sample_report(COMMIT)
    report["gates"][0]["result"] = "FAIL"
    with pytest.raises(ValueError, match="required gate failed"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_hosted_conclusion_must_be_success() -> None:
    """A hosted run without a success conclusion is not evidence."""
    report = sample_report(COMMIT)
    host_gate(report)["counts"]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="hosted CI conclusion"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize("result", ["MAYBE", None, "Gated_Not_Run"])
def test_unknown_native_result_is_rejected(result: object) -> None:
    """Only the two native-q result tokens are accepted."""
    report = sample_report(COMMIT)
    host_gate(report)["native_q"]["result"] = result
    with pytest.raises(ValueError, match="not accepted"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize(
    "key",
    [
        "enabled",
        "conclusion",
        "result",
        "tests_executed",
        "tests_skipped",
        "tests_failed",
        "evidence",
        "reason",
    ],
)
def test_missing_native_field_is_rejected(key: str) -> None:
    """The native record must carry every schema field."""
    report = sample_report(COMMIT)
    del host_gate(report)["native_q"][key]
    with pytest.raises(ValueError, match="native q field missing"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("enabled", "explicit enablement"),
        ("executed", "executed tests"),
        ("skipped", "no skipped or failed"),
        ("failed", "no skipped or failed"),
        ("conclusion", "successful job"),
    ],
)
def test_native_pass_requires_full_evidence(mutate: str, match: str) -> None:
    """A native PASS needs enablement, executed tests and no skips/fails."""
    report = passed_report(COMMIT)
    native = host_gate(report)["native_q"]
    if mutate == "enabled":
        native["enabled"] = False
    elif mutate == "executed":
        native["tests_executed"] = 0
    elif mutate == "skipped":
        native["tests_skipped"] = 2
    elif mutate == "failed":
        native["tests_failed"] = 1
    else:
        native["conclusion"] = "skipped"
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_gated_native_requires_skipped_conclusion() -> None:
    """A GATED_NOT_RUN record must describe a skipped job."""
    report = sample_report(COMMIT)
    host_gate(report)["native_q"]["conclusion"] = "success"
    with pytest.raises(ValueError, match="requires a skipped job"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_gated_native_must_not_report_executed_tests() -> None:
    """A gated tier cannot claim executed tests."""
    report = sample_report(COMMIT)
    host_gate(report)["native_q"]["tests_executed"] = 8
    with pytest.raises(ValueError, match="must execute no tests"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_gated_native_is_rejected_for_persistence_scope() -> None:
    """The persistence scope requires an executed native tier."""
    report = sample_report(COMMIT)
    report["release_scope"] = PERSISTENCE_SCOPE
    with pytest.raises(ValueError, match="not permitted"):
        RELEASE_BUNDLE.validate_report(report, COMMIT, PERSISTENCE_SCOPE)


def test_manifest_states_scope_without_persistence_claim() -> None:
    """The portable manifest states its scope and limits q persistence."""
    report = sample_report(COMMIT)
    manifest = RELEASE_BUNDLE.render_manifest(
        "2026-11-27T00:00:00Z",
        COMMIT,
        1,
        (1, 1),
        0,
        "main only (local and remote)",
        "PR required.",
        report=report,
        rows=[],
    )
    assert f"Release scope: {RELEASE_SCOPE}" in manifest
    assert (
        "q persistence is NOT QUALIFIED at release scope PORTABLE_RESEARCH"
        in manifest
    )
    assert "Q_PERSISTENCE" not in manifest
    assert f"Native q tier: GATED_NOT_RUN: {GATED_REASON}." in manifest
    assert "GATED / NOT RUN" not in manifest


def test_manifest_renders_native_pass_from_report() -> None:
    """A passed native tier is rendered from its recorded explanation."""
    report = passed_report(COMMIT)
    manifest = RELEASE_BUNDLE.render_manifest(
        "2026-11-27T00:00:00Z",
        COMMIT,
        1,
        (1, 1),
        0,
        "main only (local and remote)",
        "PR required.",
        report=report,
        rows=[],
    )
    assert f"Native q tier: PASS: {PASS_REASON}." in manifest
    assert "GATED_NOT_RUN" not in manifest
    assert "q persistence is NOT QUALIFIED" in manifest


def test_bundle_verify_checks_instrument_digests(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A bundle with valid snapshots verifies successfully."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    assert result["instrument_snapshots"] == 2
    assert result["native_q"] == "GATED_NOT_RUN"


def test_stage_optional_evidence_excludes_auxiliary_report(
    sandbox: Sandbox, workspace: Path
) -> None:
    """An assurance copy of the report is not staged as bundle evidence."""
    assurance = sandbox.repo / "data" / "assurance"
    assurance.mkdir(parents=True)
    (assurance / "english-audit.json").write_text("[]", encoding="utf-8")
    (assurance / "verification-report.json").write_text("{}", encoding="utf-8")
    quant_root = workspace / "quant"
    quant_root.mkdir()
    RELEASE_BUNDLE.stage_optional_evidence(quant_root)
    staged = quant_root / "evidence" / "assurance"
    assert (staged / "english-audit.json").is_file()
    assert not (staged / "verification-report.json").exists()


def test_bundle_verify_rejects_auxiliary_report(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A pre-bundle assurance report copy cannot support a release."""
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, auxiliary_report=True
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any(
        "auxiliary verification report is not permitted" in error
        for error in result["errors"]
    )


def test_bundle_keeps_only_canonical_report(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """The canonical report is bundled; the auxiliary copy is absent."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
    assert RELEASE_BUNDLE.REPORT_ENTRY in names
    assert RELEASE_BUNDLE.AUXILIARY_REPORT_ENTRY not in names


def test_bundle_verify_rejects_corrupt_snapshot(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A bundled snapshot with wrong bytes is detected by verify."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=True)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any(
        "instrument snapshot SHA-256 mismatch" in error
        for error in result["errors"]
    )


def test_bundle_with_native_pass_verifies(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A bundle with an evidenced native PASS verifies end to end."""
    report = passed_report(COMMIT)
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, report=report
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    assert result["native_q"] == "PASS"
    with zipfile.ZipFile(bundle) as archive:
        manifest = archive.read("quant/MANIFEST.md").decode("utf-8")
    assert f"Native q tier: PASS: {PASS_REASON}." in manifest


def test_bundle_scope_mismatch_fails(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """Verification under the wrong release scope fails closed."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    result = RELEASE_BUNDLE.verify_bundle(bundle, PERSISTENCE_SCOPE)
    assert not result["ok"]
    assert any("report release scope" in error for error in result["errors"])


def evidence_summary(audit_path: Path) -> dict:
    """Return the exact summary of dispositions regenerated from an audit."""
    raw = audit_path.read_bytes()
    findings = AUDIT_DISPOSITION.parse_findings(json.loads(raw))
    report = AUDIT_DISPOSITION.build_report(raw, findings)
    review = AUDIT_DISPOSITION.build_review_report(raw, report)
    return AUDIT_DISPOSITION.verify_payloads(raw, report, review)


def coherent_report(findings: list[dict] | None = None) -> dict:
    """Return a sample report whose audit gate describes the given findings."""
    report = sample_report(COMMIT)
    raw = json.dumps(
        findings if findings is not None else audit_findings(), indent=2
    ).encode("utf-8")
    parsed = AUDIT_DISPOSITION.parse_findings(json.loads(raw))
    dispositions = AUDIT_DISPOSITION.build_report(raw, parsed)
    review = AUDIT_DISPOSITION.build_review_report(raw, dispositions)
    summary = AUDIT_DISPOSITION.verify_payloads(raw, dispositions, review)
    gate = named_gate(report, "audit_disposition_evidence")
    gate["counts"] = {"applicable": True, **summary}
    gate["result"] = "PASS"
    gate["reason"] = EVIDENCE_REASON
    gate["steps"][0]["output"] = "verify: PASS"
    return report


def write_test_bundle(
    workspace: Path,
    snapshots: SnapshotFixture,
    *,
    corrupt: bool,
    report: dict | None = None,
    scope: str = RELEASE_SCOPE,
    audit_evidence: bool | str | None = None,
    sync_evidence: bool = True,
    auxiliary_report: bool = False,
) -> Path:
    """Write a small bundle with a report, provenance and snapshots.

    By default the report's audit gate is kept coherent with the evidence
    staged in the bundle. ``sync_evidence=False`` lets a caller write a stale
    report for negative coherence tests.
    """
    quant_root = workspace / "quant"
    quant_root.mkdir()
    evidence = quant_root / "evidence"
    evidence.mkdir()
    if report is None:
        report = sample_report(COMMIT)
    audit_path: Path | None = None
    if audit_evidence is not None:
        assurance = evidence / "assurance"
        assurance.mkdir(parents=True, exist_ok=True)
        audit_path = assurance / "english-audit.json"
        audit_path.write_bytes(
            json.dumps(audit_findings(), indent=2).encode("utf-8")
        )
        RELEASE_BUNDLE.regenerate_audit_dispositions(quant_root)
        if audit_evidence == "stale":
            path = assurance / "audit-dispositions.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["source_digest"] = "0" * 64
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    if sync_evidence:
        gate = named_gate(report, "audit_disposition_evidence")
        if audit_path is None:
            gate["result"] = "NOT_APPLICABLE"
            gate["counts"] = {
                "applicable": False,
                **AUDIT_DISPOSITION.empty_evidence_summary(),
                "missing_files": ["evidence/assurance/english-audit.json"],
            }
            gate["reason"] = (
                "generated audit evidence is absent from this bundle"
            )
            gate["steps"][0]["output"] = gate["reason"]
        else:
            summary = evidence_summary(audit_path)
            gate["counts"] = {"applicable": True, **summary}
            gate["result"] = "PASS"
            gate["reason"] = EVIDENCE_REASON
            gate["steps"][0]["output"] = "verify: PASS"
    (evidence / "verification-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    governance = quant_root / "governance"
    governance.mkdir()
    (governance / "instrument-master-snapshots.json").write_text(
        snapshots.provenance.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for relative, payload in snapshots.payloads.items():
        path = quant_root / RELEASE_BUNDLE.INSTRUMENT_DIR / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupt\n" if corrupt else payload)
    if auxiliary_report:
        assurance = quant_root / "evidence" / "assurance"
        assurance.mkdir(parents=True, exist_ok=True)
        (assurance / "verification-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
    manifest = RELEASE_BUNDLE.render_manifest(
        "2026-11-27T00:00:00Z",
        COMMIT,
        1,
        (1, 1),
        0,
        "main only (local and remote)",
        "PR required.",
        report=report,
        rows=RELEASE_BUNDLE.manifest_rows(quant_root),
        scope=scope,
    )
    (quant_root / "MANIFEST.md").write_text(manifest, encoding="utf-8")
    output = workspace / "bundle.zip"
    RELEASE_BUNDLE.create_zip(quant_root, output)
    return output


ALLOWED_SKIP_ID = "tests/test_dataset_contract.py::test_skipped_case"
OTHER_SKIP_ID = "tests/test_dataset_contract.py::test_other_case"
CI_SKIP_ID = "tests/test_ci_security.py::test_skipped_case"
DISPOSITION_SKIP_ID = "tests/test_audit_disposition.py::test_skipped_case"
SKIP_REASON = "governed fixture unavailable"


def named_gate(report: dict, name: str) -> dict:
    """Return one gate from a report by name."""
    return next(gate for gate in report["gates"] if gate["name"] == name)


def manifest_for(report: dict) -> str:
    """Render a manifest from a sample report for offline assertions."""
    return RELEASE_BUNDLE.render_manifest(
        "2026-11-27T00:00:00Z",
        COMMIT,
        1,
        (1, 1),
        0,
        "main only (local and remote)",
        "PR required.",
        report=report,
        rows=[],
    )


def add_skip(
    report: dict, test_id: str | None = None, name: str = "portable_pytest"
) -> None:
    """Record one skipped test in a gate's counts and step output."""
    gate = named_gate(report, name)
    gate["counts"] = {
        **gate["counts"],
        **pytest_counts(gate["counts"]["passed"], skipped=1),
    }
    if test_id is not None:
        gate["steps"][0]["output"] = (
            f"SKIPPED [1] {test_id}:42: {SKIP_REASON}\n"
            f"{gate['counts']['passed']} passed, 1 skipped in 0.1s"
        )


def test_native_q_checker_gate_is_versioned() -> None:
    """The new checker gate cannot be omitted, duplicated or renamed."""
    report = sample_report(COMMIT)
    report["gates"] = [
        gate
        for gate in report["gates"]
        if gate["name"] != "native_q_checker_tests"
    ]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    report["gates"].append(dict(named_gate(report, "native_q_checker_tests")))
    with pytest.raises(ValueError, match="duplicate gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    named_gate(report, "native_q_checker_tests")["name"] = "unlisted"
    with pytest.raises(ValueError, match="unknown gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_ci_security_gate_is_versioned() -> None:
    """The CI security gate cannot be omitted, duplicated or renamed."""
    report = sample_report(COMMIT)
    report["gates"] = [
        gate for gate in report["gates"] if gate["name"] != "ci_security_tests"
    ]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    report["gates"].append(dict(named_gate(report, "ci_security_tests")))
    with pytest.raises(ValueError, match="duplicate gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    named_gate(report, "ci_security_tests")["name"] = "unlisted"
    with pytest.raises(ValueError, match="unknown gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_ci_security_gate_has_exact_pinned_command() -> None:
    """The pinned offline command is recorded as evidence."""
    gate = named_gate(sample_report(COMMIT), "ci_security_tests")
    assert gate["command"] == (
        "python3 -m pytest tests/test_ci_security.py -o addopts='' -q"
    )
    assert gate["counts"] == pytest_counts(11)


@pytest.mark.parametrize("test_id", [None, CI_SKIP_ID, OTHER_SKIP_ID])
def test_unapproved_ci_security_skip_fails(test_id: str | None) -> None:
    """A skip in the CI security suite needs an authored, matching reason."""
    report = sample_report(COMMIT)
    gate = named_gate(report, "ci_security_tests")
    baseline = gate["counts"]["passed"]
    gate["counts"] = pytest_counts(baseline - 1, skipped=1)
    if test_id is not None:
        gate["steps"][0]["output"] = (
            f"SKIPPED [1] {test_id}:42: {SKIP_REASON}\n"
            f"{baseline - 1} passed, 1 skipped in 0.1s"
        )
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    if test_id == CI_SKIP_ID:
        report["config"]["allowed_skips"] = [
            {"test_id": CI_SKIP_ID, "reason": SKIP_REASON}
        ]
        assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report


def test_audit_disposition_gate_is_versioned() -> None:
    """The disposition gate cannot be omitted, duplicated or renamed."""
    report = sample_report(COMMIT)
    report["gates"] = [
        gate
        for gate in report["gates"]
        if gate["name"] != "audit_disposition_tests"
    ]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    report["gates"].append(dict(named_gate(report, "audit_disposition_tests")))
    with pytest.raises(ValueError, match="duplicate gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    named_gate(report, "audit_disposition_tests")["name"] = "unlisted"
    with pytest.raises(ValueError, match="unknown gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_audit_disposition_gate_has_exact_pinned_command() -> None:
    """The mandated offline command is recorded as evidence."""
    gate = named_gate(sample_report(COMMIT), "audit_disposition_tests")
    assert gate["command"] == (
        "python3 -m pytest tests/test_audit_disposition.py -o addopts='' -q"
    )
    assert gate["counts"] == pytest_counts(31)
    assert "audit_disposition_tests" in RELEASE_BUNDLE.PYTHON_GATES


@pytest.mark.parametrize("test_id", [None, DISPOSITION_SKIP_ID, OTHER_SKIP_ID])
def test_unapproved_audit_disposition_skip_fails(test_id: str | None) -> None:
    """A skip in the disposition suite needs an authored, matching reason."""
    report = sample_report(COMMIT)
    gate = named_gate(report, "audit_disposition_tests")
    baseline = gate["counts"]["passed"]
    gate["counts"] = pytest_counts(baseline - 1, skipped=1)
    if test_id is not None:
        gate["steps"][0]["output"] = (
            f"SKIPPED [1] {test_id}:42: {SKIP_REASON}\n"
            f"{baseline - 1} passed, 1 skipped in 0.1s"
        )
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    if test_id == DISPOSITION_SKIP_ID:
        report["config"]["allowed_skips"] = [
            {"test_id": test_id, "reason": SKIP_REASON}
        ]
        assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        (RELEASE_BUNDLE.BINARY_PATH_KEY, "build/quant_contract", "binary path"),
        (RELEASE_BUNDLE.BINARY_BUILD_DIR_KEY, "build", "build directory"),
        (RELEASE_BUNDLE.BINARY_DIGEST_KEY, "", "invalid"),
        (RELEASE_BUNDLE.BINARY_DIGEST_KEY, "deadbeef", "invalid"),
        (
            RELEASE_BUNDLE.BINARY_DIGEST_KEY,
            BINARY_DIGEST.upper(),
            "invalid",
        ),
        (RELEASE_BUNDLE.BINARY_COMMIT_KEY, COMMIT[:-1] + "f", "does not match"),
    ],
    ids=[
        "stale-path",
        "stale-build-dir",
        "empty-digest",
        "short-digest",
        "uppercase",
        "commit",
    ],
)
def test_canonical_binary_evidence_is_validated(
    field: str, value: str, match: str
) -> None:
    """Wrong binary evidence cannot support a release."""
    report = sample_report(COMMIT)
    named_gate(report, "portable_pytest")["counts"][field] = value
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_canonical_binary_evidence_is_rendered() -> None:
    """The manifest records the report's binary path and digest verbatim."""
    report = sample_report(COMMIT)
    manifest = manifest_for(report)
    assert (
        f"- Canonical dataset contract binary: "
        f"{RELEASE_BUNDLE.CANONICAL_BINARY_PATH} "
        f"(SHA-256 {BINARY_DIGEST}), "
        f"built in {RELEASE_BUNDLE.CANONICAL_BUILD_DIR} from commit {COMMIT}."
    ) in manifest
    assert (
        "- CI security suite (offline): 11/0/0/0/11 "
        "(passed/failed/errors/skipped/total)."
    ) in manifest


@pytest.mark.parametrize("mutate", ["report", "manifest"])
def test_stale_binary_digest_fails_bundle_verification(
    snapshots: SnapshotFixture, workspace: Path, mutate: str
) -> None:
    """A digest changed after build breaks evidence/manifest agreement."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    with zipfile.ZipFile(bundle) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    if mutate == "report":
        report = json.loads(payloads[RELEASE_BUNDLE.REPORT_ENTRY])
        gate = named_gate(report, "portable_pytest")
        gate["counts"][RELEASE_BUNDLE.BINARY_DIGEST_KEY] = STALE_DIGEST
        payloads[RELEASE_BUNDLE.REPORT_ENTRY] = json.dumps(report).encode()
    else:
        path = "quant/MANIFEST.md"
        text = payloads[path].decode("utf-8")
        payloads[path] = text.replace(BINARY_DIGEST, STALE_DIGEST).encode()
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any("verification totals" in error for error in result["errors"])


@pytest.mark.parametrize("name", RELEASE_BUNDLE.PYTHON_GATES)
@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("zero", "no tests passed"),
        ("failed", "failed or errored"),
        ("errors", "failed or errored"),
        ("skipped", "unapproved skip"),
        ("total", "inconsistent test totals"),
    ],
)
def test_python_gate_counts_must_be_clean(
    name: str, mutate: str, match: str
) -> None:
    """Every Python gate needs executed, clean, untallied counts."""
    report = sample_report(COMMIT)
    counts = named_gate(report, name)["counts"]
    baseline = dict(counts)
    if mutate == "zero":
        counts.update(pytest_counts(0))
    elif mutate == "failed":
        counts.update(pytest_counts(baseline["passed"], failed=1))
    elif mutate == "errors":
        counts.update(pytest_counts(baseline["passed"], errors=1))
    elif mutate == "skipped":
        counts.update(pytest_counts(baseline["passed"], skipped=1))
    else:
        counts["total"] += 1
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_allowlisted_skip_passes_and_is_rendered() -> None:
    """An approved skip passes and appears with counts in the manifest."""
    report = sample_report(COMMIT)
    add_skip(report, ALLOWED_SKIP_ID)
    report["config"]["allowed_skips"] = [
        {"test_id": ALLOWED_SKIP_ID, "reason": SKIP_REASON}
    ]
    assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report
    manifest = manifest_for(report)
    assert "51/0/0/1/52" in manifest
    assert "1 allowlisted skip(s)" in manifest
    assert ALLOWED_SKIP_ID in manifest
    assert SKIP_REASON in manifest


@pytest.mark.parametrize("test_id", [None, OTHER_SKIP_ID])
def test_unapproved_skip_fails(test_id: str | None) -> None:
    """A skip without an authored allowlist fails, even when hidden."""
    report = sample_report(COMMIT)
    add_skip(report, test_id)
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_hidden_skip_outside_gate_suite_fails() -> None:
    """An allowlist for another test file cannot approve a hidden skip."""
    report = sample_report(COMMIT)
    add_skip(report)
    report["config"]["allowed_skips"] = [
        {"test_id": "tests/test_research.py::test_other", "reason": "other"}
    ]
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_hidden_skip_id_mismatch_fails() -> None:
    """An allowlist for another test cannot approve a hidden skip."""
    report = sample_report(COMMIT)
    add_skip(report, OTHER_SKIP_ID)
    report["config"]["allowed_skips"] = [
        {"test_id": ALLOWED_SKIP_ID, "reason": SKIP_REASON}
    ]
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"allowed_skips": 7},
        {"allowed_skips": [{"test_id": ALLOWED_SKIP_ID}]},
        {"allowed_skips": [{"test_id": ALLOWED_SKIP_ID, "reason": ""}]},
        {"allowed_skips": [{"test_id": ALLOWED_SKIP_ID, "reason": "x"}] * 2},
        {"allowed_skips": [], "extra": 1},
    ],
    ids=[
        "missing-field",
        "wrong-type",
        "missing-reason",
        "blank-reason",
        "duplicate",
        "unknown-field",
    ],
)
def test_malformed_report_config_fails(config: dict) -> None:
    """Only the documented allowlist shape can approve a skip."""
    report = sample_report(COMMIT)
    report["config"] = config
    with pytest.raises(ValueError, match="config|allowed_skips"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_missing_report_config_fails() -> None:
    """The report config is mandatory evidence, even without skips."""
    report = sample_report(COMMIT)
    del report["config"]
    with pytest.raises(ValueError, match="missing report config"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_manifest_reports_new_gate_and_skip_counts() -> None:
    """Verification text records all five counts for the new gate."""
    manifest = manifest_for(sample_report(COMMIT))
    assert (
        "Native-q checker suite (offline): 8/0/0/0/8 "
        "(passed/failed/errors/skipped/total)."
    ) in manifest
    assert (
        "Audit disposition suite (offline): 31/0/0/0/31 "
        "(passed/failed/errors/skipped/total)."
    ) in manifest
    assert (
        "Python gate skip policy: 0 allowlisted skip(s): none; "
        "unapproved skips FAIL."
    ) in manifest
    for name in (
        "Portable pytest",
        "Pinned instrument-master (W1)",
        "Release bundle suite",
        "Verification report suite",
    ):
        assert name in manifest


@pytest.mark.parametrize(
    "mutate", ["manifest-counts", "manifest-skips", "embedded-report"]
)
def test_report_manifest_mismatch_fails(
    snapshots: SnapshotFixture,
    workspace: Path,
    mutate: str,
) -> None:
    """verify rejects a manifest or report that contradicts its evidence."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    with zipfile.ZipFile(bundle) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    if mutate == "embedded-report":
        report = json.loads(payloads[RELEASE_BUNDLE.REPORT_ENTRY])
        named_gate(report, "native_q_checker_tests")["counts"]["skipped"] = 1
        payloads[RELEASE_BUNDLE.REPORT_ENTRY] = json.dumps(
            report, sort_keys=True
        ).encode("utf-8")
    else:
        text = payloads["quant/MANIFEST.md"].decode("utf-8")
        if mutate == "manifest-counts":
            text = text.replace(
                "Native-q checker suite (offline): 8/0/0/0/8",
                "Native-q checker suite (offline): 9/0/0/0/9",
            )
        else:
            text = text.replace(
                "Python gate skip policy: 0 allowlisted skip(s)",
                "Python gate skip policy: 1 allowlisted skip(s)",
            )
        payloads["quant/MANIFEST.md"] = text.encode("utf-8")
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any(
        "verification totals" in error or "report error" in error
        for error in result["errors"]
    ), result["errors"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("result", "native q result contradicts"),
        ("conclusion", "native q conclusion contradicts"),
        ("tests_executed", "native q count mismatch"),
        ("invalid-count", "native q invalid count"),
    ],
)
def test_native_q_invalid_job_state_fails(mutate: str, match: str) -> None:
    """Contradictory native-q records cannot support a release."""
    report = sample_report(COMMIT)
    native = host_gate(report)["native_q"]
    counts = host_gate(report)["counts"]
    if mutate == "result":
        host_gate(report)["native_q"] = passed_native()
    elif mutate == "conclusion":
        counts["native_q_conclusion"] = "success"
    elif mutate == "tests_executed":
        counts["native_q_tests_executed"] = 8
    else:
        native["tests_skipped"] = "0"
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_runner_attestation_gate_is_versioned() -> None:
    """The runner attestation suite cannot be omitted or renamed."""
    report = sample_report(COMMIT)
    report["gates"] = [
        gate
        for gate in report["gates"]
        if gate["name"] != "runner_attestation_tests"
    ]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    report["gates"].append(dict(named_gate(report, "runner_attestation_tests")))
    with pytest.raises(ValueError, match="duplicate gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    report = sample_report(COMMIT)
    named_gate(report, "runner_attestation_tests")["name"] = "unlisted"
    with pytest.raises(ValueError, match="unknown gate names"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_runner_attestation_gate_has_exact_pinned_command() -> None:
    """The mandated offline runner attestation command is recorded."""
    gate = named_gate(sample_report(COMMIT), "runner_attestation_tests")
    assert gate["command"] == (
        "python3 -m pytest tests/test_runner_attestation.py " "-o addopts='' -q"
    )
    assert gate["counts"] == pytest_counts(15)
    assert "runner_attestation_tests" in RELEASE_BUNDLE.PYTHON_GATES


@pytest.mark.parametrize(
    "test_id", [None, "tests/test_runner_attestation.py::t"]
)
def test_unapproved_runner_attestation_skip_fails(test_id: str | None) -> None:
    """A skip in the attestation suite needs an authored, matching reason."""
    report = sample_report(COMMIT)
    gate = named_gate(report, "runner_attestation_tests")
    baseline = gate["counts"]["passed"]
    gate["counts"] = pytest_counts(baseline - 1, skipped=1)
    if test_id is not None:
        gate["steps"][0]["output"] = (
            f"SKIPPED [1] {test_id}:42: {SKIP_REASON}\n"
            f"{baseline - 1} passed, 1 skipped in 0.1s"
        )
    with pytest.raises(ValueError, match="unapproved skip"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)
    if test_id is not None:
        report["config"]["allowed_skips"] = [
            {"test_id": test_id, "reason": SKIP_REASON}
        ]
        assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report


def test_evidence_gate_is_versioned_and_pinned() -> None:
    """The evidence gate is distinct from the disposition test suite."""
    report = sample_report(COMMIT)
    assert "audit_disposition_evidence" in RELEASE_BUNDLE.REQUIRED_GATES
    assert "audit_disposition_evidence" not in RELEASE_BUNDLE.PYTHON_GATES
    gate = named_gate(report, "audit_disposition_evidence")
    assert gate["command"] == RELEASE_BUNDLE.AUDIT_EVIDENCE_COMMAND
    assert gate["command"] == (
        "python3 scripts/audit_disposition.py verify "
        "--input data/assurance/english-audit.json "
        "--path data/assurance/audit-dispositions.json "
        "--review data/assurance/audit-disposition-review.json"
    )
    report["gates"] = [
        item
        for item in report["gates"]
        if item["name"] != "audit_disposition_evidence"
    ]
    with pytest.raises(ValueError, match="missing required gates"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_evidence_gate_not_applicable_is_accepted() -> None:
    """A clean checkout with no generated evidence is honestly reported."""
    report = sample_report(COMMIT)
    gate = named_gate(report, "audit_disposition_evidence")
    gate["result"] = "NOT_APPLICABLE"
    gate["counts"] = {
        "applicable": False,
        "source_digest": "",
        "finding_count": 0,
        "disposition_count": 0,
        "counts_by_class": {},
        "owner_status": {},
        "missing_files": ["data/assurance/audit-dispositions.json"],
    }
    gate["reason"] = "generated audit evidence is absent from this checkout"
    assert RELEASE_BUNDLE.validate_report(report, COMMIT) is report
    assert "Audit disposition evidence: NOT_APPLICABLE" in manifest_for(report)
    gate["counts"]["applicable"] = True
    with pytest.raises(ValueError, match="NOT_APPLICABLE needs a reason"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("digest", "invalid source digest"),
        ("coverage", "coverage mismatch"),
        ("class", "counts by class"),
        ("owner", "invalid owner status"),
        ("verify", "verify did not report PASS"),
    ],
)
def test_evidence_gate_rejects_invalid_evidence(
    mutate: str, match: str
) -> None:
    """A PASS verdict needs exact, internally consistent evidence counts."""
    report = sample_report(COMMIT)
    gate = named_gate(report, "audit_disposition_evidence")
    if mutate == "digest":
        gate["counts"]["source_digest"] = "not-a-digest"
    elif mutate == "coverage":
        gate["counts"]["finding_count"] = 108
    elif mutate == "class":
        gate["counts"]["counts_by_class"] = {"CORRECT_WORDING": 1}
    elif mutate == "owner":
        gate["counts"]["owner_status"]["CORRECT_WORDING"] = "OPEN"
    else:
        gate["steps"][0]["output"] = "no verification output"
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("native_q_runner_execution", "OBSERVED", "must not be claimed"),
        (
            "runner_attestation_mechanism",
            "ASSUMED",
            "mechanism is not recorded",
        ),
        ("native_q_runtime", "PASS", "runtime contradicts"),
    ],
)
def test_runner_status_fields_are_validated(
    field: str, value: str, match: str
) -> None:
    """A skipped job must not imply runner execution or a native-q pass."""
    report = sample_report(COMMIT)
    host_gate(report)["counts"][field] = value
    with pytest.raises(ValueError, match=match):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_manifest_derives_runner_status_from_report() -> None:
    """The manifest states the recorded runtime and runner observation."""
    manifest = manifest_for(sample_report(COMMIT))
    assert "Native q runtime: GATED_NOT_RUN." in manifest
    assert (
        "Runner execution: NOT_OBSERVED; "
        "runner attestation mechanism: CHECKED." in manifest
    )
    assert "native q passed" not in manifest.lower()
    assert (
        f"Audit disposition evidence: PASS: source digest {EVIDENCE_DIGEST}"
        in manifest
    )


def audit_findings() -> list[dict]:
    """Return two synthetic audit findings for staging tests."""
    return [
        {
            "source": "docs/example.md",
            "line": number,
            "category": "vague_scope",
            "phrase": "some",
            "context": "Some wording requires review.",
        }
        for number in (1, 2)
    ]


def test_regenerate_audit_dispositions_uses_staged_bytes(
    workspace: Path,
) -> None:
    """Stale dispositions are replaced from the staged audit bytes."""
    quant_root = workspace / "quant"
    assurance = quant_root / "evidence" / "assurance"
    assurance.mkdir(parents=True)
    raw = json.dumps(audit_findings(), indent=2).encode()
    audit = assurance / "english-audit.json"
    audit.write_bytes(raw)
    stale = {
        "schema_version": 1,
        "source_digest": "0" * 64,
        "advisory_findings": 109,
        "findings": [],
        "dispositions": [],
    }
    report_path = assurance / "audit-dispositions.json"
    review_path = assurance / "audit-disposition-review.json"
    for path in (report_path, review_path):
        path.write_text(json.dumps(stale), encoding="utf-8")
    stale_digests = {
        path: RELEASE_BUNDLE.digest_file(path)[1]
        for path in (report_path, review_path)
    }
    summary = RELEASE_BUNDLE.regenerate_audit_dispositions(quant_root)
    expected = hashlib.sha256(raw).hexdigest()
    assert summary["source_digest"] == expected
    report = json.loads(report_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert report["source_digest"] == expected
    assert review["source_digest"] == expected
    assert report["advisory_findings"] == 2
    assert len(report["dispositions"]) == 2
    assert AUDIT_DISPOSITION.verify_payloads(raw, report, review) == summary
    rows = {
        path: digest
        for path, _size, digest in RELEASE_BUNDLE.manifest_rows(quant_root)
    }
    for path in (report_path, review_path):
        relative = path.relative_to(quant_root).as_posix()
        assert rows[relative] == RELEASE_BUNDLE.digest_file(path)[1]
        assert rows[relative] != stale_digests[path]


def test_regenerate_requires_staged_audit(workspace: Path) -> None:
    """A build without the staged audit evidence fails closed."""
    quant_root = workspace / "quant"
    with pytest.raises(RuntimeError, match="staged audit evidence is missing"):
        RELEASE_BUNDLE.regenerate_audit_dispositions(quant_root)


def test_bundle_verify_reports_audit_evidence(
    snapshots: SnapshotFixture,
    workspace: Path,
) -> None:
    """Bundled audit evidence is verified offline and reported."""
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, audit_evidence=True
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    evidence = result["audit_evidence"]
    assert evidence["status"] == "PASS"
    assert evidence["finding_count"] == 2
    assert evidence["disposition_count"] == 2
    assert (
        evidence["source_digest"]
        == hashlib.sha256(
            json.dumps(audit_findings(), indent=2).encode()
        ).hexdigest()
    )
    assert evidence["owner_status"]["AMBIGUOUS_REQUIREMENT"] == "OPEN"


def test_bundle_verify_rejects_stale_bundled_evidence(
    snapshots: SnapshotFixture,
    workspace: Path,
) -> None:
    """A stale disposition digest bundled into a ZIP fails verification."""
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, audit_evidence="stale"
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any("stale source digest" in error for error in result["errors"])
    assert result["audit_evidence"]["status"] == "FAIL"


def test_bundle_verify_without_evidence_is_not_applicable(
    snapshots: SnapshotFixture,
    workspace: Path,
) -> None:
    """Absent generated evidence is reported, never silently passed."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    assert result["audit_evidence"]["status"] == "NOT_APPLICABLE"
    assert "absent from this bundle" in result["audit_evidence"]["reason"]
    assert result["report_evidence_coherence"] == "NOT_APPLICABLE"


def test_report_evidence_coherence_passes_for_coherent_bundle(
    snapshots: SnapshotFixture,
    workspace: Path,
) -> None:
    """A report describing the exact bundled evidence is coherent."""
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, audit_evidence=True
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    assert result["audit_evidence"]["status"] == "PASS"
    assert result["report_evidence_coherence"] == "PASS"


def test_summary_prints_coherence_verdict(
    snapshots: SnapshotFixture,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The verify summary prints the cross-artifact coherence verdict."""
    bundle = write_test_bundle(
        workspace, snapshots, corrupt=False, audit_evidence=True
    )
    RELEASE_BUNDLE.print_summary(bundle, RELEASE_BUNDLE.verify_bundle(bundle))
    assert "Report/evidence coherence: PASS" in capsys.readouterr().out


def mutate_evidence_count(report: dict, field: str) -> None:
    """Change one recorded evidence field of a coherent report."""
    counts = named_gate(report, "audit_disposition_evidence")["counts"]
    if field == "source_digest":
        counts[field] = "0" * 64
    elif field in ("finding_count", "disposition_count"):
        counts[field] += 1
    else:
        counts["counts_by_class"]["CORRECT_WORDING"] += 1


@pytest.mark.parametrize(
    ("field", "match"),
    [
        ("source_digest", "source_digest mismatch"),
        ("finding_count", "finding_count mismatch"),
        ("disposition_count", "disposition_count mismatch"),
        ("counts_by_class", "counts_by_class mismatch"),
    ],
)
def test_report_evidence_coherence_rejects_changed_counts(
    snapshots: SnapshotFixture,
    workspace: Path,
    field: str,
    match: str,
) -> None:
    """A report contradicting the ZIP evidence fails verification."""
    report = coherent_report()
    mutate_evidence_count(report, field)
    bundle = write_test_bundle(
        workspace,
        snapshots,
        corrupt=False,
        audit_evidence=True,
        report=report,
        sync_evidence=False,
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    assert any(match in error for error in result["errors"]), result["errors"]
    assert result["report_evidence_coherence"] == "FAIL"


def test_old_report_with_new_audit_fails_verification(
    snapshots: SnapshotFixture,
    workspace: Path,
) -> None:
    """The reported digest and counts must come from the bundled audit."""
    old_findings = [
        {**item, "source": item["source"] + ".old"} for item in audit_findings()
    ]
    old_findings.append(
        {
            "source": "docs/old-only.md",
            "line": 9,
            "category": "informal_verb",
            "phrase": "stuff",
            "context": "Old wording requires review.",
        }
    )
    bundle = write_test_bundle(
        workspace,
        snapshots,
        corrupt=False,
        audit_evidence=True,
        report=coherent_report(old_findings),
        sync_evidence=False,
    )
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert not result["ok"]
    errors = result["errors"]
    assert any("source_digest mismatch" in error for error in errors)
    assert any("finding_count mismatch" in error for error in errors)
    assert any("disposition_count mismatch" in error for error in errors)
    assert result["report_evidence_coherence"] == "FAIL"
