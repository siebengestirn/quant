#!/usr/bin/env python3
"""Generate a machine-readable portable verification report.

Usage:
  python3 scripts/verify_report.py run [--output PATH] [--scope SCOPE]
      [--config PATH]
  python3 scripts/verify_report.py show [--path PATH]

Every gate is executed from the repository root. The report records the
exact commands, exit codes, parsed counts and results. The native-q tier
is GATED_NOT_RUN only when its job was skipped or ran no tests and the
release scope permits that status. A native-q PASS requires a successful
job with executed integration tests and zero skipped tests; every other
conclusion or evidence state is FAIL. Skipped jobs never add to successful
test counts. The report is written even when a gate fails.

The embedded ``config`` object is the only place that may approve a skipped
Python test. Its ``allowed_skips`` field is a list of records with a
``test_id`` and a non-empty ``reason``; any other skip fails its gate and
the release bundle validator. The mandated pytest commands remain unchanged;
pytest receives ``-rs`` through the environment so that skip IDs and reasons
are captured for the allowlist check. The same config and counts are carried
into the release bundle manifest so verify can compare them exactly.

The portable pytest gate sets ``QUANT_CONTRACT_BIN`` to the freshly built
``build-ci/quant_contract`` regardless of any inherited value. It records
that path, its SHA-256, its build directory and the report commit in the gate
counts; a missing or non-executable binary fails the gate. This prevents a
stale, ignored ``build/quant_contract`` from satisfying cross-language tests.
The ``ci_security_tests`` gate is mandatory and enforces the same skip policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from quant_common import (
    BINARY_BUILD_DIR_KEY,
    BINARY_COMMIT_KEY,
    BINARY_DIGEST_KEY,
    BINARY_PATH_KEY,
    CANONICAL_BINARY_PATH,
    CANONICAL_BUILD_DIR,
)

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
NOT_APPLICABLE = "NOT_APPLICABLE"
SUCCESS = "success"
SKIPPED = "skipped"
NATIVE_LABEL = "native-q"
NATIVE_MARKERS = ("native-q", "native kdb")
NATIVE_TEST_FILE = "tests/test_kdbx.py"
NATIVE_TEST_STEP = "Run native q persistence and recovery tests"
REQUIRED_JOBS = (
    "core build and tests",
    "ASan/UBSan",
    "Style contract (reference guides)",
    "Release assurance (offline)",
)
NATIVE_RESULTS = {SKIPPED: GATED, SUCCESS: PASS}
REPORT_SCHEMA_VERSION = 1
RELEASE_SCOPE = "PORTABLE_RESEARCH"
PERSISTENCE_SCOPE = "Q_PERSISTENCE"
RELEASE_SCOPES = frozenset({RELEASE_SCOPE, PERSISTENCE_SCOPE})
REQUIRED_GATES = frozenset(
    {
        "portable_cpp",
        "sanitizer",
        "portable_pytest",
        "w1",
        "style",
        "state_freshness",
        "terminology_audit",
        "release_bundle_tests",
        "verify_report_tests",
        "native_q_checker_tests",
        "ci_security_tests",
        "audit_disposition_tests",
        "hosted_ci",
    }
)
PYTEST_FILES = (
    "tests/test_dataset_contract.py",
    "tests/test_research.py",
    "tests/test_dataset_adversarial.py",
)
CI_SECURITY_TEST_FILES = ("tests/test_ci_security.py",)
AUDIT_DISPOSITION_TEST_FILES = ("tests/test_audit_disposition.py",)
W1_TEST_FILES = ("tests/test_instrument_master.py",)
RELEASE_BUNDLE_TEST_FILES = ("tests/test_release_bundle.py",)
VERIFY_REPORT_TEST_FILES = ("tests/test_verify_report.py",)
NATIVE_Q_CHECKER_TEST_FILES = ("tests/test_native_q_ci.py",)
BINARY_ENV = "QUANT_CONTRACT_BIN"
PYTEST_RESET = "addopts="
PYTEST_SKIP_FLAGS = "-rs"
PYTEST_COUNT_KEYS = ("passed", "failed", "errors", "skipped", "total")
ALLOWED_SKIPS_KEY = "allowed_skips"
REPORT_CONFIG_KEYS = (ALLOWED_SKIPS_KEY,)
SKIP_FIELDS = frozenset({"test_id", "reason"})
ALLOWED_SKIPS: tuple[dict[str, str], ...] = ()
CHECKS_RE = re.compile(r"checks=(\d+)\s+failures=(\d+)")
CTEST_RE = re.compile(r"(\d+)%\s+tests passed[^\n]*?\bout of\s+(\d+)")
PYTEST_TOKENS_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)")
SKIPPED_LINE_RE = re.compile(r"SKIPPED \[\d+\]\s+(\S+):\d+:\s*(.*)")
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
    text = shlex.join(argv)
    if PYTEST_RESET in argv:
        # pytest receives an empty option value; show its shell spelling.
        text = text.replace(PYTEST_RESET, "addopts=''")
    return text


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
    """Parse all five pytest outcome counts, including the quiet form."""
    raw = {
        word: int(number) for number, word in PYTEST_TOKENS_RE.findall(output)
    }
    for line in output.splitlines():
        if "[100%]" in line:
            raw.setdefault("passed", line.count("."))
    counts = {
        "passed": raw.get("passed", 0),
        "failed": raw.get("failed", 0),
        "errors": raw.get("error", 0) + raw.get("errors", 0),
        "skipped": raw.get("skipped", 0),
    }
    counts["total"] = sum(counts.values())
    return counts


def nonempty_text(value: object) -> bool:
    """Return True for a non-blank string value."""
    return isinstance(value, str) and bool(value.strip())


def allowed_skip_entries(entries: object) -> tuple[dict[str, str], ...]:
    """Validate the documented allowlist of skipped Python tests."""
    if entries is None:
        return ()
    if isinstance(entries, dict):
        entries = [
            {"test_id": key, "reason": value} for key, value in entries.items()
        ]
    if not isinstance(entries, (list, tuple)):
        raise ValueError(f"{ALLOWED_SKIPS_KEY} must be a list of records")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"invalid {ALLOWED_SKIPS_KEY} record: {entry!r}")
        if set(entry) != SKIP_FIELDS:
            raise ValueError(
                f"{ALLOWED_SKIPS_KEY} requires test_id and reason: {entry!r}"
            )
        if not nonempty_text(entry["test_id"]) or not nonempty_text(
            entry["reason"]
        ):
            raise ValueError(
                f"incomplete {ALLOWED_SKIPS_KEY} record: {entry!r}"
            )
        test_id = entry["test_id"].strip()
        if test_id in seen:
            raise ValueError(f"duplicate {ALLOWED_SKIPS_KEY} entry: {test_id}")
        seen.add(test_id)
        result.append({"test_id": test_id, "reason": entry["reason"].strip()})
    return tuple(result)


def report_config(config: dict | None = None) -> dict:
    """Return a validated, JSON-serializable report config object."""
    source = ALLOWED_SKIPS if config is None else config
    if isinstance(source, dict):
        unknown = set(source) - set(REPORT_CONFIG_KEYS)
        if unknown:
            raise ValueError(
                f"unknown report config fields: {', '.join(sorted(unknown))}"
            )
        source = source.get(ALLOWED_SKIPS_KEY, ())
    entries = allowed_skip_entries(source)
    return {ALLOWED_SKIPS_KEY: [dict(entry) for entry in entries]}


def load_report_config(path: Path | None) -> dict:
    """Load a JSON report config, defaulting to no approved skips."""
    if path is None:
        return report_config()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable report config {path}: {exc}") from exc
    return report_config(payload)


def parse_skipped_tests(output: str) -> list[dict[str, str]]:
    """Parse pytest's short skip summary into test IDs and reasons."""
    return [
        {"test_id": match.group(1), "reason": match.group(2).strip()}
        for match in SKIPPED_LINE_RE.finditer(output)
    ]


