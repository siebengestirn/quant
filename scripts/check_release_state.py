#!/usr/bin/env python3
"""Validate the canonical release state and its governed evidence.

Usage:
  python3 scripts/check_release_state.py check
  python3 scripts/check_release_state.py show

The canonical record is ``governance/release-state.json``. It is the only
place where the current release identifiers are pinned in Git; volatile pins
are labelled as captured evidence and verified here against ``git HEAD``,
the assurance report, the release manifest contract and the current-state
sections of the governed documentation. No release identity or test total is
hardcoded: every expected value is derived from the canonical record itself
and reconciled with the generated report and the governed documents.
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
    "independent_acceptance",
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
# Each gate maps an inventory field to the count field of its generated
# report gate. Counts themselves come from the current record and report.
GATE_COUNT_KEYS = {
    "portable_cpp": ("cpp_checks", "checks"),
    "sanitizer": ("sanitizer_checks", "sanitizer_checks"),
    "portable_pytest": ("portable_pytest", "passed"),
    "w1": ("w1", "passed"),
    "release_bundle_tests": ("release_bundle_tests", "passed"),
    "verify_report_tests": ("verify_report_tests", "passed"),
    "native_q_checker_tests": ("native_q_checker_tests", "passed"),
    "ci_security_tests": ("ci_security_tests", "passed"),
}
SHA256_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
COMMIT_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
VERSION_RE = re.compile(r"^R\d+(?:\.\d+)+$")
LINE_MARKERS = (*HISTORICAL_MARKERS, "at pr #")
SECTION_MARKERS = (
    "historical",
    "superseded",
    "release history",
    "archive",
)


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


def verification_module() -> object | None:
    """Import the read-only gate and scope policy from the verification script."""
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
    return module


def release_scopes() -> frozenset[str] | None:
    """Return the release scopes accepted by the verification script."""
    module = verification_module()
    scopes = getattr(module, "RELEASE_SCOPES", None) if module else None
    if not isinstance(scopes, (set, frozenset, list, tuple)):
        return None
    return frozenset(map(str, scopes))


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


def pr_token(state: dict) -> str:
    """Return the current PR token used in governed documents."""
    end = pr_suffix(state)
    return f"PR #{end}" if end.isdigit() else "current PR"


def w1_count(state: dict) -> int | None:
    """Return the current W1 pass count from the canonical inventory."""
    inventory = state.get("test_inventory")
    value = inventory.get("w1") if isinstance(inventory, dict) else None
    return value if isinstance(value, int) else None


def required_check_count(state: dict) -> int | None:
    """Return the required-check count from the canonical branch policy."""
    protection = state.get("branch_protection")
    checks = (
        protection.get("required_checks")
        if isinstance(protection, dict)
        else None
    )
    return len(checks) if isinstance(checks, list) else None


def w1_count_re(count: int) -> re.Pattern[str]:
    """Match a W1 test count in either word order."""
    number = str(count)
    return re.compile(
        rf"\bW1[^\n]{{0,60}}\b{number}\s+(?:offline\s+)?"
        rf"(?:pytest\s+)?(?:tests?|passes)\b"
        rf"|\b{number}\s+(?:offline\s+)?(?:pytest\s+)?(?:W1\s+)?"
        rf"(?:tests?|passes)\b",
        re.IGNORECASE,
    )


def checks_count_re(count: int) -> re.Pattern[str]:
    """Match a required-check count."""
    return re.compile(rf"\b{count}\s+required checks\b", re.IGNORECASE)


def historical_tokens(state: dict) -> tuple[set[str], set[str]]:
    """Return historical release versions and PR numbers from the state."""
    versions: set[str] = set()
    prs: set[str] = set()
    records = state.get("historical_releases")
    if not isinstance(records, list):
        return versions, prs
    for record in records:
        if not isinstance(record, dict):
            continue
        version = str(record.get("release_version", ""))
        if version:
            versions.add(version)
        number = str(record.get("pr", ""))
        if number.isdigit():
            prs.add(number)
    return versions, prs


def stale_patterns(state: dict) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Build stale-current-claim patterns from the canonical record."""
    patterns: list[tuple[re.Pattern[str], str]] = []
    historical_versions, prs = historical_tokens(state)
    for version in sorted(filter(None, historical_versions)):
        escaped = re.escape(version)
        patterns.append(
            (
                re.compile(
                    rf"\b{escaped}\b[^\n]{{0,80}}\b(?:is|are|was|remains|becomes)"
                    rf"\s+(?:the\s+)?(?:CURRENT|current|final)\b"
                    rf"|\b(?:CURRENT|current|final)\s+release[^\n]{{0,40}}\b{escaped}\b(?!\.)",
                    re.IGNORECASE,
                ),
                f"stale {version} current claim",
            )
        )
        patterns.append(
            (
                re.compile(
                    rf"\b{escaped}\b[^\n]{{0,40}}\bIN PROGRESS\b"
                    rf"|\bIN PROGRESS\b[^\n]{{0,40}}\b{escaped}\b",
                    re.IGNORECASE,
                ),
                f"stale {version} IN PROGRESS",
            )
        )
    current = str(state.get("release_version", ""))
    if current:
        escaped = re.escape(current)
        patterns.append(
            (
                re.compile(
                    rf"\b{escaped}\b[^\n]{{0,40}}\bIN PROGRESS\b"
                    rf"|\bIN PROGRESS\b[^\n]{{0,40}}\b{escaped}\b",
                    re.IGNORECASE,
                ),
                f"stale {current} IN PROGRESS",
            )
        )
    for number in sorted(prs):
        token = rf"PR\s*#{number}\b"
        patterns.append(
            (
                re.compile(
                    rf"\b{token}[^\n]{{0,20}}\b(?:is|was|remains)\s+"
                    rf"(?:the\s+)?(?:CURRENT|current|final)\b"
                    rf"|\b(?:CURRENT|current|final)\s+release[^\n]{{0,30}}{token}"
                    rf"|\b(?:is|was|remains)\s+(?:the\s+)?(?:CURRENT|current|final)"
                    rf"\s+release[^\n]{{0,40}}{token}",
                    re.IGNORECASE,
                ),
                f"stale PR #{number} current claim",
            )
        )
    return tuple(patterns)


