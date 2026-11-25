# QUANT financial-integrity remediation — 2026-11-26

Branch: `fix/financial-integrity`. Baseline: merged `main` at `fccd5c9`.
All statuses below are `TESTED`; implementing-agent evidence is not independent
acceptance.

| ID | Finding | Implementation | Verification | Status |
|---|---|---|---|---|
| F-001 | Peak equity was not updated after a profitable sale, allowing negative drawdown and oversized risk | Added `UpdatePeakEquity()` called after Buy, Sell, mark-to-market, expenses and external flows; `drawdown()` clamps to zero | `TestHighWaterAndMoneyBounds` checks profitable close, peak and non-negative drawdown | TESTED |
| F-002 | Money conversion and multiplication were unchecked | Added `TryFromRupees`, `CheckedMultiply` and `CheckedAdd`; Buy, Sell and mark-to-market reject overflow | NaN, infinity, out-of-range and overflow tests | TESTED |
| F-003a | STT rounded to paise instead of nearest rupee | Delivery and intraday STT rounded to the nearest rupee with 50 paise rounded up | ₹500 delivery STT = ₹1; ₹10,000 intraday sell STT = ₹3 | TESTED |
| F-003b | DP charged on every delivery sell call | Added `CostAggregator` keyed by symbol and session; DP charged once per scrip per day | Two same-day sells of one symbol: DP once | TESTED |
| F-004 | Ledger invariants were implicit | Equity identity, cost-basis separation and high-water update are now tested after every mutation path | Existing accounting tests plus high-water tests | TESTED |
| F-005 | Event-sourced replay silently ignored malformed or failed postings | `RebuildPortfolio` now returns success/failure, propagates journal and JSON errors, and fails on an unknown or failed posting | `portfolio` CLI fails closed on replay error | TESTED |
| B-004 | Terminal liquidation mutated the map while iterating; equity curve omitted terminal postings | Snapshot positions before liquidation; push terminal equity; collect warnings | Full C++ suite passes | TESTED |
| D-001 | Research report embedded an older backtest result | Regenerated `backtest.json`, `backtest.md` and `research.md` from the same current run | Both report files now show 9 trades and net −₹10.56 | TESTED |
| K-001 | Feature VWAP definitions diverged across C++, q and Python | Canonical daily VWAP proxy = volume-weighted typical price `(H+L+C)/3` over 20 sessions in all three implementations | q fixture loads and C++/Python tests pass | TESTED |

## Remaining limitations

- **BLOCKED:** F-006 durable daily-risk state across process restart.
- **NOT IMPLEMENTED:** operational q startup recovery, real provider-to-q
  ingestion, FYERS adapter, point-in-time universe, genuine paper execution.
- **INCONCLUSIVE:** no alpha evidence. Current standalone backtest is
  approximately −₹10.56 net after corrected STT/DP treatment; no strategy
  acceptance is implied.
- q tests may be skipped when the runtime is absent; a skip is not a pass.
- Live execution remains locked. Current decision remains NO TRADE.

## PR #2 review follow-up (same day)

Independent review rejected F-001, F-002 and F-003. Additional changes:

- `Portfolio` now has one `RecomputeUnrealized()` used after Buy, Sell and
  mark-to-market, with ledger/position snapshots rolled back on failed
  recalculation. Regression fixture: buy at ₹100, mark to ₹120, sell at ₹120
  with zero costs → cash ₹520, cost basis ₹0, unrealized ₹0, equity ₹520.
- STT rounding changed to `floor(raw + 0.5)` on a long double. Boundary tests
  cover ₹0.01, ₹0.49, ₹0.50, ₹1.40, ₹1.50 and ₹2.50 raw charges.
- `Money::Invalid()` sentinel replaces invalid-to-zero conversion; tests assert
  NaN conversion is not valid money.
- Missing execution depth now rejects sizing; explicit historical capacity mode
  is required for backtests. Current exposure uses market value, not cost basis.
- Backtest uses `PriorAverageVolume` as labelled capacity (never execution-day
  volume), propagates mark/sell failures, applies delivery entry and exit costs
  consistently, and rejects unsupported multi-position configurations.
- q typical-price expression explicitly groups the numerator; PyKX fixture
  verifies the daily VWAP proxy against typical-price volume weighting.
- Paper conditional entry now emits `kPaperIntent`, never `kFill`, and checks
  journal append success before reporting.

Status remains TESTED. Independent acceptance has not been recorded; PR #2
stays open. F-006 daily-risk persistence and operational integration remain
BLOCKED / NOT IMPLEMENTED.

## Final financial-integrity pass (same day)

- Money now carries an explicit validity flag. Invalid values propagate through
  addition and subtraction; overflow yields invalid money, never a valid zero.
  `CheckedMultiply` uses a 128-bit product where available and a sign-aware
  fallback, removing the `INT64_MIN / -1` undefined case.
- Journal `Close()` now marks the instance closed/unhealthy and releases writer
  ownership; `Append()` requires an active lock; sequence advances only after
  successful fsync; failed `Open()` validation releases the lock; blank records,
  invalid event types and unsupported schemas are rejected. A two-writer
  ownership regression was added.
- Backtest failures halt the affected simulation instead of orphaning the open
  position; gross P&L is recorded only after a successful sale; terminal
  liquidation failures are reported; sizing receives the configured delivery
  product; gross profit factor is `NOT_MEANINGFUL` without gross losses.
- Configuration validation now rejects non-finite spread, slippage, stop
  distance, participation and traded value, plus zero queue, timeout and batch
  settings.
- Python and q now agree on the volume-weighted typical-price proxy on an
  unequal-volume fixture.

Status remains TESTED. Independent acceptance is not recorded. DP settlement
aggregation is explicitly NOT IMPLEMENTED and the cost-model version string now
states this. Daily-risk persistence, operational q recovery, real provider-to-q
ingestion and genuine paper execution remain BLOCKED / NOT IMPLEMENTED.
