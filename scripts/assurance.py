#!/usr/bin/env python3
"""QUANT assurance: one-file gate, polish and terminology audit.

Usage:
  python3 scripts/assurance.py check [--loop N]
  python3 scripts/assurance.py polish [--loop N]
  python3 scripts/assurance.py audit [--strict] [--json-out PATH]
  python3 scripts/assurance.py status

The gate is fail-closed: a step that cannot run is FAIL unless it is explicitly
optional, in which case it is recorded as SKIP. Every step runs even after a
failure, so one failure cannot hide another. Check mode never mutates the tree.
Polish mode applies formatters first, then runs the same checks and verifies
that the gate itself changes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "git-policy.json").read_text())
CPP_FILES = (
    sorted((ROOT / "include").rglob("*.hpp"))
    + sorted((ROOT / "src").rglob("*.cpp"))
    + [ROOT / "tests" / "tests.cpp"]
)
PY_FILES = [
    ROOT / "python" / "research.py",
    ROOT / "tests" / "test_research.py",
    ROOT / "tests" / "test_kdbx.py",
    ROOT / "scripts" / "assurance.py",
]
CLANG_STYLE = "{BasedOnStyle: Google, SortIncludes: false}"

PATTERNS = (
    (
        "tautology",
        "error",
        re.compile(
            r"\b(?:exact same|same exact|end result|final outcome|"
            r"each and every|"
            r"basic fundamentals|added bonus|free gift|"
            r"pre[- ]plan(?:ned|ning)?|"
            r"reason why|consensus of opinion|refer back|repeat again|"
            r"revert back|"
            r"merge together|combine together|currently existing|"
            r"unexpected surprise|true fact|necessary prerequisite|"
            r"past history|"
            r"future plans|close proximity|mutual cooperation|"
            r"first introduced)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "banned_term",
        "review",
        re.compile(
            r"\b(?:bulletproof|world[- ]class|best[- ]in[- ]class|"
            r"revolutionary|"
            r"game[- ]changing|cutting[- ]edge|enterprise[- ]grade|"
            r"nasa[- ]level|"
            r"state[- ]of[- ]the[- ]art|perfect(?:ly)?|forever|"
            r"unparalleled|"
            r"magic|black[ -]box(?:es)?|seamless)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "first_person",
        "review",
        re.compile(
            r"(?<![A-Za-z0-9_/])(?:I|We|Us|Our|Ours|My|Mine|Me|"
            r"Myself|Ourselves|"
            r"we|us|our|ours|my|mine|me|myself|ourselves)"
            r"(?:['\u2019](?:m|re|ve|ll|d))?(?![A-Za-z0-9_/])"
        ),
    ),
    (
        "intent_or_filler",
        "review",
        re.compile(
            r"\b(?:intentionally|deliberately|purposely|purposefully|knowingly|"
            r"unintentionally|accidentally|inadvertently|willfully|"
            r"voluntarily|"
            r"obviously|clearly|simply|just|really|very|extremely|"
            r"basically|"
            r"actually|undoubtedly|certainly|of course|truly|merely|"
            r"essentially|"
            r"trivially)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "vague_scope",
        "review",
        re.compile(
            r"\b(?:some|many|few|several|various|numerous|often|usually|"
            r"sometimes|soon|later|fast|slow|large|small|enough|reasonable|"
            r"appropriate|significant|substantial)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unsupported_absolute",
        "review",
        re.compile(
            r"\b(?:always|never|guarantees?|ensures?|exactly once|lossless|"
            r"production[- ]ready|zero[- ]loss|zero downtime|never fails|"
            r"always available|100%)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "informal_verb",
        "review",
        re.compile(
            r"\b(?:do the needful|make sure|works fine|goes wrong|fix it|"
            r"handle it|stuff|things|nice|good|bad|huge|tiny)\b",
            re.IGNORECASE,
        ),
    ),
)
BINDING_CATEGORIES = {"tautology", "audit_ignore"}
IGNORE = re.compile(r"(?:prose|english)-audit-ignore:")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Finding:
    """A terminology candidate."""

    source: str
    line: int
    category: str
    phrase: str
    context: str


def command(
    args: list[str], cwd: Path = ROOT, timeout: int | None = None
) -> subprocess.CompletedProcess:
    """Enforce the QUANT style contract."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def git(*args: str) -> str:
    """Enforce the QUANT style contract."""
    result = command(["git", *args])
    return result.stdout.strip() if result.returncode == 0 else ""


