#!/usr/bin/env python3
"""Validate the canonical release state and its governed evidence.

Usage:
  python3 scripts/check_release_state.py check
  python3 scripts/check_release_state.py show

The canonical record is ``governance/release-state.json``. It is the only
place where the current release identifiers are pinned in Git; volatile pins
are labelled as captured evidence and verified here against ``git HEAD``,
the assurance report, the release manifest contract and the current-state
sections of the governed documentation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

from quant_common import HISTORICAL_MARKERS

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_RELATIVE = "governance/release-state.json"
REPORT_RELATIVE = "data/assurance/verification-report.json"
INVENTORY_RELATIVE = "governance/inventory.json"
DOC_PATHS = (
    "docs/master-acceptance-register.md",
    "docs/release-status.md",
    "governance/architecture-current.md",
)
CSV_FIELDS = {
    "governance/requirements-traceability.csv": 8,
    "governance/technical-debt-register.csv": 8,
    "governance/security-findings.csv": 8,
    "governance/research-report-registry.csv": 13,
}
REQUIRED_FIELDS = (
    "branch_protection",
    "current_workstreams",
    "gate_results",
    "historical_releases",
    "main_commit",
    "manifest_sha256",
    "merged_pull_requests",
    "native_q_status",
    "release_id",
    "release_scope",
    "release_status",
    "release_version",
    "required_gate_set",
    "test_inventory",
    "trading_authorization",
    "verification_report_sha256",
)
REQUIRED_WORKSTREAMS = (
    "W1",
    "W1-D",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "W9",
    "W10",
    "W11",
    "W12",
    "W13",
    "W14",
    "W15",
    "W16",
    "W17",
    "W18",
    "W19",
    "W20",
)
REQUIRED_CHECKS = (
    "core build and tests",
    "ASan/UBSan",
    "Style contract (reference guides)",
    "Release assurance (offline)",
)
# The last field marks additive suites: later corrective tests may increase
# their count, but the recorded R8.4.2 baseline must remain a floor.
GATE_COUNT_KEYS = {
    "portable_cpp": ("cpp_checks", "checks", 14114, False),
    "sanitizer": ("sanitizer_checks", "sanitizer_checks", 14114, False),
    "portable_pytest": ("portable_pytest", "passed", 51, False),
    "w1": ("w1", "passed", 36, False),
    "release_bundle_tests": ("release_bundle_tests", "passed", 121, True),
    "verify_report_tests": ("verify_report_tests", "passed", 161, True),
    "native_q_checker_tests": ("native_q_checker_tests", "passed", 8, False),
}
CI_SECURITY_TEST_FILE = "tests/test_ci_security.py"
CI_SECURITY_TESTS = 11
TEST_FUNCTION_RE = re.compile(r"^(?:async\s+)?def\s+test_\w+", re.MULTILINE)
SHA256_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
COMMIT_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
LINE_MARKERS = (*HISTORICAL_MARKERS, "at pr #")
SECTION_MARKERS = (
    "historical",
    "superseded",
    "release history",
    "archive",
)
MARKER_WINDOW = 5
STALE_PATTERNS = (
    (
        re.compile(
            r"R8\.4\.2[^\n]{0,80}IN PROGRESS"
            r"|IN PROGRESS[^\n]{0,80}R8\.4\.2",
            re.IGNORECASE,
        ),
        "stale R8.4.2 IN PROGRESS",
    ),
    (re.compile(r"PR\s*#64\b"), "stale PR #64 current claim"),
    (
        re.compile(
            r"\b14\s+(?:offline\s+)?(?:pytest\s+)?"
            r"(?:W1\s+)?(?:tests?|passes)\b"
            r"|\bW1[^\n]{0,60}\b14\s+(?:offline\s+)?"
            r"(?:pytest\s+)?(?:tests?|passes)\b",
            re.IGNORECASE,
        ),
        "stale W1 14 tests",
    ),
)
CURRENT_36_RE = re.compile(
    r"\b36\s+(?:offline\s+)?(?:pytest\s+)?(?:W1\s+)?(?:tests?|passes)\b"
    r"|\bW1[^\n]{0,60}\b36\s+(?:offline\s+)?"
    r"(?:pytest\s+)?(?:tests?|passes)\b",
    re.IGNORECASE,
)
CURRENT_CHECKS_RE = re.compile(r"\b4\s+required checks\b", re.IGNORECASE)


def canonical_text(payload: object) -> str:
    """Render the canonical JSON form used by every tracked JSON file."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def pr_suffix(state: dict) -> str:
    """Return the final PR number in the merged pull request range."""
    value = str(state.get("merged_pull_requests", ""))
    return value.rsplit("-", maxsplit=1)[-1]


