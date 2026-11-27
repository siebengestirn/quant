# QUANT release status — 26 November 2026 (final)

Final commit, hosted CI run and merged-PR count are generated from the exact
final `main` head and the authenticated GitHub API in `MANIFEST.md` of the
final evidence bundle; volatile identifiers are not copied here.
The public web PR index showed "29 closed" because it was not fully paginated;
it is not the authoritative inventory. PR #44 was the last technical
remediation; PR #45 enforced branch protection; PR #46 is this final evidence
reconciliation.

SUPERSEDED — historical policy before 2026-11-27: open PRs after PR #46: 0;
branches: `main` only. This is a historical inventory snapshot, not the live
configuration.

This is the current record; superseded sections are labelled. The zero-approval
single-developer model in the "Final integration record" section below is the
ACTIVE POLICY. Earlier one-approval governance material is marked SUPERSEDED —
historical policy before 2026-11-27 and is retained only as evidence.

## Status vocabulary

| Status | Meaning |
|---|---|
| IMPLEMENTED | Code exists |
| UNIT-TESTED | Isolated behaviour passes |
| INTEGRATION-TESTED | Real component boundaries exercised |
| FAULT-INJECTED | Named failure points tested |
| CRASH-TESTED | Process restart recovery demonstrated |
| POWER-LOSS-TESTED | Not performed |
| HOSTED-REQUIRED-CHECK | Core, ASan/UBSan and style contract pass |
| POST-MERGE-VERIFIED | CI success on final main SHA |
| INDEPENDENTLY ACCEPTED | SUPERSEDED — historical policy before 2026-11-27 vocabulary; tracked now as independent external acceptance: PENDING / NOT RECORDED |
| OPERATIONALLY CERTIFIED | Not performed |

## Hosted CI evidence — exact-head and post-merge

| PR | Head CI | Notes |
|---|---|---|
| #30 | run `45859456765`, all three required jobs pass | first attempt `45858926359` failed on documentation fence; fixed in a follow-up commit before merge |
| #31 | run `45842865299`, all required jobs pass | — |
| #32 | run `45843615951`, all required jobs pass | — |
| #33 | run `45844459815`, all required jobs pass | — |
| #34 | run `45845253524`, **style job FAILED** on head `67b3972` | merged before the style failure was repaired; this is recorded as a failure, not retconned as green |
| #35 | run `45845776788`, all required jobs pass | style follow-up that repaired #34's finding |
| #36 | run `45848108764`, all required jobs pass | — |
| #37 | run `45849000574`, all required jobs pass | — |
| #38 | run `45859456765`, all required jobs pass | — |
| #39 | run `45860489211`, all required jobs pass | — |
| #40 | run `45861796960`, all required jobs pass | — |
| #41 | run `45862864867`, all required jobs pass | — |
| #42 | run `45863382305`, all required jobs pass | — |
| #43 | run `45867909983`, all required jobs pass | — |
| #44 | run `45869637951`, all required jobs pass | — |
| final `main` | see bundle `MANIFEST.md` | post-merge success on the exact final commit |

It is therefore **not** true that every PR head was green: PR #34 was merged
with a red style check and was repaired by PR #35. The final `main` is green.

## Governance — SUPERSEDED historical policy before 2026-11-27

Historical record of the superseded one-approval configuration; the ACTIVE
POLICY is the zero-approval model recorded at the end of this document.

Branch protection on `main` was updated to match `git-policy.json`:

- strict required status checks: `core build and tests`, `ASan/UBSan`,
  `Style contract (reference guides)`;
- one independent approving review, stale-review dismissal and
  last-push approval;
- conversation resolution required; force pushes and branch deletion disabled;
- administrators subject to the same rules; merge commits preserved
  (linear history not required).

The exported setting is `docs/branch-protection-2026-11-26.json`.
SUPERSEDED — historical policy before 2026-11-27: this export records the old
one-approval configuration, not the live configuration. The JSON exports are
untouched evidence; `git-policy.json` declares policy, while only an
authenticated API export proves live GitHub configuration.

**SUPERSEDED — historical policy before 2026-11-27. Bootstrap exception
(narrow, closed by scope, time-limited).** PR #45
installs the approval requirement and PR #46 reconciles final evidence.
Only one maintainer account exists,
so GitHub cannot supply an independent approving review for those PRs.
`git-policy.json` records a single-maintainer exception expiring
2026-12-31 that authorizes administrative merges of PR #45 and #46 only,
after all three required checks passed on each exact head. It applies to no
financial,
recovery, data-contract or execution code, and no other PR. After that date no
exception exists.

Historical
PRs, including #34 and #37, were merged before this enforcement and show no
approving review; that gap is recorded, not hidden. CI success is not
independent acceptance.

## Gate disposition after the eight remediation PRs

