# QUANT master requirements and acceptance register

This is the authoritative definition of "complete" for the platform
programme. A capability is complete only when requirement, implementation,
tests, failure behaviour, operational evidence and independent acceptance
agree. Green CI alone is not acceptance.

Statuses: `NOT IMPLEMENTED`, `IMPLEMENTED`, `UNIT-TESTED`,
`INTEGRATION-TESTED`, `FAULT-INJECTED`, `CRASH-TESTED`,
`INDEPENDENTLY QUALIFIED`, `INDEPENDENTLY ACCEPTED`, `BLOCKED`.

## CURRENT STATE (authoritative)

Machine-readable release, governance and trading state at the current head.
The exact current main SHA, CI runs and bundle digest are in `MANIFEST.md`
of the current final release bundle (external artifact, regenerated after
R8.4.2); values marked as release history at PR #58, PR #61, PR #62 and
PR #63 are not immutable current identifiers.

```yaml
release:
  current_main_sha_source: MANIFEST.md in the current final release bundle
  # Exact current main SHA, CI runs and bundle digest are external values
  # in that MANIFEST.md, not immutable identifiers in this register.
  # HISTORICAL baseline snapshot, not a current state claim:
  baseline_snapshot_sha: 0b353f84f877764066eeb3dc0f70ac4ef645bf50
  # Release history at PR #58; later reorganization supersedes it:
  final_main_sha: 1a91da2fb08995c9f6ee20e373d004ad28e58a72
  remediation_prs: MERGED
  remediation_range: "47-58"
  final_release_record_pr: 54
  documentation_inventory_pr: 55
  r7_correction_pr: 56
  r8_1_prs: [57, 58]
  # At PR #58; current digest is in the bundle MANIFEST.md (external):
  final_bundle_sha256:
    5eb0e1a87b3925117f515808591c9049ee0a11ffec246b46f61f9f79c54462c7
  final_bundle_manifest_entries: 98
  final_head_ci_run: 45969369813
  final_post_merge_ci_run: 45969624206
  # Release history at PR #61 (R8.3 and W1 A/B/C), HISTORICAL:
  r8_3_pr: 61
  r8_3_merge_commit: 201f5ba602f68fc1370579cefa50b8b599af05d5
  # At PR #61; current digest is in the bundle MANIFEST.md (external):
  r8_3_bundle_sha256:
    c5be8f1ff2ae2faeea161dfa815511ee1f8775cb347de98f3602dd500993bf8c
  r8_3_bundle_manifest_entries: 108
  # At PR #61; hosted CI passed, native q skipped/GATED_NOT_RUN:
  r8_3_head_ci_run: 46007348694
  r8_3_native_q: GATED_NOT_RUN
  # Release history at PR #62 (R8.4 COMPLETE), HISTORICAL; current values
  # are in the bundle MANIFEST.md (external artifact):
  r8_4_pr: 62
  r8_4_merge_commit: 66f037cc1d508244a821fef22dd35023d996e2ab
  r8_4_bundle_sha256:
    4558f684ca7515054e7301be3c063e7a8e5651705e735d8838cd2792d9d89980
  r8_4_bundle_manifest_entries: 115
  r8_4_bundled_w1_snapshots: 4
  r8_4_status: COMPLETE
  # At PR #62; hosted CI passed, native q skipped/GATED_NOT_RUN:
  r8_4_head_ci_run: 46015103880
  r8_4_native_q: GATED_NOT_RUN
  # HISTORICAL: R8.4.1 / PR #63 MERGED at 399652ca... (current release
  # until PR #64 merges; no current identifiers are pinned here):
  r8_4_1_pr: 63
  r8_4_1_merge_commit: 399652ca6803d65b1c460f0c57419f619c6a950d
  r8_4_1_status: COMPLETE
  # HISTORICAL at PR #63; current bundle is external (after R8.4.2):
  r8_4_1_bundle_sha256:
    f67054a7586e26219c21e3b25682bb79ea70fa3dd9b2159d212dd97813b7c795
  r8_4_1_bundle_manifest_entries: 117
  # HISTORICAL at PR #63; hosted CI success, native q skipped/GATED_NOT_RUN:
  r8_4_1_head_ci_run: 46023401318
  r8_4_1_native_q: GATED_NOT_RUN
  r8_4_1_release_scope: PORTABLE_RESEARCH
  r8_4_1_w1_offline_tests: 28
  # R8.4.2 / PR #64 is IN PROGRESS; it has no release identifiers yet:
  r8_4_2_pr: 64
  r8_4_2_status: IN PROGRESS
  # Current release scope and hosted native-q tier (until PR #64 merges):
  release_scope: PORTABLE_RESEARCH
  native_q: GATED_NOT_RUN
  # At PR #62 merge (HISTORICAL); live inventory is external:
  open_prs_at_pr_62: 0
  current_pr_inventory_source: GitHub pull request index (external)
w1: >-
  PARTIAL: 28 offline tests on captured bytes (R8.4.1 / PR #63); W1-D
  live consistency and effective dating PENDING; no full W1 exit
governance: >-
  model SINGLE_DEVELOPER, required_github_approvals 0,
  self_review_checklist REQUIRED, exact_head_ci REQUIRED,
  required_checks 3, independent_external_acceptance PENDING
trading: >-
  research INCONCLUSIVE, holdout SEALED_NOT_EVALUATED,
  live_execution DISABLED, decision NO_TRADE
```