def skip_id_allowed(test_id: str, allowed: tuple[dict[str, str], ...]) -> bool:
    """Match a skip ID against an allowlisted path, path:line or node ID."""
    for entry in allowed:
        candidate = entry["test_id"]
        if (
            test_id == candidate
            or test_id.startswith(candidate + "::")
            or test_id.startswith(candidate + ":")
        ):
            return True
    return False


def pytest_skips_ok(
    output: str,
    counts: dict,
    allowed: tuple[dict[str, str], ...],
    files: tuple[str, ...] = (),
) -> bool:
    """Require every skipped test to be individually allowlisted."""
    skipped = counts.get("skipped", 0)
    if skipped == 0:
        return True
    observed = parse_skipped_tests(output)
    if observed:
        return len(observed) == skipped and all(
            skip_id_allowed(item["test_id"], allowed) for item in observed
        )
    if files:
        scoped = [
            entry
            for entry in allowed
            if any(entry["test_id"].startswith(path) for path in files)
        ]
    else:
        scoped = list(allowed)
    return skipped <= len(scoped)


def pytest_environment() -> dict[str, str]:
    """Keep the mandated command while requesting skip IDs from pytest."""
    current = os.environ.get("PYTEST_ADDOPTS", "").strip()
    flags = f"{current} {PYTEST_SKIP_FLAGS}".strip()
    return {**os.environ, "PYTEST_ADDOPTS": flags}


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 of a file's exact bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_binary_step(commit: str) -> tuple[dict, dict]:
    """Check the build-ci binary and return its step and gate counts."""
    path = ROOT / CANONICAL_BINARY_PATH
    counts = {
        BINARY_PATH_KEY: CANONICAL_BINARY_PATH,
        BINARY_DIGEST_KEY: "",
        BINARY_BUILD_DIR_KEY: CANONICAL_BUILD_DIR,
        BINARY_COMMIT_KEY: commit,
    }
    problem = ""
    if not path.is_file():
        problem = "missing"
    elif not os.access(path, os.X_OK):
        problem = "not executable"
    else:
        counts[BINARY_DIGEST_KEY] = sha256_file(path)
    ok = not problem
    output = (
        f"{CANONICAL_BINARY_PATH} sha256={counts[BINARY_DIGEST_KEY] or 'none'} "
        f"build_dir={CANONICAL_BUILD_DIR} commit={commit}"
    )
    if problem:
        output += f" ({problem})"
    step = {
        "name": "canonical contract binary",
        "command": (
            f"test -x {shlex.quote(str(path))} && "
            f"sha256sum {shlex.quote(str(path))}"
        ),
        "exit_code": 0 if ok else 1,
        "counts": {},
        "result": PASS if ok else FAIL,
        "output": output,
    }
    return step, counts


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


