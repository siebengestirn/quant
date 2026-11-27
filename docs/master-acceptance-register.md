# QUANT master requirements and acceptance register

## CURRENT RELEASE RECORD (authoritative)

Canonical machine-readable state: `governance/release-state.json`, checked
by `scripts/check_release_state.py`. The authoritative product release
baseline R8.4.3 / PR #67 is COMPLETE; scope PORTABLE_RESEARCH; merged PRs
47-67; 4 required checks with zero GitHub approvals; native q GATED_NOT_RUN;
trading DISABLED / NO TRADE. The latest source revision PR #70 is a source
coordinate in that state, not a new product release: native q runner
execution NOT_OBSERVED and attestation mechanism CHECKED, with audit
evidence verification PENDING until PR #71 merges. W1 is PARTIAL with 46
offline tests, a bitemporal registry and ADR present; W1-D live read-only
consistency and exchange status PENDING, no full W1 exit. W5 is PARTIAL
with calendar/PIT modules offline; W6 IMPLEMENTED MODULES; W2/W3 adapters
IMPLEMENTED OFFLINE / NOT QUALIFIED. W9 NOT QUALIFIED; W13-W15 FUTURE;
W16/W18 PENDING; W19/W20 PROPOSED. R8.4.2 / PR #66 and the intermediate
PR #64 work are HISTORICAL, no longer the current product baseline.

This is the authoritative definition of "complete" for the platform
programme. A capability is complete only when requirement, implementation,
tests, failure behaviour, operational evidence and independent acceptance
agree. Green CI alone is not acceptance.

Statuses: `NOT IMPLEMENTED`, `IMPLEMENTED`, `IMPLEMENTED MODULES` (offline
code only), `UNIT-TESTED`, `INTEGRATION-TESTED`, `FAULT-INJECTED`,
`CRASH-TESTED`, `NOT QUALIFIED` (no live qualification),
`INDEPENDENTLY QUALIFIED`, `INDEPENDENTLY ACCEPTED`, `BLOCKED`.

## CURRENT STATE (authoritative)

Machine-readable release, governance and trading state at the current head.
The exact current main SHA, CI runs and bundle digest are in `MANIFEST.md`
of the current final release bundle (external artifact, regenerated after
R8.4.3); values marked as release history at PR #58, PR #61, PR #62,
PR #63 and PR #66 are not immutable current identifiers.

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
  # R8.4.3 / PR #67 is COMPLETE; canonical pins in release-state.json:
  r8_4_3_pr: 67
  r8_4_3_status: COMPLETE
  r8_4_3_merged_pr_range: "47-67"
  r8_4_3_w1_offline_tests: 46
  product_release_baseline: R8.4.3 / PR #67
  latest_source_revision: PR #70 (source coordinates, not a release)
  native_q_runner_execution: NOT_OBSERVED
  runner_attestation_mechanism: CHECKED
  audit_evidence_verification: PENDING until PR #71 merges
  # captured at PR #70: branch-protection response digest
  # de633dabfe3016d17f2885c1179282d68736f50692cacd51e9cbdb31acf02356
  # HISTORICAL at PR #66: R8.4.2 COMPLETE, range "47-66", 36 W1 tests;
  # original bundle digest (HISTORICAL at PR #66):
  # 0deebdd099912cfc4e2045d8a2c377ca4825f8382b50d600aa86f7dfa724adc3
  required_checks: 4
  required_approvals: 0
  release_scope: PORTABLE_RESEARCH
  native_q: GATED_NOT_RUN
  # At PR #62 merge (HISTORICAL); live inventory is external:
  open_prs_at_pr_62: 0
  current_pr_inventory_source: GitHub pull request index (external)
w1: >-
  PARTIAL / IMPLEMENTED MODULES: 46 offline tests at PR #67 (HISTORICAL
  at PR #63: 28 tests); bitemporal registry and ADR present; W1-D live
  read-only consistency and exchange status PENDING; no full W1 exit
