#!/usr/bin/env python3
"""Self-verifying QUANT final evidence bundle generator.

Usage:
  python3 scripts/release_bundle.py build [--output PATH] [--report PATH]
      [--staging-dir PATH]
  python3 scripts/release_bundle.py verify [--bundle PATH]

Build first fetches authoritative refs and verifies that HEAD is exactly
published ``main`` with a clean tree. It then stages the exact committed
tree from ``git archive HEAD`` together with pinned raw instrument
snapshots, derives every volatile identifier from repository state and the
authenticated GitHub API, and embeds the portable verification report as
evidence. Generated timestamps vary between runs, so a build is repeatable
and self-verifying but is not byte-identical across times. Verify checks
ZIP integrity, the SHA-256 manifest table, the embedded evidence report
and the digests of any bundled instrument snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT.parent / "quant-final-20261127.zip"
DEFAULT_REPORT = ROOT / "data" / "assurance" / "verification-report.json"
REPOSITORY = "siebengestirn/quant"
BRANCH = "main"
PREFIX = "quant/"
MANIFEST_NAME = "MANIFEST.md"
REPORT_ENTRY = f"{PREFIX}evidence/verification-report.json"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
HEX_DIGITS = frozenset("0123456789abcdef")
MANIFEST_COMMIT_RE = re.compile(r"Final commit: `([0-9a-f]{40})` on `([^`]+)`")
REPO_URL_MARKER = f"repos/{REPOSITORY}"
GITHUB_REMOTE_URLS = (
    f"https://github.com/{REPOSITORY}",
    f"https://github.com/{REPOSITORY}.git",
    f"git@github.com:{REPOSITORY}.git",
    f"ssh://git@github.com/{REPOSITORY}.git",
)
INSTRUMENT_DIR = "evidence/instrument-master"
INSTRUMENT_DATA = ROOT / "data" / "instrument-master"
INSTRUMENT_PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
INSTRUMENT_PROVENANCE_ENTRY = (
    f"{PREFIX}governance/instrument-master-snapshots.json"
)
INSTRUMENT_PREFIX = f"{PREFIX}{INSTRUMENT_DIR}/"
SNAPSHOTS_REMEDY = "run python3 scripts/fetch_instrument_snapshots.py first"
SNAPSHOT_FIELDS = frozenset(
    {
        "provider",
        "market_date",
        "original_filename",
        "original_bytes",
        "original_sha256",
        "decoded_bytes",
        "decoded_sha256",
    }
)
SNAPSHOT_TEXT_FIELDS = ("provider", "market_date", "original_filename")
SNAPSHOT_SIZE_FIELDS = ("original_bytes", "decoded_bytes")
SNAPSHOT_DIGEST_FIELDS = ("original_sha256", "decoded_sha256")


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


def git_succeeds(*args: str) -> bool:
    """Return True when a Git command exits successfully."""
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def remote_url_matches(url: str, expected: str) -> bool:
    """Return True when origin names the expected repository or URL."""
    if url == expected:
        return True
    return expected == REPOSITORY and url in GITHUB_REMOTE_URLS


def check_remote_url(expected: str) -> None:
    """Fail closed when an available origin URL names another repository."""
    if not git_succeeds("remote", "get-url", "origin"):
        return
    url = git_text("remote", "get-url", "origin")
    if not remote_url_matches(url, expected):
        raise RuntimeError(
            f"origin URL {url!r} does not match expected repository "
            f"{expected!r}"
        )


def verify_provenance(expected_remote: str | None = None) -> str:
    """Fetch authoritative refs and return verified, published HEAD.

    Every check runs before any staging, so a dirty tree, a stale remote
    ref or an unpublished commit can never reach the bundle. The expected
    remote defaults to the configured GitHub repository and can be
    overridden by callers using a local origin.
    """
    run_command(["git", "fetch", "--prune", "origin"])
    branch = git_text("branch", "--show-current")
    if branch != BRANCH:
        raise RuntimeError(
            f"expected branch {BRANCH!r}, current branch is {branch!r}"
        )
    remote_ref = f"refs/remotes/origin/{BRANCH}"
    if not git_succeeds("show-ref", "--verify", "--quiet", remote_ref):
        raise RuntimeError(f"remote branch origin/{BRANCH} is missing")
    head = git_text("rev-parse", "HEAD")
    origin = git_text("rev-parse", f"origin/{BRANCH}")
    if head != origin:
        raise RuntimeError(f"HEAD {head} differs from origin/{BRANCH} {origin}")
    staged = git_lines("diff", "--cached", "--name-only")
    if staged:
        raise RuntimeError(
            f"staged changes are present: {', '.join(staged[:10])}"
        )
    dirty = git_lines("status", "--porcelain")
    if dirty:
        raise RuntimeError(
            f"working tree is not clean: {', '.join(dirty[:10])}"
        )
    containing = git_lines(
        "branch", "-r", "--contains", head, "--format=%(refname:short)"
    )
    if f"origin/{BRANCH}" not in containing:
        raise RuntimeError(f"HEAD {head} is not contained in origin/{BRANCH}")
    expected = REPOSITORY if expected_remote is None else expected_remote
    check_remote_url(expected)
    return head


def archive_member_path(member: tarfile.TarInfo) -> Path:
    """Validate and return one safe path from the committed tree."""
    path = Path(member.name)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"unsafe archive path: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError(f"unsupported archive member: {member.name}")
    return path


def stage_tracked(quant_root: Path, head: str) -> None:
    """Extract the exact verified commit from ``git archive``."""
    payload = subprocess.run(
        ["git", "archive", head],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    detail = payload.stderr.decode("utf-8", "replace").strip()
    if payload.returncode != 0:
        raise RuntimeError(f"git archive {head} failed: {detail}")
    with tarfile.open(fileobj=io.BytesIO(payload.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            relative = archive_member_path(member)
            destination = quant_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"unreadable archive member: {member.name}")
            with handle, destination.open("wb") as output:
                shutil.copyfileobj(handle, output)


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


def parse_snapshot_provenance(text: str) -> list[dict]:
    """Parse and validate pinned instrument snapshot provenance."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid instrument provenance JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(
        document.get("snapshots"), list
    ):
        raise ValueError("instrument provenance requires a snapshots array")
    entries = document["snapshots"]
    if not entries:
        raise ValueError("instrument provenance has no snapshots")
    for entry in entries:
        if not isinstance(entry, dict) or not SNAPSHOT_FIELDS <= entry.keys():
            raise ValueError(f"invalid instrument provenance entry: {entry!r}")
        for key in SNAPSHOT_TEXT_FIELDS:
            if not isinstance(entry[key], str) or not entry[key]:
                raise ValueError(f"invalid instrument provenance field: {key}")
        for key in SNAPSHOT_SIZE_FIELDS:
            value = entry[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"invalid instrument provenance field: {key}")
        for key in SNAPSHOT_DIGEST_FIELDS:
            value = entry[key]
            digest = value.lower() if isinstance(value, str) else ""
            if len(digest) != 64 or not set(digest) <= HEX_DIGITS:
                raise ValueError(f"invalid instrument provenance field: {key}")
    return entries