def pytest_gate(
    name: str,
    files: tuple[str, ...],
    step_name: str,
    allowed_skips: object = None,
    *,
    env: dict[str, str] | None = None,
    pre_steps: tuple[dict, ...] = (),
    extra_counts: dict | None = None,
    extra_ok: bool = True,
) -> dict:
    """Run one pytest suite with the strict five-count pass policy."""
    argv = [
        "python3",
        "-m",
        "pytest",
        *files,
        "-o",
        PYTEST_RESET,
        "-q",
    ]
    test_env = pytest_environment() if env is None else env
    test = step_result(step_name, argv, test_env)
    if env is not None and BINARY_ENV in env:
        test["command"] = (
            f"{BINARY_ENV}={shlex.quote(env[BINARY_ENV])} " + test["command"]
        )
    counts = parse_pytest(test["output"])
    source = ALLOWED_SKIPS if allowed_skips is None else allowed_skips
    allowed = allowed_skip_entries(source)
    ok = (
        extra_ok
        and counts["passed"] > 0
        and counts["failed"] == 0
        and counts["errors"] == 0
        and pytest_skips_ok(test["output"], counts, allowed, files)
    )
    test = finalize(test, ok)
    gate = make_gate(
        name, [*pre_steps, test], {**counts, **(extra_counts or {})}, ok
    )
    gate["allowed_skips"] = [dict(entry) for entry in allowed]
    return gate