def stale_errors(
    text_lines: list[str], labels: list[bool], state: dict
) -> list[str]:
    """Report old release claims made in unlabelled current-state records."""
    errors: list[str] = []
    patterns = stale_patterns(state)
    count = w1_count(state)
    for number, (line, labelled) in enumerate(
        zip(text_lines, labels, strict=True), 1
    ):
        if labelled:
            continue
        for pattern, label in patterns:
            if pattern.search(line):
                errors.append(f"{number}: {label}")
        if count is None:
            continue
        for match in re.finditer(
            r"\b(?:W1[^\n]{0,60}\b)?(\d+)\s+(?:offline\s+)?"
            r"(?:pytest\s+)?(?:tests?|passes)\b",
            line,
            re.IGNORECASE,
        ):
            if int(match.group(1)) != count:
                errors.append(f"{number}: stale W1 {match.group(1)} tests")
        checks = required_check_count(state)
        if checks is None:
            continue
        for match in re.finditer(
            r"\b(\d+)\s+required checks\b", line, re.IGNORECASE
        ):
            if int(match.group(1)) != checks:
                errors.append(
                    f"{number}: stale {match.group(1)} required checks"
                )
    return errors


def labelled_lines(lines: list[str]) -> list[bool]:
    """Flag historical sections and records that may retain old values."""
    labels: list[bool] = []
    historical = False
    for line in lines:
        if line.startswith("#"):
            historical = any(
                marker in line.lower() for marker in SECTION_MARKERS
            )
        labels.append(
            historical or any(marker in line.lower() for marker in LINE_MARKERS)
        )
    return labels


def token_errors(label: str, current_text: str, state: dict) -> list[str]:
    """Require every current-record token somewhere in a document or CSV."""
    count = w1_count(state)
    checks = required_check_count(state)
    native = str(state.get("native_q_status", ""))
    scope = str(state.get("release_scope", ""))
    required = (
        (
            str(state.get("release_version", "")),
            str(state.get("release_version", "")) in current_text,
        ),
        (pr_token(state), pr_token(state) in current_text),
        (
            str(state.get("release_status", "")),
            str(state.get("release_status", "")) in current_text,
        ),
        (
            f"W1 {count} tests",
            count is not None and bool(w1_count_re(count).search(current_text)),
        ),
        (
            f"{checks} required checks",
            checks is not None
            and bool(checks_count_re(checks).search(current_text)),
        ),
        (scope, scope in current_text),
        (native, native in current_text),
    )
    return [
        f"{label}: current record missing {name}"
        for name, found in required
        if not found
    ]


def csv_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Require governed CSV field counts, current tokens and no stale claims."""
    errors: list[str] = []
    for relative, count in CSV_FIELDS.items():
        text = read_text(root, relative)
        if text is None:
            errors.append(f"{relative}: missing or unreadable")
            continue
        lines = text.splitlines()
        rows = list(csv.reader(lines))
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
        # Stale-claim detection uses historical labels; the current record
        # is independently located by the canonical release version token.
        for detail in stale_errors(lines, labelled_lines(lines), state):
            errors.append(f"{relative}:{detail}")
        version = str(state.get("release_version", ""))
        current_lines = [line for line in lines if version and version in line]
        errors.extend(token_errors(relative, "\n".join(current_lines), state))
    return errors


def doc_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Require current-state sections to agree with the canonical record."""
    errors: list[str] = []
    for relative in DOC_PATHS:
        text = read_text(root, relative)
        if text is None:
            errors.append(f"{relative}: missing or unreadable")
            continue
        lines = text.splitlines()
        labels = labelled_lines(lines)
        current_lines = [
            line
            for line, labelled in zip(lines, labels, strict=True)
            if not labelled
        ]
        current_text = "\n".join(current_lines)
        for detail in stale_errors(
            current_lines, [False] * len(current_lines), state
        ):
            errors.append(f"{relative}:{detail}")
        errors.extend(token_errors(relative, current_text, state))
    return errors


