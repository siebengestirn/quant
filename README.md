# QUANT

[![QUANT CI](https://github.com/siebengestirn/quant/actions/workflows/ci.yml/badge.svg)](https://github.com/siebengestirn/quant/actions/workflows/ci.yml)

Autonomous quantitative research and paper-trading platform for Indian cash
equities.
Initial venture capital: **Permanently compounding. No withdrawals, no
external deposits, no leverage, long-only.**

**Live Zerodha order placement is unconditionally locked in this build.**

## Three-technology architecture

| Technology | Authority |
|---|---|
| C++20 | Systems, ingestion, normalization, journal, accounting, risk, execution |
| **KDB-X / q** | High-volume time-series storage and vectorised analytics |
| Python | Research, statistics, ML; accesses q through KDB-X Python/PyKX |

Flow: `C++ → q → Python → C++`. The C++ risk engine is the only authority that
can approve a trade; q and Python have no broker access and cannot submit
orders.

## Operating labels

Every result is labelled one of: **VERIFIED** (directly observed), **ESTIMATED**
(modelled), **SIMULATED** (no real orders), **INCONCLUSIVE** (insufficient
evidence),
**NOT IMPLEMENTED** (absent by design).

## Architecture

Minimal-file hexagonal design. C++20 owns all authoritative financial
arithmetic,
costs, risk, event persistence and replay. Python is a research client only.

| File | Responsibility |
|---|---|
| `CMakeLists.txt` | Build, tests, optional ASan/UBSan |
| `config.toml` | Versioned configuration and mandatory mode gate |
| `include/quant.hpp` | Domain models, money type, interfaces, declarations |
| `src/market_data.cpp` | Upstox BOD/quotes/history, FYERS profile probe, normalisation, eligibility |
| `src/engine.cpp` | Config, costs, accounting, risk, features, backtest, journal, JSON, HTTP, reports |
| `src/zerodha.cpp` | Keychain resolution, Kite read-only probe, locked order adapter, state machine |
| `src/main.cpp` | CLI orchestration, event-sourced paper portfolio, reports |
| `python/research.py` | Purged walk-forward IC, baselines, bootstrap, deflated IC, PBO proxy |
| `q/quant.q` | Single q source: canonical schemas, ingestion, queries, features, HDB persist/load |
| `src/kdbx.cpp` | Official KX C API client; whitelisted IPC calls, typed JSON boundary |
| `tests/tests.cpp` | C++ unit/integration/financial/replay tests |
| `tests/test_research.py` | Pytest research diagnostics |
| `tests/test_kdbx.py` | KDB-X C++↔q↔PyKX integration tests |

`references/` and `STYLES.md` were pre-existing and are preserved.
`ASSESSMENT.md`
records the verified initial state. `.gitignore` excludes all generated state.

## Build and test

```bash
cd /Users/tourist/Desktop/quant
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build -j 4
ctest --test-dir build --output-on-failure
./build/quant_tests
python3 python/research.py --self-test
python3 -m pytest tests/test_research.py tests/test_kdbx.py -q
python3 scripts/assurance.py check
```

Tooling contracts are pinned in `.clang-format`, `.clang-tidy`, `.pylintrc`,
`.pylint.rc`, `pyproject.toml`, `ruff.toml`, `requirements.txt`,
`CMakePresets.json`, `.gitattributes` and `.pre-commit-config.yaml`. q style is
defined in `q/qstyle.toml` and enforced by the assurance gate. `clang-tidy`,
`pylint` and `pre-commit` are not installed in the current environment; their
checks are BLOCKED until the pinned tools are available.

The single-file assurance tool provides `check`, `polish`, `audit` and
`status`. It is fail-closed, records PASS/FAIL/SKIP per step and verifies that
a gate does not mutate the tree.

Sanitizers: `cmake -S . -B build-san -DQUANT_SANITIZERS=ON && cmake --build
build-san -j 4`.

## Commands (working, not hypothetical)

```bash
./build/quant doctor
./build/quant validate
./build/quant brokers status
./build/quant data sync
./build/quant universe scan
./build/quant market report
./build/quant research          # alias: backtest
./build/quant research verify    # alias: verify
./build/quant replay
./build/quant paper start
./build/quant portfolio
./build/quant report
./build/quant db status
./build/quant db start
./build/quant db verify
./build/quant db benchmark
./build/quant db stop
python3 python/research.py
python3 python/research.py --kdbx-check    # PyKX IPC read of the same q tables
```

Generated state is written to `data/`: `journal/events.jnl` (append-only,
checksummed),
`cache/universe.csv`, `cache/history/*.csv`, `reports/*.md|json`.

## KDB-X / q integration (amendment)

**VERIFIED 2026-11-25:** KDB-X runtime at `$HOME/.kx/bin/q` reports version `5f`
(dated 2026.10.23); `kc.lic` is present (contents never inspected or logged).
PyKX `4.2.0` installed in `data/venv/` (not committed). CMake auto-detects the
official `k.h` and `libe.so` from PyKX; build with `-DQUANT_WITH_KDBX=OFF` to
compile without it. `quant doctor` and `quant db status` report the real state.

Deployment is a single locally managed q process bound to `127.0.0.1:5001`.
`q/quant.q` owns the canonical tables `quotes`, `trades`, `depth`, `bars`,
`instruments`, `marketStatus`, `signals`, `features`, plus idempotent ingest
(sequence deduplication), out-of-order flagging, q-native rolling features,
VWAP, breadth proxy, as-of join and HDB persist/load. The C++ durable journal
remains the recovery/audit source; q is an analytical store, not the durability
authority. A q IPC response is **not** claimed as a zero-loss guarantee.

C++↔q uses one versioned JSON payload per write, parsed by q's `.j.k`, over the
official KX C API (`khpun`/`k`/`kclose`). Only whitelisted `.quant.*Json`
functions are callable; arbitrary q expressions are rejected. Python reads the
same tables via PyKX `SyncQConnection` (separate q process, never embedded q).

Community Edition limits acknowledged (verify at deployment): 16 GB RAM per q,
one instance, four secondary threads, 16 connections, 24 CPU cores. No attempt
is made to circumvent them. No KX binaries or license files are committed.

**VERIFIED integration results:** 132 C++ checks, 8 pytest tests (including
C++→q→PyKX round-trip, duplicates, out-of-order, HDB reload, restart),
round-trip
counts/state/features consistent across C++ and Python. Benchmark (synchronous,
unbatched baseline): 1,000 events at ~0.055 ms p50 / 0.098 ms p95 / 0.115 ms
p99, ~16,000 events/s, ~0.030 ms mean query. `NOT IMPLEMENTED`: bounded async
queue, backpressure measurement and batched IPC publishing.

## Credentials and security

Credentials are resolved only from macOS Keychain services named
`NIRNAY_<SUFFIX>` (prefix configurable). Status commands read **presence only**.
Secrets are never printed, logged, persisted, committed, or sent to external AI
services. Environment variables are not used to carry secrets.

Verified entitlements (2026-11-25):

- Upstox: read-only **analytics token works** (market status, quotes with depth,
  historical candles). Used for data only. v3 rejects this token; v2 is used.
- FYERS: app credentials present but access token is invalid/expired (code -16).
  The adapter reports this; it never fabricates or refreshes a token.
- Zerodha: `KITE_API_KEY` / `KITE_API_SECRET` present, **no Kite session
  token**;
  authenticated read-only connectivity therefore cannot be established. Live
  submission is locked regardless.

## Financial model (authoritative, C++)

Integer-paise arithmetic. Cost schedule `ZERODHA-EQ-2026-11-25-v1`, verified
from
the official Zerodha charges page: equity delivery brokerage ₹0; intraday ₹20 or
0.03% whichever lower; STT delivery 0.1% both sides / intraday 0.025% sell side;
NSE transaction charge 0.00307%; SEBI ₹10/crore; GST 18% on brokerage + SEBI +
transaction charges; stamp 0.015% delivery buy / 0.003% intraday buy; DP ₹15.34
on delivery sell. Slippage is configurable (default 10 bps). Round-trip
break-even is reported for every candidate.

Risk limits: 1% of initial capital per trade (research default), 2% daily loss,
10% maximum drawdown, one open position, 80% gross exposure, 10% cash reserve,
35 bps maximum spread, stale-data rejection, fail-closed on safety failures.

## Backtesting

Deterministic event-driven daily simulator. Signals use candles up to decision
date `t` only; fills occur at `t+1` open with conservative slippage. Supported:
partial accounting via persistent ledger, stop/horizon exits, drawdown and cost
tracking. Limitations: no order-book history, no survivorship-bias correction,
no intraday path simulation. Results are **SIMULATED**, not evidence of alpha.

## Current verified outcome (2026-11-25)

- Dynamic universe: 87,066 BOD records → 2,781 normalised NSE equities →
  2,755 eligible after security-type/suspension/symbol filters.
- 69 daily seed series and 3 index series downloaded from Upstox.
- Backtest: 11 simulated trades, gross ₹7.68, net ₹2.47 after ₹1.65 of costs,
  max drawdown ₹14.36, Sharpe 0.10. **INCONCLUSIVE** — no statistical alpha.
- Purged walk-forward IC across four strategy families is at or near zero after
  multiple-testing haircut. **NO TRADE** is the correct current decision.
- Market report for the next session: **NO TRADE** — no candidate combined a
  positive score, verified live depth, and risk approval outside market hours.

## Compliance

SEBI circular (Feb 2025), NSE/INVG/67858 (05-May-2025), NSE/INVG/69255
(22-Jul-2025) and the Nov-2025 NSE inspection guidance require broker-server
hosting for strategies, broker-originated order messages, algo IDs/tagging,
static-IP eligibility restricted to self-coding investors, and order-rate
controls. Local-machine order origination is not established as compliant here,
so live execution remains **DISABLED** pending separate authorisation and
review.
No withdrawal, fund-transfer or account-modification functionality exists.

## Not implemented (explicit)

LightGBM/XGBoost meta-labeling; Kalman fair value; HMM/GARCH; Johansen
cointegration; full multi-level order-flow imbalance; White Reality Check;
Hansen SPA; full CSCV logit PBO; WebSocket streaming (REST polling is used);
Zerodha authenticated connectivity (no session token); distributed
infrastructure.