def run_pytest(allowed_skips: object = None, commit: str = "") -> dict:
    """Run the portable Python subset against the freshly built binary."""
    binary_step, binary_counts = canonical_binary_step(commit)
    env = {
        **pytest_environment(),
        BINARY_ENV: str(ROOT / CANONICAL_BINARY_PATH),
    }
    return pytest_gate(
        "portable_pytest",
        PYTEST_FILES,
        "portable pytest",
        allowed_skips,
        env=env,
        pre_steps=(binary_step,),
        extra_counts=binary_counts,
        extra_ok=binary_step["result"] == PASS,
    )


def run_w1_tests(allowed_skips: object = None) -> dict:
    """Run the pinned instrument-master crosswalk suite."""
    return pytest_gate(
        "w1", W1_TEST_FILES, "W1 instrument master tests", allowed_skips
    )


def run_release_bundle_tests(allowed_skips: object = None) -> dict:
    """Run the offline release bundle suite."""
    return pytest_gate(
        "release_bundle_tests",
        RELEASE_BUNDLE_TEST_FILES,
        "release bundle tests",
        allowed_skips,
    )


def run_verify_report_tests(allowed_skips: object = None) -> dict:
    """Run the offline verification report suite without recursion."""
    return pytest_gate(
        "verify_report_tests",
        VERIFY_REPORT_TEST_FILES,
        "verification report tests",
        allowed_skips,
    )


def run_native_q_checker_tests(allowed_skips: object = None) -> dict:
    """Run the native-q result checker's offline test suite."""
    return pytest_gate(
        "native_q_checker_tests",
        NATIVE_Q_CHECKER_TEST_FILES,
        "native q checker tests",
        allowed_skips,
    )


def run_ci_security_tests(allowed_skips: object = None) -> dict:
    """Run the mandatory CI workflow security suite."""
    return pytest_gate(
        "ci_security_tests",
        CI_SECURITY_TEST_FILES,
        "CI security tests",
        allowed_skips,
    )


def run_audit_disposition_tests(allowed_skips: object = None) -> dict:
    """Run the mandatory audit disposition evidence suite."""
    return pytest_gate(
        "audit_disposition_tests",
        AUDIT_DISPOSITION_TEST_FILES,
        "audit disposition tests",
        allowed_skips,
    )


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
    lower = str(name).lower()
    return any(marker in lower for marker in NATIVE_MARKERS)


def conclusion_value(conclusion: object) -> str:
    """Return a comparable conclusion token; malformed data has no token."""
    if not isinstance(conclusion, str):
        return ""
    return conclusion.strip().lower()


def map_job_conclusion(name: str, conclusion: object) -> str:
    """Map a raw conclusion token, before test-count and scope evidence.

    A skipped native-q token is gated and a successful one is eligible for
    a pass. Required jobs pass only on success; unknown names never pass.
    The native-q record applies the final matrix with its test evidence.
    """
    state = conclusion_value(conclusion)
    job_name = str(name)
    if is_native_job(job_name):
        return NATIVE_RESULTS.get(state, FAIL)
    if job_name in REQUIRED_JOBS and state == SUCCESS:
        return PASS
    return FAIL


def explicit_enablement(job: dict | None) -> bool | None:
    """Return an explicit enablement flag, or None when it is absent."""
    if not isinstance(job, dict):
        return None
    for key in ("enabled", "enabled_hint"):
        value = job.get(key)
        if isinstance(value, bool):
            return value
    return None


def native_test_step_ok(job: dict | None) -> bool:
    """Require a successful step that runs the native integration tests."""
    if not isinstance(job, dict):
        return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", ""))
        if (
            NATIVE_TEST_STEP.lower() not in name.lower()
            and NATIVE_TEST_FILE not in name
        ):
            continue
        if conclusion_value(step.get("conclusion")) == SUCCESS:
            return True
    return False


