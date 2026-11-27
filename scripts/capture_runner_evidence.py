#!/usr/bin/env python3
"""Capture verifiable runner evidence for the gated native-q tier.

The licensed qualification job must show which runner executed it, on which
event and exact commit, and with which q runtime. GitHub does not publish
runner labels through an environment variable, so the workflow supplies the
label set that its ``runs-on`` declaration requires. Evidence is written
before validation so a failing qualification still leaves an auditable
artifact.

Usage:
  python3 scripts/capture_runner_evidence.py --output PATH
  python3 scripts/capture_runner_evidence.py --input PATH
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
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_FIELDS = (
    "runner_name",
    "runner_os",
    "repository",
    "workflow",
    "job",
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
) -> dict[str, object]:
    """Build the runner evidence payload from the runner environment."""
    version = (
        q_version
        if q_version is not None
        else probe_q_version(env.get("QHOME", ""))
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
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "job": env.get("GITHUB_JOB", ""),
        "runner_group": RUNNER_GROUP_EXPECTATION,
        "runner_group_observed": env.get("RUNNER_GROUP", "").strip(),
        "qhome": env.get("QHOME", ""),
        "q_version": version.strip(),
        "q_runtime_version": version.strip(),
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
    observed = str(evidence.get("runner_group_observed", "")).strip()
    if observed and observed != str(evidence.get("runner_group", "")):
        problems.append(
            "runner group differs from the dedicated-runner expectation"
        )
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
