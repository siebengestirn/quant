#!/usr/bin/env python3
"""Binding style contract derived from references/index.md.

Every rule here maps to a curated reference:
  cppguide.html / pyguide.html / shellguide.html -> formatters and linters
  docguide-style.html / markdown-guide.html -> documentation rules below
  jsoncguide.html -> JSON structural rules below
  qbists/style + q/qstyle.toml -> q rules below

There are no ignore files, no per-file waivers and no disabled rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_LINE = 80


def git(*args: str) -> list[str]:
    """Run a Git command and return its standard output."""
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def check_reference_index() -> list[str]:
    """Validate the reference index entries and hashes."""
    errors: list[str] = []
    index = ROOT / "references" / "index.md"
    if not index.is_file():
        return ["references/index.md missing"]
    rows = re.findall(
        r"\| `([^`]+)` \| <[^>]+> \| `([0-9a-f]{64})` \|",
        index.read_text(encoding="utf-8"),
    )
    if not rows:
        return ["references/index.md has no verified entries"]
    expected = {name for name, _digest in rows}
    actual = {
        path.name
        for path in (ROOT / "references").iterdir()
        if path.is_file() and not path.name.startswith(".")
    } - {"index.md"}
    if expected != actual:
        errors.append(
            f"reference file set mismatch: {sorted(actual ^ expected)}"
        )
    for name, digest in rows:
        path = ROOT / "references" / name
        if not path.is_file():
            errors.append(f"reference missing: {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"reference digest mismatch: {name}")
    return errors


def check_documentation() -> list[str]:
    """Validate prose style and fence language rules."""
    errors: list[str] = []
    for rel in git("ls-files", "*.md"):
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            if (
                len(line) > MAX_LINE
                and "|" not in line
                and "http://" not in line
                and "https://" not in line
            ):
                errors.append(f"{rel}:{number}: line exceeds {MAX_LINE}")
            if line != line.rstrip():
                errors.append(f"{rel}:{number}: trailing whitespace")
            if "\t" in line:
                errors.append(f"{rel}:{number}: tab character")
        if not text.endswith("\n"):
            errors.append(f"{rel}: missing final newline")
        in_fence = False
        for number, line in enumerate(lines, 1):
            if line.startswith("```"):
                if not in_fence and len(line.strip()) == 3:
                    errors.append(f"{rel}:{number}: fence without language")
                in_fence = not in_fence
        if in_fence:
            errors.append(f"{rel}: unterminated code fence")
    return errors


def check_docs_inventory() -> list[str]:
    """Enforce the bounded canonical documentation set."""
    errors: list[str] = []
    readme = ROOT / "docs" / "README.md"
    if not readme.is_file():
        errors.append("docs/README.md is required")
    tracked = [p for p in git("ls-files", "docs/*.md") if p != "docs/README.md"]
    if len(tracked) > 10:
        errors.append(
            f"docs inventory has {len(tracked)} Markdown files; limit is 10"
        )
    return errors


def check_json() -> list[str]:
    """Validate tracked JSON formatting."""
    errors: list[str] = []
    for rel in git("ls-files", "*.json"):
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"{rel}: invalid JSON: {exc}")
            continue
        canonical = json.dumps(value, indent=2, sort_keys=True) + "\n"
        if text != canonical:
            errors.append(f"{rel}: not canonical JSON (2-space, sorted keys)")
    return errors


def check_q_guide() -> list[str]:
    """Enforce mechanically checkable qbists/style rules on q/quant.q."""
    rules = tomllib.loads((ROOT / "q" / "qstyle.toml").read_text())
    step = int(rules["indent_step"])
    errors: list[str] = []
    for number, line in enumerate(
        (ROOT / "q" / "quant.q").read_text(encoding="utf-8").splitlines(), 1
    ):
        if "\t" in line:
            errors.append(f"q/quant.q:{number}: tab character")
        indent = len(line) - len(line.lstrip(" "))
        if indent % step:
            errors.append(
                f"q/quant.q:{number}: indent is not a multiple of {step}"
            )
        if len(line) > int(rules["max_line_length"]):
            errors.append(f"q/quant.q:{number}: exceeds q line limit")
    return errors


def check_tool_configs() -> list[str]:
    """Validate required formatter and linter settings."""
    errors: list[str] = []
    required = {
        ".clang-format": ["BasedOnStyle: Google", "ColumnLimit: 80"],
        ".clang-tidy": ["WarningsAsErrors: '*'", "HeaderFilterRegex"],
        "ruff.toml": [
            'select = ["E4", "E7", "E9", "F", "I", "B", "UP"]',
            "ignore = []",
        ],
        "q/qstyle.toml": [
            "max_line_length = 80",
            ".quant.asofFeatures",
        ],
    }
    for name, needles in required.items():
        path = ROOT / name
        if not path.is_file():
            errors.append(f"{name} missing")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                errors.append(f"{name} missing required contract: {needle}")
    for name in (".pylintrc", ".pylint.rc"):
        path = ROOT / name
        if not path.is_file():
            errors.append(f"{name} missing")
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^disable\s*=\s*(.+)$", text, re.MULTILINE):
            if match.group(1).strip():
                errors.append(f"{name} disables messages")
    return errors


def check_shell() -> list[str]:
    """Validate shell script syntax and style."""
    errors: list[str] = []
    shell = pathlib.Path("/usr/bin/env")
    del shell  # Shell sources are checked by shellcheck in CI when present.
    for rel in git("ls-files", "*.sh"):
        path = ROOT / rel
        if not path.is_file():
            continue
        result = subprocess.run(
            ["shellcheck", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"{rel}: shellcheck failed")
    return errors


def main() -> int:
    """Parse arguments and run the research command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    checks = {
        "reference-index": check_reference_index,
        "documentation": check_documentation,
        "docs-inventory": check_docs_inventory,
        "json": check_json,
        "tool-configs": check_tool_configs,
        "shell": check_shell,
        "q-guide": check_q_guide,
    }
    report: dict[str, list[str]] = {}
    failures = 0
    for name, check in checks.items():
        errors = check()
        report[name] = errors
        status = "PASS" if not errors else "FAIL"
        print(f"{name}: {status}")
        for error in errors[:20]:
            print(f"  {error}")
        failures += len(errors)
    if args.json_out:
        output = pathlib.Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