def tracked() -> list[str]:
    """Enforce the QUANT style contract."""
    return [p for p in git("ls-files", "-z", "--cached").split("\0") if p]


def tree_fingerprint() -> str:
    """Enforce the QUANT style contract."""
    parts = [
        git("status", "--porcelain=v1", "-z"),
        git("diff", "--binary", "HEAD"),
        git("diff", "--cached", "--binary"),
    ]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def exclude_path(path: str) -> bool:
    """Enforce the QUANT style contract."""
    excluded = set(POLICY["generated_paths"]["forbidden_components"])
    excluded.update(POLICY["generated_paths"]["forbidden_component_prefixes"])
    if path.startswith(("references/", "data/", ".git/")):
        return True
    if any(part in excluded for part in path.split("/")):
        return True
    return path.endswith(tuple(POLICY["generated_paths"]["forbidden_suffixes"]))


def prose_lines(path: str, text: str) -> list[tuple[int, str]]:
    """Return authored prose with code, markup and generated text removed."""
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        out: list[tuple[int, str]] = []
        fenced = False
        for number, line in enumerate(text.splitlines(), 1):
            if FENCE.match(line.strip()):
                fenced = not fenced
                continue
            if fenced or line.lstrip().startswith("#") or "|" in line:
                continue
            if "http://" in line or "https://" in line:
                continue
            out.append((number, re.sub(r"`[^`]*`", " ", line)))
        return out
    if suffix in {".cpp", ".hpp"}:
        return [
            (n, line.strip()[2:].strip())
            for n, line in enumerate(text.splitlines(), 1)
            if line.strip().startswith("//")
        ]
    if suffix == ".q":
        return [
            (n, line.strip().lstrip("/").strip())
            for n, line in enumerate(text.splitlines(), 1)
            if line.strip().startswith("/")
            and not line.strip().startswith("//")
        ]
    if suffix in {".py", ".toml", ".yml", ".yaml", ".sh", ".txt"}:
        out: list[tuple[int, str]] = []
        in_doc = False
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if '"""' in line or "'''" in line:
                marker = '"""' if '"""' in line else "'''"
                in_doc = not in_doc if line.count(marker) % 2 else in_doc
                continue
            if in_doc or stripped.startswith("#"):
                out.append((number, stripped.lstrip("#").strip()))
        return out
    return []


def audit(json_out: Path | None = None) -> tuple[list[Finding], Path | None]:
    """Run the terminology audit and optionally write a JSON report."""
    findings: list[Finding] = []
    for path in tracked():
        if exclude_path(path):
            continue
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for number, line in prose_lines(path, text):
            if IGNORE.search(line):
                if not IGNORE.split(line, maxsplit=1)[1].strip(" :\t"):
                    findings.append(
                        Finding(
                            path, number, "audit_ignore", "bare marker", line
                        )
                    )
                continue
            for category, _severity, pattern in PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        Finding(
                            path, number, category, match.group(0), line.strip()
                        )
                    )
    output = None
    if json_out is not None:
        output = json_out
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps([asdict(item) for item in findings], indent=2),
            encoding="utf-8",
        )
    return findings, output


