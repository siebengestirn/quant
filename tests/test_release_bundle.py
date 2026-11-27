"""Offline tests for release bundle provenance and snapshot staging."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_bundle", ROOT / "scripts" / "release_bundle.py"
)
assert SPEC is not None and SPEC.loader is not None
LOADER = SPEC.loader
RELEASE_BUNDLE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(RELEASE_BUNDLE)

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
CI_COMMIT = "c" * 40
ORIGINAL_BYTES = b"pinned original snapshot bytes\n"
DECODED_BYTES = b"pinned decoded snapshot bytes\n"


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


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sandbox]:
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


@pytest.fixture
def workspace() -> Iterator[Path]:
    """Provide an approved temporary directory for staging and ZIP output."""
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        yield Path(name)


@pytest.fixture
def snapshots() -> Iterator[SnapshotFixture]:
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
    report["gates"][-1]["counts"]["head_sha"] = CI_COMMIT
    with pytest.raises(ValueError, match="ci commit"):
        RELEASE_BUNDLE.validate_commit_chain(report, COMMIT)


def test_validate_report_rejects_ci_commit_mismatch() -> None:
    """The build path rejects a report whose CI commit differs from HEAD."""
    report = sample_report(COMMIT)
    report["gates"][-1]["counts"]["head_sha"] = CI_COMMIT
    with pytest.raises(ValueError, match="ci commit"):
        RELEASE_BUNDLE.validate_report(report, COMMIT)


def test_stage_instrument_snapshots(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """Pinned snapshots are copied to their provenance paths."""
    stage = workspace / "stage"
    stage.mkdir()
    count = RELEASE_BUNDLE.stage_instrument_snapshots(
        stage, snapshots.data_root, snapshots.provenance
    )
    assert count == 2
    for relative, payload in snapshots.payloads.items():
        staged = stage / RELEASE_BUNDLE.INSTRUMENT_DIR / relative
        assert staged.read_bytes() == payload


def test_missing_instrument_snapshot_fails(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A missing pinned snapshot names the fetch remedy."""
    for path in snapshots.data_root.rglob("*"):
        if path.is_file():
            path.unlink()
            break
    with pytest.raises(RuntimeError, match="fetch_instrument_snapshots.py"):
        RELEASE_BUNDLE.stage_instrument_snapshots(
            workspace / "stage", snapshots.data_root, snapshots.provenance
        )


def test_corrupt_instrument_snapshot_fails(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """Corrupt source bytes are rejected before staging."""
    path = next(
        path for path in snapshots.data_root.rglob("*") if path.is_file()
    )
    path.write_bytes(b"corrupt snapshot bytes\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        RELEASE_BUNDLE.stage_instrument_snapshots(
            workspace / "stage", snapshots.data_root, snapshots.provenance
        )


def sample_report(commit: str) -> dict:
    """Return a minimal report that passes bundle validation."""
    simple = (
        "portable_cpp",
        "sanitizer",
        "portable_pytest",
        "style",
        "terminology_audit",
    )
    gates = [{"name": name, "counts": {}, "result": "PASS"} for name in simple]
    gates.append(
        {
            "name": "hosted_ci",
            "counts": {
                "conclusion": "success",
                "head_sha": commit,
                "head_sha_match": True,
            },
            "native_q": {
                "conclusion": "skipped",
                "result": "GATED_NOT_RUN",
            },
            "result": "PASS",
        }
    )
    return {"commit": commit, "gates": gates}


def test_bundle_verify_checks_instrument_digests(
    snapshots: SnapshotFixture, workspace: Path
) -> None:
    """A bundle with valid snapshots verifies successfully."""
    bundle = write_test_bundle(workspace, snapshots, corrupt=False)
    result = RELEASE_BUNDLE.verify_bundle(bundle)
    assert result["ok"], result["errors"]
    assert result["instrument_snapshots"] == 2


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


def write_test_bundle(
    workspace: Path, snapshots: SnapshotFixture, *, corrupt: bool
) -> Path:
    """Write a small bundle with a report, provenance and snapshots."""
    quant_root = workspace / "quant"
    quant_root.mkdir()
    evidence = quant_root / "evidence"
    evidence.mkdir()
    report = sample_report(COMMIT)
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
    )
    (quant_root / "MANIFEST.md").write_text(manifest, encoding="utf-8")
    output = workspace / "bundle.zip"
    RELEASE_BUNDLE.create_zip(quant_root, output)
    return output