governance: >-
  model SINGLE_DEVELOPER, required_github_approvals 0,
  self_review_checklist REQUIRED, exact_head_ci REQUIRED,
  required_checks 4, native_q GATED_NOT_RUN,
  independent_acceptance PENDING
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
R8.4.1 is COMPLETE at PR #63 (HISTORICAL release record): 28 W1 offline
tests were recorded, W1 remains PARTIAL with W1-D PENDING, and the release
scope was PORTABLE_RESEARCH. R8.4.2 / PR #66 is COMPLETE under
PORTABLE_RESEARCH with 36 W1 offline tests, 4 required checks and native q
GATED_NOT_RUN (HISTORICAL release record). R8.4.3 / PR #67 is COMPLETE
under PORTABLE_RESEARCH with 46 W1 offline tests, 4 required checks and
native q GATED_NOT_RUN; PR #64 was an intermediate HISTORICAL step (runner
isolation, native_q_checker gate, skip enforcement, explicit W1 evidence
and hosted release assurance), not the final release.
The exact current digest and CI runs are in `MANIFEST.md` of the current
bundle (external; the current bundle is generated after R8.4.3).
PRs #47–#67 are all MERGED; the live PR inventory is external. Merges use
the single-developer policy: zero required GitHub approvals, a self-review
checklist and exact-head CI with 4 required checks. Independent
external technical acceptance is PENDING / NOT RECORDED. The older
`0b353f84…` SHA is the baseline snapshot retained as historical evidence,
not a current state claim.

The product release baseline stays R8.4.3 / PR #67; the latest source
revision PR #70 is recorded only as `latest_source_revision` and
`source_commit` in `governance/release-state.json`. It does not change the
release identity, gate set or acceptances. Native q remained GATED_NOT_RUN
with runner execution NOT_OBSERVED and attestation mechanism CHECKED.
Branch protection was recaptured at PR #70 (HISTORICAL point-in-time
capture, response digest in the CURRENT STATE block above and in
`governance/branch-protection-capture.json`). Audit evidence verification
remains PENDING until PR #71 merges. The post-baseline offline modules
(bitemporal registry, calendar/PIT, corporate actions, V3 and Data Socket
adapters) are source-level implementation only: they change no release
identity, gate set or acceptance, and are qualified nowhere in this
register.

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
| W1 Instrument master | W1-A/B/C implemented from pinned public Upstox and FYERS masters; W1-D bitemporal Observation registry and ADR IMPLEMENTED OFFLINE; live exchange status source absent | 46 offline tests at PR #67; 9,926 VERIFIED crosswalk matches, 174 UNMAPPED; four bundled pinned snapshots; registry as_of/resolve/provider-coverage and separated status/eligibility offline | Pending: W1-D live read-only consistency, exchange status and effective dating; no full W1 exit | W1-D live consistency; exchange status source |
| W2 Upstox V3 | PARTIAL: read-only V2 REST is the actual integration; V3 historical daily adapter IMPLEMENTED OFFLINE / NOT QUALIFIED (injected HTTP, mocked tests only) | Unit-tested corrections and offline adapter tests; no live V3 credential, raw payload archive or 10-session qualification | Pending: V3 provider qualification | W1; V3-capable token |
| W3 FYERS Data Socket | IMPLEMENTED MODULES / NOT QUALIFIED: read-only Data Socket adapter with injected transport, mocked tests only; General Socket excluded | Offline tests pass; no live credential, reconnect or replay evidence | Pending: live qualification | W1, fresh read-only token |
| W4 Provider journal | Not proven | Synthetic coverage only | Pending | W2, W3 |
| W5 calendar/PIT | PARTIAL: exchange-calendar and PIT-universe modules IMPLEMENTED OFFLINE; no real NSE calendar data | Offline contract tests with labelled SYNTHETIC fixtures | Pending: authoritative NSE calendar and PIT universe evidence | W1; real NSE calendar data |
| W6 corporate actions | IMPLEMENTED MODULES / NOT ACCEPTED: event ledger and versioned adjustment engine offline; no real action data | Offline tests with provenanced synthetic events | Pending: real action data and adjustment parity | W1; W5; real action data |
| W12 Durability | Partial | Process/crash evidence only | Pending | W4, fault rig |
| W16 Independent acceptance | Documented merge controls | Register present; exact-head CI evidence | NOT RECORDED (external) | defined high-risk release gates |

Every workstream row below inherits these three separate fields. A failed
upstream workstream blocks every dependent release decision; `Blocked by`
contains requirement IDs from the same register.

## Workstreams

