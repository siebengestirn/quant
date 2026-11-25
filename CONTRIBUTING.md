# Contributing to QUANT

This file states the contribution workflow. `git-policy.json` and
`docs/git-workflow.md` are authoritative when this summary differs.

## Workflow

1. Start from current `main` with `git fetch --prune origin` and
   `git pull --ff-only origin main`.
2. Create one bounded topic branch for one coherent change.
3. Add tests with behavior changes, including failure paths.
4. Run the full local gates: CMake build, CTest, C++ tests, Python tests,
   sanitizers, prose sweep and secret scan.
5. Run `python3 scripts/check_git_policy.py` and `git diff --check`.
6. Open a pull request with exact commands, exits, test counts and unresolved
   gates. Keep live execution locked.
7. Do not rewrite published history, force-push or bypass a red gate.

## Policy sources

- Branches, merges and PRs: `docs/git-workflow.md`, `git-policy.json`.
- Engineering rules: `AGENTS.md`.
- Initial review record: `ASSESSMENT.md`.
- Correctness records: `docs/correctness-baseline-2026-11-25.md` and
  `docs/correctness-fixes-2026-11-25.md`.
- Style references: `STYLES.md` and `references/`.

A passing check does not authorize provider contact, paid subscriptions,
account changes or live orders.