def check_style_references() -> bool:
    """Enforce the QUANT style contract."""
    index = (ROOT / "references" / "index.md").read_text(encoding="utf-8")
    rows = re.findall(r"\| `([^`]+)` \| <[^>]+> \| `([0-9a-f]{64})` \|", index)
    if not rows:
        return False
    ok = True
    for name, expected in rows:
        path = ROOT / "references" / name
        if (
            not path.exists()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            print(f"reference hash mismatch: {name}")
            ok = False
    return ok


def is_generated(path: str) -> bool:
    """Enforce the QUANT style contract."""
    excluded = set(POLICY["generated_paths"]["forbidden_components"])
    excluded.update(POLICY["generated_paths"]["forbidden_component_prefixes"])
    return any(part in excluded for part in path.split("/")) or path.endswith(
        tuple(POLICY["generated_paths"]["forbidden_suffixes"])
    )


def check_generated_paths() -> bool:
    """Enforce the QUANT style contract."""
    bad = [path for path in tracked() if is_generated(path)]
    for path in bad[:10]:
        print(f"generated file tracked: {path}")
    return not bad


def check_reference_integrity() -> bool:
    """Enforce the QUANT style contract."""
    directory = ROOT / "references"
    index = directory / "index.md"
    if not index.is_file():
        print("references/index.md missing")
        return False
    text = index.read_text(encoding="utf-8")
    rows = re.findall(r"\| `([^`]+)` \| <([^>]+)> \| `([0-9a-f]{64})` \|", text)
    if not rows:
        print("references/index.md has no verified entries")
        return False
    expected = {name for name, _source, _digest in rows}
    actual = {
        path.name
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".")
    }
    actual.discard("index.md")
    ok = True
    if actual != expected:
        print(f"references file set mismatch: {sorted(actual ^ expected)}")
        ok = False
    for name, _source, digest in rows:
        path = directory / name
        if not path.is_file():
            print(f"reference missing: {name}")
            ok = False
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            print(f"reference digest mismatch: {name}")
            ok = False
    return ok


def check_q_style() -> bool:
    """Enforce the QUANT style contract."""
    rules = tomllib.loads((ROOT / "q" / "qstyle.toml").read_text())
    text = (ROOT / "q" / "quant.q").read_text(encoding="utf-8")
    lines = text.splitlines()
    ok = True
    over = [
        n
        for n, line in enumerate(lines, 1)
        if len(line) > rules["max_line_length"]
    ]
    if over:
        print(f"q lines exceed {rules['max_line_length']}: {over[:10]}")
        ok = False
    if rules["required_namespace"] not in text:
        print("q namespace missing")
        ok = False
    for table in rules["required_tables"]:
        if not re.search(rf"\b{re.escape(table)}\s*:", text):
            print(f"q table missing: {table}")
            ok = False
    for function in rules["required_functions"]:
        if function not in text:
            print(f"q function missing: {function}")
            ok = False
    for token in rules["forbidden_tokens"]:
        if token in text:
            print(f"q forbidden token present: {token}")
            ok = False
    if not re.search(r"bars:\(\[date:", text):
        print("bars table is not keyed")
        ok = False
    return ok


def check_secrets() -> bool:
    """Enforce the QUANT style contract."""
    services = [
        "NIRNAY_FYERS_APP_ID",
        "NIRNAY_FYERS_APP_SECRET",
        "NIRNAY_FYERS_ACCESS_TOKEN",
        "NIRNAY_UPSTOX_ANALYTICS_TOKEN",
        "NIRNAY_KITE_API_KEY",
        "NIRNAY_KITE_API_SECRET",
    ]
    secrets = {}
    for service in services:
        result = command(
            ["security", "find-generic-password", "-s", service, "-w"]
        )
        value = result.stdout.strip()
        if len(value) >= 6:
            secrets[service] = value
    leaks = 0
    for path in tracked():
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for name, value in secrets.items():
            if value in text:
                print(f"secret leak: {path} ({name})")
                leaks += 1
    return leaks == 0


def check_git_policy(allow_dirty: bool) -> bool:
    """Enforce the QUANT style contract."""
    ok = True
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get(
            "GITHUB_REF_NAME", "HEAD"
        )
    prefixes = tuple(POLICY["branch"]["allowed_topic_prefixes"])
    active = set(POLICY["branch"]["active_topic_branches"])
    if branch != "main" and not (
        branch.startswith(prefixes) or branch in active
    ):
        print(f"branch name not allowed: {branch}")
        ok = False
    if not allow_dirty and command(["git", "status", "--porcelain"]).stdout:
        print("working tree is not clean")
        ok = False
    if command(["git", "stash", "list"]).stdout.strip():
        print("stash entries present")
        ok = False
    if len(git("worktree", "list").splitlines()) != 1:
        print("worktree count differs from policy")
        ok = False
    base = "main"
    boundary = POLICY["commit_subject"]["grandfathered_through_commit"]
    commits = git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    index = next(
        (i for i, sha in enumerate(commits) if sha.startswith(boundary)), None
    )
    commits = commits[index + 1 :] if index is not None else commits
    pattern = re.compile(POLICY["commit_subject"]["pattern"])
    for sha in commits:
        subject = git("show", "-s", "--format=%s", sha)
        if not pattern.match(subject):
            print(f"commit subject violates policy: {sha} {subject}")
            ok = False
    if branch != "main" and os.environ.get("GITHUB_ACTIONS") != "true":
        pr = command(["gh", "pr", "view", "--json", "state"])
        if pr.returncode != 0 or '"OPEN"' not in pr.stdout:
            print(f"no open pull request for topic branch: {pr.stderr.strip()}")
            ok = False
    return ok