def parse_native_evidence(logs: str) -> dict:
    """Parse native pytest counts from a hosted job log."""
    counts = parse_pytest(logs)
    failed = sum(counts.get(key, 0) for key in ("failed", "error", "errors"))
    return {
        "pytest_counts": counts,
        "tests_executed": counts.get("passed", 0) + failed,
        "tests_skipped": counts.get("skipped", 0),
        "tests_failed": failed,
        "test_file_in_log": NATIVE_TEST_FILE in logs,
    }


def native_q_record(job: dict | None, scope: str, logs: str = "") -> dict:
    """Apply the full native-q matrix and explain the recorded status."""
    raw = job if isinstance(job, dict) else {}
    explicit = explicit_enablement(job)
    conclusion = conclusion_value(raw.get("conclusion"))
    test_step = native_test_step_ok(job)
    evidence = parse_native_evidence(logs)
    executed = evidence["tests_executed"]
    skipped = evidence["tests_skipped"]
    failed = evidence["tests_failed"]
    record = {
        "name": raw.get("name", NATIVE_LABEL),
        "enabled": (
            explicit
            if explicit is not None
            else (conclusion == SUCCESS and test_step)
        ),
        "conclusion": raw.get("conclusion"),
        "result": FAIL,
        "tests_executed": executed,
        "tests_skipped": skipped,
        "tests_failed": failed,
        "evidence": {
            "source": "hosted job steps and pytest job log",
            "test_file": NATIVE_TEST_FILE,
            "test_step": NATIVE_TEST_STEP,
            "test_step_executed": test_step,
            "test_file_in_log": evidence["test_file_in_log"],
            "pytest_counts": evidence["pytest_counts"],
        },
        "reason": "",
        "database_id": raw.get("database_id", raw.get("databaseId")),
    }
    if job is None or conclusion not in (SKIPPED, SUCCESS):
        record["reason"] = (
            "native-q job is missing from the hosted run"
            if job is None
            else f"native-q conclusion {raw.get('conclusion')!r} is neither "
            "skipped nor success"
        )
        return record
    if explicit is True and conclusion == SKIPPED:
        record["reason"] = "native-q is explicitly enabled but its job skipped"
        return record
    if conclusion == SKIPPED or not test_step:
        record["tests_executed"] = 0
        record["tests_skipped"] = 0
        record["tests_failed"] = 0
        record["evidence"]["pytest_counts"] = {}
        record["evidence"]["test_file_in_log"] = False
        if scope == RELEASE_SCOPE and explicit is not True:
            record["result"] = GATED
            record["reason"] = (
                "native-q feature is not enabled; hosted job skipped or ran "
                "no integration tests"
            )
        else:
            record["reason"] = (
                f"native-q ran no integration tests under release scope "
                f"{scope}"
            )
        return record
    if explicit is False:
        record["reason"] = "native-q succeeded while explicitly disabled"
        return record
    if executed <= 0 or skipped > 0 or failed > 0:
        record["reason"] = (
            f"native-q integration evidence is insufficient: "
            f"{executed} executed, {skipped} skipped, {failed} failed"
        )
        return record
    record["result"] = PASS
    record["reason"] = (
        f"native-q integration tests executed: {executed} passed, "
        f"{skipped} skipped, {failed} failed"
    )
    return record


def map_job(job: dict) -> dict:
    """Record a hosted job's raw conclusion and its mapped status."""
    name = str(job.get("name", ""))
    conclusion = job.get("conclusion")
    record = {
        "name": name,
        "conclusion": conclusion,
        "result": map_job_conclusion(name, conclusion),
        "database_id": job.get("databaseId"),
    }
    if is_native_job(name):
        record["steps"] = job.get("steps", [])
        record["enabled_hint"] = explicit_enablement(job)
    return record


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


def native_job(jobs: list[dict]) -> dict | None:
    """Return the recorded native-q job, if the run contains one."""
    return next((job for job in jobs if is_native_job(job["name"])), None)