## Release state reconciliation

The authoritative state is the CURRENT STATE block above and the ACTIVE
POLICY record in `docs/release-status.md`. The exact current main SHA, CI
runs and bundle digest are recorded in `MANIFEST.md` of the current final
release bundle (external artifact), not as immutable identifiers here.
Release history at PR #58 records `main` as `1a91da2…`
(full SHA `1a91da2fb08995c9f6ee20e373d004ad28e58a72`) in the then-final
bundle `MANIFEST.md`; `99bc70c…` was the state at PR #56 and is
HISTORICAL. Release history at PR #61 (HISTORICAL) records the R8.3 +
W1 A/B/C merge at `201f5ba602f68fc1370579cefa50b8b599af05d5`, with a
then-final bundle of 108 entries and hosted CI run `46007348694`
success; native q was skipped/GATED_NOT_RUN. Release history at PR #62
(HISTORICAL) records the R8.4 merge at
`66f037cc1d508244a821fef22dd35023d996e2ab`, a then-final bundle with
115 entries and four pinned W1 snapshots, hosted CI run `46015103880`
success and native q skipped/GATED_NOT_RUN; R8.4 is COMPLETE at PR #62.
Release history at PR #63 (HISTORICAL) records the R8.4.1 merge at
`399652ca6803d65b1c460f0c57419f619c6a950d`, a then-final 117-entry
bundle with digest
`f67054a7586e26219c21e3b25682bb79ea70fa3dd9b2159d212dd97813b7c795`,
hosted CI run `46023401318` success and native q skipped/GATED_NOT_RUN.
R8.4.1 is COMPLETE at PR #63 and is the CURRENT RELEASE until PR #64
merges; 28 W1 offline tests were recorded, W1 remains PARTIAL with W1-D
PENDING, and the release scope was PORTABLE_RESEARCH. R8.4.2 / PR #64 is
IN PROGRESS (runner isolation, native_q_checker gate, skip enforcement,
explicit W1 evidence and hosted release assurance); it has no exit claim.
The exact current digest and CI runs are in `MANIFEST.md` of the current
bundle (external; the current bundle is generated after R8.4.2).
PRs #47–#63 are all MERGED; the live PR inventory is external. Merges use
the single-developer policy: zero required GitHub approvals, a self-review
checklist and exact-head CI with three required checks. Independent
external technical acceptance is PENDING / NOT RECORDED. The older
`0b353f84…` SHA is the baseline snapshot retained as historical evidence,
not a current state claim.

### HISTORICAL / SUPERSEDED snapshot (retained as evidence, not current state)

