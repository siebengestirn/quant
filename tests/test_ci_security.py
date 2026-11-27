"""Offline security invariants for the hosted CI workflow and git policy."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = ROOT / "git-policy.json"
RELEASE_CHECK = "Release assurance (offline)"
SELF_HOSTED = "self-hosted"
NATIVE_LABEL = "quant-native-q"
NATIVE_RUNS_ON = [SELF_HOSTED, NATIVE_LABEL]
SELF_HOSTED_GUARD = (
    "github.event_name != 'pull_request' && "
    "vars.QUANT_NATIVE_Q_ENABLED == 'true'"
)
PR_EXACT_REF = "${{ github.event.pull_request.head.sha || github.sha }}"
FULL_SHA_RE = re.compile(r"@[0-9a-f]{40}$")
OFFLINE_SUITES = (
    "tests/test_release_bundle.py",
    "tests/test_verify_report.py",
    "tests/test_native_q_ci.py",
    "tests/test_ci_security.py",
)
ASSURANCE = ROOT / "scripts" / "assurance.py"
NATIVE_Q_SKIP_REASON = (
    "KDB-X disabled in portable build; native-q qualification is a "
    "separate gated tier"
)


def parse_workflow(text: str) -> dict[str, Any]:
    """Parse the restricted YAML subset used by .github/workflows/ci.yml."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    position = 0

    def scalar(token: str) -> Any:
        token = token.strip()
        if token.startswith("[") and token.endswith("]"):
            inner = token[1:-1].strip()
            if not inner:
                return []
            return [part.strip().strip("'\"") for part in inner.split(",")]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
            return token[1:-1]
        return token

    def value(token: str, indent: int) -> Any:
        nonlocal position
        if token in ("|", "|-", ">", ">-"):
            block_lines: list[str] = []
            while position < len(lines) and lines[position][0] > indent:
                block_lines.append(lines[position][1])
                position += 1
            if token.startswith(">"):
                return " ".join(block_lines)
            return "\n".join(block_lines)
        return scalar(token)

    def block(indent: int) -> Any:
        nonlocal position
        if position >= len(lines) or lines[position][0] < indent:
            return None
        if lines[position][1].startswith("- "):
            items: list[Any] = []
            while (
                position < len(lines)
                and lines[position][0] == indent
                and lines[position][1].startswith("- ")
            ):
                remainder = lines[position][1][2:]
                position += 1
                key, separator, item_value = remainder.partition(":")
                if not separator:
                    items.append(scalar(remainder))
                    continue
                item: dict[str, Any] = {
                    key.strip(): value(item_value.strip(), indent)
                }
                while position < len(lines) and lines[position][0] > indent:
                    nested = block(lines[position][0])
                    if not isinstance(nested, dict):
                        break
                    item.update(nested)
                items.append(item)
            return items
        mapping: dict[str, Any] = {}
        while (
            position < len(lines)
            and lines[position][0] == indent
            and not lines[position][1].startswith("- ")
        ):
            key, separator, item_value = lines[position][1].partition(":")
            position += 1
            if not separator:
                mapping[key.strip()] = None
            elif item_value.strip():
                mapping[key.strip()] = value(item_value.strip(), indent)
            else:
                mapping[key.strip()] = block(indent + 2)
        return mapping

    parsed = block(lines[0][0]) if lines else {}
    return parsed if isinstance(parsed, dict) else {}


def workflow() -> dict[str, Any]:
    """Parse the tracked workflow from disk."""
    return parse_workflow(WORKFLOW.read_text(encoding="utf-8"))