def snapshot_relative(provider: str, market_date: str, filename: str) -> str:
    """Return a safe provenance-relative path for one snapshot."""
    path = Path(provider) / market_date / filename
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe instrument snapshot path: {path}")
    if not path.parts or any(part in ("", ".") for part in path.parts):
        raise ValueError(f"unsafe instrument snapshot path: {path}")
    return path.as_posix()


def decoded_filename(original_filename: str) -> str:
    """Return the storage name for the decoded snapshot bytes."""
    if original_filename.endswith(".gz"):
        return original_filename[:-3]
    if original_filename.endswith(".json"):
        return original_filename[:-5] + ".decoded.json"
    raise ValueError(
        f"unsupported instrument snapshot filename: {original_filename}"
    )


def snapshot_files(entry: dict) -> list[tuple[str, int, str]]:
    """Return relative path, size and digest for both pinned byte forms."""
    provider = entry["provider"]
    market_date = entry["market_date"]
    original = snapshot_relative(
        provider, market_date, entry["original_filename"]
    )
    decoded = snapshot_relative(
        provider, market_date, decoded_filename(entry["original_filename"])
    )
    return [
        (original, entry["original_bytes"], entry["original_sha256"].lower()),
        (decoded, entry["decoded_bytes"], entry["decoded_sha256"].lower()),
    ]


