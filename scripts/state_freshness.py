#!/usr/bin/env python3
"""Detect stale state claims and broken references in tracked authored files.

The self-test uses the system temporary directory on every platform. Set
QUANT_FRESHNESS_TEMP to an existing directory to override that location.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from quant_common import HISTORICAL_MARKERS

HISTORICAL_HEADER_LINES = 12
HISTORICAL_HEADER_MARKERS = ("superseded", "historical")
MARKER_WINDOW = 5
PARAGRAPH_LIMIT = 20
STATIC_REFERENCE_DIR = "references/"
STALE_PHRASES = (
    "regeneration pending",
    "remains to be regenerated",
    "to be recorded",
    "will be regenerated",
    "to be rerun",
    "full suite is to be rerun",
    "seven open prs",
    "eight open prs",
)
STALE_RE = re.compile(
    "|".join(re.escape(phrase) for phrase in STALE_PHRASES), re.IGNORECASE
)
IDENTIFIERS = (
    (
        "commit SHA",
        re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{40}(?![0-9A-Za-z])"),
    ),
    (
        "bundle digest",
        re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9A-Za-z])"),
    ),
    ("CI run ID", re.compile(r"(?<![0-9A-Za-z])\d{11}(?![0-9A-Za-z])")),
)
MARKERS = (*HISTORICAL_MARKERS, "at pr #", "at_pr_", "old")
CURRENT_KEY_RE = re.compile(
    r"(?<![\w])([a-z_]*current[a-z_0-9]*)\s*[\"']?\s*[:=]\s*(.+)",
    re.IGNORECASE,
)
BACKTICK_RE = re.compile(r"`([^`]+)`")
PATH_EXTS = (
    ".md",
    ".json",
    ".csv",
    ".py",
    ".q",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".cpp",
    ".hpp",
    ".txt",
)
LOGICAL_ARTIFACTS = frozenset(
    {
        "contract.json",
        "manifest.json",
        "spec.json",
        "backtest.json",
        "backtest.md",
        "research.md",
    }
)
PATH_SKIP = (
    "http://",
    "https://",
    "git@",
    "-",
    "MANIFEST.md",
    "evidence/",
    "data/",
    "build/",
    "build-",
    "cache/",
    ".git/",
    "references/",
    "quant-final-",
    "quant-review-",
)
LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
NON_PATH_RE = re.compile(r"[\s<>*?\[\]|\\]")
EXTENSION_RE = re.compile(r"^\.[a-z]+$", re.IGNORECASE)


def tracked_files(root: pathlib.Path) -> tuple[list[str], str | None]:
    """Return tracked authored text paths, or a fail-closed error string."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.md", "*.csv", "*.json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return [], f"git ls-files failed: {error}"
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        return [], f"git ls-files failed: {detail}"
    files = sorted(path for path in result.stdout.splitlines() if path)
    if not files:
        return [], "no tracked authored text files found"
    return files, None


def read_lines(root: pathlib.Path, relative: str) -> list[str]:
    """Return file lines, treating an unreadable file as absent."""
    try:
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()


def is_historical(lines: list[str]) -> bool:
    """Report whether a file header declares historical or superseded state."""
    header = " ".join(lines[:HISTORICAL_HEADER_LINES]).lower()
    return any(marker in header for marker in HISTORICAL_HEADER_MARKERS)


def is_labelled(lines: list[str], index: int) -> bool:
    """Report whether a historical marker labels this logical record."""
    start = max(0, index - MARKER_WINDOW)
    paragraph = index
    while (
        paragraph > 0
        and lines[paragraph - 1].strip()
        and index - paragraph < PARAGRAPH_LIMIT
    ):
        paragraph -= 1
    context = " ".join(lines[min(start, paragraph) : index + 1]).lower()
    return any(marker in context for marker in MARKERS)


def stale_errors(
    relative: str, lines: list[str], historical: bool
) -> list[str]:
    """Report stale promise phrases with file and line coordinates."""
    errors: list[str] = []
    for index, line in enumerate(lines):
        if historical or is_labelled(lines, index):
            continue
        for match in STALE_RE.finditer(line):
            errors.append(
                f"{relative}:{index + 1}: stale phrase: "
                f"{match.group(0).lower()}"
            )
    return errors


def identifier_errors(
    relative: str, lines: list[str], historical: bool
) -> list[str]:
    """Report current-state pins and unlabelled volatile identifiers."""
    errors: list[str] = []
    static = relative.startswith(STATIC_REFERENCE_DIR)
    for index, line in enumerate(lines):
        number = index + 1
        current = CURRENT_KEY_RE.search(line)
        if current and any(
            pattern.search(current.group(2)) for _kind, pattern in IDENTIFIERS
        ):
            errors.append(
                f"{relative}:{number}: current-state identifier: "
                f"{current.group(1)}"
            )
            continue
        if historical or is_labelled(lines, index):
            continue
        for kind, pattern in IDENTIFIERS:
            if static and kind == "bundle digest":
                continue
            for match in pattern.finditer(line):
                errors.append(
                    f"{relative}:{number}: unlabelled {kind}: "
                    f"{match.group(0)[:12]}"
                )
    return errors


def is_path_token(root: pathlib.Path, token: str) -> bool:
    """Report whether a backticked token denotes a repository-relative path."""
    if token in LOGICAL_ARTIFACTS:
        return False
    if token.startswith(PATH_SKIP) or EXTENSION_RE.match(token):
        return False
    if token.endswith(PATH_EXTS):
        return True
    first = token.split("/", 1)[0]
    return "/" in token and (root / first).is_dir()


