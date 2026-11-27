#!/usr/bin/env python3
"""Post-job runner attestation from the GitHub Actions REST API.

The in-job capture script records workflow-declared label values; only the
GitHub API can show which runner executed a completed job. This
script queries that API after the native-q job finishes and writes a canonical
attestation of the observed runner identity, the exact head SHA and the job
lifecycle timestamps.

Usage:
  python3 scripts/attest_runner.py attest \
      --run-id ID --job-id ID --expected-sha SHA --output PATH
  python3 scripts/attest_runner.py skipped --run-id ID --output PATH
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "assurance" / "runner-attestation.json"
REPOSITORY = "siebengestirn/quant"
REQUIRED_GROUP = "quant-native-q"
REQUIRED_LABEL = "quant-native-q"
RUNNER_EXECUTION_OBSERVED = "OBSERVED"
RUNNER_EXECUTION_NOT_OBSERVED = "NOT_OBSERVED"
RUNNER_EXECUTION_VALUES = (
    RUNNER_EXECUTION_OBSERVED,
    RUNNER_EXECUTION_NOT_OBSERVED,
)
ATTESTATION_MECHANISM_CHECKED = "CHECKED"
GATED_NOT_RUN = "GATED_NOT_RUN"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTITY_FIELDS = (
    "runner_id",
    "runner_name",
    "runner_labels",
    "runner_group_id",
    "runner_group_name",
)
RUNNER_ID_FIELDS = frozenset({"runner_id", "runner_group_id"})
RUNNER_NAME_FIELDS = frozenset({"runner_name", "runner_group_name"})
LIFECYCLE_FIELDS = ("started_at", "completed_at")
DEACTIVATION_FIELDS = ("runner_deregistered_at", "runner_deactivated_at")
DEREGISTRATION_REQUIREMENT = (
    "The governed native-q runner must be deregistered and destroyed by the "
    "external runner operator after every single job, on success and on "
    "failure. The GitHub job API does not report deregistration, so this "
    "attestation records deregistration_verified=false unless an API field "
    "explicitly documents deactivation."
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


class AttestationError(RuntimeError):
    """Raised when the GitHub API cannot supply usable job evidence."""


def normalize_labels(value: Any) -> list[str]:
    """Return the non-empty runner labels from an API list value."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def observed_labels(job: Mapping[str, Any]) -> list[str]:
    """Return runner labels from an API job (the ``labels`` field)."""
    labels = job.get("labels")
    if labels is None:
        labels = job.get("runner_labels")
    return normalize_labels(labels)