def plan_from_entries(entries: list[dict]) -> list[tuple[str, int, str]]:
    """Flatten provenance entries into a unique snapshot staging plan."""
    plan: list[tuple[str, int, str]] = []
    for entry in entries:
        plan.extend(snapshot_files(entry))
    paths = [path for path, _, _ in plan]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate instrument snapshot paths in provenance")
    return plan


def instrument_plan(provenance: Path) -> list[tuple[str, int, str]]:
    """Load the pinned snapshot plan from a provenance file."""
    try:
        text = provenance.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"unreadable instrument provenance {provenance}: {exc}"
        ) from exc
    return plan_from_entries(parse_snapshot_provenance(text))


def check_snapshot_bytes(
    relative: str, payload: bytes, size: int, digest: str
) -> None:
    """Fail closed when snapshot bytes disagree with the pinned digest."""
    actual = hashlib.sha256(payload).hexdigest()
    if len(payload) != size or actual != digest:
        raise ValueError(
            f"instrument snapshot SHA-256 mismatch: {relative} "
            f"(expected {digest}, got {actual})"
        )


def stage_instrument_snapshots(
    quant_root: Path,
    data_root: Path = INSTRUMENT_DATA,
    provenance: Path = INSTRUMENT_PROVENANCE,
) -> int:
    """Stage pinned raw snapshots from the ignored data tree."""
    plan = instrument_plan(provenance)
    sources = [
        (data_root / relative, relative, size, digest)
        for relative, size, digest in plan
    ]
    missing = [
        str(source) for source, _, _, _ in sources if not source.is_file()
    ]
    if missing:
        raise RuntimeError(
            "missing pinned instrument snapshots: "
            + ", ".join(missing)
            + f"; {SNAPSHOTS_REMEDY}"
        )
    target = quant_root / INSTRUMENT_DIR
    for source, relative, size, digest in sources:
        payload = source.read_bytes()
        check_snapshot_bytes(relative, payload, size, digest)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return len(sources)


def response_urls(payload: object) -> list[str]:
    """Collect URL fields from a nested GitHub API response."""
    if isinstance(payload, dict):
        found: list[str] = []
        for key, value in payload.items():
            if key in ("url", "html_url") and isinstance(value, str):
                found.append(value)
            else:
                found.extend(response_urls(value))
        return found
    if isinstance(payload, list):
        found = []
        for item in payload:
            found.extend(response_urls(item))
        return found
    return []


def validate_api_response(
    payload: object, label: str, branch: bool = False
) -> None:
    """Reject API evidence whose URLs name another repository or branch."""
    for url in response_urls(payload):
        if "github" not in url:
            continue
        if REPO_URL_MARKER not in url:
            raise RuntimeError(f"{label}: unexpected repository in URL: {url}")
        if (
            branch
            and f"/branches/{BRANCH}/" not in url
            and not url.endswith(f"/branches/{BRANCH}")
        ):
            raise RuntimeError(f"{label}: unexpected branch in URL: {url}")


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
    validate_api_response(protection, "branch protection", branch=True)
    validate_api_response(merged, "merged pull requests")
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


def load_report(path: Path) -> dict:
    """Load the verification report, failing closed on any read error."""
    if not path.is_file():
        raise ValueError(f"verification report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"unreadable verification report {path}: {exc}"
        ) from exc


