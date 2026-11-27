# QUANT release status — 27 November 2026 (record through PR #61)

The exact current main SHA, hosted CI runs and bundle digest are recorded in
`MANIFEST.md` of the current final release bundle (external artifact), not
as immutable identifiers in this document. Values labelled "at PR #58" or
"at PR #61" are HISTORICAL release records; they do not claim the current
head after reorganization. PR #61 merged the R8.3 + W1 A/B/C work; R8.4
(native-q conclusion mapping, remote provenance and W1 snapshot
reproducibility) is IN PROGRESS. The public web PR index showed "29 closed"
because it was not fully paginated; it is not the authoritative inventory.
PR #44 was an earlier technical remediation and PR #45 enforced branch
protection. PR #58 was the closeout at that time; PR #56, PR #57, PR #58
and PR #61 are the latest merged corrections.

SUPERSEDED — historical policy before 2026-11-27: open PRs after PR #46: 0;
branches: `main` only. This is a historical inventory snapshot, not the live
configuration.

This is the current record; superseded sections are labelled. The zero-approval
single-developer model in the "ACTIVE POLICY — Integration record" section
below governs. Earlier one-approval governance material is marked SUPERSEDED —
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
| final `main` (at PR #58) | run `45969624206`, all three required jobs pass | HISTORICAL post-merge success at PR #58 on `1a91da2…`; current head in bundle `MANIFEST.md` |
| #61 | run `46007348694`, three jobs pass and native-q skipped | HISTORICAL at PR #61: R8.3 + W1 A/B/C merge `201f5ba…`; native-q GATED_NOT_RUN, never reported as passed |

It is therefore **not** true that every PR head was green: PR #34 was merged
with a red style check and was repaired by PR #35. Hosted CI at PR #61
succeeded with the native-q tier skipped; the current head is in
`MANIFEST.md` of the current final release bundle (external artifact).

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

The exported setting is `governance/history/branch-protection-2026-11-26.json`.
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

## Gate disposition after remediation PRs #47–#53, #56–#58 and #61 (HISTORICAL)

| Gate | Status |
|---|---|
| 0 policy/review hygiene | IMPLEMENTED, ENFORCED by branch protection from this release; historical approvals absent |
| 1 PIT data/calendar | INTEGRATION-TESTED for ticker-keyed status and W1-A/B/C offline public-master crosswalk (9,926 VERIFIED / 174 UNMAPPED); effective-dated identity (W1-D) PENDING |
| 2 KDB-X generations | IMPLEMENTED, FAULT-INJECTED; native q tested locally and still GATED in hosted CI (skipped/GATED_NOT_RUN at PR #61) |
| 3 accounting replay | INTEGRATION-TESTED with conflict detection |
| 4 publication transactions | IMMUTABLE transaction IDs; fault-injected at audit/rename/commit boundaries |
| 5 event runtime | UNIT-TESTED in-memory prototype; not durable or concurrent |
| 6 research methodology | NESTED selection/evaluation; outer-test IC only; holdout SEALED_NOT_EVALUATED |
| 7 paper OMS | Exact fill arithmetic, authoritative risk capability, strict replay; CLI lifecycle not operational |
| 8 asset contracts | Model only; NSE cash-equity daily profile only |
| 9 live-route controls | DISABLED unconditionally |

## Remaining limitations (reconciled)

Research INCONCLUSIVE/negative; White Reality Check and Hansen SPA unavailable;
cost model GROSS_ONLY; final holdout SEALED_NOT_EVALUATED. Provider streaming,
contract-note reconciliation, corporate-action ingestion and operational paper
trading NOT IMPLEMENTED. W1-A/B/C map pinned public masters offline, but W1-D
live consistency and effective dating are PENDING; no full W1 exit is claimed.
Native q hosted required check GATED/NOT REQUIRED (skipped/GATED_NOT_RUN at
PR #61); power-loss durability NOT PROVEN; intraday/HFT UNSUPPORTED;
multi-asset venue conformance NOT ESTABLISHED; independent reviewer acceptance
for recorded releases NOT RECORDED; live execution DISABLED; trading
decision NO TRADE.

## Governance evidence refresh (27 November 2026) — SUPERSEDED

SUPERSEDED — historical policy before 2026-11-27: this section and its export
record the superseded one-approval configuration, not the live configuration.
The branch-protection endpoint was exported again after PR #46. The response
is `governance/history/branch-protection-2026-11-27.json`; the SHA-256 of
the raw API response bytes is
`59741f72e3971a2e4e38608ddea47fa87686e46f22282214eeebe0b4ddece989`.
It requires three strict checks, one independent approving review,
conversation resolution, last-push approval and administrator enforcement,
and prohibits force pushes and branch deletion. It was an authenticated
export of the endpoint as configured at that time, not the live configuration.
The JSON export is untouched evidence. `git-policy.json` declares policy;
only an authenticated API export proves live GitHub configuration.

## ACTIVE POLICY — Integration record — 27 November 2026 (updated through PR #61) <!-- https://github.com/siebengestirn/quant/pull/61 -->

This section is the ACTIVE POLICY. It supersedes the one-approval governance
material above; zero PRs are open and `main` is the sole integration branch.

All seven remediation PRs #47–#53 were merged sequentially with merge commits
after fresh exact-head CI on each synced head; PRs #56–#58 and #61 (R8.3/W1)
were later merged under the same policy. `main` is the sole integration
branch. Branch protection was corrected to the single-developer policy used
in the sibling repositories: zero required GitHub approvals, explicit
self-review checklist, exact-head CI evidence, three required checks, PR,
conversation resolution, no force pushes and no branch deletion. No admin
bypass or history rewrite was used.

This record supersedes the earlier seven-open-PR snapshot. Operating status
remains: research INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED;
provider streaming, contract-note reconciliation, corporate actions and
operational paper trading NOT IMPLEMENTED; W1-A/B/C offline public-master
evidence only, with W1-D effective dating and live consistency PENDING; native
q hosted tier GATED (skipped/GATED_NOT_RUN at PR #61); power-loss durability
NOT PROVEN; intraday/HFT UNSUPPORTED; multi-asset venue conformance NOT
ESTABLISHED; live execution DISABLED; decision NO TRADE.

### Active release-bundle policy (R8.1 corrective release)

Release history only: PR #56 is MERGED and its material is HISTORICAL. Its
head `7c4895c9299480d33915c50172085c0527c17f2b` passed hosted CI run
`45962275474`; post-merge CI run `45962576571` passed on
`99bc70c015a3744a27ffbaca76b32f3025cb7c37`, which was `main` at PR #56
and is not final. The historical bundle digests `d94a6254…` (97 entries)
and `b5ff5ecc…` (98 entries) are HISTORICAL / SUPERSEDED, not current.

PR #57 (R8.1 exact integer contract) is MERGED. Its head
`21a62e3eca480440981a3c1fa0681f9a12493171` passed hosted CI run
`45968572026`; post-merge CI run `45968891063` passed on the R8.1 merge
commit `460b8f2d…`. Portable verification recorded 14090 C++ checks,
14090 sanitizer checks, zero failures, 50 portable pytest passes and all
portable assurance gates PASS. PR #57 is not the final closeout.

PR #58 is MERGED and was the closeout at that time. Its head
`fb9170ca5e9003b0dbea7c1191e874ff4e7fa8b2` passed hosted CI run
`45969369813`; final post-merge CI run `45969624206` passed on `main`
`1a91da2fb08995c9f6ee20e373d004ad28e58a72`. The then-final bundle
`MANIFEST.md` recorded 98 verified entries and digest
`5eb0e1a87b3925117f515808591c9049ee0a11ffec246b46f61f9f79c54462c7`.
These are release-history values at PR #58, not immutable current
identifiers: the exact current main SHA, CI runs and bundle digest are in
`MANIFEST.md` of the current final release bundle (external artifact).

### Active release-bundle policy (R8.3 / PR #61 — HISTORICAL)

PR #61 merged the R8.3 + W1 A/B/C work at
`201f5ba602f68fc1370579cefa50b8b599af05d5`. HISTORICAL at PR #61: the
then-final bundle `MANIFEST.md` recorded 108 entries and digest
`c5be8f1ff2ae2faeea161dfa815511ee1f8775cb347de98f3602dd500993bf8c`,
and hosted CI run `46007348694` succeeded with three passing jobs and one
skipped native-q job (GATED_NOT_RUN). Local verification recorded 14114 C++
checks / 0 failures, 14114 sanitizer checks / 0 failures, 51 portable
pytest passes, 11 W1 pytest passes, style 8/8 PASS and 0 binding audit
findings. These are release-history values at PR #61; the authoritative
current main SHA, CI runs and bundle digest are in `MANIFEST.md` of the
current final release bundle (external artifact).

R8.3 closed the legacy volume-validity migration, leading-zero count parity,
bundle manifest-total verification, a provenance guard that fails closed on
a missing verification report, and a portable state-freshness self-test.
W1 A/B/C used pinned public Upstox and FYERS masters with 9,926 VERIFIED
and 174 UNMAPPED crosswalk identities; W1-D effective dating and live
consistency remain PENDING. R8.4 work — native-q conclusion mapping, remote
provenance and W1 snapshot reproducibility — is IN PROGRESS and has no exit
claim. Independent acceptance remains NOT RECORDED and the independent
security review remains OPEN.

Branch protection was captured from the authenticated endpoint
`https://api.github.com/repos/siebengestirn/quant/branches/main/protection`
at `2026-11-27T13:18:16Z` with HTTP 200, API version `2022-11-28` and
response SHA-256
`824414cccfa205a54326357edb182c4fb40e320b6755b323189eb1e1c5b7cc11`.
The capture recorded `main` as `464ea1bba57cdc91b3d93eb77804fd882e245022`
at capture time; later heads are in the bundle `MANIFEST.md`. It
records `required_approvals` 0 and three required checks (`core build and
tests`, `ASan/UBSan`, `Style contract (reference guides)`). The tracked
record is `governance/branch-protection-capture.json` in the repository;
the bundle copy is `evidence/branch-protection.json` in the external
final bundle. `git-policy.json` declares policy; only an authenticated
API export proves the live GitHub configuration. The historical JSON
exports do not establish the current zero-approval setting.
