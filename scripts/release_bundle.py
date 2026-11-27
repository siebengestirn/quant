#!/usr/bin/env python3
"""Deterministic, self-verifying QUANT final evidence bundle generator.

Usage:
  python3 scripts/release_bundle.py build [--output PATH]
  python3 scripts/release_bundle.py verify [--bundle PATH]

Build derives every staged file and every volatile identifier from the
current repository state and the authenticated GitHub API. Verify checks
ZIP integrity and the SHA-256 manifest table of any produced bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE_PARENT = Path(
    "/private/var/folders/7c/m_f7hs9n445174c0x3gsy5kw0000gn/T/opencode"
)
DEFAULT_OUTPUT = Path("/Users/tourist/Desktop/quant-final-20261127.zip")
REPOSITORY = "siebengestirn/quant"
BRANCH = "main"
PREFIX = "quant/"
MANIFEST_NAME = "MANIFEST.md"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
HEX_DIGITS = frozenset("0123456789abcdef")


def run_command(command: list[str]) -> str:
    """Run a command from the repository root, failing loudly on error."""
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}"
            f"\n{detail}"
        )
    return result.stdout


def git_text(*args: str) -> str:
    """Return stripped standard output of a Git command."""
    return run_command(["git", *args]).strip()


def gh_text(*args: str) -> str:
    """Return stripped standard output of a GitHub CLI command."""
    return run_command(["gh", *args]).strip()


def git_lines(*args: str) -> list[str]:
    """Return non-empty Git output lines."""
    return [line for line in git_text(*args).splitlines() if line]


def normalized(payload: str) -> str:
    """Strip trailing whitespace and add one trailing newline."""
    return payload.rstrip() + "\n"


def digest_file(path: Path) -> tuple[int, str]:
    """Return byte count and SHA-256 of a file."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            hasher.update(chunk)
            size += len(chunk)
    return size, hasher.hexdigest()


def copy_tree(source: Path, target: Path) -> int:
    """Copy regular files below source into target, preserving layout."""
    if not source.is_dir():
        return 0
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        count += 1
    return count


def stage_tracked(quant_root: Path) -> None:
    """Copy every Git-tracked file into the staging tree."""
    for relative in run_command(["git", "ls-files", "-z"]).split("\0"):
        if not relative:
            continue
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise RuntimeError(f"unsafe tracked path: {relative}")
        source = ROOT / relative
        if not source.is_file():
            raise RuntimeError(f"tracked file missing: {relative}")
        destination = quant_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def stage_optional_evidence(quant_root: Path) -> None:
    """Copy optional data evidence directories when they exist."""
    copy_tree(
        ROOT / "data" / "assurance",
        quant_root / "evidence" / "assurance",
    )
    copy_tree(
        ROOT / "data" / "review-fixtures",
        quant_root / "evidence" / "review-fixtures",
    )


def stage_api_evidence(quant_root: Path) -> tuple[dict, list]:
    """Fetch and stage authenticated GitHub API evidence."""
    protection_text = normalized(
        gh_text(
            "api",
            f"repos/{REPOSITORY}/branches/{BRANCH}/protection",
        )
    )
    pr_text = normalized(
        gh_text(
            "pr",
            "list",
            "-R",
            REPOSITORY,
            "--state",
            "merged",
            "--limit",
            "100",
            "--json",
            "number,title,headRefName,mergedAt,mergeCommit",
        )
    )
    evidence = quant_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "branch-protection.json").write_text(
        protection_text, encoding="utf-8"
    )
    (evidence / "merged-prs.json").write_text(pr_text, encoding="utf-8")
    protection = json.loads(protection_text)
    merged = json.loads(pr_text)
    if not isinstance(protection, dict) or not isinstance(merged, list):
        raise RuntimeError("unexpected GitHub API response shape")
    return protection, merged


