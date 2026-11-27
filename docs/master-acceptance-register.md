# QUANT master requirements and acceptance register

This is the authoritative definition of "complete" for the platform
programme. A capability is complete only when requirement, implementation,
tests, failure behaviour, operational evidence and independent acceptance
agree. Green CI alone is not acceptance.

Statuses: `NOT IMPLEMENTED`, `IMPLEMENTED`, `UNIT-TESTED`,
`INTEGRATION-TESTED`, `FAULT-INJECTED`, `CRASH-TESTED`,
`INDEPENDENTLY QUALIFIED`, `INDEPENDENTLY ACCEPTED`, `BLOCKED`.

## CURRENT STATE (authoritative)

Machine-readable release, governance and trading state at the current head:

```yaml
main_sha: 0b353f84f877764066eeb3dc0f70ac4ef645bf50
remediation_prs: "47-53"
remediation_prs_status: MERGED
final_release_record_pr: 54
final_release_record_pr_status: MERGED
documentation_inventory_pr: 55
documentation_inventory_pr_status: MERGED
governance_model: SINGLE_DEVELOPER
required_github_approvals: 0
self_review_checklist: REQUIRED
exact_head_ci: REQUIRED
independent_external_acceptance: PENDING
research: INCONCLUSIVE
holdout: SEALED_NOT_EVALUATED
live_execution: DISABLED
decision: NO_TRADE
```

## Release state reconciliation

The authoritative state is the CURRENT STATE block above and the ACTIVE POLICY
record in `docs/release-status.md`. `main` is at
`0b353f84f877764066eeb3dc0f70ac4ef645bf50`. Remediation PRs #47–#53, final
release record PR #54 and documentation inventory PR #55 are all MERGED; zero
PRs are open. Merges use the single-developer policy: zero required GitHub
approvals, a self-review checklist and exact-head CI with three required
checks. Independent external technical acceptance is PENDING / NOT RECORDED.

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
| W1 Instrument master | Not implemented | Not tested | Pending | none |
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
| W1 | Instrument master and multi-venue identity | NOT IMPLEMENTED beyond NSE cash-equity model | none | Effective-dated instrument registry; per-class conformance matrix | Yes for multi-asset |
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

CI evidence is an automated gate, not a human independent review. Required
merge controls are zero GitHub approvals, the self-review checklist and
exact-head CI. External technical review is optional; independent acceptance
is mandatory only for defined high-risk release gates, not every merge.

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
