#!/usr/bin/env python3
"""Generate a machine-readable portable verification report.

Usage:
  python3 scripts/verify_report.py run [--output PATH]
  python3 scripts/verify_report.py show [--path PATH]

Every gate is executed from the repository root. The report records the
exact commands, exit codes, parsed counts and results. A gated native-q
job is recorded as GATED_NOT_RUN and is never reported as passed. The
report is written even when a gate fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "assurance" / "verification-report.json"
REPOSITORY = "siebengestirn/quant"
BRANCH = "main"
AUDIT_RELATIVE = "data/assurance/english-audit.json"
AUDIT_PATH = ROOT / AUDIT_RELATIVE
BINDING_CATEGORIES = frozenset({"tautology", "audit_ignore"})
PASS = "PASS"
FAIL = "FAIL"
GATED = "GATED_NOT_RUN"
NATIVE_MARKERS = ("native-q", "native kdb")
PYTEST_FILES = (
    "tests/test_dataset_contract.py",
    "tests/test_research.py",
    "tests/test_dataset_adversarial.py",
)
CHECKS_RE = re.compile(r"checks=(\d+)\s+failures=(\d+)")
CTEST_RE = re.compile(r"(\d+)%\s+tests passed[^\n]*?\bout of\s+(\d+)")
PYTEST_TOKENS_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)")
STYLE_PASS_RE = re.compile(r"^\S+:\s*PASS\s*$", re.MULTILINE)
STYLE_LINE_RE = re.compile(r"^\S+:\s*(?:PASS|FAIL)\s*$", re.MULTILINE)
SELF_TEST_RE = re.compile(r"self-test:\s*(PASS|FAIL)")


def utc_now() -> str:
    """Return the current UTC instant in a stable Z-suffixed format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def execute(
    argv: list[str], env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run a command from the repository root, never raising on failure."""
    try:
        result = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        return 127, "", f"command unavailable: {exc}"
    return result.returncode, result.stdout, result.stderr


def command_text(argv: list[str]) -> str:
    """Render a command as a shell-quoted, reproducible string."""
    return shlex.join(argv)


def step_result(
    name: str, argv: list[str], env: dict[str, str] | None = None
) -> dict:
    """Execute one verification step and record its outcome."""
    code, stdout, stderr = execute(argv, env)
    return {
        "name": name,
        "command": command_text(argv),
        "exit_code": code,
        "counts": {},
        "result": PASS if code == 0 else FAIL,
        "output": (stdout + stderr).strip(),
    }


def finalize(step: dict, ok: bool) -> dict:
    """Combine a step's process exit code with parsed gate evidence."""
    step["result"] = PASS if step["exit_code"] == 0 and ok else FAIL
    return step


def compact(step: dict) -> dict:
    """Return a step with bounded output for the JSON report."""
    result = dict(step)
    result["output"] = result["output"][-2000:]
    return result


def make_gate(
    name: str, steps: list[dict], counts: dict, ok: bool = True
) -> dict:
    """Assemble one gate from its ordered steps and parsed counts."""
    codes = [step["exit_code"] for step in steps if step["exit_code"]]
    return {
        "name": name,
        "command": " && ".join(step["command"] for step in steps),
        "exit_code": codes[0] if codes else 0,
        "counts": counts,
        "result": (
            PASS
            if ok and all(step["result"] == PASS for step in steps)
            else FAIL
        ),
        "steps": [compact(step) for step in steps],
    }


def parse_checks(output: str) -> dict:
    """Parse the C++ test binary's checks/failures line."""
    match = CHECKS_RE.search(output)
    if not match:
        return {}
    return {
        "checks": int(match.group(1)),
        "failures": int(match.group(2)),
    }


def parse_ctest(output: str) -> dict:
    """Parse CTest's aggregate pass line."""
    match = CTEST_RE.search(output)
    if not match:
        return {}
    percent = int(match.group(1))
    total = int(match.group(2))
    return {
        "ctest_percent": percent,
        "ctest_total": total,
        "ctest_passed": total * percent // 100,
    }


def parse_pytest(output: str) -> dict:
    """Parse pytest counts, including its double-quiet progress form."""
    counts = {
        word: int(number) for number, word in PYTEST_TOKENS_RE.findall(output)
    }
    for line in output.splitlines():
        if "[100%]" in line:
            counts.setdefault("passed", line.count("."))
    return counts


def parse_style(output: str) -> dict:
    """Count PASS lines emitted by the style contract."""
    return {
        "pass_checks": len(STYLE_PASS_RE.findall(output)),
        "checks_total": len(STYLE_LINE_RE.findall(output)),
    }


def parse_self_test(output: str) -> dict:
    """Parse the state-freshness self-test verdict."""
    match = SELF_TEST_RE.search(output)
    return {"self_test": match.group(1) if match else FAIL}


def count_output_lines(output: str) -> int:
    """Count non-empty diagnostic lines in a command's output."""
    return len([line for line in output.splitlines() if line.strip()])


def git_commit() -> str:
    """Return the current commit, or an empty string if Git fails."""
    code, stdout, _stderr = execute(["git", "rev-parse", "HEAD"])
    return stdout.strip() if code == 0 else ""


def version_of(argv: list[str]) -> str:
    """Capture the first version line reported by a tool."""
    code, stdout, stderr = execute(argv)
    lines = (stdout + stderr).splitlines()
    if code != 0 or not lines:
        return "unavailable"
    return lines[0].strip()


def toolchain() -> dict:
    """Capture Python, CMake and compiler versions from commands."""
    return {
        "python": version_of([sys.executable, "--version"]),
        "cmake": version_of(["cmake", "--version"]),
        "compiler": version_of(["c++", "--version"]),
    }


def run_portable() -> dict:
    """Configure, build and test the portable CI preset."""
    configure = step_result(
        "portable configure", ["cmake", "--preset", "portable-ci"]
    )
    build = step_result(
        "portable build", ["cmake", "--build", "build-ci", "--parallel", "2"]
    )
    ctest = step_result(
        "portable ctest",
        ["ctest", "--test-dir", "build-ci", "--output-on-failure"],
    )
    binary = step_result("portable unit tests", ["./build-ci/quant_tests"])
    binary["counts"] = parse_checks(binary["output"])
    ctest["counts"] = parse_ctest(ctest["output"])
    ctest_ok = (
        ctest["counts"].get("ctest_passed")
        == ctest["counts"].get("ctest_total")
        and ctest["counts"].get("ctest_total", 0) > 0
    )
    binary = finalize(binary, binary["counts"].get("failures") == 0)
    ctest = finalize(ctest, ctest_ok)
    counts = {**binary["counts"], **ctest["counts"]}
    ok = binary["counts"].get("failures") == 0 and ctest_ok
    return make_gate(
        "portable_cpp", [configure, build, ctest, binary], counts, ok
    )


def run_sanitizer() -> dict:
    """Configure, build and test the sanitizer preset."""
    configure = step_result(
        "sanitizer configure", ["cmake", "--preset", "sanitizers"]
    )
    build = step_result(
        "sanitizer build", ["cmake", "--build", "build-san", "--parallel", "2"]
    )
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
    binary = step_result(
        "sanitizer unit tests", ["./build-san/quant_tests"], env
    )
    ctest = step_result(
        "sanitizer ctest",
        ["ctest", "--test-dir", "build-san", "--output-on-failure"],
    )
    binary["counts"] = parse_checks(binary["output"])
    ctest["counts"] = parse_ctest(ctest["output"])
    ctest_ok = (
        ctest["counts"].get("ctest_passed")
        == ctest["counts"].get("ctest_total")
        and ctest["counts"].get("ctest_total", 0) > 0
    )
    binary = finalize(binary, binary["counts"].get("failures") == 0)
    ctest = finalize(ctest, ctest_ok)
    counts = {
        "sanitizer_" + key: value for key, value in binary["counts"].items()
    }
    counts.update(
        {"sanitizer_" + key: value for key, value in ctest["counts"].items()}
    )
    ok = binary["counts"].get("failures") == 0 and ctest_ok
    return make_gate("sanitizer", [configure, build, binary, ctest], counts, ok)


def run_pytest() -> dict:
    """Run the portable Python test subset."""
    argv = ["python3", "-m", "pytest", *PYTEST_FILES, "-q"]
    test = step_result("portable pytest", argv)
    counts = parse_pytest(test["output"])
    ok = counts.get("passed", 0) > 0 and not any(
        counts.get(key) for key in ("failed", "error", "errors")
    )
    test = finalize(test, ok)
    return make_gate("portable_pytest", [test], counts, ok)


def run_style() -> dict:
    """Run the style contract and count passing checks."""
    test = step_result(
        "style contract", ["python3", "scripts/style_contract.py"]
    )
    counts = parse_style(test["output"])
    ok = counts.get("checks_total", 0) > 0 and (
        counts.get("pass_checks") == counts.get("checks_total")
    )
    test = finalize(test, ok)
    return make_gate("style", [test], counts, ok)


def run_freshness() -> dict:
    """Run the state-freshness self-test and the normal check."""
    self_test = step_result(
        "freshness self-test",
        ["python3", "scripts/state_freshness.py", "--self-test"],
    )
    check = step_result(
        "freshness check", ["python3", "scripts/state_freshness.py"]
    )
    self_counts = parse_self_test(self_test["output"])
    check_counts = {"findings": count_output_lines(check["output"])}
    self_test = finalize(self_test, self_counts["self_test"] == PASS)
    check = finalize(check, check_counts["findings"] == 0)
    counts = {**self_counts, **check_counts}
    ok = self_counts["self_test"] == PASS and check_counts["findings"] == 0
    return make_gate("state_freshness", [self_test, check], counts, ok)


def audit_counts() -> dict:
    """Count binding terminology findings from the JSON audit report."""
    counts = {"findings": 0, "binding": 0, "advisory": 0}
    payload = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("terminology audit JSON must be a list")
    counts["findings"] = len(payload)
    binding = [
        item
        for item in payload
        if isinstance(item, dict) and item.get("category") in BINDING_CATEGORIES
    ]
    counts["binding"] = len(binding)
    counts["advisory"] = counts["findings"] - counts["binding"]
    return counts


def run_audit() -> dict:
    """Run the terminology audit and verify its binding count."""
    test = step_result(
        "terminology audit",
        [
            "python3",
            "scripts/assurance.py",
            "audit",
            "--json-out",
            AUDIT_RELATIVE,
        ],
    )
    counts = {"findings": 0, "binding": 0, "advisory": 0}
    try:
        counts = audit_counts()
    except (OSError, ValueError) as exc:
        counts["error"] = str(exc)
    ok = counts.get("binding") == 0 and "error" not in counts
    test = finalize(test, ok)
    return make_gate("terminology_audit", [test], counts, ok)


def is_native_job(name: str) -> bool:
    """Identify the gated native-q job by name."""
    lower = name.lower()
    return any(marker in lower for marker in NATIVE_MARKERS)


def map_job(job: dict) -> dict:
    """Map a hosted job conclusion to a reported result."""
    name = str(job.get("name", ""))
    conclusion = job.get("conclusion")
    result = (
        GATED
        if is_native_job(name)
        else (PASS if conclusion == "success" else FAIL)
    )
    return {"name": name, "conclusion": conclusion, "result": result}


def parse_jobs(output: str) -> list[dict]:
    """Parse hosted job conclusions from the GitHub CLI response."""
    payload = json.loads(output)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("hosted CI jobs response is empty or malformed")
    return [map_job(job) for job in jobs if isinstance(job, dict)]


def parse_run(output: str) -> dict:
    """Parse the newest hosted run on the tracked branch."""
    payload = json.loads(output)
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError("expected exactly one hosted CI run")
    run = payload[0]
    if not isinstance(run, dict):
        raise ValueError("hosted CI run is not an object")
    return run


def hosted_gate(commit: str) -> dict:
    """Verify the hosted CI run, its head SHA and every job conclusion."""
    listing = step_result(
        "hosted CI list",
        [
            "gh",
            "run",
            "list",
            "-R",
            REPOSITORY,
            "--branch",
            BRANCH,
            "--limit",
            "1",
            "--json",
            "databaseId,headSha,conclusion",
        ],
    )
    run: dict = {}
    jobs: list[dict] = []
    error = ""
    try:
        run = parse_run(listing["output"])
    except ValueError as exc:
        error = str(exc)
    run_id = run.get("databaseId")
    viewing = step_result(
        "hosted CI jobs",
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "-R",
            REPOSITORY,
            "--json",
            "jobs",
        ],
    )
    if not error:
        try:
            jobs = parse_jobs(viewing["output"])
        except ValueError as exc:
            error = str(exc)
    native = next((job for job in jobs if is_native_job(job["name"])), None)
    head_sha = str(run.get("headSha", ""))
    conclusion = run.get("conclusion")
    match = bool(commit) and head_sha == commit
    counts = {
        "run_id": run_id,
        "head_sha": head_sha,
        "head_sha_match": match,
        "conclusion": conclusion,
        "jobs_total": len(jobs),
        "jobs_passed": len([j for j in jobs if j["result"] == PASS]),
        "jobs_failed": len([j for j in jobs if j["result"] == FAIL]),
        "native_q_conclusion": native["conclusion"] if native else None,
        "native_q_result": native["result"] if native else None,
    }
    if error:
        counts["error"] = error
    ok = (
        not error
        and match
        and conclusion == "success"
        and native is not None
        and native["result"] == GATED
        and counts["jobs_failed"] == 0
        and counts["jobs_passed"] == len(jobs) - 1
    )
    listing = finalize(listing, not error)
    viewing = finalize(viewing, not error and bool(jobs))
    gate = make_gate("hosted_ci", [listing, viewing], counts, ok)
    gate["jobs"] = jobs
    gate["native_q"] = native or {"conclusion": None, "result": GATED}
    return gate