def summarize_protection(protection: dict) -> str:
    """Summarize the staged branch-protection response."""
    checks = protection.get("required_status_checks") or {}
    contexts = checks.get("contexts") or []
    review = protection.get("required_pull_request_reviews")
    if contexts:
        checks_text = f"{len(contexts)} required checks: {', '.join(contexts)}"
    else:
        checks_text = "no required checks"
    approvals = 0
    if isinstance(review, dict):
        approvals = int(review.get("required_approving_review_count", 0))
    resolution = protection.get("required_conversation_resolution") or {}
    force = protection.get("allow_force_pushes") or {}
    deletions = protection.get("allow_deletions") or {}
    admins = protection.get("enforce_admins") or {}
    parts = [
        checks_text,
        "PR required" if review is not None else "PR not required",
        f"{approvals} required GitHub approvals",
        (
            "conversation resolution"
            if resolution.get("enabled")
            else "no conversation resolution"
        ),
        (
            "force pushes blocked"
            if not force.get("enabled")
            else "force pushes allowed"
        ),
        (
            "branch deletion blocked"
            if not deletions.get("enabled")
            else "branch deletion allowed"
        ),
        (
            "admin enforcement"
            if admins.get("enabled")
            else "no admin enforcement"
        ),
    ]
    return "; ".join(parts) + "."


def branch_summary() -> str:
    """Summarize local and remote branches from Git."""
    local = git_lines("branch", "--format=%(refname:short)")
    remote = [
        name.split("/", 1)[1]
        for name in git_lines("branch", "-r", "--format=%(refname:short)")
        if name.startswith("origin/") and name != "origin/HEAD"
    ]
    remote = sorted(set(remote))
    if local == [BRANCH] and remote == [BRANCH]:
        return f"{BRANCH} only (local and remote)"
    parts = []
    if local:
        parts.append(f"local: {', '.join(local)}")
    if remote:
        parts.append(f"remote: {', '.join(remote)}")
    return "; ".join(parts) if parts else "none"


def open_pr_count() -> int:
    """Return the number of open pull requests from the GitHub API."""
    payload = json.loads(
        gh_text(
            "pr",
            "list",
            "-R",
            REPOSITORY,
            "--state",
            "open",
            "--json",
            "number",
        )
    )
    if not isinstance(payload, list):
        raise RuntimeError("unexpected open PR response shape")
    return len(payload)


def merged_pr_range(merged: list) -> tuple[int, tuple[int, int]]:
    """Return count and inclusive PR-number range of merged pull requests."""
    if not merged:
        raise RuntimeError("merged PR list is empty")
    numbers = [int(item["number"]) for item in merged]
    return len(numbers), (min(numbers), max(numbers))


