# Contributing to QUANT

This file states the contribution workflow. `git-policy.json` and
`docs/git-workflow.md` are authoritative when this summary differs.

## Workflow

1. Start from current `main` with `git fetch --prune origin` and
   `git pull --ff-only origin main`.
2. Create one bounded topic branch for one coherent change.
3. Add tests with behavior changes, including failure paths.
4. Run `python3 scripts/assurance.py check` for the full fail-closed gate.
   Use `python3 scripts/assurance.py polish` to apply formatting first, and
   `python3 scripts/assurance.py audit --strict` for terminology only.
5. Run `python3 scripts/check_git_policy.py` and `git diff --check`.
6. Open a pull request with exact commands, exits, test counts and unresolved
   gates. Keep live execution locked.
7. Do not rewrite published history, force-push or bypass a red gate.

## Evidence freshness

Volatile identifiers (commit SHAs, CI run IDs and bundle digests) belong only
in `MANIFEST.md` of the generated release bundle; never hardcode them as
current in tracked files. Label historical values explicitly, keep
documentation and code changes in the same PR, and run the full gate,
including state freshness, before publishing. Run
`python3 scripts/state_freshness.py` for a standalone check.

## Policy sources

- Branches, merges and PRs: `docs/git-workflow.md`, `git-policy.json`.
- Engineering rules: `AGENTS.md`.
- Initial review record: `ASSESSMENT.md`.
- Correctness records: `docs/correctness-baseline.md` and
  `docs/correctness-fixes.md`.
- Style references: `STYLES.md` and `references/`.

A passing check does not authorize provider contact, paid subscriptions,
account changes or live orders.