def report_gate(report: dict, name: str) -> dict:
    """Return a named gate from the verification report."""
    gates = report.get("gates")
    if not isinstance(gates, list):
        raise ValueError("verification report has no gates list")
    for gate in gates:
        if isinstance(gate, dict) and gate.get("name") == name:
            return gate
    raise ValueError(f"verification report is missing gate: {name}")


def validate_report(report: object, head: str) -> dict:
    """Reject reports that do not attest to the exact committed tree."""
    if not isinstance(report, dict):
        raise ValueError("verification report must be a JSON object")
    gates = report.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("verification report has no gates")
    for gate in gates:
        if not isinstance(gate, dict) or gate.get("result") != "PASS":
            name = gate.get("name") if isinstance(gate, dict) else repr(gate)
            raise ValueError(f"required gate failed: {name}")
    hosted = report_gate(report, "hosted_ci")
    counts = hosted.get("counts") or {}
    if counts.get("conclusion") != "success":
        raise ValueError("hosted CI conclusion is not success")
    if counts.get("head_sha_match") is not True:
        raise ValueError("hosted CI head SHA does not match HEAD")
    validate_commit_chain(report, head)
    native = hosted.get("native_q") or {}
    if native.get("result") != "GATED_NOT_RUN":
        raise ValueError("native q is not reported as GATED_NOT_RUN")
    if native.get("conclusion") == "skipped" and native.get("result") == "PASS":
        raise ValueError("native q skipped but reported as passed")
    return report


def gate_counts(report: dict, name: str) -> dict:
    """Return the parsed counts of one report gate."""
    counts = report_gate(report, name).get("counts")
    return counts if isinstance(counts, dict) else {}


def validate_commit_chain(report: dict, head: str) -> None:
    """Require report, CI and release commits to equal verified HEAD."""
    commits = {
        "report": report.get("commit"),
        "ci": gate_counts(report, "hosted_ci").get("head_sha"),
        "release": head,
    }
    for label, commit in commits.items():
        if commit != head:
            raise ValueError(
                f"{label} commit {commit!r} does not match verified HEAD "
                f"{head!r}"
            )


def render_verification(report: dict) -> list[str]:
    """Render human-readable verification totals from the report."""
    portable = gate_counts(report, "portable_cpp")
    sanitizer = gate_counts(report, "sanitizer")
    pytest = gate_counts(report, "portable_pytest")
    style = gate_counts(report, "style")
    audit = gate_counts(report, "terminology_audit")
    hosted = gate_counts(report, "hosted_ci")
    native = report_gate(report, "hosted_ci").get("native_q") or {}
    return [
        "## Verification",
        "",
        "- Portable C++ unit tests (portable-ci preset): "
        f"{portable.get('checks')} checks, "
        f"{portable.get('failures')} failures.",
        "- CTest portable-ci: "
        f"{portable.get('ctest_passed')}/{portable.get('ctest_total')}; "
        f"sanitizer CTest: "
        f"{sanitizer.get('sanitizer_ctest_passed')}/"
        f"{sanitizer.get('sanitizer_ctest_total')}; "
        "ASan/UBSan unit tests: "
        f"{sanitizer.get('sanitizer_checks')} checks, "
        f"{sanitizer.get('sanitizer_failures')} failures.",
        "- Portable pytest (dataset contract, research, adversarial): "
        f"{pytest.get('passed')} passed.",
        "- Portable assurance gates: PASS; style contract: "
        f"{style.get('pass_checks')}/{style.get('checks_total')} PASS, "
        "including the exact authored-file allowlist.",
        f"- Terminology audit: {audit.get('binding')} binding findings "
        f"and {audit.get('advisory')} advisory findings.",
        "- Exact-integer OI/volume parity and wall-clock journal replay "
        "verified.",
        f"- Hosted CI: run {hosted.get('run_id')} "
        f"({hosted.get('conclusion')}); native q job conclusion "
        f"{native.get('conclusion')} reported as "
        f"{native.get('result')}.",
        "- Native q tier: GATED / NOT RUN (KDB-X C API unavailable "
        "locally). CI is an automated gate, not human independent review.",
    ]


