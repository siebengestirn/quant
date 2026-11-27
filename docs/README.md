# Documentation index

This directory is the canonical documentation set for QUANT. It holds ten
Markdown documents plus this index. New material belongs inside one of those
documents; extra Markdown files are not admitted. Historical snapshots remain
in Git history and in release bundles.

The inventory limit is enforced by `scripts/style_contract.py` through the
`docs-inventory` check. The check requires `docs/README.md` and at most ten
tracked `docs/*.md` files.

| Document | Authority |
|---|---|
| `master-acceptance-register.md` | Requirements, workstreams, dependencies, exit evidence and release-blocking conditions |
| `release-status.md` | Final integrated state, verification results and operating limits |
| `dataset-contract.md` | Snapshot manifest, SHA-256 identity, CSV schema versions, provider timestamp policy |
| `financial-integrity.md` | Integer money, accounting postings, costs and replay invariants |
| `risk-authorization-model.md` | In-process authorization capability and its boundary |
| `execution-compliance.md` | Zerodha lock, data-only mode and execution environment controls |
| `git-workflow.md` | Branch model, merge policy, CI evidence and single-developer review rule |
| `git-state.md` | Repository inventory and branch hygiene record |
| `correctness-baseline.md` | Initial correctness requirements and baseline findings |
| `correctness-fixes.md` | Remediation history for the baseline findings |

## Reading order

Start with `master-acceptance-register.md` for the definition of complete, then
`release-status.md` for the current state. Consult the contract documents for
implementation rules and the correctness records for historical rationale.

Statuses are distinct: implemented, unit-tested, integration-tested,
fault-injected, crash-tested, independently qualified and independently
accepted describe different evidence. Green CI is one gate, not acceptance.
