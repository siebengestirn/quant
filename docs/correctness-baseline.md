# QUANT correctness sprint — GATE 0 baseline

> SUPERSEDED — historical snapshot; see current policy in `git-policy.json`
> and current state in `docs/master-acceptance-register.md`.

Date: 2026-11-25T08:45:07Z
Commit: `48b3a95ebcefaab5333e85d932943527c75ad10e` (branch `main` at
baseline; work continues on `correctness/gate0`)
Working tree: clean apart from two untracked prompt-clipping backups, copied to
`data/prompts/` (ignored) for session continuity.

## Reproduction evidence

Portable CI configuration (`QUANT_WITH_KDBX=OFF`, no QHOME):

- CMake configure/build: exit 0.
- CTest: 3/3 passed.
- C++ tests: 132 checks, 0 failures.
- pytest: 5 passed, 3 KDB-X tests skipped by design (no q runtime).

Local KDB-X integration (macOS):

- q 5f runtime and license detected; client connected on loopback.
- `db verify`: 3 quotes, 1 duplicate, 1 out-of-order; HDB reload passed.
- Destructive reset was used by verification — recorded as a P0 defect below.

## Reproduced P0 findings

| ID | Component | Evidence | State |
|---|---|---|---|
| F1 | Backtest sizing | `659:  const double risk_budget = std::min(
939:        const double risk_budget =` | CONFIRMED |
| F2 | Stop fill | `864:          const bool stop = candle.low <= entry_price.rupees() * 0.95;` and `868:            const Money exit_price = Money::FromRupees(candle.open);` | CONFIRMED |
| F3 | Timestamps | `164:  quote.provider_timestamp_ms = quote.exchange_timestamp_ms;` | CONFIRMED |
| F4 | Journal durability | `1129:  output << event->sequence << '|' << event->id << '|'`; no fsync or full validation before append | CONFIRMED |
| F5 | q server auth | `165:    char* argv[] = {const_cast<char*>(program.data()),`; no server-side user auth | CONFIRMED |
| F6 | Cost attribution | `994:  metrics.cost_to_gross =`; slippage excluded | CONFIRMED |
| F7 | q as-of join | `150:  q aj.j from q}`; feature table computed but unused | CONFIRMED |
| F8 | q returns/VWAP | `127:    ret20:(close%mavg[20;close])-1,`; `39:  ret120:`float$();vol20:`float$();volSurprise:`float$();vwapDev:`float$())
132:    vwapDev:(close%mavg[20;(high+low+close)%3])-1` | CONFIRMED |
| F9 | IPC precision | `345:  K argument = kp(const_cast<char*>(Json::ObjectValue(payload).Dump().c_str()));
369:  K argument = kp(const_cast<char*>(Json::ObjectValue(payload).Dump().c_str()));`; 64-bit values via double | CONFIRMED |
| F10 | Test isolation | `419:  if (!Reset(error)) return false;
495:  if (!Reset(error)) return false;` in SelfTest and Benchmark | CONFIRMED |

## Authority freeze

- C++: domain, journal, accounting, risk, execution, event identity.
- KDB-X/q: analytical time series only; reconstructible from journal.
- Python: research only; no execution authority.
- No live orders, no account changes, no paid services, no reset of
  production data.

## Task states

GATE 0 baseline: TESTED. Contract freeze: pending lead sign-off.
All P0 defects above: IN_PROGRESS after this record.

## Prompt backups

- `/Users/tourist/Desktop/quant/Pasted 2026-11-25 at 14.11.13.textClipping`
- `/Users/tourist/Desktop/quant/Pasted 2026-11-25 at 14.11.26.textClipping`
- Copies: `data/prompts/` (ignored, not committed).