def gh_api(path: str, runner: Runner = subprocess.run) -> Any:
    """Call ``gh api`` for a repository path and decode its JSON response."""
    result = runner(
        ["gh", "api", f"repos/{REPOSITORY}/{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or "no error output"
        raise AttestationError(f"gh api {path} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise AttestationError(f"malformed gh api JSON for {path}") from exc


def find_job(payload: Any, job_id: str, run_id: str) -> Mapping[str, Any]:
    """Locate one job in a workflow-run jobs API response."""
    if not isinstance(payload, dict) or not isinstance(
        payload.get("jobs"), list
    ):
        raise AttestationError(
            "workflow jobs response must contain a jobs array"
        )
    matches = [
        item
        for item in payload["jobs"]
        if isinstance(item, dict) and str(item.get("id")) == job_id
    ]
    if len(matches) != 1:
        raise AttestationError(
            f"job {job_id} not uniquely found in workflow run {run_id}"
        )
    return matches[0]


def fetch_job(
    run_id: str, job_id: str, runner: Runner = subprocess.run
) -> dict[str, Any]:
    """Read the completed job from both workflow-job API endpoints."""
    listed = find_job(
        gh_api(f"actions/runs/{run_id}/jobs", runner), job_id, run_id
    )
    observed = gh_api(f"actions/jobs/{job_id}", runner)
    if not isinstance(observed, dict):
        raise AttestationError("workflow job response must be a JSON object")
    if str(observed.get("id")) != job_id:
        raise AttestationError("workflow job id differs from the requested id")
    if (
        str(listed.get("run_id")) != run_id
        or str(observed.get("run_id")) != run_id
    ):
        raise AttestationError(
            "job run id does not match the requested workflow run"
        )
    return observed


def missing_identity_fields(job: Mapping[str, Any]) -> list[str]:
    """List observed runner identity fields that are absent or invalid."""
    missing: list[str] = []
    for field in IDENTITY_FIELDS:
        value = job.get(field)
        if field == "runner_labels":
            if not observed_labels(job):
                missing.append(field)
        elif field in RUNNER_ID_FIELDS:
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                missing.append(field)
        elif field in RUNNER_NAME_FIELDS:
            if not isinstance(value, str) or not value.strip():
                missing.append(field)
        elif value is None or isinstance(value, bool):
            missing.append(field)
    return missing


def deregistration_from_api(job: Mapping[str, Any]) -> tuple[bool, str]:
    """Return explicit API deactivation evidence, if the API supplies any."""
    for field in DEACTIVATION_FIELDS:
        value = job.get(field)
        if isinstance(value, str) and value.strip():
            return True, value.strip()
    return False, ""


def validate_job(
    job: Mapping[str, Any], expected_sha: str, run_id: str
) -> list[str]:
    """Return every reason the API job cannot attest the native-q runner."""
    problems: list[str] = []
    if str(job.get("run_id", "")).strip() != run_id.strip():
        problems.append("job run id does not match the requested workflow run")
    if not SHA_RE.fullmatch(expected_sha.strip()):
        problems.append("expected SHA is not an exact 40-hex commit id")
    head_sha = str(job.get("head_sha", "")).strip()
    if head_sha != expected_sha.strip():
        problems.append(
            "head SHA mismatch: head="
            + (head_sha or "missing")
            + " expected="
            + (expected_sha.strip() or "missing")
        )
    for field in missing_identity_fields(job):
        problems.append("missing runner identity field: " + field)
    group = str(job.get("runner_group_name", "")).strip()
    if group and group != REQUIRED_GROUP:
        problems.append(f"runner group is {group}, expected {REQUIRED_GROUP}")
    if REQUIRED_LABEL not in observed_labels(job):
        problems.append(f"runner labels lack required label {REQUIRED_LABEL}")
    if str(job.get("status", "")).strip() != "completed":
        problems.append("workflow job is not completed in the API response")
    conclusion = str(job.get("conclusion", "")).strip().lower()
    if conclusion == "skipped":
        problems.append("skipped native-q job cannot attest runner execution")
    elif conclusion != "success":
        problems.append(f"unexpected job conclusion: {conclusion or 'missing'}")
    for field in LIFECYCLE_FIELDS:
        if not str(job.get(field, "")).strip():
            problems.append("missing lifecycle evidence: " + field)
    return problems


def build_attestation(
    job: Mapping[str, Any],
    run_id: str,
    job_id: str,
    *,
    native_q_runner_execution: str = RUNNER_EXECUTION_OBSERVED,
    runner_attestation_mechanism: str = ATTESTATION_MECHANISM_CHECKED,
) -> dict[str, Any]:
    """Build the canonical attestation document from an API job object."""
    verified, evidence = deregistration_from_api(job)
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "run_id": run_id,
        "job_id": job_id,
        "job_name": str(job.get("name", "")),
        "observed": {
            "runner_id": job.get("runner_id"),
            "runner_name": str(job.get("runner_name", "")),
            "runner_labels": observed_labels(job),
            "runner_group_id": job.get("runner_group_id"),
            "runner_group_name": str(job.get("runner_group_name", "")),
            "head_sha": str(job.get("head_sha", "")),
        },
        "expected": {
            "runner_group_name": REQUIRED_GROUP,
            "runner_label": REQUIRED_LABEL,
        },
        "ephemeral_lifecycle": {
            "job_id": job_id,
            "status": str(job.get("status", "")),
            "conclusion": str(job.get("conclusion", "")),
            "started_at": str(job.get("started_at", "")),
            "completed_at": str(job.get("completed_at", "")),
            "deregistration_verified": verified,
            "deregistration_evidence": evidence,
            "requirement": DEREGISTRATION_REQUIREMENT,
        },
        "evidence_status": {
            "native_q_runner_execution": native_q_runner_execution,
            "runner_attestation_mechanism": runner_attestation_mechanism,
        },
        "api_endpoints": [
            f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs",
            f"repos/{REPOSITORY}/actions/jobs/{job_id}",
        ],
        "scope": "Q_PERSISTENCE",
        "required_for_portable_research": False,
    }


def build_skipped_attestation(run_id: str) -> dict[str, Any]:
    """Build a stub for a skipped native-q job without claiming execution."""
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "run_id": run_id,
        "job_id": "",
        "job_name": "Native KDB-X recovery (gated tier)",
        "observed": {},
        "expected": {
            "runner_group_name": REQUIRED_GROUP,
            "runner_label": REQUIRED_LABEL,
        },
        "native_q_result": GATED_NOT_RUN,
        "evidence_status": {
            "native_q_runner_execution": RUNNER_EXECUTION_NOT_OBSERVED,
            "runner_attestation_mechanism": ATTESTATION_MECHANISM_CHECKED,
        },
        "note": (
            "The native-q job was skipped; no runner identity or q runtime "
            "execution was observed. The post-job attestation mechanism was "
            "checked, but no completed job evidence exists for this run."
        ),
        "api_endpoints": [],
        "scope": "Q_PERSISTENCE",
        "required_for_portable_research": False,
    }


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize an attestation document deterministically."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the post-job attestation command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    attest = subparsers.add_parser(
        "attest", help="record observed runner identity from the GitHub API"
    )
    attest.add_argument("--run-id", required=True)
    attest.add_argument("--job-id", required=True)
    attest.add_argument("--expected-sha", required=True)
    attest.add_argument(
        "--native-q-runner-execution",
        choices=RUNNER_EXECUTION_VALUES,
        default=RUNNER_EXECUTION_OBSERVED,
        help="runner execution status carried by the evidence",
    )
    attest.add_argument(
        "--runner-attestation-mechanism",
        default=ATTESTATION_MECHANISM_CHECKED,
        help="attestation mechanism status carried by the evidence",
    )
    attest.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="evidence path"
    )
    skipped = subparsers.add_parser(
        "skipped", help="record that no native-q runner execution was observed"
    )
    skipped.add_argument("--run-id", required=True)
    skipped.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="evidence path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Attest the observed runner identity and return an exit status."""
    args = parse_args(argv)
    run_id = args.run_id.strip()
    if not run_id.isdigit():
        print("FAIL: --run-id must be a positive integer", file=sys.stderr)
        return 1
    if args.command == "skipped":
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            canonical_json(build_skipped_attestation(run_id)),
            encoding="utf-8",
        )
        print(f"runner attestation stub written: {output}")
        print(
            "native q runner execution NOT_OBSERVED; no q runtime "
            "execution claimed"
        )
        return 0
    job_id = args.job_id.strip()
    if not job_id.isdigit():
        print(
            "FAIL: --run-id and --job-id must be positive integers",
            file=sys.stderr,
        )
        return 1
    try:
        job = fetch_job(run_id, job_id)
    except AttestationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        canonical_json(
            build_attestation(
                job,
                run_id,
                job_id,
                native_q_runner_execution=args.native_q_runner_execution,
                runner_attestation_mechanism=(
                    args.runner_attestation_mechanism
                ),
            )
        ),
        encoding="utf-8",
    )
    print(f"runner attestation written: {output}")
    problems = validate_job(job, args.expected_sha, run_id)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        print(
            f"runner attestation failed: {len(problems)} problem(s)",
            file=sys.stderr,
        )
        return 1
    print(
        "runner attestation passed: dedicated group, label, SHA and lifecycle; "
        f"runner execution {args.native_q_runner_execution}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