The earlier `docs/release-status.md` describes the state at PR #46
(merged PRs #1–#46, no open PRs on `main`). The governance state at that time
was seven open PRs #47–#53 on `main` at `953dac24…`, all with successful
exact-head hosted CI and no independent approval recorded. The earlier file
remains historical evidence; this register governs the present programme.

## PR #47–#53 traceability

| PR | Scope | Amended | Merge evidence (current) | Independent acceptance (historical) |
|---|---|---|---|---|
| #47 | Dataset security | Yes — no-follow descriptor reads and exact raw digest grammar | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #48 | Risk authorization | Yes — full identity, venue and data binding | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #49 | OMS replay | Yes — recoverable AMBIGUOUS state | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #50 | Publication filesystem | Yes — no-follow descriptor recovery | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #51 | KDB-X recovery | Yes — argv-safe helper, secure temp, host-aware lock | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #52 | Research report scope | No amendment required; reviewer disposition was "retain" | MERGED; self-review and exact-head CI, zero approvals | Pending |
| #53 | Release truth | Yes — machine-readable policy state reconciled | MERGED; self-review and exact-head CI, zero approvals | Pending |

All seven PRs were merged under the single-developer policy with exact-head
CI and three required checks; independent external technical acceptance is
NOT RECORDED. The final column is the historical pending snapshot, not the
current state.

The in-process capability boundary of #48 remains an explicit limitation.
A shared lock is not distributed authorization and is not protection against
hostile code in another process.

## Completion fields

Implementation, verification and independent acceptance are tracked
separately. `IMPLEMENTED` never means `CERTIFIED`, and green CI never means
accepted.

| Workstream | Implementation | Verification | Independent acceptance | Blocked by |
|---|---|---|---|---|
| W1 Instrument master | W1-A/B/C implemented from pinned public Upstox and FYERS masters; effective-dated registry (W1-D) still PENDING | 28 offline tests on captured bytes; 9,926 VERIFIED crosswalk matches, 174 UNMAPPED; four bundled pinned snapshots | Pending: W1-D live consistency and effective dating; no full W1 exit | W1-D live consistency |
| W2 Upstox V3 | Partial (V2 REST; V3 fixture-schema normalisation only) | Unit-tested corrections | Pending | W1 |
| W3 FYERS Data Socket | Not implemented | Not tested | Pending | W1, valid token |
| W4 Provider journal | Not proven | Synthetic coverage only | Pending | W2, W3 |
| W12 Durability | Partial | Process/crash evidence only | Pending | W4, fault rig |
| W16 Independent acceptance | Documented merge controls | Register present; exact-head CI evidence | NOT RECORDED (external) | defined high-risk release gates |

Every workstream row below inherits these three separate fields. A failed
upstream workstream blocks every dependent release decision; `Blocked by`
contains requirement IDs from the same register.

## Workstreams

| ID | Workstream | Current status | Dependencies | Exit evidence | Release blocking |
|---|---|---|---|---|---|
| W0 | Evidence-integrity registers | PARTIAL: four governance CSV registers tracked; no prior W0 definition in repository (UNVERIFIED PROVENANCE) | none | Independent reviewer disposition of the four registers | No for routine merges |
| W1 | Instrument master and multi-venue identity | PARTIAL: W1-A/B/C implemented from pinned public Upstox/FYERS masters (9,926 VERIFIED / 174 UNMAPPED); 28 offline tests and four bundled snapshots; W1-D live consistency and effective dating PENDING | none | Effective-dated registry, provider crosswalk report, per-class conformance matrix and live consistency tests | Yes for multi-asset |
| W2 | Upstox V3 data adapter | PARTIAL: read-only V2 REST; V3 seven-field candle schema normalisation of fixtures only | W1 | ISO timestamp parsing, V3 seven-field candles, ascending canonical history, raw payload archive, 10-session qualification | Yes for provider certification |
| W3 | FYERS Data Socket adapter | NOT IMPLEMENTED | W1, fresh token | REST quotes, history, Data Socket only, reconnect and replay evidence | Yes for dual-provider |
| W4 | Provider-to-journal-to-q path | NOT PROVEN with real events | W2, W3 | Raw and normalised events, durable journal, q replay parity | Yes |
| W5 | Exchange calendar and PIT universe | PARTIAL | W1 | Authoritative calendar, universe revisions, effective-dated aliases | Yes for research |
| W6 | Corporate actions | NOT IMPLEMENTED | W1, W5 | Split, bonus, dividend, rights, merger and demerger fixtures with provenance | Yes |
| W7 | Research and statistical acceptance | INCONCLUSIVE / BLOCKED | W5, W6 | Nested validation, full trial registry, Reality Check/SPA or approved alternative, cost model, sealed holdout | Yes for strategy acceptance |
| W8 | Feature registry | PARTIAL | W5 | Versioned definitions, availability tests, C++/Python parity | No for data release; yes for strategy |
| W9 | Contract-note reconciliation | NOT IMPLEMENTED | W1, W6 | Independent charge recalculation; zero unexplained differences | Yes for financial gate |
| W10 | Operational paper OMS | NOT IMPLEMENTED | W2–W4, W9 | Full lifecycle, partial fills, cancels, restarts, 20-session qualification | Yes |
| W11 | Native q hosted check | GATED | governed licensed runner | Required workflow passing on q-changing PRs | Yes for persistence releases |
| W12 | Durability qualification | NOT PROVEN | W4 | Process, OS, power and storage fault matrix on named hardware; acknowledged event survival | Yes for operational release |
| W13 | Intraday support | UNSUPPORTED | W2–W5, W11 | Intraday session conformance, auction and halt handling, intraday costs | Yes for intraday scope |
| W14 | HFT feasibility | SEPARATE PROJECT | W13, low-latency infrastructure | Tick-by-tick conformance, clock sync, latency distribution | Yes for HFT scope |
| W15 | Multi-asset conformance | NOT ESTABLISHED | W1, W6, W9 | Signed per-class conformance matrix | Yes per class |
| W16 | Independent acceptance | NOT RECORDED (external) | all relevant gates | Required merge controls: zero GitHub approvals, self-review checklist and exact-head CI. External technical review is optional. Independent acceptance is mandatory only for defined high-risk release gates | No for routine merges; yes for defined high-risk gates |
| W17 | Live execution authorisation | DISABLED | W7, W10, W12, W16, compliance | Written broker and compliance approval; separate release decision | Yes for any live route |
| W18 | Independent release assurance and security remediation | PENDING (independent reviewer) | W16 and all defined high-risk release gates | This row is the authoritative W18 definition. Exit evidence: disposition of each security finding by an independent reviewer and recorded external acceptance. Local checks and CI are not acceptance; acceptance NOT RECORDED | Yes for release acceptance |

CI evidence is an automated gate, not a human independent review. Required
merge controls are zero GitHub approvals, the self-review checklist and
exact-head CI. External technical review is optional; independent acceptance
is mandatory only for defined high-risk release gates, not every merge.

## Authoritative gate dashboard (W0–W18)

This is the single authoritative dashboard. Statuses and blocked-by edges
are derived from the Workstreams table above and from
`governance/requirements-traceability.csv`; R8.1/R8.3/R8.4/R8.4.1 are
corrective work entries, not extra gates. No volatile identifier is pinned
here: current main SHA, CI runs and bundle digest are in the `MANIFEST.md`
of the current final release bundle (external artifact).

| Gate | Current status | Blocked by |
|---|---|---|
| W0 | PARTIAL | independent reviewer (acceptance) |
| W1 | PARTIAL: W1-A/B/C pinned public masters; 28 offline tests and four bundled snapshots; W1-D and effective dating PENDING | none |
| W2 | PARTIAL: V2 REST only; V3 fixture-schema normalisation | W1 |
| W3 | NOT IMPLEMENTED (FYERS profile probe only) | W1, fresh token |
| W4 | NOT PROVEN with real events | W2, W3 |
| W5 | PARTIAL (ticker-keyed status) | W1 |
| W6 | NOT IMPLEMENTED | W1, W5 |
| W7 | INCONCLUSIVE / BLOCKED | W5, W6 |
| W8 | PARTIAL | W5 |
| W9 | NOT IMPLEMENTED | W1, W6 |
| W10 | NOT IMPLEMENTED | W2, W3, W4, W9 |
| W11 | GATED (native q) | governed licensed runner |
| W12 | NOT PROVEN (power loss) | W4, fault rig |
| W13 | UNSUPPORTED | W2, W3, W4, W5, W11 |
| W14 | SEPARATE PROJECT | W13, low-latency infrastructure |
| W15 | NOT ESTABLISHED | W1, W6, W9 |
| W16 | NOT RECORDED (external) | all relevant gates |
| W17 | DISABLED | W7, W10, W12, W16, compliance |
| W18 | PENDING (independent reviewer) | W16 and all defined high-risk release gates |

No row above claims full W1 exit, independently accepted status, live
execution authorisation or `READY FOR TRADE`.

### Corrective work status

| Record | Status | Scope and evidence |
|---|---|---|
| R8.1 | IMPLEMENTED / MERGED (HISTORICAL at PR #57/#58) | Exact-integer OI and volume parity, integral volume contract, wall-clock journal replay test |
| R8.3 | COMPLETE at PR #61 (HISTORICAL release record) | Legacy volume validity, leading-zero grammar, W1 A/B/C, bundle totals, provenance guard and portable self-test |
| R8.4 | COMPLETE at PR #62 (HISTORICAL release record) | Native-q conclusion mapping, remote provenance, governance sync and W1 snapshot reproducibility; 14 W1 tests and four bundled snapshots (HISTORICAL); native q still GATED_NOT_RUN |
| R8.4.1 | COMPLETE at PR #63 (HISTORICAL release record; current release until PR #64 merges) | Bundle validator PASS support, complete report schema, native-q self-provisioning CI, offline W1 fixture and hardened retrieval; 28 W1 offline tests, hosted CI 46023401318 success, 117-entry bundle, native q GATED_NOT_RUN, scope PORTABLE_RESEARCH |
| R8.4.2 | IN PROGRESS (PR #64) | Runner isolation, native_q_checker gate, skip enforcement, explicit W1 evidence and hosted release assurance; no exit claim |

### R8.1 corrective work (local implementation)

The R8.1 corrective work — exact-integer OI parsing with C++/Python parity
above 2^53, the integral-volume dataset contract and a wall-clock journal
replay test — is IMPLEMENTED and MERGED in PR #57. Verification recorded
14090 C++ checks, 14090 sanitizer checks, zero failures, 50 portable pytest
passes and all portable assurance gates PASS. At PR #57, hosted exact-head
CI run 45968572026 succeeded on head
`21a62e3eca480440981a3c1fa0681f9a12493171`; post-merge CI run 45968891063
succeeded on the R8.1 merge commit `460b8f2d…`. PR #58 closed out the
release: its head `fb9170ca5e9003b0dbea7c1191e874ff4e7fa8b2` passed hosted
CI run 45969369813, and final post-merge CI run 45969624206 passed on
`main` `1a91da2fb08995c9f6ee20e373d004ad28e58a72`. The then-final bundle
`MANIFEST.md` recorded 98 verified entries and digest
`5eb0e1a87b3925117f515808591c9049ee0a11ffec246b46f61f9f79c54462c7`;
these are release-history values at PR #58. The exact current main SHA, CI
runs and bundle digest are in the current final release bundle
`MANIFEST.md` (external artifact). Earlier bundles are HISTORICAL.
Independent acceptance is NOT RECORDED; native q hosted is GATED/NOT RUN
and the trading decision remains NO TRADE.

Provider scope (W2/W3): the live Upstox integration is read-only V2 REST
(`https://api.upstox.com/v2` in `src/market_data.cpp`); there is no V3 HTTP
endpoint call. V3 seven-field candle parsing is schema normalisation of
fixtures only. V3 candle schema normalisation implemented; actual Upstox V3
endpoints NOT established; streaming NOT IMPLEMENTED; real feed certification
NOT achieved. FYERS is a profile probe against
`https://api-t1.fyers.in/api/v3`; no Data Socket streaming is implemented.

### R8.3 corrective work (PR #61 — HISTORICAL release record)

At PR #61 the R8.3 + W1 A/B/C work was MERGED at
`201f5ba602f68fc1370579cefa50b8b599af05d5`. This merge SHA, the CI run
`46007348694` (success) and the 108-entry bundle digest
`c5be8f1ff2ae2faeea161dfa815511ee1f8775cb347de98f3602dd500993bf8c`
are HISTORICAL values at PR #61, not current identifiers. The hosted
CI had three passing jobs and one skipped native-q job; native q was
GATED_NOT_RUN. At PR #61, local verification recorded 14114 C++ checks
with 0 failures, 14114 sanitizer checks with 0 failures, 51 portable
pytest passes, 11 W1 pytest passes, style 8/8 PASS and 0 binding audit
findings. The exact current main SHA, CI runs and bundle digest are in
`MANIFEST.md` of the current final release bundle (external artifact).

R8.3 closed the legacy volume-validity migration: newly constructed
quotes default to `UNKNOWN` and legacy events without the field replay as
`UNKNOWN`, so a substituted zero is never promoted to a verified
observation. Both canonical CSV readers reject leading zeros, signs and
decimal points for counts. The release-bundle generator now renders
manifest totals from the verification report and checks them against the
embedded report, fails closed on a missing/unreadable provenance report
and uses a portable self-test temp directory with a `QUANT_FRESHNESS_TEMP`
override. These are closure claims for R8.3 only; they do not establish
independent security acceptance.

W1 A/B/C are offline evidence from two pinned public masters recorded in
`governance/instrument-master-snapshots.json`: the Upstox NSE master and
the FYERS NSE_CM symbol master. At PR #61 (HISTORICAL), W1 pytest ran 11
tests on the captured bytes; the crosswalk reported 9,926 VERIFIED and
174 UNMAPPED identities. W1-D live consistency and effective dating remain
PENDING; full W1 exit is NOT claimed. At PR #61 (HISTORICAL), R8.4 was
still IN PROGRESS; R8.4 is COMPLETE at PR #62 (release record below).

### R8.4 corrective work (PR #62 — COMPLETE, HISTORICAL release record)

R8.4 was MERGED in PR #62 at
`66f037cc1d508244a821fef22dd35023d996e2ab`. That SHA, hosted CI run
`46015103880` (success), the 115-entry then-final bundle and its digest
`4558f684ca7515054e7301be3c063e7a8e5651705e735d8838cd2792d9d89980`
are HISTORICAL release-history values at PR #62, not current identifiers;
exact current values are in `MANIFEST.md` of the current bundle (external
artifact). The hosted CI had three passing jobs and one skipped native-q
job; the skipped conclusion was mapped as GATED_NOT_RUN, never as passed.

Local verification at PR #62 (HISTORICAL) recorded 14114 C++ checks with
zero failures, 14114 sanitizer checks with zero failures, 51 portable
pytest passes, 14 W1 pytest passes, 16 release-bundle passes, 106
verify-report passes, style 8/8 PASS and zero binding audit findings. The
release scope was PORTABLE_RESEARCH and native q was GATED_NOT_RUN.

R8.4 closed four code-level items: native-q conclusion mapping, remote
provenance of the pinned public masters, governance synchronisation and
W1 snapshot reproducibility. The bundle carries 4 digest-checked pinned W1
snapshots; the crosswalk still reports 9,926 VERIFIED / 174 UNMAPPED.
W1-D live consistency and effective dating remain PENDING, full W1 exit is
NOT claimed and independent acceptance is NOT RECORDED. Native-q runner
provisioning remains OPEN under TD-015 in the technical debt register.
At PR #62 (HISTORICAL), R8.4.1 (PR #63) was IN PROGRESS: bundle validator
PASS support, complete report schema, native-q self-provisioning CI,
offline W1 fixture and hardened retrieval. It is COMPLETE at PR #63
(release record below) and is the current release until PR #64 merges.

### R8.4.1 corrective work (PR #63 — COMPLETE, HISTORICAL release record)

R8.4.1 was MERGED in PR #63 at
`399652ca6803d65b1c460f0c57419f619c6a950d`. HISTORICAL at PR #63: hosted
CI run `46023401318` succeeded with three passing jobs and one skipped
native-q job, the then-final bundle had 117 entries and digest
`f67054a7586e26219c21e3b25682bb79ea70fa3dd9b2159d212dd97813b7c795`.
Current main SHA, CI runs and bundle digest are external: the current
bundle is generated after R8.4.2 merges. R8.4.1 is COMPLETE and is the
CURRENT RELEASE until PR #64 merges.

R8.4.1 closed the bundle validator PASS path, the complete report schema,
native-q self-provisioning CI, the offline W1 fixture and hardened
retrieval. Local verification recorded 28 W1 offline tests on captured
bytes; W1 is PARTIAL (9,926 VERIFIED / 174 UNMAPPED crosswalk identities,
four bundled pinned snapshots) with W1-D live consistency and effective
dating PENDING and no full W1 exit. The release scope was
PORTABLE_RESEARCH and native q was GATED_NOT_RUN; the skipped hosted job
was reported as GATED_NOT_RUN, never as passed. Independent acceptance is
NOT RECORDED and native-q runner provisioning stays OPEN under TD-015.

R8.4.2 / PR #64 is IN PROGRESS (runner isolation, native_q_checker gate,
skip enforcement, explicit W1 evidence and hosted release assurance). It
has no exit claim, no recorded CI run and no bundle digest; R8.4.1 remains
the current release until PR #64 merges.

## Data-only testing invariants

For provider tests: `LIVE_TRADING_ENABLED=false`, `EXECUTION_MODE=DISABLED`,
no Zerodha session token in the collector, no execution gateway instantiated,
read-only provider credentials, provider market-data endpoints only. The
Zerodha submission lock must remain blocked in a negative test.

## Prohibited claims

No claim of strategy profitability, evaluated holdout, provider streaming
certification, operational paper trading, fully proven universal power-loss
durability, HFT suitability, multi-asset conformance, full W1 exit,
independent acceptance, live execution authorisation or `READY FOR TRADE`
is permitted until the corresponding exit evidence above is recorded.