def step(label: str, fn) -> tuple[str, bool, float]:
    """Enforce the QUANT style contract."""
    started = time.monotonic()
    print(f"\n== {label}")
    try:
        outcome = fn() if callable(fn) else command(fn).returncode == 0
        status = "PASS" if outcome else "FAIL"
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        AssertionError,
    ) as error:
        print(f"error: {error}")
        status = "FAIL"
    elapsed = time.monotonic() - started
    print(f"== {label}: {status} ({elapsed:.1f}s)")
    return status, status != "FAIL", elapsed


class Gate:
    """Fail-closed runner with a summary."""

    def __init__(self) -> None:
        """Enforce the QUANT style contract."""
        self.rows: list[tuple[str, str, float]] = []
        self.failed = False

    def run(self, label: str, fn) -> None:
        """Enforce the QUANT style contract."""
        status, ok, elapsed = step(label, fn)
        self.rows.append((label, status, elapsed))
        if not ok:
            self.failed = True

    def skip(self, label: str, reason: str) -> None:
        """Enforce the QUANT style contract."""
        print(f"== {label}: skipped ({reason})")
        self.rows.append((label, "SKIP", 0.0))

    def summary(self) -> int:
        """Enforce the QUANT style contract."""
        print("\n======== gate summary ========")
        for label, status, elapsed in self.rows:
            print(f"{label:<54} {status:<5} {elapsed:.1f}s")
        print("RESULT: FAIL" if self.failed else "RESULT: PASS")
        return 1 if self.failed else 0


def apply_format() -> bool:
    """Enforce the QUANT style contract."""
    ok = True
    if shutil.which("clang-format"):
        result = command(
            [
                "clang-format",
                "-i",
                *[str(p) for p in CPP_FILES],
            ]
        )
        ok = ok and result.returncode == 0
    if shutil.which("black"):
        result = command(
            [
                "python3",
                "-m",
                "black",
                "--quiet",
                "--line-length",
                "80",
                *[str(p) for p in PY_FILES],
            ]
        )
        ok = ok and result.returncode == 0
    return ok