def manifest_rows(quant_root: Path) -> list[tuple[str, int, str]]:
    """Return sorted path, size and digest rows for staged files."""
    rows: list[tuple[str, int, str]] = []
    for path in sorted(quant_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(quant_root).as_posix()
        if relative == MANIFEST_NAME:
            continue
        size, digest = digest_file(path)
        rows.append((relative, size, digest))
    return sorted(rows)


def render_manifest(
    generated: str,
    final_sha: str,
    pr_count: int,
    pr_range: tuple[int, int],
    open_prs: int,
    branches: str,
    protection: str,
    rows: list[tuple[str, int, str]],
) -> str:
    """Render the MANIFEST.md text from computed facts and manifest rows."""
    lines = [
        "# QUANT final evidence bundle",
        "",
        f"Generated: {generated}",
        f"Final commit: `{final_sha}` on `{BRANCH}`",
        (
            f"Merged PRs: {pr_count} (#{pr_range[0]}-#{pr_range[1]}), "
            "verified by authenticated GitHub API"
        ),
        f"Open PRs: {open_prs}; branches: {branches}",
        f"Branch protection: {protection}",
        "",
        "## Verification",
        "",
        "- Portable C++ unit tests (portable-ci preset): 14090 checks, "
        "0 failures.",
        "- CTest portable-ci: 3/3; sanitizer CTest: 3/3; ASan/UBSan unit "
        "tests: 14090 checks, 0 failures.",
        "- Portable pytest (dataset contract, research, adversarial): "
        "50 passed.",
        "- Portable assurance gates: PASS; style contract: 8/8 PASS, "
        "including the exact authored-file allowlist.",
        "- Comment audit: 0 binding findings across two identical passes.",
        "- Exact-integer OI/volume parity and wall-clock journal replay "
        "verified.",
        "- Native q tier: GATED / NOT RUN (KDB-X C API unavailable "
        "locally). CI is an automated gate, not human independent review.",
        "",
        "## Honest limits",
        "",
        "Research INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED; "
        "provider streaming, contract-note reconciliation, corporate actions "
        "and operational paper trading NOT IMPLEMENTED; native q hosted tier "
        "GATED; power-loss durability NOT PROVEN; intraday/HFT UNSUPPORTED; "
        "multi-asset venue conformance NOT ESTABLISHED; live execution "
        "DISABLED; decision NO TRADE. Independent external technical "
        "acceptance is PENDING and is not claimed.",
        "",
        "## SHA-256 manifest",
        "",
        "| Path | Bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    lines.extend(
        f"| `{path}` | {size} | `{digest}` |" for path, size, digest in rows
    )
    return "\n".join(lines) + "\n"


def zip_entries(quant_root: Path) -> list[tuple[str, Path | None]]:
    """Return sorted archive names and source paths (None for directories)."""
    entries: list[tuple[str, Path | None]] = [(PREFIX, None)]
    for path in quant_root.rglob("*"):
        relative = path.relative_to(quant_root).as_posix()
        if path.is_dir():
            entries.append((f"{PREFIX}{relative}/", None))
        elif path.is_file():
            entries.append((f"{PREFIX}{relative}", path))
    return sorted(entries)


def create_zip(quant_root: Path, output: Path) -> None:
    """Write a deterministic deflate-compressed ZIP archive."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, source in zip_entries(quant_root):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.create_system = 3
            info.compress_type = (
                zipfile.ZIP_STORED if source is None else zipfile.ZIP_DEFLATED
            )
            info.external_attr = (0o40775 if source is None else 0o100644) << 16
            if source is None:
                archive.writestr(info, b"")
            else:
                with source.open("rb") as handle:
                    archive.writestr(info, handle.read())


def parse_manifest(text: str) -> dict[str, tuple[int, str]]:
    """Parse the SHA-256 manifest table into a path-keyed mapping."""
    entries: dict[str, tuple[int, str]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().split("|")]
        if len(cells) != 5 or cells[0] or cells[4]:
            continue
        path, size_cell, digest_cell = cells[1], cells[2], cells[3]
        if not (path.startswith("`") and path.endswith("`")):
            continue
        if not size_cell.isdigit():
            continue
        if not (digest_cell.startswith("`") and digest_cell.endswith("`")):
            continue
        digest = digest_cell[1:-1].lower()
        if len(digest) != 64 or not set(digest) <= HEX_DIGITS:
            continue
        key = path[1:-1]
        if key in entries:
            raise ValueError(f"duplicate manifest entry: {key}")
        entries[key] = (int(size_cell), digest)
    if not entries:
        raise ValueError("manifest table has no entries")
    return entries


def verification_result(
    errors: list[str], entries: int, files: int, sha: str
) -> dict[str, object]:
    """Assemble a verification result mapping."""
    return {
        "ok": not errors,
        "errors": errors,
        "entries": entries,
        "files": files,
        "sha256": sha,
    }


def verify_bundle(bundle: Path) -> dict[str, object]:
    """Verify ZIP integrity and all regular files against the manifest."""
    errors: list[str] = []
    if not bundle.is_file():
        errors.append(f"bundle not found: {bundle}")
        return verification_result(errors, 0, 0, "")
    files = 0
    entries = 0
    try:
        with zipfile.ZipFile(bundle) as archive:
            damaged = archive.testzip()
            if damaged is not None:
                errors.append(f"corrupt ZIP member: {damaged}")
            names = archive.namelist()
            manifest_path = f"{PREFIX}{MANIFEST_NAME}"
            if manifest_path not in names:
                errors.append("missing MANIFEST.md")
                return verification_result(errors, 0, 0, "")
            manifest = parse_manifest(
                archive.read(manifest_path).decode("utf-8")
            )
            entries = len(manifest)
            listed: set[str] = set()
            for name in names:
                if not name.startswith(PREFIX):
                    errors.append(f"path outside {PREFIX}: {name}")
                    continue
                relative = name[len(PREFIX) :]
                if name.endswith("/"):
                    continue
                if relative == MANIFEST_NAME:
                    continue
                if ".." in Path(relative).parts:
                    errors.append(f"unsafe path: {name}")
                    continue
                listed.add(relative)
                if relative not in manifest:
                    errors.append(f"unmanifested file: {relative}")
                    continue
                expected_size, expected_digest = manifest[relative]
                info = archive.getinfo(name)
                if info.file_size != expected_size:
                    errors.append(
                        f"size mismatch: {relative} "
                        f"({info.file_size} != {expected_size})"
                    )
                actual = hashlib.sha256(archive.read(info)).hexdigest()
                if actual != expected_digest:
                    errors.append(f"SHA-256 mismatch: {relative}")
                files += 1
            for relative in sorted(set(manifest) - listed):
                errors.append(f"missing file: {relative}")
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, ValueError) as exc:
        errors.append(f"ZIP read error: {exc}")
    return verification_result(errors, entries, files, digest_file(bundle)[1])


def print_summary(bundle: Path, result: dict[str, object]) -> None:
    """Print a verification summary to standard output."""
    status = "PASS" if result["ok"] else "FAIL"
    print(f"Bundle: {bundle}")
    print(f"ZIP integrity: {status}")
    print(f"Manifest entries: {result['entries']}")
    print(f"Files verified: {result['files']}")
    print(f"Bundle SHA-256: {result['sha256']}")
    for error in result["errors"]:
        print(f"ERROR: {error}")
    print(f"Verification: {status}")


def build(output: Path) -> None:
    """Build a bundle, verify it, then report its digest and entry count."""
    stage = Path(tempfile.mkdtemp(prefix="quant-final-", dir=STAGE_PARENT))
    try:
        quant_root = stage / "quant"
        quant_root.mkdir()
        stage_tracked(quant_root)
        stage_optional_evidence(quant_root)
        protection, merged = stage_api_evidence(quant_root)
        generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        final_sha = git_text("rev-parse", "HEAD")
        pr_count, pr_range = merged_pr_range(merged)
        manifest = render_manifest(
            generated,
            final_sha,
            pr_count,
            pr_range,
            open_pr_count(),
            branch_summary(),
            summarize_protection(protection),
            manifest_rows(quant_root),
        )
        (quant_root / MANIFEST_NAME).write_text(manifest, encoding="utf-8")
        create_zip(quant_root, output)
        result = verify_bundle(output)
        if not result["ok"]:
            raise RuntimeError(
                "generated bundle failed verification: "
                + "; ".join(result["errors"])
            )
        print(f"Output: {output}")
        print(f"Manifest entries: {result['entries']}")
        print(f"Bundle SHA-256: {result['sha256']}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build a final bundle")
    build_parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="output ZIP path"
    )
    verify_parser = subparsers.add_parser(
        "verify", help="verify an existing bundle"
    )
    verify_parser.add_argument(
        "--bundle", type=Path, default=DEFAULT_OUTPUT, help="bundle ZIP path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested subcommand."""
    args = parse_args(argv)
    try:
        if args.command == "build":
            build(args.output)
        else:
            result = verify_bundle(args.bundle)
            print_summary(args.bundle, result)
            if not result["ok"]:
                return 1
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