def jobs(document: dict[str, Any]) -> dict[str, Any]:
    """Return the job mapping from a parsed workflow."""
    value = document.get("jobs")
    return value if isinstance(value, dict) else {}


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the step mapping objects of one job."""
    value = job.get("steps")
    if not isinstance(value, list):
        return []
    return [step for step in value if isinstance(step, dict)]


def commands(job: dict[str, Any]) -> str:
    """Join all shell commands of a job into one searchable string."""
    return "\n".join(str(step.get("run", "")) for step in steps(job))


def is_self_hosted(job: dict[str, Any]) -> bool:
    """Report whether a job declares a self-hosted runner or label set."""
    value = job.get("runs-on")
    if isinstance(value, list):
        return SELF_HOSTED in {str(item) for item in value}
    return str(value) == SELF_HOSTED


def events(document: dict[str, Any]) -> set[str]:
    """Return event names, tolerant of YAML 1.1 parsing of ``on`` as True."""
    triggers = document.get("on")
    if not isinstance(triggers, dict):
        triggers = document.get(True)
    if not isinstance(triggers, dict):
        return set()
    return {str(name) for name in triggers}


def pr_can_reach(job: dict[str, Any]) -> bool:
    """Report whether a job condition permits a pull_request event."""
    condition = str(job.get("if", ""))
    if "github.event_name != 'pull_request'" in condition:
        return False
    if "== 'pull_request'" in condition:
        return True
    if "github.event_name" in condition:
        return False
    return True


def test_workflow_subset_parses_jobs_triggers_and_permissions():
    """The offline parser sees every job, PR trigger and read-only token."""
    document = workflow()
    assert {
        "core",
        "sanitizers",
        "native-q",
        "release-assurance",
        "style-contract",
    } <= set(jobs(document))
    assert "pull_request" in events(document)
    assert document.get("permissions") == {"contents": "read"}


def test_native_q_has_explicit_pr_guard():
    """The licensed job carries the exact required pull_request guard."""
    native = jobs(workflow())["native-q"]
    assert native["runs-on"] == NATIVE_RUNS_ON
    assert str(native["if"]) == SELF_HOSTED_GUARD
    assert not pr_can_reach(native)


def test_native_q_uses_dedicated_runner_and_uploads_evidence():
    """The licensed job runs only on the labelled runner and records proof."""
    native = jobs(workflow())["native-q"]
    assert NATIVE_LABEL in str(native["runs-on"])
    assert "QUANT_NATIVE_Q_ENABLED" in str(native["if"])
    assert (
        "NOT a required status check for PORTABLE_RESEARCH"
        in WORKFLOW.read_text(encoding="utf-8")
    )
    evidence_steps = [
        step
        for step in steps(native)
        if "capture_runner_evidence.py" in str(step.get("run", ""))
    ]
    assert len(evidence_steps) == 1
    evidence = evidence_steps[0]
    assert "--output data/assurance/runner-evidence.json" in str(
        evidence["run"]
    )
    assert "RUNNER_LABELS" in evidence["env"]
    assert NATIVE_LABEL in str(evidence["env"]["RUNNER_LABELS"])
    assert "QUANT_EXPECTED_SHA" in evidence["env"]
    uploads = [
        step
        for step in steps(native)
        if "upload-artifact@" in str(step.get("uses", ""))
        and "runner-evidence.json" in str(step.get("with", ""))
    ]
    assert uploads


def test_portable_pytest_excludes_native_q_module():
    """Portable core tests never run the licensed kdbx integration module."""
    core = jobs(workflow())["core"]
    assert "tests/test_kdbx.py" not in commands(core)
    assurance = ASSURANCE.read_text(encoding="utf-8")
    pytest_gate = assurance[assurance.index('"pytest",') :]
    pytest_gate = pytest_gate[: pytest_gate.index("gate.skip")]
    assert "tests/test_kdbx.py" not in pytest_gate
    assert 'gate.skip(\n        "native q tests",' in assurance
    assert 'code="NOT_APPLICABLE"' in assurance
    assert NATIVE_Q_SKIP_REASON.split("; ", maxsplit=1)[0] in assurance
    assert '"separate gated tier"' in assurance


def test_native_q_qualifies_exact_protected_main_tip():
    """Qualification fetches main and checks reachability and exact SHA."""
    native = jobs(workflow())["native-q"]
    qualification = "\n".join(
        str(step.get("run", ""))
        for step in steps(native)
        if "origin/main" in str(step.get("run", ""))
    )
    assert "git fetch --no-tags origin main" in qualification
    assert "git rev-parse origin/main" in qualification
    assert "git merge-base --is-ancestor" in qualification
    assert '"$main_sha" != "$EXPECTED_SHA"' in qualification
    assert "EXPECTED_SHA" in qualification


def test_licensed_runner_requirements_are_documented():
    """The workflow records the mandatory runner and credential controls."""
    text = WORKFLOW.read_text(encoding="utf-8")
    markers = (
        "dedicated runner group",
        "ephemeral single-job runner",
        "no runner reuse",
        "restricted egress",
        "protected secrets",
        "full commit SHAs",
        "destroy the runner",
        "Q_PERSISTENCE",
        "evidence checker",
        "NOT a required status check for PORTABLE_RESEARCH",
    )
    for marker in markers:
        assert marker in text


def test_release_assurance_is_github_hosted_on_push_and_pr():
    """Offline assurance runs on GitHub-hosted runners for PR events."""
    job = jobs(workflow())["release-assurance"]
    assert job["name"] == RELEASE_CHECK
    assert job["runs-on"] == "ubuntu-latest"
    assert SELF_HOSTED not in str(job["runs-on"])
    assert (
        str(job["if"])
        == "github.event_name == 'push' || github.event_name == 'pull_request'"
    )
    assert pr_can_reach(job)


def test_release_assurance_checks_out_exact_event_commit():
    """The hosted job checks out the exact PR head or exact push SHA."""
    job = jobs(workflow())["release-assurance"]
    checkout = steps(job)[0]
    assert str(checkout["uses"]).startswith("actions/checkout@")
    assert checkout["with"]["ref"] == PR_EXACT_REF
    verification = "\n".join(
        str(step.get("run", ""))
        for step in steps(job)
        if "git rev-parse HEAD" in str(step.get("run", ""))
    )
    assert "actual_sha" in verification
    assert "$EXPECTED_SHA" in verification


def test_release_assurance_runs_only_offline_suites():
    """All offline suites, schema checks and bundle gates are present."""
    text = commands(jobs(workflow())["release-assurance"])
    assert (
        "python3 -m pytest tests/test_instrument_master.py -o addopts='' -q"
        in text
    )
    for suite in OFFLINE_SUITES:
        assert suite in text
    assert "-o addopts='' -q" in text
    assert "python3 scripts/style_contract.py" in text
    assert "python3 scripts/state_freshness.py" in text
    assert "python3 scripts/release_bundle.py verify" in text
    assert "NOT_APPLICABLE" in text


def test_ci_security_suite_is_in_hosted_release_assurance():
    """CI security invariants run in the required hosted assurance job."""
    text = commands(jobs(workflow())["release-assurance"])
    assert "tests/test_ci_security.py" in text
    offline_block = text[text.index("tests/test_release_bundle.py") :]
    assert "tests/test_ci_security.py" in offline_block
    assert "-o addopts='' -q" in offline_block


def test_no_self_hosted_job_can_run_on_pull_request():
    """No self-hosted job in the workflow is reachable from a PR event."""
    self_hosted = {
        name: job
        for name, job in jobs(workflow()).items()
        if is_self_hosted(job)
    }
    assert self_hosted
    for name, job in self_hosted.items():
        condition = str(job.get("if", ""))
        assert not pr_can_reach(job), name
        assert "!=" in condition, name
        assert "== 'pull_request'" not in condition, name


def test_all_actions_use_full_commit_sha_pins():
    """Every third-party Action is pinned to a full 40-hex commit SHA."""
    for name, job in jobs(workflow()).items():
        for step in steps(job):
            uses = step.get("uses")
            if uses is None:
                continue
            assert FULL_SHA_RE.search(str(uses)), f"{name}: {uses}"


def test_required_checks_include_release_assurance():
    """Branch protection requires four hosted checks, never native-q."""
    document = json.loads(POLICY.read_text(encoding="utf-8"))
    checks = document["github_protection_audit"]["required_status_checks"]
    assert checks == [
        "core build and tests",
        "ASan/UBSan",
        "Style contract (reference guides)",
        RELEASE_CHECK,
    ]
    assert len(checks) == 4
    assert "native-q" not in checks
    assert document["pr_review"]["required_approving_reviews"] == 0


def test_git_policy_is_canonical_json():
    """The tracked policy stays canonical: two-space indent, sorted keys."""
    text = POLICY.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
