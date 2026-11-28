# QUANT release status — baseline PR #67 / source PR #86

HISTORICAL records are retained below with explicit HISTORICAL labels.

## CURRENT RELEASE RECORD (authoritative)

Canonical machine-readable state: `governance/release-state.json`, checked
by `scripts/check_release_state.py`. The authoritative product release
baseline is R8.4.3 / PR #67, COMPLETE; merged PRs 47-67. The latest source
revision is PR #86 at the captured `source_commit` in that state; source
changes after the baseline do not redefine the released product. Scope
PORTABLE_RESEARCH; 4 required checks with zero GitHub approvals; native q
GATED_NOT_RUN, native q runner execution NOT_OBSERVED and runner attestation
mechanism CHECKED. Trading DISABLED / NO TRADE; independent acceptance
PENDING. W1 is PARTIAL with 71 offline tests and W1-D PENDING.
Current source record — PR #86; W1 71 tests; 4 required checks;
PORTABLE_RESEARCH; GATED_NOT_RUN; NOT_OBSERVED; CHECKED; R8.4.3 COMPLETE.
The exact current main SHA, CI runs and bundle digest are in `MANIFEST.md`
of the current final
release bundle (external artifact), with captured pins in the canonical
state file. R8.4.2 / PR #66 and PR #64 are HISTORICAL.

## Latest source revision (captured at PR #70)

The source tree advanced to PR #70 after the R8.4.3 / PR #67 product
baseline. `latest_source_revision` PR #70 and `source_commit` in the
canonical state are captured source coordinates, not a new product release.
The hosted native-q tier remained GATED_NOT_RUN; its runner execution was
NOT_OBSERVED and the post-job API attestation mechanism was CHECKED. Audit
evidence verification remains PENDING until PR #71 merges. No new release,
independent acceptance or q runtime result is claimed for this revision.

## Current source note — offline modules (post-baseline)

The working source adds read-only modules that are implemented offline and
NOT QUALIFIED. They do not change the R8.4.3 / PR #67 product baseline or
the PR #70 latest-source distinction; no new release is claimed.

- `python/instrument_master.py`: bitemporal Observation registry with
  `as_of`/`resolve`, provider coverage and separated status/eligibility;
  ADR `governance/w1d-architecture.md`.
- `python/exchange_calendar.py`, `python/pit_universe.py`: versioned
  calendar/PIT contracts with no real NSE calendar data.
- `python/corporate_actions.py`: event ledger and adjustment engine with
  no real corporate-action data.
- `python/upstox_v3.py`: read-only V3 daily adapter, injected HTTP and
  mocked tests only; V2 REST remains the actual integration.
- `python/fyers_socket.py`: read-only Data Socket adapter, injected
  transport and mocked tests only; no live credentials.

No live provider qualification, exchange status source, research
acceptance or full W1 exit is recorded.

## Release history (HISTORICAL)

The exact current main SHA, hosted CI runs and bundle digest are recorded in
`MANIFEST.md` of the current final release bundle (external artifact,
regenerated after R8.4.3), not as immutable identifiers in this document.
Values labelled "at PR #58", "at PR #61", "at PR #62", "at PR #63" or
"at PR #66" are HISTORICAL release records; they do not claim the current
head after reorganization. PR #61 merged the R8.3 + W1 A/B/C work
(HISTORICAL). PR #62 merged R8.4, which is COMPLETE at that release:
native-q conclusion
mapping, remote provenance, governance sync and W1 snapshot reproducibility.
PR #63 merged R8.4.1, which is COMPLETE at
`399652ca6803d65b1c460f0c57419f619c6a950d`: hosted CI run `46023401318`
succeeded with native-q skipped/GATED_NOT_RUN, the 117-entry bundle digest
`f67054a7586e26219c21e3b25682bb79ea70fa3dd9b2159d212dd97813b7c795` is
HISTORICAL, 28 W1 offline tests were recorded and the scope was
PORTABLE_RESEARCH. PR #64 was the intermediate R8.4.2 step and PR #66 was
the final R8.4.2 merge; both are HISTORICAL. The final R8.4.3 merge is
PR #67 (shown in the current record above). The public web PR index
showed "29 closed" because it was not fully paginated; it is not the
authoritative inventory. PR #44 was an earlier technical remediation and
PR #45 enforced branch protection. PR #58 was the closeout at that time;
PR #56, PR #57, PR #58, PR #61, PR #62, PR #63, PR #64, PR #65, PR #66
and PR #67 are merged corrections.

