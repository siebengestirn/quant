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
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def violations(allow_dirty: bool) -> list[str]:
    out: list[str] = []
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

    base = POLICY["history"]["branch"]["base"] if "branch" in POLICY["history"] else "main"
    boundary = POLICY["commit_subject"]["grandfathered_through_commit"]
    commits = git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    boundary_index = next(
        (i for i, sha in enumerate(commits) if sha.startswith(boundary)), None
    )
    if boundary_index is not None:
        commits = commits[boundary_index + 1 :]
    pattern = re.compile(POLICY["commit_subject"]["pattern"])
    forbidden_subjects = set(POLICY["commit_subject"]["forbidden_exact_subjects"])
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