| Gate | Status |
|---|---|
| 0 policy/review hygiene | IMPLEMENTED, ENFORCED by branch protection from this release; historical approvals absent |
| 1 PIT data/calendar | INTEGRATION-TESTED for ticker-keyed status; permanent instrument identity remains open |
| 2 KDB-X generations | IMPLEMENTED, FAULT-INJECTED; native q tested locally and still GATED in hosted CI |
| 3 accounting replay | INTEGRATION-TESTED with conflict detection |
| 4 publication transactions | IMMUTABLE transaction IDs; fault-injected at audit/rename/commit boundaries |
| 5 event runtime | UNIT-TESTED in-memory prototype; not durable or concurrent |
| 6 research methodology | NESTED selection/evaluation; outer-test IC only; holdout SEALED_NOT_EVALUATED |
| 7 paper OMS | Exact fill arithmetic, authoritative risk capability, strict replay; CLI lifecycle not operational |
| 8 asset contracts | Model only; NSE cash-equity daily profile only |
| 9 live-route controls | DISABLED unconditionally |

## Remaining limitations (unchanged)

Research INCONCLUSIVE/negative; White Reality Check and Hansen SPA unavailable;
cost model GROSS_ONLY; final holdout SEALED_NOT_EVALUATED. Provider streaming,
contract-note reconciliation, corporate-action ingestion and operational paper
trading NOT IMPLEMENTED. Native q hosted required check GATED/NOT REQUIRED;
power-loss durability NOT PROVEN; intraday/HFT UNSUPPORTED; multi-asset venue
conformance NOT ESTABLISHED; independent reviewer acceptance for historical
releases NOT RECORDED; live execution DISABLED; trading decision NO TRADE.

## Governance evidence refresh (27 November 2026) — SUPERSEDED

SUPERSEDED — historical policy before 2026-11-27: this section and its export
record the superseded one-approval configuration, not the live configuration.
The branch-protection endpoint was exported again after PR #46. The response
is `docs/branch-protection-2026-11-27.json`; the SHA-256 of the raw API
response bytes is
`59741f72e3971a2e4e38608ddea47fa87686e46f22282214eeebe0b4ddece989`.
It requires three strict checks, one independent approving review,
conversation resolution, last-push approval and administrator enforcement,
and prohibits force pushes and branch deletion. It was an authenticated
export of the endpoint as configured at that time, not the live configuration.
The JSON export is untouched evidence. `git-policy.json` declares policy;
only an authenticated API export proves live GitHub configuration.

## ACTIVE POLICY — Final integration record — 27 November 2026

This section is the ACTIVE POLICY. It supersedes the one-approval governance
material above; zero PRs are open and `main` is the sole integration branch.

All seven remediation PRs #47–#53 were merged sequentially with merge commits
after fresh exact-head CI on each synced head. `main` is the sole integration
branch. Branch protection was corrected to the single-developer policy already
used in the sibling repositories: zero required GitHub approvals, explicit
self-review checklist, exact-head CI evidence, three required checks, PR,
conversation resolution, no force pushes and no branch deletion. No admin
bypass or history rewrite was used.

This record supersedes the earlier seven-open-PR snapshot. Operating status is
unchanged: research INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED;
provider streaming, contract-note reconciliation, corporate actions and
operational paper trading NOT IMPLEMENTED; native q hosted tier GATED;
power-loss durability NOT PROVEN; intraday/HFT UNSUPPORTED; multi-asset venue
conformance NOT ESTABLISHED; live execution DISABLED; decision NO TRADE.

### Active release-bundle policy (R8.1 corrective release)

PR #56 is MERGED. Its head `7c4895c9299480d33915c50172085c0527c17f2b`
passed hosted CI run `45962275474`; post-merge CI run `45962576571` passed
on the final `main` SHA `99bc70c015a3744a27ffbaca76b32f3025cb7c37`. The
previous final bundle is `/Users/tourist/Desktop/quant-final-20261127.zip`,
SHA-256
`d94a625425bf3ed2090749f8eafa10d819e70d72dc3b58f47511d788afcf923e`,
with 97 manifest entries verified. That bundle is HISTORICAL: it does not
cover the R8.1 corrective work. The R8.1 release regenerates the final
bundle, and the new `MANIFEST.md` records the superseding digest, exact-head
CI and final main SHA; do not treat the previous digest as current.

Branch protection was captured from the authenticated endpoint
`https://api.github.com/repos/siebengestirn/quant/branches/main/protection`
at `2026-11-27T08:44:38Z` with HTTP 200, API version `2022-11-28` and response
SHA-256
`824414cccfa205a54326357edb182c4fb40e320b6755b323189eb1e1c5b7cc11`. The
capture records main SHA `99bc70c015a3744a27ffbaca76b32f3025cb7c37`,
`required_approvals` 0 and three required checks (`core build and tests`,
`ASan/UBSan`, `Style contract (reference guides)`). The tracked record is the
root file `branch-protection-capture.json`; the bundle copy is
`evidence/branch-protection.json` in the regenerated final bundle.
`git-policy.json` declares policy; only an authenticated API export proves the
live GitHub configuration. The historical JSON exports do not establish the
current zero-approval setting.
