# Governance records

This directory holds authored, curated records about repository policy and
evidence. It does not hold generated software output: builds, caches and data
snapshots stay out of Git, as listed in `git-policy.json`. The state-freshness
gate protects these records from stale identifiers and broken references.

## Index

```text
governance/
  README.md                      this index
  inventory.json                 authoritative authored-file allowlist
  architecture-current.md        current architecture record
  baseline-inventory.md          baseline repository inventory
  branch-protection-capture.json captured protection settings
  instrument-master-snapshots.json public-master snapshot provenance
  requirements-traceability.csv  requirement-to-evidence register
  research-report-registry.csv   research report register
  security-findings.csv          security finding register
  technical-debt-register.csv    technical debt register
  history/
    branch-protection-2026-11-26.json
    branch-protection-2026-11-27.json
```

`governance/inventory.json` is the authoritative allowlist of every tracked
authored `.md`, `.json` and `.csv` file. `scripts/style_contract.py` uses it
for the `docs-inventory` and `authored-inventory` checks. `git-policy.json`
defines which paths count as generated and must never be tracked.
