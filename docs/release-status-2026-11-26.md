# QUANT release status — 26 November 2026 (final)

Commit: `8a45da3886271235cf989111fbd27bda1065fe24` on `main`.
Merged pull requests: **#1–#44 (44)**, verified with the authenticated GitHub
API (`gh api 'repos/siebengestirn/quant/pulls?state=all&per_page=100'`).
The public web PR index showed "29 closed" because it was not fully paginated;
it is not the authoritative inventory. Open PRs: 0. Branches: `main` only.

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
| INDEPENDENTLY ACCEPTED | Not recorded for historical PRs; now enforced going forward |
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
| final `main` | run `45869954361`, success on `8a45da3` | post-merge verification on the exact final commit |

It is therefore **not** true that every PR head was green: PR #34 was merged
with a red style check and was repaired by PR #35. The final `main` is green.

## Governance

Branch protection on `main` was updated to match `git-policy.json`:

- strict required status checks: `core build and tests`, `ASan/UBSan`,
  `Style contract (reference guides)`;
- one independent approving review, stale-review dismissal and
  last-push approval;
- conversation resolution required; force pushes and branch deletion disabled;
- administrators subject to the same rules; merge commits preserved
  (linear history not required).

The exported setting is `docs/branch-protection-2026-11-26.json`. Historical
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
