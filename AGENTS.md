# QUANT agent rules

## Read first

- `README.md` — current scope and document map.
- `ASSESSMENT.md` — verified initial state and amendment record.
- `docs/git-workflow.md` and `git-policy.json` — branch and merge policy.
- `docs/correctness-fixes-2026-11-25.md` — correctness findings and task states.
- `STYLES.md` and `references/` — binding style references.

## Behaviour rules

- Preserve the working tree and published history. Do not reset, regenerate or
  rewrite published commits.
- Keep live execution locked. Do not place, modify or cancel orders, transfer
  funds or change broker settings.
- Keep credentials in Keychain. Never print, commit or transmit secrets.
- Keep generated data, builds and virtual environments out of Git.
- Reproduce defects before changing code. Add tests with every critical fix.
- Run the full gate suite before publishing a change. Report exact commands,
  exit statuses, test counts and unresolved gates.
- Use `TESTED` for implementing-agent evidence. Only an independent reviewer
  may mark `INDEPENDENTLY_VERIFIED` or `ACCEPTED`.
- Do not claim alpha, production readiness, HFT capability or zero loss without
  separate measured evidence and owner approval.
