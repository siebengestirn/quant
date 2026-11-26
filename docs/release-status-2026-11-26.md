# QUANT release status — 26 November 2026

Commit: `2e50a35017c7aa69e91437ae114c560e158ff184` on `main`.
Hosted CI: run `45848570547`, success on that exact commit.
Merged PRs: #2–#36. Open PRs: 0. Branches: `main` only.

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
| INDEPENDENTLY ACCEPTED | Not recorded |
| OPERATIONALLY CERTIFIED | Not performed |

## Gates

| Gate | Status |
|---|---|
| 0 policy/review hygiene | IMPLEMENTED, UNIT-TESTED; independent approval evidence not recorded |
| 1 PIT data/calendar | INTEGRATION-TESTED for ticker-keyed status; permanent-ID join open |
| 2 KDB-X generations | IMPLEMENTED, FAULT-INJECTED locally; native hosted tier gated |
| 3 accounting replay | INTEGRATION-TESTED with conflict detection |
| 4 publication transactions | FAULT-INJECTED for audit/rename/commit boundaries |
| 5 event runtime | UNIT-TESTED in-memory prototype; not durable or concurrent |
| 6 research methodology | OOS fold metrics reported; holdout SEALED_NOT_EVALUATED |
| 7 paper OMS | Partial library; CLI lifecycle not operational |
| 8 asset contracts | Model only; NSE cash-equity daily profile only |
| 9 live-route controls | DISABLED unconditionally |

## Operating decision

Research INCONCLUSIVE/negative; provider streaming, contract-note
reconciliation, corporate-action ingestion and operational paper trading NOT
IMPLEMENTED; intraday/HFT UNSUPPORTED; multi-asset conformance NOT
ESTABLISHED; power-loss durability NOT PROVEN; live execution DISABLED;
trading decision NO TRADE.
