#!/usr/bin/env python3
"""Fail closed on native-q JUnit results that do not prove a real run.

The native-q tier is disabled for portable research. Once it is enabled on a
governed runner, a JUnit report alone can look green while every integration
test was skipped or silently absent. This checker requires executed tests,
the expected integration test names, no failures or errors, no skips when
``QUANT_NATIVE_Q_REQUIRED=1``, and recorded q/PyKX/C API evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JUNIT = ROOT / "data" / "assurance" / "native-q-junit.xml"
DEFAULT_EVIDENCE = ROOT / "data" / "assurance" / "native-q-evidence.json"

REQUIRED_INTEGRATION_TESTS = (
    "test_cpp_verify_uses_isolated_instance",
    "test_pykx_reads_same_events",
    "test_q_feature_math_matches_fixture",
    "test_q_daily_vwap_proxy_uses_typical_price",
    "test_python_and_q_vwap_proxy_agree",
    "test_point_in_time_features_and_recovery",
    "test_production_service_unchanged",
    "test_benchmark_is_isolated",
    "test_new_process_checkpoint_recovery",
    "test_interrupted_checkpoint_is_rejected",
    "test_same_row_count_corruption_is_rejected",
    "test_generation_directory_cannot_be_reused",
    "test_ordinary_historical_load_verifies_manifest",
    "test_generation_survives_reset_and_advances",
    "test_orphan_staging_generation_is_removed",
    "test_writer_lock_blocks_concurrent_persist",
    "test_safe_path_grammar",
    "test_stale_writer_lock_recovery",
    "test_sha256_digest_is_canonical",
    "test_orphan_lock_is_explicit",
    "test_orphan_cleanup_rejects_unsafe_names",
    "test_sequence_conflict_detection",
    "test_executable_allowlist_and_secure_temp",
)

RUNTIME_EVIDENCE_FIELDS = ("q_runtime_version", "pykx_version")
EMPTY_VALUES = {"", "unknown", "unavailable", "absent", "none", "null"}
C_API_VALUES = {"compiled", "available"}


def read_junit(path: Path) -> tuple[list[ET.Element], str]:
    """Return the testcase elements of a JUnit report or an error message."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"cannot read JUnit report {path}: {exc}"
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [], f"malformed JUnit XML {path}: {exc}"
    return list(root.iter("testcase")), ""


def count_outcomes(cases: list[ET.Element]) -> tuple[int, int, int, int]:
    """Count total, failed, errored and skipped test cases."""
    total = len(cases)
    failures = sum(len(case.findall("failure")) for case in cases)
    errors = sum(len(case.findall("error")) for case in cases)
    skipped = sum(len(case.findall("skipped")) for case in cases)
    return total, failures, errors, skipped


def missing_required_tests(cases: list[ET.Element]) -> list[str]:
    """List required integration tests absent from the JUnit report."""
    names = {case.get("name", "") for case in cases}
    return [name for name in REQUIRED_INTEGRATION_TESTS if name not in names]


def read_evidence(path: Path) -> tuple[dict[str, object], str]:
    """Return decoded evidence JSON or an error message."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"cannot read evidence file {path}: {exc}"
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return {}, f"malformed evidence JSON {path}: {exc}"
    if not isinstance(payload, dict):
        return {}, f"evidence file {path} must contain a JSON object"
    return payload, ""


def check_evidence(evidence: dict[str, object]) -> list[str]:
    """Verify runtime versions and C API enablement evidence."""
    problems = []
    for field in RUNTIME_EVIDENCE_FIELDS:
        value = evidence.get(field)
        if not isinstance(value, str) or value.strip().lower() in EMPTY_VALUES:
            problems.append(f"missing runtime evidence: {field}")

    c_api = evidence.get("c_api")
    if not isinstance(c_api, str) or c_api.strip().lower() not in C_API_VALUES:
        problems.append("C API evidence must report c_api=compiled/available")

    detail = evidence.get("c_api_detail")
    if not isinstance(detail, str) or "c_api=available" not in detail.lower():
        problems.append(
            "missing C API evidence: c_api_detail must record "
            "c_api=available from quant doctor"
        )
    return problems


def check_results(
    junit: Path, evidence_path: Path, required: bool
) -> tuple[list[str], tuple[int, int, int, int]]:
    """Collect rejection reasons and the JUnit outcome counts."""
    problems = []
    counts = (0, 0, 0, 0)
    cases, error = read_junit(junit)
    if error:
        problems.append(error)
    else:
        counts = count_outcomes(cases)
        total, failures, errors, skipped = counts
        if total == 0:
            problems.append("no tests executed in the native-q JUnit report")
        if failures or errors:
            problems.append(f"native-q failures={failures} errors={errors}")
        if required and skipped:
            problems.append(f"QUANT_NATIVE_Q_REQUIRED=1 but skipped={skipped}")
        absent = missing_required_tests(cases)
        if absent:
            problems.append(
                "missing required integration tests: " + ", ".join(absent)
            )

    evidence, error = read_evidence(evidence_path)
    if error:
        problems.append(error)
    else:
        problems.extend(check_evidence(evidence))
    return problems, counts


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse checker command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit",
        type=Path,
        default=DEFAULT_JUNIT,
        help="JUnit XML produced by the native-q pytest run",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=DEFAULT_EVIDENCE,
        help="JSON file with q/PyKX/C API runtime evidence",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the checker and return a process exit status."""
    args = parse_args(argv)
    required = os.environ.get("QUANT_NATIVE_Q_REQUIRED") == "1"
    problems, counts = check_results(args.junit, args.evidence, required)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        print(f"native-q evidence check failed: {len(problems)} problem(s)")
        return 1
    total, failures, errors, skipped = counts
    print(
        "native-q evidence check passed: "
        f"tests={total} failures={failures} errors={errors} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
