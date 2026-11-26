# QUANT Git workflow

The canonical repository is `siebengestirn/quant`. ZIP exports are snapshots,
never sources of Git history.

## Model

`main` is the latest verified, integrated project. A feature branch carries one
bounded engineering objective. A pull request records the evidence and decision
to integrate that objective.

The repository uses protected `main`, short-lived topic branches, mandatory PRs
and merge commits. There is no permanent `develop` or release branch. The
machine-readable policy is `git-policy.json`; this document must agree with it.

## Rules

| Rule | Required behavior |
|---|---|
| Direct commits to `main` | Prohibited |
| Active topic branches | Temporary, one coherent deliverable each |
| Merge method | Merge commit only |
| Squash / rebase merging | Prohibited |
| Published history rewriting | Prohibited |
| Force push | Prohibited |
| CI | Required on the exact PR head before merge |
| Strict base freshness | Topic branch must be current with `main` when CI requires it |
| Merge review | Self-review checklist, CI evidence and independent review for critical work |
| Branch cleanup | After the merge is verified on `main` |
| Final local branches | `main` only |
| Final remote tracking | `origin/main` only |
| Stashes / worktrees | Zero stashes; one worktree unless justified |

Branch prefixes: `feat/`, `fix/`, `docs/`, `test/`, `ci/`, `chore/` and
`correctness/`. The active `correctness/gate0` branch is the one bounded review
epic documented in PR #1.

Commit subjects use conventional prefixes. Published correctness commits through
`f321d0c` predate this policy and are grandfathered; no published commit is
rewritten.

## Start and finish routine

```bash
git fetch --prune origin
git switch main
git pull --ff-only origin main
git status --short --branch
python3 scripts/check_git_policy.py
```

At finish:

```bash
git diff --check
git diff --stat origin/main...HEAD
git log --graph --decorate --oneline origin/main..HEAD
python3 scripts/check_git_policy.py
ctest --test-dir build --output-on-failure
```

Run the full project gate suite before requesting review. A passing gate does
not authorize provider contact, live execution or a merge.

## GitHub protection

Audit with the GitHub CLI. Required settings are in `git-policy.json`. Branch
protection must restrict direct pushes, require a PR and status checks, reject
force pushes and deletion, allow only merge commits, and require conversation
resolution. `git-policy.json` requires one independent approving review for
every PR. A
passing CI gate and an author-written checklist are not that review; for
financial, recovery, data-contract and execution-critical changes an
independent reviewer must record acceptance against the exact PR head.

## Never

Never force-push, rewrite published commits, merge a red PR, delete a branch
solely because it looks old, leave an outdated PR description, treat a ZIP as
Git history, or let local `main` drift from `origin/main`.