def is_historical(state: dict) -> bool:
    """Report whether the captured state is explicitly labelled historical."""
    return "historical" in str(state.get("capture_context", "")).lower()


def read_text(root: pathlib.Path, relative: str) -> str | None:
    """Read a UTF-8 file, returning None when it is absent or unreadable."""
    try:
        return (root / relative).read_text(encoding="utf-8")
    except OSError:
        return None


def load_state(root: pathlib.Path) -> tuple[dict, str, list[str]]:
    """Load the canonical state and report every structural defect."""
    text = read_text(root, STATE_RELATIVE)
    if text is None:
        return {}, "", [f"{STATE_RELATIVE}: missing or unreadable"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, text, [f"{STATE_RELATIVE}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return {}, text, [f"{STATE_RELATIVE}: object required"]
    errors: list[str] = []
    if text != canonical_text(payload):
        errors.append(f"{STATE_RELATIVE}: not canonical JSON (2-space, sorted)")
    return payload, text, errors


def git_head(root: pathlib.Path) -> str:
    """Return the tracked repository HEAD, or an empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def verify_report_constants() -> frozenset[str] | None:
    """Import the read-only gate set from the verification script."""
    path = ROOT / "scripts" / "verify_report.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("verify_report_state", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, RuntimeError, ValueError):
        return None
    names = getattr(module, "REQUIRED_GATES", None)
    if not isinstance(names, (set, frozenset, list, tuple)):
        return None
    return frozenset(map(str, names))


def inventory_errors(root: pathlib.Path) -> list[str]:
    """Require the canonical state to be an allowlisted current policy file."""
    text = read_text(root, INVENTORY_RELATIVE)
    if text is None:
        return [f"{INVENTORY_RELATIVE}: missing or unreadable"]
    try:
        inventory = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"{INVENTORY_RELATIVE}: invalid JSON: {exc}"]
    entries = inventory.get("entries") if isinstance(inventory, dict) else None
    if not isinstance(entries, list):
        return [f"{INVENTORY_RELATIVE}: entries must be a list"]
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") == STATE_RELATIVE
    ]
    if len(matches) != 1:
        return [f"{INVENTORY_RELATIVE}: {STATE_RELATIVE} needs one entry"]
    entry = matches[0]
    expected = {
        "class": "policy",
        "owner": "maintainer",
        "retention": "current",
    }
    return [
        f"{INVENTORY_RELATIVE}: {STATE_RELATIVE} {field} must be {value!r}"
        for field, value in expected.items()
        if entry.get(field) != value
    ]


def csv_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Require governed CSV field counts and current-state records."""
    errors: list[str] = []
    for relative, count in CSV_FIELDS.items():
        text = read_text(root, relative)
        if text is None:
            errors.append(f"{relative}: missing or unreadable")
            continue
        rows = list(csv.reader(text.splitlines()))
        if not rows:
            errors.append(f"{relative}: empty CSV")
            continue
        header = len(rows[0])
        if header != count:
            errors.append(
                f"{relative}:1: header has {header} fields, expected {count}"
            )
        for number, row in enumerate(rows[1:], 2):
            if len(row) != header or len(row) != count:
                errors.append(
                    f"{relative}:{number}: {len(row)} fields, expected {count}"
                )
        lines = text.splitlines()
        # CSV records are independent rows: only a row's own marker labels it.
        current_text = "\n".join(
            line
            for line in lines
            if not any(marker in line.lower() for marker in LINE_MARKERS)
        )
        end = pr_suffix(state)
        pr_token = f"PR #{end}" if end.isdigit() else "current PR"
        status = str(state.get("release_status", "COMPLETE"))
        tokens = (
            (
                str(state.get("release_version", "R8.4.2")),
                str(state.get("release_version", "R8.4.2")) in current_text,
            ),
            (pr_token, pr_token in current_text),
            (status, status in current_text),
            ("W1 36 tests", bool(CURRENT_36_RE.search(current_text))),
            ("4 required checks", bool(CURRENT_CHECKS_RE.search(current_text))),
            ("PORTABLE_RESEARCH", "PORTABLE_RESEARCH" in current_text),
            ("GATED_NOT_RUN", "GATED_NOT_RUN" in current_text),
        )
        for label, found in tokens:
            if not found:
                errors.append(f"{relative}: current record missing {label}")
    return errors


def labelled_lines(lines: list[str]) -> list[bool]:
    """Flag historical sections and records that may retain old values."""
    labels: list[bool] = []
    historical = False
    for index, line in enumerate(lines):
        if line.startswith("#"):
            historical = any(
                marker in line.lower() for marker in SECTION_MARKERS
            )
        window = " ".join(lines[max(0, index - MARKER_WINDOW) : index + 1])
        window = window.lower()
        labels.append(
            historical or any(marker in window for marker in LINE_MARKERS)
        )
    return labels


def doc_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Require current-state sections to agree with the canonical record."""
    errors: list[str] = []
    version = str(state.get("release_version", ""))
    for relative in DOC_PATHS:
        text = read_text(root, relative)
        if text is None:
            errors.append(f"{relative}: missing or unreadable")
            continue
        lines = text.splitlines()
        labels = labelled_lines(lines)
        current = [
            line
            for line, labelled in zip(lines, labels, strict=True)
            if not labelled
        ]
        for number, (line, labelled) in enumerate(
            zip(lines, labels, strict=True), 1
        ):
            if labelled:
                continue
            for pattern, label in STALE_PATTERNS:
                if pattern.search(line):
                    errors.append(f"{relative}:{number}: {label}")
        current_text = "\n".join(current)
        if version and version not in current_text:
            errors.append(f"{relative}: current record missing {version}")
        end = pr_suffix(state)
        pr_token = f"PR #{end}" if end.isdigit() else "current PR"
        status = str(state.get("release_status", "COMPLETE"))
        required = (
            (pr_token, pr_token in current_text),
            (status, status in current_text),
            ("W1 36 tests", bool(CURRENT_36_RE.search(current_text))),
            ("4 required checks", bool(CURRENT_CHECKS_RE.search(current_text))),
            ("GATED_NOT_RUN", "GATED_NOT_RUN" in current_text),
            ("PORTABLE_RESEARCH", "PORTABLE_RESEARCH" in current_text),
        )
        for label, found in required:
            if not found:
                errors.append(f"{relative}: current record missing {label}")
    return errors


def required_field_errors(state: dict) -> list[str]:
    """Report every missing or empty field of the canonical state."""
    return [
        f"{STATE_RELATIVE}: missing required field {field}"
        for field in REQUIRED_FIELDS
        if field not in state or state[field] in (None, "", [], {})
    ]


def identity_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Validate fixed release values, digests and the captured commit."""
    errors: list[str] = []
    if state.get("release_version") != "R8.4.2":
        errors.append(f"{STATE_RELATIVE}: release_version is not R8.4.2")
    if state.get("release_scope") != "PORTABLE_RESEARCH":
        errors.append(
            f"{STATE_RELATIVE}: release_scope is not PORTABLE_RESEARCH"
        )
    if state.get("release_status") != "COMPLETE":
        errors.append(f"{STATE_RELATIVE}: release_status is not COMPLETE")
    commit = state["main_commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        errors.append(f"{STATE_RELATIVE}: main_commit is not a full SHA-256")
    if not is_historical(state):
        head = git_head(root)
        if not head:
            errors.append(f"{STATE_RELATIVE}: git HEAD is unavailable")
        elif head != commit:
            errors.append(
                f"{STATE_RELATIVE}: main_commit does not match git HEAD"
            )
    for field in ("manifest_sha256", "verification_report_sha256"):
        digest = state.get(field)
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(f"{STATE_RELATIVE}: {field} is not a SHA-256 digest")
    return errors


def report_file_errors(
    state: dict, commit: str, historical: bool, report_text: str
) -> tuple[list[str], dict, bool]:
    """Validate a generated report, returning its gates and parse validity."""
    errors: list[str] = []
    digest = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
    if not historical and digest != state.get("verification_report_sha256"):
        errors.append(f"{STATE_RELATIVE}: verification_report_sha256 mismatch")
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as exc:
        errors.append(f"{REPORT_RELATIVE}: invalid JSON: {exc}")
        return errors, {}, False
    if not isinstance(report, dict):
        errors.append(f"{REPORT_RELATIVE}: object required")
        return errors, {}, False
    if not historical and report.get("commit") != commit:
        errors.append(f"{REPORT_RELATIVE}: commit differs from main_commit")
    if report.get("release_scope") != state.get("release_scope"):
        errors.append(f"{REPORT_RELATIVE}: scope differs from state")
    if report.get("overall") != "PASS":
        errors.append(f"{REPORT_RELATIVE}: overall is not PASS")
    gates = {
        gate.get("name"): gate
        for gate in report.get("gates", [])
        if isinstance(gate, dict)
    }
    return errors, gates, True


def gate_errors(state: dict, gates: dict) -> list[str]:
    """Reconcile the recorded gate set and results with the report."""
    errors: list[str] = []
    if set(gates) != set(state.get("gate_results", {})):
        errors.append(f"{STATE_RELATIVE}: gate_results differ from report")
    for name, result in state.get("gate_results", {}).items():
        gate = gates.get(name)
        if gate is None:
            continue
        if gate.get("result") != result:
            errors.append(f"{STATE_RELATIVE}: gate {name} result differs")
    required = state.get("required_gate_set")
    if not isinstance(required, list) or set(required) != set(gates):
        errors.append(
            f"{STATE_RELATIVE}: required_gate_set differs from report"
        )
    constants = verify_report_constants()
    if constants is not None and set(required or ()) != constants:
        errors.append(
            f"{STATE_RELATIVE}: required_gate_set differs from verify_report"
        )
    return errors


def test_inventory_errors(
    root: pathlib.Path, state: dict, gates: dict
) -> list[str]:
    """Validate the baselines and observed counts of every Python suite."""
    errors: list[str] = []
    inventory = state.get("test_inventory")
    if not isinstance(inventory, dict):
        errors.append(f"{STATE_RELATIVE}: test_inventory must be an object")
        return errors
    for name, (
        inventory_key,
        report_key,
        expected,
        additive,
    ) in GATE_COUNT_KEYS.items():
        gate = gates.get(name)
        observed = gate.get("counts", {}).get(report_key) if gate else None
        if not isinstance(observed, int):
            errors.append(f"{STATE_RELATIVE}: {name} count is missing")
        elif inventory.get(inventory_key) != expected:
            errors.append(f"{STATE_RELATIVE}: {name} baseline differs")
        elif additive and observed < expected:
            errors.append(f"{STATE_RELATIVE}: {name} count below baseline")
        elif not additive and observed != expected:
            errors.append(f"{STATE_RELATIVE}: {name} test count differs")
    security = read_text(root, CI_SECURITY_TEST_FILE)
    expected = CI_SECURITY_TESTS
    if security is not None and len(TEST_FUNCTION_RE.findall(security)) < (
        expected
    ):
        errors.append(f"{STATE_RELATIVE}: ci_security test count differs")
    if inventory.get("ci_security_tests") != expected:
        errors.append(f"{STATE_RELATIVE}: ci_security baseline differs")
    return errors


def branch_policy_errors(state: dict, gates: dict) -> list[str]:
    """Validate branch protection, the native-q gate and trading status."""
    errors: list[str] = []
    protection = state.get("branch_protection")
    if not isinstance(protection, dict):
        errors.append(f"{STATE_RELATIVE}: branch_protection must be an object")
    else:
        checks = protection.get("required_checks")
        if not isinstance(checks, list) or len(checks) != 4:
            errors.append(f"{STATE_RELATIVE}: branch protection needs 4 checks")
        elif set(checks) != set(REQUIRED_CHECKS):
            errors.append(f"{STATE_RELATIVE}: required check names differ")
        if protection.get("required_approvals") != 0:
            errors.append(f"{STATE_RELATIVE}: required_approvals is not 0")
        if protection.get("native_q_gated") is not True:
            errors.append(f"{STATE_RELATIVE}: native q is not gated")
    hosted = gates.get("hosted_ci", {}).get("counts", {})
    native = hosted.get("native_q_result")
    if native != "GATED_NOT_RUN" or state.get("native_q_status") != native:
        errors.append(f"{STATE_RELATIVE}: native_q_status is not GATED_NOT_RUN")
    trading = state.get("trading_authorization")
    if (
        not isinstance(trading, dict)
        or trading.get("live_execution") != ("DISABLED")
        or trading.get("decision") != "NO_TRADE"
    ):
        errors.append(f"{STATE_RELATIVE}: trading authorization is not locked")
    return errors


def workstream_errors(state: dict) -> list[str]:
    """Validate workstream coverage, PR range and labelled release history."""
    errors: list[str] = []
    workstreams = state.get("current_workstreams")
    if not isinstance(workstreams, dict):
        errors.append(
            f"{STATE_RELATIVE}: current_workstreams must be an object"
        )
    else:
        missing = set(REQUIRED_WORKSTREAMS) - set(workstreams)
        if missing:
            errors.append(
                f"{STATE_RELATIVE}: missing workstreams "
                f"{', '.join(sorted(missing))}"
            )
    range_match = RANGE_RE.match(str(state.get("merged_pull_requests")))
    if not range_match or (range_match.group(1), range_match.group(2)) != (
        "47",
        "66",
    ):
        errors.append(f"{STATE_RELATIVE}: merged_pull_requests must be 47-66")
    history = state.get("historical_releases")
    if not isinstance(history, list) or not history:
        errors.append(f"{STATE_RELATIVE}: historical_releases must be a list")
        return errors
    for number, record in enumerate(history, 1):
        required = {"note", "pr", "release_version", "scope", "status"}
        if not isinstance(record, dict) or not required <= set(record):
            errors.append(
                f"{STATE_RELATIVE}: historical_releases[{number}] incomplete"
            )
    return errors


def state_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Cross-check the state against read-only repository evidence."""
    errors = required_field_errors(state)
    if errors:
        return errors
    errors.extend(identity_errors(root, state))
    report_text = read_text(root, REPORT_RELATIVE)
    if report_text is None:
        # A generated report is absent in a clean checkout. A historical
        # capture does not claim that generated file, so the check is
        # NOT_APPLICABLE there; a current-state record must still provide it.
        if not is_historical(state):
            errors.append(f"{REPORT_RELATIVE}: missing or unreadable")
        return errors
    report_errors, gates, valid = report_file_errors(
        state, state["main_commit"], is_historical(state), report_text
    )
    errors.extend(report_errors)
    if not valid:
        return errors
    errors.extend(gate_errors(state, gates))
    errors.extend(test_inventory_errors(root, state, gates))
    errors.extend(branch_policy_errors(state, gates))
    errors.extend(workstream_errors(state))
    return errors


def check(root: pathlib.Path = ROOT) -> list[str]:
    """Return every release-state defect, with file and field coordinates."""
    state, _text, errors = load_state(root)
    if not state and errors:
        return errors
    errors.extend(state_errors(root, state))
    errors.extend(inventory_errors(root))
    errors.extend(csv_errors(root, state))
    errors.extend(doc_errors(root, state))
    return errors


def show(root: pathlib.Path = ROOT) -> int:
    """Print a concise summary of the canonical release state."""
    state, _text, errors = load_state(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    for name in (
        "release_id",
        "release_version",
        "release_status",
        "release_scope",
        "merged_pull_requests",
        "main_commit",
        "manifest_sha256",
        "verification_report_sha256",
        "native_q_status",
    ):
        print(f"{name}: {state.get(name, 'missing')}")
    trading = state.get("trading_authorization", {})
    print(
        "trading_authorization: "
        f"{trading.get('live_execution', 'missing')} / "
        f"{trading.get('decision', 'missing')}"
    )
    for name, result in sorted(state.get("gate_results", {}).items()):
        print(f"gate {name}: {result}")
    verdict = "PASS" if not check(root) else "FAIL"
    print(f"release state check: {verdict}")
    return 0 if verdict == "PASS" else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate all release-state evidence")
    subparsers.add_parser("show", help="print a canonical state summary")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested command, failing closed on any defect."""
    args = parse_args(argv)
    try:
        if args.command == "show":
            return show(ROOT)
        errors = check(ROOT)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        print(error)
    print(
        "release state check: FAIL" if errors else "release state check: PASS"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