def run_checks(gate: Gate, allow_dirty: bool) -> None:
    """Enforce the QUANT style contract."""
    gate.run("style reference integrity", check_style_references)
    gate.run("generated-path policy", check_generated_paths)
    gate.run("reference integrity", check_reference_integrity)
    gate.run(
        "style contract (guides)",
        lambda: command(
            [
                "python3",
                "scripts/style_contract.py",
                "--json-out",
                "data/assurance/style-contract.json",
            ]
        ).returncode
        == 0,
    )
    gate.run("q style contract", check_q_style)
    gate.run("git policy", lambda: check_git_policy(allow_dirty))
    if shutil.which("clang-format"):
        gate.run(
            "C++ formatting check",
            lambda: command(
                [
                    "clang-format",
                    "--dry-run",
                    "--Werror",
                    *[str(p) for p in CPP_FILES],
                ]
            ).returncode
            == 0,
        )
    else:
        gate.skip("C++ formatting check", "clang-format unavailable")
    if shutil.which("black"):
        gate.run(
            "Python formatting check",
            lambda: command(
                [
                    "python3",
                    "-m",
                    "black",
                    "--quiet",
                    "--check",
                    "--line-length",
                    "80",
                    *[str(p) for p in PY_FILES],
                ]
            ).returncode
            == 0,
        )
    else:
        gate.skip("Python formatting check", "black unavailable")
    findings, report = audit(ROOT / "data" / "assurance" / "english-audit.json")
    binding = [item for item in findings if item.category in BINDING_CATEGORIES]
    gate.run(
        "terminology audit (strict)",
        lambda: not binding,
    )
    print(f"terminology advisory findings: {len(findings) - len(binding)}")
    if report:
        print(f"audit report: {report}")
    if shutil.which("security"):
        gate.run("secret scan", check_secrets)
    else:
        gate.skip("secret scan", "Keychain unavailable on this platform")
    gate.run(
        "CMake configure",
        lambda: command(
            ["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Debug"]
        ).returncode
        == 0,
    )
    gate.run(
        "CMake build",
        lambda: command(["cmake", "--build", "build", "-j", "4"]).returncode
        == 0,
    )
    gate.run(
        "CTest",
        lambda: command(
            ["ctest", "--test-dir", "build", "--output-on-failure"]
        ).returncode
        == 0,
    )
    gate.run(
        "C++ unit tests",
        lambda: command([str(ROOT / "build" / "quant_tests")]).returncode == 0,
    )
    gate.run(
        "Python research self-test",
        lambda: command(
            ["python3", str(ROOT / "python" / "research.py"), "--self-test"]
        ).returncode
        == 0,
    )
    gate.run(
        "pytest",
        lambda: command(
            [
                "python3",
                "-m",
                "pytest",
                "tests/test_research.py",
                "tests/test_kdbx.py",
                "-q",
            ]
        ).returncode
        == 0,
    )
    gate.run(
        "sanitizer build",
        lambda: (
            command(
                [
                    "cmake",
                    "-S",
                    ".",
                    "-B",
                    "build-san",
                    "-DCMAKE_BUILD_TYPE=Debug",
                    "-DQUANT_SANITIZERS=ON",
                ]
            ).returncode
            == 0
            and command(["cmake", "--build", "build-san", "-j", "4"]).returncode
            == 0
        ),
    )
    gate.run(
        "sanitizer unit tests",
        lambda: command([str(ROOT / "build-san" / "quant_tests")]).returncode
        == 0,
    )
    qhome = Path(__import__("os").environ.get("QHOME", ""))
    qbin = qhome / "bin" / "q"
    if qbin.exists():
        gate.run(
            "q script load",
            lambda: command(
                [str(qbin), "-q", str(ROOT / "q" / "quant.q")], timeout=20
            ).returncode
            == 0,
        )
    else:
        gate.skip("q script load", "q runtime unavailable")


def main() -> int:
    """Enforce the QUANT style contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["check", "polish", "audit", "status"]
    )
    parser.add_argument("--loop", type=int, default=1)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--allow-dirty", action="store_true", help="skip clean-tree requirement"
    )
    args = parser.parse_args()

    if args.command == "audit":
        findings, report = audit(args.json_out)
        binding = [
            item for item in findings if item.category in BINDING_CATEGORIES
        ]
        if report:
            print(f"audit report: {report}")
        print(f"findings={len(findings)} binding={len(binding)}")
        return 1 if args.strict and binding else 0

    if args.command == "status":
        print(f"root: {ROOT}")
        print(f"branch: {git('rev-parse', '--abbrev-ref', 'HEAD')}")
        print(f"commit: {git('rev-parse', 'HEAD')}")
        print(
            f"tools: cmake={shutil.which('cmake') is not None} "
            f"clang-format={shutil.which('clang-format') is not None} "
            f"black={shutil.which('black') is not None} "
            f"gh={shutil.which('gh') is not None}"
        )
        print(f"q runtime: {(Path.home() / '.kx' / 'bin' / 'q').exists()}")
        return 0

    if args.loop < 1:
        print("--loop requires an integer >= 1")
        return 2

    initial = tree_fingerprint()
    if args.command == "polish":
        if not apply_format():
            print("format apply failed")
            return 1
    baseline = tree_fingerprint()
    final = 0
    for iteration in range(1, args.loop + 1):
        print(
            f"\n======== assurance iteration {iteration}/{args.loop} ========"
        )
        gate = Gate()
        run_checks(
            gate, allow_dirty=args.command == "polish" or args.allow_dirty
        )
        now = tree_fingerprint()
        if now == baseline:
            print("tree stability: PASS (gate mutated nothing)")
        else:
            print("tree stability: FAIL (gate modified the tree)")
            gate.failed = True
        final |= gate.summary()
    if args.command == "check" and tree_fingerprint() != initial:
        print("tree stability: FAIL (check mutated the tree)")
        final = 1
    return final


if __name__ == "__main__":
    sys.exit(main())
