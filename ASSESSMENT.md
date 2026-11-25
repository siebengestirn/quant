# QUANT initial assessment — 2026-11-25

Status: VERIFIED (direct inspection), credentials checked by service name only.

## Repository
- `/Users/tourist/Desktop/quant` is **not** a git repository. No source code,
  build system,
  tests, data, or environment files exist.
- Existing content preserved verbatim: `STYLES.md` and `references/` (7 mirrored
  style
guides with SHA-256 index). `STYLES.md` references
`scripts/check_style_references.py`
and `docs/coding-standard.md`, which are absent in this snapshot; they are not
created.
- Toolchain VERIFIED: Apple clang 21 (C++20/23), CMake 4.5.1, Python 3.15.2, git
  2.56.1,
libcurl 8.7.1, system zlib, pyarrow 26.0.1, pytest. numpy/pandas/scipy/sklearn
absent.

## Broker entitlements (VERIFIED empirically, no secret values read or printed)
macOS Keychain services present: `NIRNAY_FYERS_ACCESS_TOKEN`,
`NIRNAY_FYERS_APP_ID`,
`NIRNAY_FYERS_APP_SECRET`, `NIRNAY_UPSTOX_ANALYTICS_TOKEN`,
`NIRNAY_KITE_API_KEY`,
`NIRNAY_KITE_API_SECRET`.

- **Upstox analytics token: WORKING (read-only).** Verified v2 endpoints: market
  status,
LTP, full quotes (with depth), historical daily candles, instrument search. BOD
instrument
files downloadable from `assets.upstox.com` (gzip JSON). v3 endpoints reject
this token.
- **FYERS: token present but INVALID/EXPIRED** (`code -16`). Requires user
  re-authorisation;
  no credentials are fabricated or refreshed by the system.
- **Zerodha Kite:** api_key/api_secret present; no Kite session access token
  exists, so no
authenticated account connectivity is possible. Public instrument master is
reachable.
  Live order placement remains hard-locked and unauthorised.

## Regulatory (VERIFIED from official SEBI/NSE index pages)
- SEBI circular Feb 2025 "Safer participation of retail investors in algorithmic
  trading".
- NSE/INVG/67858 (05-May-2025) implementation standards; NSE/INVG/69255
  (22-Jul-2025)
  detailed operational modalities; corrigendum INVG/69289.
- NSE FAQ (03-Nov-2025): strategies must run on broker servers and order
  messages originate
from broker servers; static IP restricted to self-coding retail investors; algo
IDs/tagging
and order-rate caps apply. A Nov-2025 inspection circular further restricts
static IP.
- Consequently, local-machine order origination is not established as compliant.
  Live
  execution stays DISABLED regardless of code readiness.

## Smallest viable set of changes
Greenfield implementation of the specified ~10-file architecture:
CMakeLists.txt,
config.toml, README.md, include/quant.hpp,
src/{main,market_data,engine,zerodha}.cpp,
python/research.py, tests/tests.cpp, plus a justified .gitignore for generated
data.

## Amendment (2026-11-25): KDB-X / q added as first-class technology

- **VERIFIED:** no restart or reinitialisation; all prior source, data and
  reports preserved.
- **VERIFIED:** KDB-X q 5f (2026.10.23) installed at `$HOME/.kx/bin/q`, valid
  `kc.lic`
  present (contents never read or printed). PyKX 4.2.0 installed in `data/venv/`
  (generated, ignored); official `k.h` and `libe.so` used by the C++ client.
- **VERIFIED:** `q/quant.q`, `src/kdbx.cpp`, `src/main.cpp` db commands, config
  `[kdbx]`, CMake `QUANT_WITH_KDBX`, PyKX IPC in `python/research.py`, and
  `tests/test_kdbx.py` added. No other components were rewritten.
- **VERIFIED:** C++→q→Python round-trip, duplicate/out-of-order handling,
  q-native
  features, HDB persistence/reload, and PyKX reads all pass; benchmark recorded.
- **NOT IMPLEMENTED:** bounded async publishing, backpressure measurement,
  batched IPC writes, multi-process TP/RDB/HDB deployment.
- **REGULATORY/EXECUTION:** unchanged — Zerodha live orders remain locked.

## Correctness sprint continuation (2026-11-25)

- - Baseline and reproduced P0 findings:
  `docs/correctness-baseline-2026-11-25.md`.
- - Fixes, evidence and remaining blockers:
  `docs/correctness-fixes-2026-11-25.md`.
- - Work continues on branch `correctness/gate0`; default branch and live
  execution
  remain unchanged and locked.

## Label legend
VERIFIED — directly observed. ESTIMATED — modelled. SIMULATED — synthetic/no
live orders.
INCONCLUSIVE — insufficient evidence. NOT IMPLEMENTED — absent by design.