def missing_jobs(jobs: list[dict], expect_native: bool) -> list[str]:
    """Return expected job labels that the hosted run does not contain."""
    names = [job["name"] for job in jobs]
    missing = [name for name in REQUIRED_JOBS if name not in names]
    if expect_native and not any(is_native_job(name) for name in names):
        missing.append(NATIVE_LABEL)
    return missing


def job_counts(jobs: list[dict]) -> dict:
    """Count statuses; skipped jobs stay out of successful counts."""
    return {
        "jobs_total": len(jobs),
        "jobs_passed": len([j for j in jobs if j["result"] == PASS]),
        "jobs_failed": len([j for j in jobs if j["result"] == FAIL]),
        "jobs_gated": len([j for j in jobs if j["result"] == GATED]),
        "jobs_skipped": len(
            [
                job
                for job in jobs
                if conclusion_value(job["conclusion"]) == SKIPPED
            ]
        ),
    }


def native_result(native: dict | None, expect_native: bool) -> str:
    """Return the native-q status, including when its job is absent."""
    if native is not None and native["result"] != FAIL:
        return native["result"]
    if not expect_native:
        return NOT_APPLICABLE
    return FAIL


def hosted_ok(
    run: dict,
    jobs: list[dict],
    native: dict | None,
    missing: list[str],
    match: bool,
    expect_native: bool,
    scope: str,
) -> bool:
    """Decide whether hosted evidence satisfies all required jobs."""
    if not match or run.get("conclusion") != SUCCESS or missing:
        return False
    required = [job for job in jobs if job["name"] in REQUIRED_JOBS]
    if len(required) != len(REQUIRED_JOBS):
        return False
    if any(job["result"] != PASS for job in required):
        return False
    status = native_result(native, expect_native)
    if expect_native:
        allowed = (PASS,) if scope == PERSISTENCE_SCOPE else (PASS, GATED)
        if status not in allowed:
            return False
    elif status not in (PASS, GATED, NOT_APPLICABLE):
        return False
    counts = job_counts(jobs)
    return (
        counts["jobs_failed"] == 0
        and counts["jobs_passed"] == counts["jobs_total"] - counts["jobs_gated"]
    )


def native_log_step(native: dict) -> dict | None:
    """Fetch the successful native job's log for pytest evidence."""
    database_id = native.get("database_id")
    if (
        not isinstance(database_id, int)
        or isinstance(database_id, bool)
        or database_id <= 0
    ):
        return None
    return step_result(
        "native q test log",
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/jobs/{database_id}/logs",
        ],
    )


