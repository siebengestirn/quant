#!/usr/bin/env python3
"""Read-only local validation of QUANT git policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "git-policy.json").read_text())


def git(*args: str) -> str:
    """Run a Git command and return its standard output."""
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def policy_consistency_violations() -> list[str]:
    """Compare repository state with the machine policy."""
    out: list[str] = []
    inventory = POLICY["inventory"]
    if inventory["target_local_branches"] != ["main"]:
        out.append("policy target local branches must be only main")
    if inventory["target_remote_tracking_branches"] != ["origin/main"]:
        out.append("policy target remote tracking must be only origin/main")
    review = POLICY["pr_review"]
    required = review["required_approving_reviews"]
    if required not in (0, 1):
        out.append("required approving reviews must be zero or one")
    if required == 1 and not review.get("independent_reviewer_required", True):
        out.append("independent reviewer must be required with one approval")
    if required == 0 and not review.get("self_review_checklist_required", False):
        out.append("self-review checklist must be required without GitHub approvals")
    if not review.get("ci_evidence_required", False):
        out.append("CI evidence must be required for every PR")
    workflow = (ROOT / "docs" / "git-workflow.md").read_text()
    expected = (
        "requires one independent approving review"
        if required == 1
        else "uses self-review plus independent CI with zero required GitHub approvals"
    )
    if expected not in workflow:
        out.append("git-workflow.md does not state the required review policy")
    if not POLICY["pr_review"].get("ci_evidence_per_pr_head", False):
        out.append("CI evidence must be recorded per exact PR head")
    if POLICY["pr_review"].get("ci_evidence_reuse_final_main_run", True):
        out.append("final-main run must not be reused as per-PR evidence")
    if POLICY["automation"]["must_not_delete_remote_branches"]:
        out.append("branch cleanup policy conflicts with single-branch target")
    cleanup = POLICY.get("branch_cleanup", {})
    for key in (
        "requires_merged_pr",
        "requires_no_open_pr_dependency",
        "requires_commits_reachable_from_main",
        "requires_owner_authorization",
    ):
        if not cleanup.get(key, False):
            out.append(f"branch cleanup guard missing: {key}")
    return out


def violations(allow_dirty: bool) -> list[str]:
    """Collect policy violations from the repository."""
    out: list[str] = policy_consistency_violations()
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    prefixes = tuple(POLICY["branch"]["allowed_topic_prefixes"])
    active = set(POLICY["branch"]["active_topic_branches"])
    if branch != "main" and not (
        branch.startswith(prefixes) or branch in active
    ):
        out.append(f"branch name is not allowed: {branch}")

    if not allow_dirty and git("status", "--porcelain"):
        out.append("working tree is not clean")

    forbidden = set(POLICY["generated_paths"]["forbidden_components"])
    forbidden.update(POLICY["generated_paths"]["forbidden_component_prefixes"])
    suffixes = tuple(POLICY["generated_paths"]["forbidden_suffixes"])
    tracked = git("ls-files").splitlines()
    for path in tracked:
        components = set(path.split("/"))
        if components & forbidden or path.endswith(suffixes):
            out.append(f"generated file is tracked: {path}")

    stashes = git("stash", "list").splitlines()
    if len(stashes) != POLICY["inventory"]["target_stash_count"]:
        out.append("stash count differs from policy")

    worktrees = git("worktree", "list").splitlines()
    if len(worktrees) != POLICY["inventory"]["target_worktree_count"]:
        out.append("worktree count differs from policy")

    remotes = git("remote").splitlines()
    expected = POLICY["inventory"]["target_remote_names"]
    if sorted(remotes) != sorted(expected):
        out.append(f"unexpected remotes: {remotes}")

    base = (
        POLICY["history"]["branch"]["base"]
        if "branch" in POLICY["history"]
        else "main"
    )
    boundary = POLICY["commit_subject"]["grandfathered_through_commit"]
    commits = git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    boundary_index = next(
        (i for i, sha in enumerate(commits) if sha.startswith(boundary)), None
    )
    if boundary_index is not None:
        commits = commits[boundary_index + 1 :]
    pattern = re.compile(POLICY["commit_subject"]["pattern"])
    forbidden_subjects = set(
        POLICY["commit_subject"]["forbidden_exact_subjects"]
    )
    for sha in commits:
        subject = git("show", "-s", "--format=%s", sha)
        if not pattern.match(subject) or subject in forbidden_subjects:
            out.append(f"commit subject violates policy: {sha} {subject}")

    if branch != "main":
        pr = subprocess.run(
            ["gh", "pr", "view", "--json", "state"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if pr.returncode != 0 or '"OPEN"' not in pr.stdout:
            out.append(f"no open pull request for branch: {branch}")
    return out


def main() -> int:
    """Parse arguments and run the research command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty", action="store_true", help="skip clean-tree requirement"
    )
    args = parser.parse_args()
    found = violations(args.allow_dirty)
    if found:
        for item in found:
            print(f"VIOLATION: {item}")
        return 1
    print("QUANT git policy: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
