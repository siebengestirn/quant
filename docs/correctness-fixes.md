# QUANT correctness sprint — P0 fixes (2026-11-25)

Branch: `correctness/gate0`. Baseline:
`docs/correctness-baseline.md`.

| ID | Finding | Fix | Verification | State |
|---|---|---|---|---|
| F1 | Backtest used initial-capital sizing and a private 85% cash cap | Added `SizePosition` in C++ as the single allocator. It uses current equity, reserve, drawdown scaling, exposure, depth participation and explicit entry costs. Risk engine and backtester both call it. | `TestDynamicSizing`: larger equity sizes more, drawdown reduces size, insufficient cash returns NO TRADE. | TESTED (independent review pending) |
| F2 | Stop crossed but filled at open; union-date execution alignment | Added `ConservativeStopFill` (gap-down fills at open; otherwise stop) and `NextSessionIndex` using the instrument's own sessions. | `TestExecutionSemantics` covers stop-cross with recovery, gap-down and weekend gaps. | TESTED (independent review pending) |
| F3 | Provider update and last-trade timestamps conflated | `Quote` now carries provider update, last-trade and exchange event fields separately. Upstox `timestamp` and `last_trade_time` are parsed independently; freshness uses provider update, with `IsStaleTrade` for trade-specific rules. | Source inspection and unit coverage via `IsStale` path. | TESTED (review pending) |
| F4 | Journal append not durable; open did not validate history | `Open` validates the full journal via `Replay` before any append. `Append` writes with `write()` and `fsync()`; the durability boundary is the successful fsync. Incomplete trailing records are explicit errors. | Journal tests reject corrupted history and truncated tail; replay remains checksum/sequence validated. | TESTED (independent review pending) |
| F5 | q server unauthenticated; tests reset production state | Verification/benchmark now spawn an isolated q process on port+1 with a separate HDB dir, then terminate it by verified PID. `db stop` verifies the PID's command line contains the configured script and port before signalling. | Production table counts unchanged before/after `db verify` and `db benchmark`. | TESTED (partial, review pending) |
| F6 | `cost_to_gross` omitted slippage; duplicate net-P&L update | Ledger tracks slippage; `cost_to_gross` includes it; the redundant entry-time net-P&L mutation was removed. | C++ tests pass; backtest report regenerated. | TESTED (review pending) |
| F7 | q as-of join joined quotes to themselves | `.quant.asofFeatures` now builds a typed feature table, timestamps daily features at the next session boundary, and performs a real `aj` on `symbol`,`ts`. | Direct q fixture and pytest `test_q_feature_math_matches_fixture`. | TESTED (independent review pending) |
| F8 | q returns used moving averages; "VWAP" was typical-price average | Returns use `P_t/P_(t-n)-1`; realized volatility uses daily return stdev; VWAP uses price × volume weighted average. | q fixture: 20-session return = 0.2 for 100→120. | TESTED (independent review pending) |
| F9 | 64-bit sequence/timestamps passed through JSON doubles | C++→q ingest now uses typed KX IPC (`kj` for sequence/ns, `kf` for prices). JSON functions retained only for diagnostics. | Isolated q round-trip and PyKX reads pass. | TESTED (review pending) |
| F10 | Self-test reset production q state | `db verify` and `db benchmark` use an isolated q process; production q is untouched. | Production counts stable across both commands. | TESTED (independent review pending) |

## Remaining blockers

- - **NOT IMPLEMENTED:** q server-side IPC authentication. The supported `-u`
  mechanism was not verified on this runtime; do not treat loopback binding as
  authorization. Write functions remain callable by any local process.
- - **NOT IMPLEMENTED:** date-partitioned HDB with transactional checkpoints.
  `.quant.persist` still writes whole-table snapshots.
- - **PARTIAL:** the composite provider/sequence idempotency key is persisted in
  HDB snapshots, but is not automatically restored into `.quant.seen` after a q
  restart.
- **NOT IMPLEMENTED:** true CSCV/PBO, White Reality Check, Hansen SPA.
- - **NOT IMPLEMENTED:** operational FYERS data adapter; only the FYERS profile
  probe exists and its token is expired.
- - **NOT IMPLEMENTED:** point-in-time universe, corporate actions and
  historical surveillance data.
- - **NOT IMPLEMENTED:** bounded async queue and batched typed IPC publishing.
  Current benchmark is synchronous and honestly labelled.
- - **BLOCKED:** live execution regulatory verification and owner authorization;
  live trading remains locked.

## Gate status

GATE 0 baseline: TESTED. GATE 1 financial correctness and GATE 2 backtest
correctness: independently verified for the listed items. GATE 3 journal
durability: fsync/validation tested; power-loss testing remains outstanding.
GATE 4 q correctness: listed items verified; authentication and HDB partitioning
remain open. No later gate is claimed.

## Independent-review remediation (QG-001 and P0 follow-up)

