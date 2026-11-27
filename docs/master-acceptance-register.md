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
of the current final release bundle (external artifact); values marked as
release history at PR #58 are not immutable current identifiers.

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
  open_prs: 0
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
HISTORICAL. PRs #47–#58 are all MERGED; zero PRs are open. Merges use the
single-developer policy: zero required GitHub approvals, a self-review
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
| W1 Instrument master | W1-A/B/C implemented from public Upstox and FYERS masters; full effective-dated registry still pending | 11 offline tests on captured bytes; 9,926 verified crosswalk matches, 174 unmapped | Pending | W1-D live consistency |
| W2 Upstox V3 | Partial (V2 REST; V3 fixture-schema normalisation only) | Unit-tested corrections | Pending | W1 |
| W3 FYERS Data Socket | Not implemented | Not tested | Pending | W1, valid token |
| W4 Provider journal | Not proven | Synthetic coverage only | Pending | W2, W3 |
| W12 Durability | Partial | Process/crash evidence only | Pending | W4, fault rig |
| W16 Independent acceptance | Documented merge controls | Register present; exact-head CI evidence | NOT RECORDED externally | defined high-risk release gates |

Every workstream row below inherits these three separate fields. A failed
upstream workstream blocks every dependent release decision; `Blocked by`
contains requirement IDs from the same register.

## Workstreams

| ID | Workstream | Current status | Dependencies | Exit evidence | Release blocking |
|---|---|---|---|---|---|
| W1 | Instrument master and multi-venue identity | PARTIAL: W1-A/B/C implemented from pinned public masters; W1-D pending | none | Effective-dated registry, provider crosswalk report, per-class conformance matrix and live consistency tests | Yes for multi-asset |
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
| W18 | Independent release assurance and security remediation | PENDING; authoritative owner is the independent reviewer | W16 and all defined high-risk release gates | This row is the authoritative W18 definition. Exit evidence: disposition of each security finding by an independent reviewer and recorded external acceptance. Local checks and CI are not acceptance; acceptance NOT RECORDED | Yes for release acceptance |

CI evidence is an automated gate, not a human independent review. Required
merge controls are zero GitHub approvals, the self-review checklist and
exact-head CI. External technical review is optional; independent acceptance
is mandatory only for defined high-risk release gates, not every merge.

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

## Data-only testing invariants

For provider tests: `LIVE_TRADING_ENABLED=false`, `EXECUTION_MODE=DISABLED`,
no Zerodha session token in the collector, no execution gateway instantiated,
read-only provider credentials, provider market-data endpoints only. The
Zerodha submission lock must remain blocked in a negative test.

## Prohibited claims

No claim of strategy profitability, evaluated holdout, provider streaming
certification, operational paper trading, fully proven universal power-loss
durability, HFT suitability, multi-asset conformance, independent acceptance,
live execution authorisation or `READY FOR TRADE` is permitted until the
corresponding exit evidence above is recorded.