def hosted_gate(
    commit: str, expect_native: bool = True, scope: str = RELEASE_SCOPE
) -> dict:
    """Verify the hosted CI run, its head SHA and its job evidence."""
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
            "databaseId,headSha,conclusion,url",
        ],
    )
    run: dict = {}
    jobs: list[dict] = []
    error = ""
    try:
        run = parse_run(listing["output"])
    except ValueError as exc:
        error = str(exc)
    viewing = step_result(
        "hosted CI jobs",
        [
            "gh",
            "run",
            "view",
            str(run.get("databaseId")),
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
    native = native_job(jobs)
    has_native = native is not None
    log_step = None
    logs = ""
    if has_native and conclusion_value(native["conclusion"]) == SUCCESS:
        log_step = native_log_step(native)
        if log_step is not None and log_step["exit_code"] == 0:
            logs = log_step["output"]
    native = native_q_record(native, scope, logs)
    if has_native:
        jobs = [native if is_native_job(job["name"]) else job for job in jobs]
    head_sha = str(run.get("headSha", ""))
    match = bool(commit) and head_sha == commit
    missing = missing_jobs(jobs, expect_native)
    counts = {
        "run_id": run.get("databaseId"),
        "run_url": run.get("url"),
        "head_sha": head_sha,
        "head_sha_match": match,
        "conclusion": run.get("conclusion"),
        "commit": commit,
        "native_expected": expect_native,
        "missing_jobs": missing,
        "release_scope": scope,
        "native_q_conclusion": native["conclusion"],
        "native_q_result": native_result(native, expect_native),
        "native_q_tests_executed": native["tests_executed"],
        "native_q_tests_skipped": native["tests_skipped"],
        "native_q_tests_failed": native["tests_failed"],
        **job_counts(jobs),
    }
    if error:
        counts["error"] = error
    ok = not error and hosted_ok(
        run, jobs, native, missing, match, expect_native, scope
    )
    listing = finalize(listing, not error)
    viewing = finalize(viewing, not error and bool(jobs))
    steps = [listing, viewing]
    if log_step is not None:
        steps.append(log_step)
    gate = make_gate("hosted_ci", steps, counts, ok)
    gate["jobs"] = jobs
    gate["run"] = {
        "id": run.get("databaseId"),
        "url": run.get("url"),
        "head_sha": head_sha,
        "conclusion": run.get("conclusion"),
    }
    gate["native_q"] = native
    return gate


def run_gates(
    commit: str, scope: str = RELEASE_SCOPE, config: dict | None = None
) -> list[dict]:
    """Execute every portable, assurance and hosted verification gate."""
    allowed = (config or {}).get(ALLOWED_SKIPS_KEY)
    return [
        run_portable(),
        run_sanitizer(),
        run_pytest(allowed, commit),
        run_w1_tests(allowed),
        run_release_bundle_tests(allowed),
        run_verify_report_tests(allowed),
        run_native_q_checker_tests(allowed),
        run_ci_security_tests(allowed),
        run_audit_disposition_tests(allowed),
        run_style(),
        run_freshness(),
        run_audit(),
        hosted_gate(commit, scope=scope),
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


def run_report(
    output: Path,
    scope: str = RELEASE_SCOPE,
    config: dict | Path | None = None,
) -> int:
    """Execute all gates, write a report and return a process exit code."""
    started = utc_now()
    commit = git_commit()
    resolved = load_report_config(config)
    if config is None:
        gates = run_gates(commit, scope)
    else:
        gates = run_gates(commit, scope, resolved)
    completed = utc_now()
    overall = PASS if all(gate["result"] == PASS for gate in gates) else FAIL
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "release_scope": scope,
        "commit": commit,
        "branch": BRANCH,
        "generated_at": utc_now(),
        "started_at": started,
        "completed_at": completed,
        "toolchain": toolchain(),
        "presets": ["portable-ci", "sanitizers"],
        "config": resolved,
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
    print(f"Schema version: {report.get('schema_version', 'missing')}")
    print(f"Release scope: {report.get('release_scope', 'missing')}")
    print(f"Commit: {report.get('commit', '')}")
    print(f"Branch: {report.get('branch', '')}")
    print(f"Generated: {report.get('generated_at', '')}")
    print(f"Started: {report.get('started_at', '')}")
    print(f"Completed: {report.get('completed_at', '')}")
    print(f"Toolchain: {report.get('toolchain', {})}")
    print(f"Presets: {', '.join(report.get('presets', []))}")
    config = report.get("config") or {}
    skips = config.get(ALLOWED_SKIPS_KEY) or []
    print(f"Allowed skips: {len(skips)}")
    portable = gate_named(report, "portable_pytest") or {}
    binary = portable.get("counts") or {}
    print(
        f"Canonical contract binary: {binary.get(BINARY_PATH_KEY, 'missing')} "
        f"sha256={binary.get(BINARY_DIGEST_KEY) or 'none'}"
    )
    for gate in report.get("gates", []):
        print(f"- {gate['name']}: {gate['result']} {gate.get('counts', {})}")
    hosted = gate_named(report, "hosted_ci") or {}
    native = hosted.get("native_q") or {}
    print(
        "- Native q tier: "
        f"{native.get('result', FAIL)} "
        f"({native.get('reason', 'no explanation recorded')})"
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
    run_parser.add_argument(
        "--scope",
        choices=sorted(RELEASE_SCOPES),
        default=RELEASE_SCOPE,
        help="release scope for native-q qualification",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        help="JSON report config with documented allowed_skips records",
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
            return run_report(args.output, args.scope, args.config)
        return show_report(args.path)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