SUPERSEDED — historical policy before 2026-11-27: open PRs after PR #46: 0;
branches: `main` only. This is a historical inventory snapshot, not the live
configuration.

The zero-approval single-developer model in the "ACTIVE POLICY — Integration
record" section below governs. Earlier one-approval governance material is
marked SUPERSEDED — historical policy before 2026-11-27 and is retained only
as evidence.

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
| #62 | run `46015103880`, three jobs pass and native-q skipped | HISTORICAL at PR #62: R8.4 merge `66f037c…`; native-q GATED_NOT_RUN, never reported as passed |
| #63 | run `46023401318`, three jobs pass and native-q skipped | HISTORICAL at PR #63: R8.4.1 merge `399652ca…`; native-q GATED_NOT_RUN, never reported as passed |
| #66 | four required checks pass, native-q skipped | HISTORICAL at PR #66: R8.4.2 merge `83b6e435…`; native-q GATED_NOT_RUN, never reported as passed |
| #67 | run `46049228842`, 4 required checks pass, native-q skipped | CURRENT product baseline at PR #67: R8.4.3 merge `8ab4b674…`; native-q GATED_NOT_RUN, never reported as passed |
| #70 | latest source revision; 4 required checks unchanged | Not a product release: native q GATED_NOT_RUN, runner execution NOT_OBSERVED and attestation mechanism CHECKED |

It is therefore **not** true that every PR head was green: PR #34 was merged
with a red style check and was repaired by PR #35. Hosted CI at PR #62 and
PR #63 (both HISTORICAL) succeeded with native-q skipped/GATED_NOT_RUN;
at PR #67 four required checks passed on hosted run `46049228842` with
native q GATED_NOT_RUN. The latest source revision PR #70 is not a product
release: native q stayed GATED_NOT_RUN, runner execution was NOT_OBSERVED
and the attestation mechanism was CHECKED. The current head is in
`MANIFEST.md` of the current final release bundle (external artifact,
regenerated after R8.4.3).

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

## Gate disposition (HISTORICAL): PRs #47–#53, #56–#58, #61, #62, #63