def required_field_errors(state: dict) -> list[str]:
    """Report every missing or empty field of the canonical state."""
    return [
        f"{STATE_RELATIVE}: missing required field {field}"
        for field in REQUIRED_FIELDS
        if field not in state or state[field] in (None, "", [], {})
    ]


def identity_errors(root: pathlib.Path, state: dict) -> list[str]:
    """Validate derived release identity, digests and the captured commit."""
    errors: list[str] = []
    version = str(state.get("release_version", ""))
    if not VERSION_RE.fullmatch(version):
        errors.append(
            f"{STATE_RELATIVE}: release_version is not a release version"
        )
    pr = pr_suffix(state)
    expected_id = f"{version}-PR{pr}" if pr.isdigit() else ""
    if not expected_id or state.get("release_id") != expected_id:
        errors.append(
            f"{STATE_RELATIVE}: release_id is not bound to version and PR"
        )
    scope = str(state.get("release_scope", ""))
    scopes = release_scopes()
    if not scope or (scopes is not None and scope not in scopes):
        errors.append(
            f"{STATE_RELATIVE}: release_scope is not an allowed scope"
        )
    if state.get("release_status") != "COMPLETE":
        errors.append(f"{STATE_RELATIVE}: release_status is not COMPLETE")
    if state.get("independent_acceptance") != "PENDING":
        errors.append(
            f"{STATE_RELATIVE}: independent_acceptance is not PENDING"
        )
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
    return errors


def test_inventory_errors(
    root: pathlib.Path, state: dict, gates: dict
) -> list[str]:
    """Reconcile every recorded test count with the generated report."""
    errors: list[str] = []
    inventory = state.get("test_inventory")
    if not isinstance(inventory, dict):
        errors.append(f"{STATE_RELATIVE}: test_inventory must be an object")
        return errors
    for name, (inventory_key, report_key) in GATE_COUNT_KEYS.items():
        gate = gates.get(name)
        observed = gate.get("counts", {}).get(report_key) if gate else None
        if not isinstance(observed, int):
            errors.append(f"{STATE_RELATIVE}: {name} count is missing")
            continue
        recorded = inventory.get(inventory_key)
        if recorded != observed:
            errors.append(f"{STATE_RELATIVE}: {name} test count differs")
    return errors


def branch_policy_errors(state: dict, gates: dict) -> list[str]:
    """Validate branch protection, the native-q gate and trading status."""
    errors: list[str] = []
    protection = state.get("branch_protection")
    if not isinstance(protection, dict):
        errors.append(f"{STATE_RELATIVE}: branch_protection must be an object")
    else:
        checks = protection.get("required_checks")
        if not isinstance(checks, list) or set(checks) != set(REQUIRED_CHECKS):
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
        or trading.get("live_execution") != "DISABLED"
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
    match = RANGE_RE.match(str(state.get("merged_pull_requests")))
    pr = pr_suffix(state)
    if (
        not match
        or not pr.isdigit()
        or match.group(2) != pr
        or (int(match.group(1)) >= int(pr))
    ):
        errors.append(
            f"{STATE_RELATIVE}: merged_pull_requests must end at current PR"
        )
    history = state.get("historical_releases")
    if not isinstance(history, list) or not history:
        errors.append(f"{STATE_RELATIVE}: historical_releases must be a list")
        return errors
    for number, record in enumerate(history, 1):
        required = {"note", "pr", "release_version", "scope", "status"}
        label = f"{STATE_RELATIVE}: historical_releases[{number}]"
        if not isinstance(record, dict) or not required <= set(record):
            errors.append(f"{label} incomplete")
            continue
        if "historical" not in str(record["note"]).lower():
            errors.append(f"{label} not labelled historical")
        if not isinstance(record["pr"], int) or record["pr"] >= int(pr):
            errors.append(f"{label} has an invalid historical PR")
        if not VERSION_RE.fullmatch(str(record["release_version"])):
            errors.append(f"{label} has an invalid release version")
        if record["release_version"] == state.get("release_version"):
            errors.append(f"{label} duplicates the current release version")
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
        "independent_acceptance",
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
