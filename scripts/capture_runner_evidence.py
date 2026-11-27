#!/usr/bin/env python3
"""Capture in-job runner evidence for the gated native-q tier.

The licensed qualification job records the event, exact commit, declared
runner labels and q runtime. GitHub does not publish runner labels or the
runner group through environment variables, so these values are declared by
the workflow, not observed here. The authoritative observed identity is
attested after the job by ``scripts/attest_runner.py`` through the GitHub API.
Evidence is written before validation so a failing qualification still leaves
an auditable artifact.

Usage:
  python3 scripts/capture_runner_evidence.py --output PATH
  python3 scripts/capture_runner_evidence.py --input PATH
  python3 scripts/capture_runner_evidence.py --q-version "" \
      --native-q-runner-execution NOT_OBSERVED --output PATH
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "assurance" / "runner-evidence.json"
REQUIRED_LABEL = "quant-native-q"
RUNNER_GROUP_EXPECTATION = "quant-native-q"
ATTESTATION_MECHANISM_CHECKED = "CHECKED"
RUNNER_EXECUTION_NOT_OBSERVED = "NOT_OBSERVED"
RUNNER_EXECUTION_OBSERVED = "OBSERVED"
RUNNER_EXECUTION_VALUES = frozenset(
    (RUNNER_EXECUTION_OBSERVED, RUNNER_EXECUTION_NOT_OBSERVED)
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_FIELDS = (
    "runner_name",
    "runner_os",
    "repository",
    "workflow",
    "job",
)
ATTESTATION_PATH = "data/assurance/runner-attestation.json"
DEREGISTRATION_REQUIREMENT = (
    "The governed native-q runner must be deregistered and destroyed by the "
    "external runner operator after every single job, on success and on "
    "failure. This in-job capture cannot verify deregistration; the separate "
    "post-job API attestation does not claim it without API evidence."
)


def parse_labels(raw: str) -> list[str]:
    """Decode a comma-separated or JSON-array runner label list."""
    text = raw.strip()
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = None
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def probe_q_version(qhome: str) -> str:
    """Read the q version from the QHOME binary without printing its path."""
    qbin = Path(qhome) / "bin" / "q"
    if not qhome or not qbin.is_file():
        return ""
    try:
        probe = subprocess.run(
            [str(qbin), "-q"],
            input="echo 'QVERSION=', .z.K, '.', .z.k; exit 0\n",
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(
        r"QVERSION=(\d+(?:\.\d+){1,3})", probe.stdout + probe.stderr
    )
    return match.group(1) if match else ""


def resolve_head_sha(explicit: str | None) -> str:
    """Return the exact checked-out HEAD SHA or an explicit override."""
    if explicit is not None:
        return explicit.strip()
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def capture(
    env: Mapping[str, str],
    *,
    q_version: str | None = None,
    head_sha: str | None = None,
    native_q_runner_execution: str | None = None,
    runner_attestation_mechanism: str = ATTESTATION_MECHANISM_CHECKED,
) -> dict[str, object]:
    """Build the runner evidence payload from the runner environment."""
    version = (
        q_version
        if q_version is not None
        else probe_q_version(env.get("QHOME", ""))
    )
    execution = (
        native_q_runner_execution
        if native_q_runner_execution is not None
        else (
            RUNNER_EXECUTION_OBSERVED
            if version.strip()
            else RUNNER_EXECUTION_NOT_OBSERVED
        )
    )
    return {
        "schema_version": 1,
        "event_name": env.get("GITHUB_EVENT_NAME", ""),
        "head_sha": resolve_head_sha(head_sha),
        "expected_sha": env.get(
            "QUANT_EXPECTED_SHA", env.get("GITHUB_SHA", "")
        ).strip(),
        "runner_name": env.get("RUNNER_NAME", ""),
        "runner_os": env.get("RUNNER_OS", ""),
        "runner_labels": parse_labels(
            env.get("RUNNER_LABELS", env.get("QUANT_RUNNER_LABELS", ""))
        ),
        "runner_labels_source": "workflow-declared; not API-observed",
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "job": env.get("GITHUB_JOB", ""),
        "runner_group_expected": RUNNER_GROUP_EXPECTATION,
        "runner_group_observed": env.get("RUNNER_GROUP", "").strip(),
        "identity_attestation_required": True,
        "identity_attestation_path": ATTESTATION_PATH,
        "ephemeral_lifecycle": {
            "deregistration_verified": False,
            "deregistration_evidence": "",
            "requirement": DEREGISTRATION_REQUIREMENT,
        },
        "qhome": env.get("QHOME", ""),
        "q_version": version.strip(),
        "q_runtime_version": version.strip(),
        "native_q_runner_execution": execution,
        "runner_attestation_mechanism": runner_attestation_mechanism,
        "scope": "Q_PERSISTENCE",
        "required_for_portable_research": False,
    }


def validate(evidence: Mapping[str, object]) -> list[str]:
    """Return every reason the evidence cannot qualify the native-q job."""
    problems: list[str] = []
    labels = evidence.get("runner_labels")
    label_set = (
        {str(item) for item in labels} if isinstance(labels, list) else set()
    )
    if REQUIRED_LABEL not in label_set:
        problems.append(
            f"runner labels lack required dedicated label {REQUIRED_LABEL}"
        )
    if str(evidence.get("event_name", "")).strip().casefold() == "pull_request":
        problems.append("pull_request events cannot use the native-q runner")
    head = str(evidence.get("head_sha", "")).strip()
    expected = str(evidence.get("expected_sha", "")).strip()
    if not SHA_RE.fullmatch(head):
        problems.append("head SHA is not an exact 40-hex commit id")
    if head != expected:
        problems.append(
            f"head SHA mismatch: head={head or 'missing'} "
            f"expected={expected or 'missing'}"
        )
    for field in EVIDENCE_FIELDS:
        if not str(evidence.get(field, "")).strip():
            problems.append(f"missing runner evidence: {field}")
    expected_group = str(evidence.get("runner_group_expected", "")).strip()
    if expected_group != RUNNER_GROUP_EXPECTATION:
        problems.append("runner group expectation is not quant-native-q")
    observed = str(evidence.get("runner_group_observed", "")).strip()
    if observed and observed != expected_group:
        problems.append(
            "runner group differs from the dedicated-runner expectation"
        )
    lifecycle = evidence.get("ephemeral_lifecycle")
    if isinstance(lifecycle, dict):
        if (
            lifecycle.get("deregistration_verified") is True
            and not str(lifecycle.get("deregistration_evidence", "")).strip()
        ):
            problems.append("deregistration claimed without evidence")
    execution = str(evidence.get("native_q_runner_execution", ""))
    if execution not in RUNNER_EXECUTION_VALUES:
        problems.append(
            "native_q_runner_execution must be OBSERVED or NOT_OBSERVED"
        )
    if (
        execution == RUNNER_EXECUTION_OBSERVED
        and not str(evidence.get("q_version", "")).strip()
    ):
        problems.append("q runtime execution claimed without q version")
    if not str(evidence.get("runner_attestation_mechanism", "")).strip():
        problems.append("missing runner attestation mechanism")
    return problems


def read_evidence(path: Path) -> tuple[dict[str, object] | None, str]:
    """Load evidence JSON or return an error message."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"malformed runner evidence JSON {path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"runner evidence {path} must contain a JSON object"
    return payload, ""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse capture and offline verification options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="path of the machine-readable evidence file",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="verify an existing evidence file instead of capturing one",
    )
    parser.add_argument(
        "--q-version",
        help="explicit q version override for offline verification",
    )
    parser.add_argument(
        "--head-sha",
        help="explicit HEAD SHA override for offline verification",
    )
    parser.add_argument(
        "--native-q-runner-execution",
        choices=RUNNER_EXECUTION_VALUES,
        help="override the derived q runner execution status",
    )
    parser.add_argument(
        "--runner-attestation-mechanism",
        default=ATTESTATION_MECHANISM_CHECKED,
        help="attestation mechanism status carried by the evidence",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Capture or verify runner evidence and return an exit status."""
    args = parse_args(argv)
    if args.input is not None:
        evidence, error = read_evidence(args.input)
        if error:
            print(f"FAIL: {error}", file=sys.stderr)
            return 1
    else:
        evidence = capture(
            os.environ,
            q_version=args.q_version,
            head_sha=args.head_sha,
            native_q_runner_execution=args.native_q_runner_execution,
            runner_attestation_mechanism=args.runner_attestation_mechanism,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(f"runner evidence written: {args.output}")
    problems = validate(evidence if evidence is not None else {})
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        print(
            f"runner evidence check failed: {len(problems)} problem(s)",
            file=sys.stderr,
        )
        return 1
    print("runner evidence check passed: dedicated runner, event and SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
