# Documentation index

This directory is the curated canonical subject-documentation set for QUANT.
Membership is defined by the exact allowlist in `governance/inventory.json` and
enforced by `scripts/style_contract.py` checks `docs-inventory` and
`authored-inventory`. Additions require an inventory entry with an owner and a
purpose. Generated files and external release bundles are never documentation.

| Document | Authority |
|---|---|
| `master-acceptance-register.md` | Current record: requirements, workstreams, dependencies, exit evidence and release-blocking conditions, with clearly labelled historical sections |
| `release-status.md` | Current record: final integrated state, verification results and operating limits, with clearly labelled superseded historical sections |
| `dataset-contract.md` | Snapshot manifest, SHA-256 identity, CSV schema versions, provider timestamp policy |
| `financial-integrity.md` | Integer money, accounting postings, costs and replay invariants |
| `risk-authorization-model.md` | In-process authorization capability and its boundary |
| `execution-compliance.md` | Zerodha lock, data-only mode and execution environment controls |
| `git-workflow.md` | Branch model, merge policy, CI evidence and single-developer review rule |
| `git-state.md` | HISTORICAL: adoption-era repository inventory snapshot |
| `correctness-baseline.md` | HISTORICAL: initial correctness requirements and baseline findings |
| `correctness-fixes.md` | Remediation history for the baseline findings |

The table above is an enumeration of current members, not a numeric quota.

## Reading order

Start with `master-acceptance-register.md` for the definition of complete, then
`release-status.md` for the current state; both mark superseded material
explicitly. Consult the contract documents for implementation rules and the
correctness records for historical rationale.

Statuses are distinct: implemented, unit-tested, integration-tested,
fault-injected, crash-tested, independently qualified and independently
accepted describe different evidence. Green CI is one gate, not acceptance.
