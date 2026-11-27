# Current architecture — R8 target versus implemented state (2026-11-27)

This document maps the target logical architecture of the R8 directive onto
the code at the PR #61 merge (release history, R8.3 + W1 A/B/C), including
the R8.1 corrections for exact-integer open interest and volume and the
wall-clock journal replay field. The PR #58 main SHA
`1a91da2fb08995c9f6ee20e373d004ad28e58a72` and the PR #61 merge SHA
`201f5ba602f68fc1370579cefa50b8b599af05d5` are HISTORICAL values; the
authoritative current main SHA, CI runs and bundle digest are recorded in
the final release bundle `MANIFEST.md` (external artifact). Statuses describe
implementation evidence only: they do not mean qualified, certified or
accepted.

Legend: `IMPLEMENTED` means code exists for the stated scope; `PARTIAL` means
a bounded model or prototype exists but not the full target contract;
`NOT IMPLEMENTED` means no code path exists; `GATED` means a guarded tier is
not run. `PENDING` and `NOT PROVEN` describe missing evidence, not closure.

## 1. Target components and truthful current status

| Component | Target role (R8) | Current status | Source evidence / limitation |
|---|---|---|---|
| Control plane | Configuration, CLI orchestration, mode gate | PARTIAL | `src/main.cpp:1331` (doctor), `src/main.cpp:1340` (data sync), `src/main.cpp:1352` (research), `src/main.cpp:1360` (paper start); `include/quant.hpp:277`, `src/engine.cpp:484`. Commands exist; paper lifecycle is not operational. |
| Market data | Provider ingestion and normalisation | PARTIAL | `src/market_data.cpp:23` (Upstox V2 base), `src/market_data.cpp:26` (FYERS base), `src/market_data.cpp:686` (ISO parser), `src/market_data.cpp:805` (quote normalisation), `src/market_data.cpp:1043` (universe sync), `src/market_data.cpp:1429` (quotes). Read-only V2 REST only; no V3 endpoint call; no streaming; FYERS is a profile probe. R8.3 added explicit `volume_validity` and fail-closed legacy replay as `UNKNOWN`. |
| Canonical instrument master | Effective-dated identity across venues and classes | PARTIAL | `include/quant.hpp:334` (AssetClass), `include/quant.hpp:343` (InstrumentSpec), `include/quant.hpp:366` (ReferenceInstrument), `include/quant.hpp:392` (InstrumentClass), `include/quant.hpp:403` (Instrument); `src/engine.cpp:994`, `src/engine.cpp:1043`; `q/quant.q:45`. W1-A/B/C add an offline crosswalk (9,926 VERIFIED / 174 UNMAPPED) from pinned public Upstox and FYERS masters in `governance/instrument-master-snapshots.json`; effective dating (W1-D) and per-class conformance are PENDING. |
| Validation | Fail-closed contracts at every boundary | PARTIAL | `src/engine.cpp:484` (config), `src/engine.cpp:1107` (bar), `src/engine.cpp:1372` (fill), `src/engine.cpp:4495` (event); `src/dataset.cpp:301`, `src/dataset.cpp:566`; `python/dataset_contract.py:409`; `src/market_data.cpp:686`. R8.3 enforces one decimal count grammar and an explicit volume-validity state in both canonical readers; cross-language boundary fixtures and real-provider qualification are still PENDING. |
| Durable journal | Append-only, checksummed recovery and audit source | PARTIAL | `include/quant.hpp:1058` (Journal), `include/quant.hpp:1069` (health), `src/engine.cpp:4529` (Open validates via Replay), `src/engine.cpp:4649` (Append with fsync), `src/engine.cpp:4739` (Replay), `src/engine.cpp:1146` (pipeline writes journal). Power-loss durability NOT PROVEN; process/crash evidence only. |
| q storage | High-volume time-series analytics and generated replay state | PARTIAL | `q/quant.q:427` (persist), `q/quant.q:517` (load), `q/quant.q:532` (recover), `q/quant.q:29` (quotes); `src/kdbx.cpp:205`, `src/kdbx.cpp:259`, `src/kdbx.cpp:351`; `include/quant.hpp:1531`. R8.3 makes `KdbxClient` reject unverified volume before q publication. Local C API not compiled (libe.so missing); hosted native-q job GATED / NOT RUN (skipped at PR #61); q is not the durability authority. |
| Research | Statistics, multiple-testing diagnostics, sealed holdout | PARTIAL | `python/research.py:4` (research-only scope), `python/research.py:1048` (holdout split), `python/research.py:1086` (purged walk-forward), `python/research.py:1307` (nested validation), `python/research.py:1517` (delegation to C++), `python/research.py:1893`. Results INCONCLUSIVE/negative; holdout SEALED_NOT_EVALUATED. |
| Strategy | Signal definition and simulated portfolio construction | PARTIAL | `include/quant.hpp:471` (Signal), `include/quant.hpp:959` (metrics), `src/engine.cpp:4063` (RunBacktest), `src/engine.cpp:4032` (conservative fill), `python/research.py:500`, `python/research.py:511`, `python/research.py:516`. Daily simulated backtest only; no accepted strategy; decision NO TRADE. |
| Independent risk | Authorisation capability isolated from order clients | PARTIAL | `include/quant.hpp:768` (RiskAuthorization), `include/quant.hpp:915` (RiskEngine), `src/engine.cpp:3576`, `src/engine.cpp:3767` (Verify), `src/engine.cpp:3798` (market-data binding), `src/engine.cpp:2708` (state recovery). In-process capability only; not an independent human reviewer; persistence across restart NOT IMPLEMENTED (QG-003). |
| Paper OMS | Event-sourced simulated order lifecycle | PARTIAL | `include/quant.hpp:1156` (states), `include/quant.hpp:1182` (PaperOrder), `include/quant.hpp:1215` (PaperBroker); `src/engine.cpp:1559` (transitions), `src/engine.cpp:1652` (risk approval), `src/engine.cpp:1986` (journal rehydration); `src/main.cpp:1360`. Model and replay exist; CLI lifecycle not operational (W10). |
| Restricted gateway | Controlled, constrained broker-side order route | PARTIAL | `include/quant.hpp:1584` (ZerodhaGateway), `src/zerodha.cpp:125` (read-only probe), `src/zerodha.cpp:171` (Submit returns `LOCKED`). Submission is hard-locked; no broker-server-hosted or restricted network gateway is deployed. |
| Broker | Broker/provider connectivity and account interface | PARTIAL | `src/zerodha.cpp:125` (read-only profile probe), `src/zerodha.cpp:171` (no submission), `src/market_data.cpp:23` and `src/market_data.cpp:26` (read-only provider adapters). No Kite session token; FYERS token expired; Upstox analytics token read-only. |
| Reconciliation | Accounting, replay and q-state parity checks | PARTIAL | `src/engine.cpp:1870` (paper reconcile), `src/engine.cpp:2211` (posting digest), `src/engine.cpp:2272` (rebuild from journal), `src/engine.cpp:2398` (apply posting), `src/engine.cpp:955` (q recovery plan), `q/quant.q:532`. Internal reconciliation exists; contract-note reconciliation with broker statements NOT IMPLEMENTED (W9). |
| Monitoring | Health, status and diagnostic visibility | PARTIAL | `src/main.cpp:1331` (doctor), `src/main.cpp:1333` (validate), `src/main.cpp:1337` (brokers status), `q/quant.q:144` (counts), `q/quant.q:149` (state), `scripts/assurance.py`. Diagnostics exist; no operational alerting or independent acceptance. |

No component in the table is marked `IMPLEMENTED` against the full R8 target.
Every `PARTIAL` scope is bounded as stated and is not a claim of closure.

### R8.3 and W1 A/B/C at PR #61 (release history)

At PR #61 the R8.3 + W1 A/B/C work was MERGED. W1 A/B/C consume only
captured bytes from the pinned public Upstox NSE and FYERS NSE_CM masters
described in `governance/instrument-master-snapshots.json`; parsing and the
crosswalk are offline. W1 pytest reported 11 tests on the captured bytes
and 9,926 VERIFIED / 174 UNMAPPED identities. W1-D live consistency,
effective dating and per-class conformance are PENDING; there is no full
W1 exit. The R8.3 data-path closures are explicit `Quote::volume_validity`
with legacy replay as `UNKNOWN`, one decimal count grammar in both canonical
CSV readers, and a `KdbxClient` guard that rejects unverified volume before
q publication. Release tooling closes manifest totals against the embedded
verification report, fails closed on a missing provenance report and uses a
portable state-freshness self-test. At PR #61 hosted CI succeeded with
native q skipped/GATED_NOT_RUN; the merge SHA, CI run and bundle digest are
HISTORICAL values at PR #61 and current identifiers live in `MANIFEST.md`.
R8.4 — native-q conclusion mapping, remote provenance and W1 snapshot
reproducibility — is IN PROGRESS.

## 2. C++ / Python / q authority separation as implemented

### C++20 — authoritative core

Money is integer-paise arithmetic in `Money` (`include/quant.hpp:121`), with
checked add/subtract in `src/engine.cpp:281` and `src/engine.cpp:294`.
Costs are computed by `CostAggregator` (`include/quant.hpp:571`,
`src/engine.cpp:768`). Portfolio invariants are enforced in
`Portfolio::ValidateInvariants` (`src/engine.cpp:2882`). Risk authority is the
opaque `RiskAuthorization` capability (`include/quant.hpp:768`), creatable
only by `RiskEngine` (`src/engine.cpp:3576`) and verified against the order,
market-data snapshot and daily state (`src/engine.cpp:3767`,
`src/engine.cpp:3798`, `src/engine.cpp:3815`). The durable journal is written
only through C++ (`src/engine.cpp:4649`). Live submission is locked in C++
(`src/zerodha.cpp:171`).

### Python — research client only

`python/research.py:4` states the implemented boundary: research computes
statistics and delegates authoritative simulated P&L to the C++ binary via
`cpp_authoritative_backtest` (`python/research.py:1517`). Python reads q via
PyKX (`python/research.py:1462`) and reads canonical CSV manifests through
`python/dataset_contract.py`. There is no order-submission, risk-approval or
journal-write code path in `python/`. Research outputs are reports whose
current status is INCONCLUSIVE/negative; they cannot authorise an order.

### q — analytical store, not durability authority

`q/quant.q` owns canonical analytical tables (`q/quant.q:29` quotes,
`q/quant.q:45` instruments), idempotent ingest, q-native features, as-of
joins and HDB persist/load (`q/quant.q:427`, `q/quant.q:532`). C++ publishes
typed events to q through `KdbxClient` (`src/kdbx.cpp:259`,
`src/kdbx.cpp:297`). R8.3 makes that client reject a quote whose
`volume_validity` is not `VALID`: no volume-dependent q table or feature
may consume an unverified or legacy-substituted zero. The journal write
happens before publication in `MarketDataPipeline::IngestQuote`
(`src/engine.cpp:1146`); `Publish` (`src/engine.cpp:1213`) is the
downstream q step. A q IPC response is not a zero-loss guarantee.

## 3. Explicit boundaries

- C++ is authoritative for money, costs, accounting, risk and order
  decisions. No other layer may authorise an order.
- Python research cannot authorise orders; it can only compute diagnostics
  and request authoritative simulation from C++ (`python/research.py:4`,
  `python/research.py:1517`).
- q analytics is not the only copy of financial events. The durable journal
  is the recovery and audit source (`src/engine.cpp:4739`,
  `src/engine.cpp:2272`); q state is derived and replayable
  (`q/quant.q:532`, `src/engine.cpp:955`).
- The journal is the recovery source: `Journal::Open` validates history
  before append (`src/engine.cpp:4529`) and `Append` defines the durability
  boundary as a successful write and fsync (`src/engine.cpp:4649`). Power
  loss is NOT PROVEN.
- The risk capability is in-process. A shared lock is not distributed
  authorization and is not protection against hostile code in another
  process (`docs/master-acceptance-register.md`, PR #48 traceability).
- CI evidence is an automated gate, not human independent review. Required
  merge controls are zero GitHub approvals, the self-review checklist and
  exact-head CI; independent external acceptance is PENDING / NOT RECORDED.

## 4. Dependencies and blocked-by relationships

| Dependency edge | Current effect |
|---|---|
| W1 instrument master before provider qualification (W2, W3) | W1 is PARTIAL: W1-A/B/C map pinned public Upstox and FYERS masters offline (9,926 VERIFIED / 174 UNMAPPED); W1-D live consistency and effective dating are PENDING. W2 is PARTIAL with no V3 endpoint call and W3 is NOT IMPLEMENTED, so no provider is qualified. |
| W2 and W3 before W4 provider-to-journal-to-q path | W4 is NOT PROVEN with real events; only synthetic coverage exists. |
| W4 before W10 operational paper trading | W10 is NOT IMPLEMENTED; the paper OMS has a model and replay but no operational lifecycle. |
| W5 and W6 before W7 strategy acceptance | W5 is PARTIAL and W6 is NOT IMPLEMENTED; W7 is INCONCLUSIVE/BLOCKED and the holdout is SEALED_NOT_EVALUATED. |
| W4 plus a fault rig before W12 durability qualification | W12 is NOT PROVEN; power-loss testing is outstanding. |
| W11 hosted native q before persistence releases | GATED / NOT RUN on a governed licensed runner; local native q cannot run on this Mac. |
| W7, W10, W12, W16 and compliance before W17 live route | W17 remains DISABLED; live execution must not be enabled by this baseline. |
| All relevant gates before W16 independent acceptance | PENDING / NOT RECORDED; no independent external acceptance exists. |
| This W0 documentation baseline before any later workstream | Documentation-only deliverables; they unblock no implementation, qualification or release gate. |

## 5. Current operating state

Consistent with `docs/master-acceptance-register.md` and
`docs/release-status.md`: research INCONCLUSIVE/negative; holdout
SEALED_NOT_EVALUATED; Upstox read-only V2 REST with no V3 endpoint call;
V3 limited to CSV fixture-schema normalisation; FYERS profile probe only;
no streaming; W1-A/B/C offline public-master evidence with W1-D effective
dating PENDING; paper OMS not operational; power-loss durability NOT PROVEN;
intraday/HFT UNSUPPORTED; multi-asset NOT ESTABLISHED; live execution
DISABLED; decision NO TRADE. The R7 remediations, the R8.1 corrections,
the R8.3/W1 A/B/C work and the reorganized governance records are included.
At PR #58 (HISTORICAL) hosted CI runs `45969369813` and `45969624206`
passed; at PR #61 (HISTORICAL) hosted CI run `46007348694` passed with
native-q skipped/GATED_NOT_RUN. Current main SHA, CI runs and bundle digest
are recorded in the final release bundle `MANIFEST.md` (external artifact).