| ID | Workstream | Current status | Dependencies | Exit evidence | Release blocking |
|---|---|---|---|---|---|
| W0 | Evidence-integrity registers | PARTIAL: four governance CSV registers tracked; no prior W0 definition in repository (UNVERIFIED PROVENANCE) | none | Independent reviewer disposition of the four registers | No for routine merges |
| W1 | Instrument master and multi-venue identity | PARTIAL / IMPLEMENTED MODULES: W1-A/B/C from pinned public Upstox/FYERS masters (9,926 VERIFIED / 174 UNMAPPED); 46 offline tests at PR #67 and four bundled snapshots; W1-D bitemporal Observation registry (as_of/resolve/provider coverage, separated status/eligibility) and ADR present; W1-D live read-only consistency and exchange status PENDING; no full W1 exit | none | Effective-dated registry, provider crosswalk report, per-class conformance matrix, exchange status source and live consistency tests | Yes for multi-asset |
| W2 | Upstox V3 data adapter | PARTIAL: read-only V2 REST is the actual integration; V3 historical daily adapter IMPLEMENTED OFFLINE / NOT QUALIFIED (injected HTTP, mocked tests only); no live V3 endpoint qualification | W1 | ISO timestamp parsing, V3 seven-field candles, ascending canonical history, raw payload archive, 10-session qualification | Yes for provider certification |
| W3 | FYERS Data Socket adapter | IMPLEMENTED MODULES / NOT QUALIFIED: read-only Data Socket adapter with injected transport and mocked tests only; no live credentials or feed certification; General Socket excluded | W1, fresh read-only token | REST quotes, history, Data Socket only, reconnect and replay evidence | Yes for dual-provider |
| W4 | Provider-to-journal-to-q path | NOT PROVEN with real events | W2, W3 | Raw and normalised events, durable journal, q replay parity | Yes |
| W5 | Exchange calendar and PIT universe | PARTIAL: `python/exchange_calendar.py` and `python/pit_universe.py` IMPLEMENTED OFFLINE; no real NSE calendar data | W1 | Authoritative NSE calendar, universe revisions, effective-dated aliases | Yes for research |
| W6 | Corporate actions | IMPLEMENTED MODULES / NOT ACCEPTED: `python/corporate_actions.py` append-only event ledger and versioned adjustment engine offline; no real action data | W1, W5 | Split, bonus, dividend, rights, merger and demerger events with provenance and adjustment parity | Yes |
| W7 | Research and statistical acceptance | INCONCLUSIVE / BLOCKED | W5, W6 | Nested validation, full trial registry, Reality Check/SPA or approved alternative, cost model, sealed holdout | Yes for strategy acceptance |
| W8 | Feature registry | PARTIAL | W5 | Versioned definitions, availability tests, C++/Python parity | No for data release; yes for strategy |
| W9 | Contract-note reconciliation | NOT QUALIFIED: reconciliation not implemented | W1, W6 | Independent charge recalculation; zero unexplained differences | Yes for financial gate |
| W10 | Operational paper OMS | NOT IMPLEMENTED | W2–W4, W9 | Full lifecycle, partial fills, cancels, restarts, 20-session qualification | Yes |
| W11 | Native q hosted check | GATED | governed licensed runner | Required workflow passing on q-changing PRs | Yes for persistence releases |
| W12 | Durability qualification | NOT PROVEN | W4 | Process, OS, power and storage fault matrix on named hardware; acknowledged event survival | Yes for operational release |
| W13 | Intraday support | FUTURE (intraday unsupported) | W2–W5, W11 | Intraday session conformance, auction and halt handling, intraday costs | Yes for intraday scope |
| W14 | HFT feasibility | FUTURE (separate project) | W13, low-latency infrastructure | Tick-by-tick conformance, clock sync, latency distribution | Yes for HFT scope |
| W15 | Multi-asset conformance | FUTURE (not established) | W1, W6, W9 | Signed per-class conformance matrix | Yes per class |
| W16 | Independent acceptance | PENDING (external, NOT RECORDED) | all relevant gates | Required merge controls: zero GitHub approvals, self-review checklist and exact-head CI. External technical review is optional. Independent acceptance is mandatory only for defined high-risk release gates | No for routine merges; yes for defined high-risk gates |
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
| W1 | PARTIAL / IMPLEMENTED MODULES: W1-A/B/C pinned public masters; bitemporal registry and ADR present; 46 offline tests and four bundled snapshots; W1-D live read-only consistency and exchange status PENDING | none |
| W2 | PARTIAL: V2 REST actual integration; V3 historical adapter IMPLEMENTED OFFLINE / NOT QUALIFIED | W1 |
| W3 | IMPLEMENTED MODULES / NOT QUALIFIED (Data Socket adapter, no live credentials) | W1, fresh read-only token |
| W4 | NOT PROVEN with real events | W2, W3 |
| W5 | PARTIAL: calendar/PIT modules IMPLEMENTED OFFLINE; no real NSE calendar data | W1 |
| W6 | IMPLEMENTED MODULES / NOT ACCEPTED: event ledger and adjustment engine; no real action data | W1, W5 |
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
| R8.4.2 | COMPLETE at PR #66 (HISTORICAL release record) | Runner isolation, native_q_checker gate, skip enforcement, explicit W1 evidence and hosted release assurance; 36 W1 tests, 4 required checks, native q GATED_NOT_RUN; intermediate PR #64 had no exit claim |
| R8.4.3 | COMPLETE at PR #67 (CURRENT release record) | R8.4.3 release-state refresh: 46 W1 tests, 141 release-bundle tests, 171 verify-report tests, 14 CI security tests, 4 required checks, PORTABLE_RESEARCH and native q GATED_NOT_RUN |

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
endpoint call from the C++ integration. V3 seven-field candle parsing is
schema normalisation of fixtures only. The post-baseline `python/upstox_v3.py`
read-only V3 daily adapter is implemented offline with injected HTTP and
mocked tests only; actual V3 endpoint qualification, raw payload archive
and 10-session certification are NOT achieved. Streaming is NOT IMPLEMENTED.
FYERS live integration remains a profile probe against
`https://api-t1.fyers.in/api/v3`; `python/fyers_socket.py` implements a
read-only Data Socket adapter offline with an injected transport and mocked
tests only, with no live credential or certification.

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