def stage_verification_report(quant_root: Path, report_path: Path) -> None:
    """Embed the exact verification report used to build the bundle."""
    evidence = quant_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, evidence / "verification-report.json")


def render_manifest(
    generated: str,
    final_sha: str,
    pr_count: int,
    pr_range: tuple[int, int],
    open_prs: int,
    branches: str,
    protection: str,
    *,
    report: dict,
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
    ]
    lines.extend(render_verification(report))
    lines.extend(
        [
            "",
            "## Honest limits",
            "",
            "Research INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED; "
            "provider streaming, contract-note reconciliation, corporate "
            "actions and operational paper trading NOT IMPLEMENTED; native q "
            "hosted tier GATED; power-loss durability NOT PROVEN; "
            "intraday/HFT UNSUPPORTED; multi-asset venue conformance NOT "
            "ESTABLISHED; live execution DISABLED; decision NO TRADE. "
            "Independent external technical acceptance is PENDING and is "
            "not claimed.",
            "",
            "## SHA-256 manifest",
            "",
            "| Path | Bytes | SHA-256 |",
            "|---|---:|---|",
        ]
    )
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


def manifest_final_commit(text: str) -> str:
    """Extract the final commit recorded in MANIFEST.md."""
    match = MANIFEST_COMMIT_RE.search(text)
    if not match:
        raise ValueError("MANIFEST.md has no final commit line")
    return match.group(1)


def verification_lines(manifest: str) -> list[str]:
    """Extract the verification section from MANIFEST.md."""
    lines = manifest.splitlines()
    if "## Verification" not in lines:
        raise ValueError("MANIFEST.md has no Verification section")
    start = lines.index("## Verification")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    section = lines[start:end]
    while section and not section[-1].strip():
        section.pop()
    return section


def check_evidence(
    archive: zipfile.ZipFile, manifest_text: str, errors: list[str]
) -> dict[str, object]:
    """Verify the embedded report, its commit and the rendered totals."""
    details: dict[str, object] = {
        "report": False,
        "commit": "",
        "native_q": "missing",
    }
    if REPORT_ENTRY not in archive.namelist():
        errors.append(f"missing {REPORT_ENTRY}")
        return details
    try:
        report = json.loads(archive.read(REPORT_ENTRY).decode("utf-8"))
        commit = manifest_final_commit(manifest_text)
        details["report"] = True
        details["commit"] = commit
        validate_report(report, commit)
        native = report_gate(report, "hosted_ci").get("native_q") or {}
        details["native_q"] = native.get("result", "missing")
        if verification_lines(manifest_text) != render_verification(report):
            errors.append("MANIFEST.md verification totals do not match report")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        errors.append(f"evidence report error: {exc}")
    return details


def verify_instrument_snapshots(
    archive: zipfile.ZipFile, errors: list[str]
) -> int:
    """Digest-check bundled instrument snapshots against the pinned plan."""
    names = set(archive.namelist())
    present = sorted(
        name
        for name in names
        if name.startswith(INSTRUMENT_PREFIX) and not name.endswith("/")
    )
    if not present:
        return 0
    if INSTRUMENT_PROVENANCE_ENTRY not in names:
        errors.append(
            "instrument snapshots present but provenance is missing from "
            "the bundle"
        )
        return len(present)
    try:
        text = archive.read(INSTRUMENT_PROVENANCE_ENTRY).decode("utf-8")
        plan = plan_from_entries(parse_snapshot_provenance(text))
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        errors.append(f"instrument provenance error: {exc}")
        return len(present)
    expected = {
        f"{INSTRUMENT_PREFIX}{relative}": (size, digest)
        for relative, size, digest in plan
    }
    for name in present:
        if name not in expected:
            errors.append(f"unexpected instrument snapshot: {name}")
            continue
        payload = archive.read(name)
        size, digest = expected[name]
        relative = name[len(INSTRUMENT_PREFIX) :]
        try:
            check_snapshot_bytes(relative, payload, size, digest)
        except ValueError as exc:
            errors.append(str(exc))
    for name in sorted(set(expected) - set(present)):
        errors.append(f"missing instrument snapshot: {name}")
    return len(present)


