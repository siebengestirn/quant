# QUANT correctness sprint — P0 fixes (2026-11-25)

Branch: `correctness/gate0`. Baseline: `docs/correctness-baseline-2026-11-25.md`.

| ID | Finding | Fix | Verification | State |
|---|---|---|---|---|
| F1 | Backtest used initial-capital sizing and a private 85% cash cap | Added `SizePosition` in C++ as the single allocator. It uses current equity, reserve, drawdown scaling, exposure, depth participation and explicit entry costs. Risk engine and backtester both call it. | `TestDynamicSizing`: larger equity sizes more, drawdown reduces size, insufficient cash returns NO TRADE. | INDEPENDENTLY_VERIFIED |
| F2 | Stop crossed but filled at open; union-date execution alignment | Added `ConservativeStopFill` (gap-down fills at open; otherwise stop) and `NextSessionIndex` using the instrument's own sessions. | `TestExecutionSemantics` covers stop-cross with recovery, gap-down and weekend gaps. | INDEPENDENTLY_VERIFIED |
| F3 | Provider update and last-trade timestamps conflated | `Quote` now carries provider update, last-trade and exchange event fields separately. Upstox `timestamp` and `last_trade_time` are parsed independently; freshness uses provider update, with `IsStaleTrade` for trade-specific rules. | Source inspection and unit coverage via `IsStale` path. | TESTED |
| F4 | Journal append not durable; open did not validate history | `Open` validates the full journal via `Replay` before any append. `Append` writes with `write()` and `fsync()`; the durability boundary is the successful fsync. Incomplete trailing records are explicit errors. | Journal tests reject corrupted history and truncated tail; replay remains checksum/sequence validated. | INDEPENDENTLY_VERIFIED |
| F5 | q server unauthenticated; tests reset production state | Verification/benchmark now spawn an isolated q process on port+1 with a separate HDB dir, then terminate it by verified PID. `db stop` verifies the PID's command line contains the configured script and port before signalling. | Production table counts unchanged before/after `db verify` and `db benchmark`. | TESTED (partial) |
| F6 | `cost_to_gross` omitted slippage; duplicate net-P&L update | Ledger tracks slippage; `cost_to_gross` includes it; the redundant entry-time net-P&L mutation was removed. | C++ tests pass; backtest report regenerated. | TESTED |
| F7 | q as-of join joined quotes to themselves | `.quant.asofFeatures` now builds a typed feature table, timestamps daily features at the next session boundary, and performs a real `aj` on `symbol`,`ts`. | Direct q fixture and pytest `test_q_feature_math_matches_fixture`. | INDEPENDENTLY_VERIFIED |
| F8 | q returns used moving averages; "VWAP" was typical-price average | Returns use `P_t/P_(t-n)-1`; realized volatility uses daily return stdev; VWAP uses price × volume weighted average. | q fixture: 20-session return = 0.2 for 100→120. | INDEPENDENTLY_VERIFIED |
| F9 | 64-bit sequence/timestamps passed through JSON doubles | C++→q ingest now uses typed KX IPC (`kj` for sequence/ns, `kf` for prices). JSON functions retained only for diagnostics. | Isolated q round-trip and PyKX reads pass. | TESTED |
| F10 | Self-test reset production q state | `db verify` and `db benchmark` use an isolated q process; production q is untouched. | Production counts stable across both commands. | INDEPENDENTLY_VERIFIED |

## Remaining blockers

- **NOT IMPLEMENTED:** q server-side IPC authentication. The supported `-u` mechanism was not verified on this runtime; do not treat loopback binding as authorization. Write functions remain callable by any local process.
- **NOT IMPLEMENTED:** date-partitioned HDB with transactional checkpoints. `.quant.persist` still writes whole-table snapshots.
- **PARTIAL:** the composite provider/sequence idempotency key is persisted in HDB snapshots, but is not automatically restored into `.quant.seen` after a q restart.
- **NOT IMPLEMENTED:** true CSCV/PBO, White Reality Check, Hansen SPA.
- **NOT IMPLEMENTED:** operational FYERS data adapter; only the FYERS profile probe exists and its token is expired.
- **NOT IMPLEMENTED:** point-in-time universe, corporate actions and historical surveillance data.
- **NOT IMPLEMENTED:** bounded async queue and batched typed IPC publishing. Current benchmark is synchronous and honestly labelled.
- **BLOCKED:** live execution regulatory verification and owner authorization; live trading remains locked.

## Gate status

GATE 0 baseline: TESTED. GATE 1 financial correctness and GATE 2 backtest
correctness: independently verified for the listed items. GATE 3 journal
durability: fsync/validation tested; power-loss testing remains outstanding.
GATE 4 q correctness: listed items verified; authentication and HDB partitioning
remain open. No later gate is claimed.