R8.4.2 / PR #66 is COMPLETE with 36 W1 tests, 4 required checks and
native q GATED_NOT_RUN; PR #64 was an intermediate HISTORICAL step with no
exit claim. R8.4.3 / PR #67 is the CURRENT release: COMPLETE under
PORTABLE_RESEARCH with 46 W1 tests, 141 release-bundle tests, 171
verify-report tests, 14 CI security tests, 4 required checks and native q
GATED_NOT_RUN. Independent acceptance remains PENDING. The later source
revision PR #70 is not a product release: runner execution NOT_OBSERVED,
attestation mechanism CHECKED and audit evidence verification PENDING until
PR #71 merges.

## Current source modules (offline, qualification pending)

These modules are present in the current working source after the release
baseline. They are offline Python code with injected I/O and mocked tests;
they are not a product release and do not change the R8.4.3 / PR #67
baseline or the PR #70 latest-source distinction.

- `python/instrument_master.py` adds an append-only bitemporal Observation
  registry with `as_of`, `resolve`, canonical-ID/provider-alias resolution
  and provider coverage. Identity, conformance, provider trading status,
  exchange status, exchange eligibility, data freshness and eligibility are
  separate and fail closed. The design record is
  `governance/w1d-architecture.md` (AUTHORED, not acceptance).
- `python/exchange_calendar.py` and `python/pit_universe.py` define a
  versioned session/halt contract and a point-in-time universe from
  published status history. All values are caller-supplied; no real NSE
  calendar data is bundled and fixtures are labelled SYNTHETIC.
- `python/corporate_actions.py` provides an append-only event ledger and
  versioned adjusted series. No real corporate-action data is supplied and
  no adjustment is accepted.
- `python/upstox_v3.py` is a read-only V3 historical daily adapter with
  injected HTTP and network disabled by default; `python/fyers_socket.py`
  is a read-only Data Socket adapter with an injected transport and the
  General Socket explicitly forbidden. Both are NOT QUALIFIED.

No live provider qualification, exchange status source, real NSE calendar
or corporate-action data, research acceptance or full W1 exit is claimed.
No integration into the C++ authoritative core, q tables or research keys
is claimed; these modules write no journal and authorise no order.

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