def verification_result(
    errors: list[str],
    entries: int,
    files: int,
    sha: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Assemble a verification result mapping."""
    result: dict[str, object] = {
        "ok": not errors,
        "errors": errors,
        "entries": entries,
        "files": files,
        "sha256": sha,
    }
    result.update(details or {})
    return result


def verify_bundle(bundle: Path) -> dict[str, object]:
    """Verify ZIP integrity and all regular files against the manifest."""
    errors: list[str] = []
    if not bundle.is_file():
        errors.append(f"bundle not found: {bundle}")
        return verification_result(errors, 0, 0, "")
    files = 0
    entries = 0
    details: dict[str, object] = {}
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
            manifest_text = archive.read(manifest_path).decode("utf-8")
            manifest = parse_manifest(manifest_text)
            details = check_evidence(archive, manifest_text, errors)
            details["instrument_snapshots"] = verify_instrument_snapshots(
                archive, errors
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
        details = {}
    return verification_result(
        errors, entries, files, digest_file(bundle)[1], details
    )


def print_summary(bundle: Path, result: dict[str, object]) -> None:
    """Print a verification summary to standard output."""
    status = "PASS" if result["ok"] else "FAIL"
    print(f"Bundle: {bundle}")
    print(f"ZIP integrity: {status}")
    print(f"Manifest entries: {result['entries']}")
    print(f"Files verified: {result['files']}")
    print(f"Bundle SHA-256: {result['sha256']}")
    print(f"Evidence report: {result.get('report')}")
    print(f"Manifest commit: {result.get('commit', '')}")
    print(f"Instrument snapshots: {result.get('instrument_snapshots', 0)}")
    print(f"Native q tier: {result.get('native_q')}")
    for error in result["errors"]:
        print(f"ERROR: {error}")
    print(f"Verification: {status}")


def build_stage(
    stage: Path, output: Path, report_path: Path, report: dict, head: str
) -> None:
    """Stage, render, zip and verify one bundle in a prepared directory."""
    quant_root = stage / "quant"
    quant_root.mkdir()
    stage_tracked(quant_root, head)
    stage_instrument_snapshots(quant_root)
    stage_optional_evidence(quant_root)
    stage_verification_report(quant_root, report_path)
    protection, merged = stage_api_evidence(quant_root)
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pr_count, pr_range = merged_pr_range(merged)
    manifest = render_manifest(
        generated,
        head,
        pr_count,
        pr_range,
        open_pr_count(),
        branch_summary(),
        summarize_protection(protection),
        report=report,
        rows=manifest_rows(quant_root),
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
    print_summary(output, result)


def build(
    output: Path, report_path: Path, staging_dir: Path | None = None
) -> None:
    """Guard provenance, validate evidence and build a verified bundle."""
    head = verify_provenance()
    report = validate_report(load_report(report_path), head)
    if staging_dir is None:
        with tempfile.TemporaryDirectory(prefix="quant-") as name:
            build_stage(Path(name), output, report_path, report, head)
        return
    staging_dir.mkdir(parents=True, exist_ok=True)
    if any(staging_dir.iterdir()):
        raise RuntimeError(f"staging directory is not empty: {staging_dir}")
    build_stage(staging_dir, output, report_path, report, head)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build a final bundle")
    build_parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="output ZIP path"
    )
    build_parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="verification report JSON path",
    )
    build_parser.add_argument(
        "--staging-dir",
        type=Path,
        help="keep the staging tree at this (initially empty) path",
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
            build(args.output, args.report, args.staging_dir)
        else:
            result = verify_bundle(args.bundle)
            print_summary(args.bundle, result)
            if not result["ok"]:
                return 1
    except (
        OSError,
        RuntimeError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