def run_gates(commit: str) -> list[dict]:
    """Execute every portable and hosted verification gate."""
    return [
        run_portable(),
        run_sanitizer(),
        run_pytest(),
        run_style(),
        run_freshness(),
        run_audit(),
        hosted_gate(commit),
    ]


def gate_named(report: dict, name: str) -> dict | None:
    """Return a named gate from a report, if present."""
    for gate in report.get("gates", []):
        if gate.get("name") == name:
            return gate
    return None


def write_report(report: dict, path: Path) -> None:
    """Write the JSON report, creating its parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_report(output: Path) -> int:
    """Execute all gates, write a report and return a process exit code."""
    started = utc_now()
    commit = git_commit()
    gates = run_gates(commit)
    completed = utc_now()
    overall = PASS if all(gate["result"] == PASS for gate in gates) else FAIL
    report = {
        "commit": commit,
        "branch": BRANCH,
        "generated_at": utc_now(),
        "started_at": started,
        "completed_at": completed,
        "toolchain": toolchain(),
        "presets": ["portable-ci", "sanitizers"],
        "overall": overall,
        "gates": gates,
    }
    write_report(report, output)
    for gate in gates:
        print(f"{gate['name']}: {gate['result']}")
    print(f"Overall: {overall}")
    print(f"Report: {output}")
    return 0 if overall == PASS else 1


def show_report(path: Path) -> int:
    """Print a concise, human-readable summary of a saved report."""
    report = json.loads(path.read_text(encoding="utf-8"))
    print(f"Commit: {report.get('commit', '')}")
    print(f"Branch: {report.get('branch', '')}")
    print(f"Generated: {report.get('generated_at', '')}")
    print(f"Started: {report.get('started_at', '')}")
    print(f"Completed: {report.get('completed_at', '')}")
    print(f"Toolchain: {report.get('toolchain', {})}")
    print(f"Presets: {', '.join(report.get('presets', []))}")
    for gate in report.get("gates", []):
        print(f"- {gate['name']}: {gate['result']} {gate.get('counts', {})}")
    hosted = gate_named(report, "hosted_ci") or {}
    native = hosted.get("native_q") or {}
    print(
        "- Native q tier: "
        f"{native.get('result', GATED)} "
        f"(hosted conclusion: {native.get('conclusion')})"
    )
    overall = report.get("overall", FAIL)
    print(f"Overall: {overall}")
    return 0 if overall == PASS else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="execute gates and write")
    run_parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    show_parser = subparsers.add_parser("show", help="summarize a report")
    show_parser.add_argument(
        "--path", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested command."""
    args = parse_args(argv)
    try:
        if args.command == "run":
            return run_report(args.output)
        return show_report(args.path)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