The independent review did not accept the correctness gates. Implementing-agent
status is
TESTED throughout this section. Only an independent reviewer may mark an item
INDEPENDENTLY_VERIFIED or ACCEPTED.

| Task | Finding | Implementation | Status |
|---|---|---|---|
| QG-001 | Python fixture accepted an existing q server, reset it and stopped an unowned process; C++ test port was fixed and ownership unproven | Fixture now starts its own q process on an OS-assigned port with a unique instance id, temporary directory and identity handshake. C++ `db verify`/`benchmark` use the same contract and delete only their own temp dir. Production endpoint is never contacted by tests. | TESTED (review pending) |
| QG-002 | Buy sizing used bid depth/LTP, reserve was not preserved, exposure and stop distance were ignored | `SizingInput` is side/price/depth/stop/exposure aware; reserve is enforced after costs; tick validation added. Risk engine uses ask price and ask depth. | TESTED (review pending) |
| QG-003 | Daily loss limit was not enforced | `DailyRiskState` and shared `RiskEngine` state establish a session baseline, latch a kill switch on breach, and reset on a new UTC date. Persistence across process restart remains open. | TESTED (review pending) |
| QG-004 | Indices could enter equity universe; surveillance failure fail-open | Canonical `InstrumentClass` added; non-equity classes rejected; missing surveillance status now fails closed as `INELIGIBLE_PENDING_VERIFICATION`; traded-value filter enforced in market report. | TESTED (review pending) |
| QG-005 | Backtest used execution-session total volume, leaking future information | `PriorAverageVolume` uses only sessions strictly before the execution index. Tests verify execution-day volume cannot change the earlier decision input. | TESTED (review pending) |
| QG-006 | Multi-session strategy used intraday charges; terminal position not liquidated | `BACKTEST_DELIVERY` and `TERMINAL_LIQUIDATION` config; delivery costs used for entry/exit; terminal liquidation optional and reported. | TESTED (review pending) |
| QG-007 | Provider freshness fallback and monotonic timestamp conversion | Missing provider time is UNKNOWN and rejected by risk; trade freshness separate; monotonic receive time converted to ns for q; source identity preserved from the event instead of hardcoded. | TESTED (review pending) |
| QG-008 | Journal failure state, writer lock, checksum identity, permissions | Failed write/sync marks journal unhealthy; exclusive `flock` writer lock; 0600 file and lock; checksum includes journal sequence; EINTR handled; explicit `Close`. | TESTED (review pending) |
| QG-009 | q vol20 was full-history deviation; VWAP proxy mislabelled | One point-in-time feature contract now used by q and Python: `P_t/P_(t-n)-1`, rolling 20-session population deviation, daily volume-weighted typical-price proxy named explicitly. | TESTED (review pending) |
| QG-010 | Latest-only feature table joined to historical quotes | `.quant.featureHistory` generates one row per instrument/session using only prior bars; availability is the next bar in that instrument's own series; `.quant.asofFeatures` joins quotes to that history with `symbol`,`featureTs`. | TESTED (review pending) |
| QG-011 | Historical bars not uniquely keyed; recovery returned data without restoring state | `bars` keyed by `(date,symbol)`; duplicate bar ingestion verified; `.quant.recover` restores tables, seen keys and committed state and publishes a watermark. | TESTED (review pending) |
| QG-012..016 | Real pipeline, q security, point-in-time universe, statistics, audit lifecycle | NOT IMPLEMENTED. | BLOCKED |

No gate is accepted. PR #1 was later merged into `main`; acceptance is a
separate independent decision.

## QG addendum — updated R8.1 (2026-11-27, PR #57/#58)

Status note (updated R8.1): QG-007a (freshness) remains as recorded above.
QG-007b (exact-integer OI/volume boundary fixtures at 2^53) is IMPLEMENTED
and MERGED in PR #57, independently unreviewed; large-timestamp fixtures
remain NOT IMPLEMENTED. No independent acceptance is claimed.

- QG-003 persistence of daily risk state across restart: NOT IMPLEMENTED.
- QG-004 research/execution universe separation: PARTIAL; execution eligibility
  still lacks price bands and full historical status data.
- QG-007 cross-language boundary fixtures at 2^53: exact-integer open
  interest and integral volume parity is IMPLEMENTED locally (C++/Python
  parity above 2^53; 9 portable dataset-contract pytest passes).
  Large-timestamp cross-language fixtures remain NOT IMPLEMENTED.
- QG-008 process-kill and power-loss tests: NOT IMPLEMENTED; existing tests
  cover parsing, lock, sequence and checksum behaviour only.
- Journal checksum identity changed to include the sequence. The pre-change
  generated journal was preserved as
  `data/journal/events.jnl.legacy-checksum-v1` and a new journal started.
- QG-003 daily state persistence across process restart: NOT IMPLEMENTED.
- QG-007 exact-integer OI/volume boundary fixtures at 2^53: IMPLEMENTED
  locally. Large-timestamp cross-language fixtures: NOT IMPLEMENTED.
- QG-012..016: unchanged blockers.