def path_errors(
    root: pathlib.Path, relative: str, lines: list[str], historical: bool
) -> list[str]:
    """Report backticked repository paths that do not resolve to a file."""
    errors: list[str] = []
    base = root / pathlib.Path(relative).parent
    for index, line in enumerate(lines):
        number = index + 1
        context = " ".join(lines[index : index + 2]).lower()
        absent_history = historical and "absent" in context
        for token in BACKTICK_RE.findall(line):
            if not is_path_token(root, token):
                continue
            candidate = LINE_SUFFIX_RE.sub("", token)
            if NON_PATH_RE.search(candidate) or candidate.endswith("/"):
                continue
            if candidate.startswith(("/", "$", "~")):
                continue
            if (root / candidate).is_file() or (base / candidate).is_file():
                continue
            if absent_history or (historical and not token.endswith(PATH_EXTS)):
                continue
            errors.append(f"{relative}:{number}: broken path: {token}")
    return errors


def release_state_errors(root: pathlib.Path) -> list[str]:
    """Validate the canonical release state when this root provides one."""
    path = root / "governance" / "release-state.json"
    if not path.is_file():
        return []
    script = root / "scripts" / "check_release_state.py"
    spec = importlib.util.spec_from_file_location("check_release_state", script)
    if spec is None or spec.loader is None:
        return ["governance/release-state.json: validator unavailable"]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return list(module.check(root))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return [f"governance/release-state.json: validator failed: {exc}"]


def check(root: pathlib.Path) -> list[str]:
    """Return precise state-freshness errors for tracked authored files."""
    resolved = root.resolve()
    files, failure = tracked_files(resolved)
    if failure:
        return [failure]
    errors: list[str] = []
    for relative in files:
        lines = read_lines(resolved, relative)
        historical = is_historical(lines)
        errors.extend(stale_errors(relative, lines, historical))
        errors.extend(identifier_errors(relative, lines, historical))
        if relative.endswith(".md"):
            errors.extend(path_errors(resolved, relative, lines, historical))
    errors.extend(release_state_errors(resolved))
    return errors


def temp_root() -> str | None:
    """Return the optional environment override for the self-test temp dir."""
    return os.environ.get("QUANT_FRESHNESS_TEMP") or None


def self_test() -> bool:
    """Verify that injected defects fail and legitimate records pass."""
    ok = True
    samples = {
        "docs/clean.md": (
            "# Clean\n"
            "# at PR #58\n"
            "final_main_sha: "
            "1a91da2fb08995c9f6ee20e373d004ad28e58a72\n"
            "See `docs/target.md`, `MANIFEST.md`, `contract.json`.\n"
        ),
        "docs/target.md": "# Target\n",
        "docs/defects.md": (
            "# Defects\n"
            "regeneration pending\n"
            "1a91da2fb08995c9f6ee20e373d004ad28e58a72\n"
            "current_main_sha: "
            "1a91da2fb08995c9f6ee20e373d004ad28e58a72\n"
            "`scripts/missing.py`\n"
        ),
        "docs/labelled.md": (
            "# Labelled\n"
            "# HISTORICAL release history\n"
            "regeneration pending\n"
        ),
        "docs/historical.md": (
            "# Historical snapshot — SUPERSEDED\n"
            "regeneration pending\n"
            "1a91da2fb08995c9f6ee20e373d004ad28e58a72\n"
            "`scripts/absent-tool.py` was absent\n"
            "`scripts/needed.py`\n"
        ),
    }
    with tempfile.TemporaryDirectory(prefix="quant-", dir=temp_root()) as tmp:
        root = pathlib.Path(tmp)
        subprocess.run(
            ["git", "init", "-q"], cwd=root, capture_output=True, check=True
        )
        (root / "docs").mkdir()
        for relative, text in samples.items():
            (root / relative).write_text(text, encoding="utf-8")
        subprocess.run(
            ["git", "add", *samples],
            cwd=root,
            capture_output=True,
            check=True,
        )
        errors = check(root)
        expectations = (
            ("docs/defects.md:2: stale phrase", True),
            ("docs/defects.md:3: unlabelled commit SHA", True),
            ("docs/defects.md:4: current-state identifier", True),
            ("docs/defects.md:5: broken path: scripts/missing.py", True),
            ("docs/clean.md:", False),
            ("docs/target.md:", False),
            ("docs/labelled.md:", False),
            ("docs/historical.md:2: stale", False),
            ("docs/historical.md:3: unlabelled", False),
            ("docs/historical.md:4: broken path", False),
            ("docs/historical.md:5: broken path: scripts/needed.py", True),
        )
        for needle, wanted in expectations:
            found = any(needle in error for error in errors)
            if found != wanted:
                print(f"self-test mismatch: {needle} (found={found})")
                ok = False
    with tempfile.TemporaryDirectory(prefix="quant-", dir=temp_root()) as tmp:
        if not any(
            "git ls-files failed" in e for e in check(pathlib.Path(tmp))
        ):
            print("self-test mismatch: fail-closed on missing repository")
            ok = False
    return ok


def main() -> int:
    """Print errors, or run the built-in self-test."""
    if "--self-test" in sys.argv[1:]:
        passed = self_test()
        print("self-test: PASS" if passed else "self-test: FAIL")
        return 0 if passed else 1
    errors = check(pathlib.Path(__file__).resolve().parents[1])
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
