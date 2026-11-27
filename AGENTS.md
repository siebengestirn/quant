# QUANT agent rules

## Read first

- `README.md` — current scope and document map.
- `ASSESSMENT.md` — verified initial state and amendment record.
- `docs/git-workflow.md` and `git-policy.json` — branch and merge policy.
- `STYLES.md` and `references/` — binding style references.

## Behaviour rules

- Preserve the working tree and published history. Do not reset, regenerate or
  rewrite published commits.
- Keep credentials in Keychain. Never print, commit or transmit secrets.
- Keep generated data, builds and virtual environments out of Git.
- Reproduce defects before changing code. Add tests with critical fix.
- Run the full gate suite before publishing a change. Report exact commands,
  exit statuses, test counts and unresolved gates.
