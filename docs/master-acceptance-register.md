# QUANT master requirements and acceptance register

This is the authoritative definition of "complete" for the platform
programme. A capability is complete only when requirement, implementation,
tests, failure behaviour, operational evidence and independent acceptance
agree. Green CI alone is not acceptance.

Statuses: `NOT IMPLEMENTED`, `IMPLEMENTED`, `UNIT-TESTED`,
`INTEGRATION-TESTED`, `FAULT-INJECTED`, `CRASH-TESTED`,
`INDEPENDENTLY QUALIFIED`, `INDEPENDENTLY ACCEPTED`, `BLOCKED`.

## Release state reconciliation

The earlier `docs/release-status-2026-11-26.md` describes the state at PR #46
(merged PRs #1–#46, no open PRs on `main`). The current governance state is
seven open PRs #47–#53 on `main` at `953dac24…`, all with successful
exact-head hosted CI and no independent approval recorded. The earlier file
remains historical evidence; this register governs the present programme.

## PR #47–#53 traceability

| PR | Scope | Amended | Independent acceptance |
|---|---|---|---|
| #47 | Dataset security | Yes — no-follow descriptor reads and exact raw digest grammar | Pending |
| #48 | Risk authorization | Yes — full identity, venue and data binding | Pending |
| #49 | OMS replay | Yes — recoverable AMBIGUOUS state | Pending |
| #50 | Publication filesystem | Yes — no-follow descriptor recovery | Pending |
| #51 | KDB-X recovery | Yes — argv-safe helper, secure temp, host-aware lock | Pending |
| #52 | Research report scope | No amendment required; reviewer disposition was "retain" | Pending |
| #53 | Release truth | Yes — machine-readable policy state reconciled | Pending |

The in-process capability boundary of #48 remains an explicit limitation.
A shared lock is not distributed authorization and is not protection against
hostile code in another process.

## Workstreams

| ID | Workstream | Current status | Dependencies | Exit evidence | Release blocking |
|---|---|---|---|---|---|
| W1 | Instrument master and multi-venue identity | NOT IMPLEMENTED beyond NSE cash-equity model | none | Effective-dated instrument registry; per-class conformance matrix | Yes for multi-asset |
| W2 | Upstox V3 data adapter | PARTIAL: V2 read-only REST and history | W1 | ISO timestamp parsing, V3 seven-field candles, ascending canonical history, raw payload archive, 10-session qualification | Yes for provider certification |
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
| W16 | Independent acceptance | NOT RECORDED | all relevant gates | Non-author review recorded per PR and release | Yes for every merge |
| W17 | Live execution authorisation | DISABLED | W7, W10, W12, W16, compliance | Written broker and compliance approval; separate release decision | Yes for any live route |

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