| Gate | Status |
|---|---|
| 0 policy/review hygiene | IMPLEMENTED, ENFORCED by branch protection from this release; historical approvals absent |
| 1 PIT data/calendar | INTEGRATION-TESTED for ticker-keyed status and W1-A/B/C offline public-master crosswalk (at PR #62, HISTORICAL: 14 tests, four bundled snapshots, 9,926 VERIFIED / 174 UNMAPPED); effective-dated identity (W1-D) PENDING |
| 2 KDB-X generations | IMPLEMENTED, FAULT-INJECTED; native q tested locally and still GATED in hosted CI (skipped/GATED_NOT_RUN at PR #61, PR #62 and PR #63, all HISTORICAL) |
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
trading NOT IMPLEMENTED. W1-A/B/C map pinned public masters offline: 46
tests at PR #67, four bundled snapshots and 9,926 VERIFIED / 174 UNMAPPED
identities, but W1-D live consistency and effective dating are PENDING;
no full W1 exit is claimed. Native q hosted required check GATED/NOT REQUIRED
(skipped/GATED_NOT_RUN at PR #61, PR #62 and PR #63, all HISTORICAL);
power-loss durability NOT PROVEN; intraday/HFT UNSUPPORTED; multi-asset venue
conformance NOT ESTABLISHED; independent reviewer acceptance for recorded
releases NOT RECORDED; live execution DISABLED; trading decision NO TRADE.
The R8.4 and R8.4.1 release scopes were PORTABLE_RESEARCH (HISTORICAL).
R8.4.1 is COMPLETE at PR #63 (HISTORICAL release record). R8.4.2 / PR #66
is COMPLETE (HISTORICAL). R8.4.3 / PR #67 is COMPLETE with 4 required
checks and native q GATED_NOT_RUN. The product baseline remains PR #67;
the latest source revision PR #70 records native q runner execution
NOT_OBSERVED, attestation mechanism CHECKED and audit evidence verification
PENDING until PR #71 merges.

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

## ACTIVE POLICY — Integration record — 28 November 2026 (updated through PR #67) <!-- https://github.com/siebengestirn/quant/pull/67 -->

This section is the ACTIVE POLICY. It supersedes the one-approval governance
material above; zero PRs were open at the PR #62 merge (HISTORICAL); PR #63,
PR #66 and PR #67 were merged under the same policy. `main` is the sole
branch.
The live PR inventory is external.

All seven remediation PRs #47–#53 were merged sequentially with merge commits
after fresh exact-head CI on each synced head; PRs #56–#58, #61 (R8.3/W1),
#62 (R8.4), #63 (R8.4.1), #64 (intermediate R8.4.2 step, HISTORICAL),
#65, #66 (R8.4.2 final) and #67 (R8.4.3 final) were later merged under the
same policy.
`main` is the sole integration branch. Branch protection was corrected to
the single-developer policy used in the sibling repositories: zero required
GitHub approvals, explicit self-review checklist, exact-head CI evidence,
4 required checks, PR, conversation resolution, no force pushes and no
branch deletion. No admin bypass or history rewrite was used.

This record supersedes the earlier seven-open-PR snapshot. Operating status
remains: research INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED;
provider streaming, contract-note reconciliation, corporate actions and
operational paper trading NOT IMPLEMENTED; W1-A/B/C offline public-master
evidence only (46 tests at PR #67 — HISTORICAL, four bundled snapshots,
9,926 VERIFIED /
174 UNMAPPED), with W1-D effective dating and live consistency PENDING and
no full W1 exit; native q hosted tier GATED (skipped/GATED_NOT_RUN at
PR #62 and PR #63, both HISTORICAL); power-loss durability NOT PROVEN;
intraday/HFT UNSUPPORTED; multi-asset venue conformance NOT ESTABLISHED;
live execution DISABLED; decision NO TRADE. The R8.4, R8.4.1, R8.4.2 and
R8.4.3 release scopes were PORTABLE_RESEARCH. R8.4.1 is COMPLETE at PR #63
(HISTORICAL release record). R8.4.2 / PR #66 is COMPLETE (HISTORICAL).
R8.4.3 / PR #67 is COMPLETE with 4 required checks, 46 W1 tests and native q
GATED_NOT_RUN; the intermediate PR #64 runner work and PR #66 are HISTORICAL.
The latest source revision PR #70 is a source coordinate only: native q
runner execution NOT_OBSERVED, attestation mechanism CHECKED and audit
evidence verification PENDING until PR #71 merges.

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
consistency remain PENDING. At PR #61 (HISTORICAL), R8.4 was IN PROGRESS;
it is COMPLETE at PR #62 and R8.4.1 is COMPLETE at PR #63 (release records
below). Independent acceptance remains NOT RECORDED and the independent
security review remains OPEN.

### Active release-bundle policy (R8.4 / PR #62 — COMPLETE, HISTORICAL)

PR #62 merged R8.4 at `66f037cc1d508244a821fef22dd35023d996e2ab`. HISTORICAL
at PR #62: the then-final bundle `MANIFEST.md` recorded 115 entries, 4 pinned
W1 snapshots and digest
`4558f684ca7515054e7301be3c063e7a8e5651705e735d8838cd2792d9d89980`, and
hosted CI run `46015103880` succeeded with three passing jobs and one
skipped native-q job (GATED_NOT_RUN). Local verification recorded 14114 C++
checks / 0 failures, 14114 sanitizer checks / 0 failures, 51 portable
pytest passes, 14 W1 pytest passes, 16 release-bundle passes, 106
verify-report passes, style 8/8 PASS and zero binding audit findings. These
are release-history values at PR #62; current runs and digest are in
`MANIFEST.md` of the current bundle (external artifact).

R8.4 closed native-q conclusion mapping (a skipped job is reported as
GATED_NOT_RUN, never passed), remote provenance of the pinned public
masters, governance synchronisation and W1 snapshot reproducibility: four
pinned snapshots are bundled and digest-checked, with 9,926 VERIFIED /
174 UNMAPPED identities. The release scope was PORTABLE_RESEARCH and native
q remained GATED_NOT_RUN. W1-D effective dating and live consistency remain
PENDING; full W1 exit and independent acceptance are NOT claimed. Native-q
runner provisioning remains OPEN under TD-015. At PR #62 (HISTORICAL),
R8.4.1 (PR #63) was IN PROGRESS (bundle validator PASS support, complete
report schema, native-q self-provisioning CI, offline W1 fixture and
hardened retrieval); it is COMPLETE at PR #63 (release record below).

### Active release-bundle policy (R8.4.1 / PR #63 — COMPLETE, HISTORICAL)

PR #63 merged R8.4.1 at
`399652ca6803d65b1c460f0c57419f619c6a950d`. HISTORICAL at PR #63: the
then-final bundle `MANIFEST.md` recorded 117 entries and digest
`f67054a7586e26219c21e3b25682bb79ea70fa3dd9b2159d212dd97813b7c795`, and
hosted CI run `46023401318` succeeded with three passing jobs and one
skipped native-q job (GATED_NOT_RUN, never reported as passed). These are
release-history values at PR #63; the current bundle is generated after
R8.4.2 and current values are in its `MANIFEST.md` (external artifact).

R8.4.1 closed the bundle validator PASS path, complete report schema,
native-q self-provisioning CI, offline W1 fixture and hardened retrieval.
Local verification recorded 28 W1 offline tests on captured bytes; W1 is
PARTIAL with 9,926 VERIFIED / 174 UNMAPPED identities and four bundled
pinned snapshots, W1-D effective dating and live consistency remain
PENDING, and no full W1 exit is claimed. The release scope was
PORTABLE_RESEARCH and native q remained GATED_NOT_RUN. R8.4.1 was the
CURRENT RELEASE until PR #64 merges; R8.4.2 was IN PROGRESS at PR #64
(runner isolation, native_q_checker gate, skip enforcement, explicit W1
evidence and hosted release assurance). Both are HISTORICAL; the current
release is R8.4.3 / PR #67. Independent acceptance remains PENDING and
native-q runner provisioning stays OPEN under TD-015.

Branch protection was captured from the authenticated endpoint
`https://api.github.com/repos/siebengestirn/quant/branches/main/protection`
at `2026-11-27T20:07:24Z` with HTTP 200 (HTTP/2.0), API version
`2022-11-28` and response SHA-256
`de633dabfe3016d17f2885c1179282d68736f50692cacd51e9cbdb31acf02356`.
This HISTORICAL point-in-time capture at PR #70 records `main` as
`90f45f31e8f28070f0bbcb38c1e9189a020e6cdc`, the latest source commit;
later heads are in the bundle `MANIFEST.md`. The response body is
byte-identical to the previous capture, so the governed settings had not
changed. It records `required_approvals` 0,
`required_conversation_resolution` true, `required_linear_history` false,
force pushes and branch deletion disabled, `enforce_admins` true and four
strict required checks (`core build and tests`, `ASan/UBSan`,
`Style contract (reference guides)`, `Release assurance (offline)`). The
tracked record is `governance/branch-protection-capture.json`; the prior
PR #67 capture and the earlier PR #64 capture are retained under its
`historical_captures` array. The bundle copy is
`evidence/branch-protection.json` in the external final bundle.
`git-policy.json` declares policy; only an authenticated API export proves
the live GitHub configuration. The historical JSON exports do not establish
the current zero-approval setting.

 Current source record — PR #86; release PR #67; W1 71 tests; 4 req
 uired checks; PORTABLE_RESEARCH; GATED_NOT_RUN; NOT_OBSERVED; CHECKED; R8.4.
 3 COMPLETE.
