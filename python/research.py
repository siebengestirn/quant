#!/usr/bin/env python3
"""QUANT quantitative research (Python).

Research only. Authoritative accounting, costs, risk and order decisions live in
the C++ core. This module computes signal statistics and multiple-testing
diagnostics, then invokes `quant research` for authoritative simulated P&L.

Labels: VERIFIED = observed, ESTIMATED = modelled, SIMULATED = no real orders,
INCONCLUSIVE = insufficient evidence, NOT IMPLEMENTED = absent.

Usage:
    python3 python/research.py [--data data/cache/history] [--out data/reports]
    python3 python/research.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Callable

# Research constants (ESTIMATED). They are not trading parameters.
KDBX_WHITELIST = (
    ".quant.counts[]",
    ".quant.state[]",
    ".quant.featureTable[]",
    ".quant.dbStatus[]",
)
KDBX_DEFAULT_HOST = "127.0.0.1"
KDBX_DEFAULT_PORT = 5001

HOLDING_SESSIONS = 5
PURGE_SESSIONS = 2
EMBARGO_SESSIONS = 5
TRAIN_WINDOW = 180
TEST_WINDOW = 40
MIN_HISTORY = 130
BOOTSTRAP_DRAWS = 1000
TRIALS = 8
SEED = 20261125


def load_history(directory: Path) -> dict[str, list[dict[str, float]]]:
    """Load cached daily candles written by data sync (VERIFIED source)."""
    history: dict[str, list[dict[str, float]]] = {}
    if not directory.is_dir():
        return history
    for path in sorted(directory.glob("*.csv")):
        rows: list[dict[str, float]] = []
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                try:
                    rows.append(
                        {
                            "date": raw["date"],
                            "open": float(raw["open"]),
                            "high": float(raw["high"]),
                            "low": float(raw["low"]),
                            "close": float(raw["close"]),
                            "volume": float(raw["volume"]),
                        }
                    )
                except (KeyError, ValueError, TypeError):
                    continue
        rows.sort(key=lambda row: row["date"])
        if rows:
            history[path.stem] = rows
    return history


def features(rows: list[dict[str, float]], index: int) -> dict[str, float]:
    """Features computed strictly from rows[:index+1] (no lookahead)."""
    window = rows[: index + 1]
    close = [row["close"] for row in window]
    volume = [row["volume"] for row in window]

    def ret(periods: int) -> float:
        if len(close) <= periods or close[-1 - periods] <= 0:
            return 0.0
        return close[-1] / close[-1 - periods] - 1.0

    recent = close[-20:]
    return_source = close[-21:] if len(close) >= 21 else close
    returns = [
        return_source[i] / return_source[i - 1] - 1.0
        for i in range(1, len(return_source))
        if return_source[i - 1] > 0
    ]
    vols = volume[-20:]
    avg_volume = statistics.fmean(vols) if vols else 0.0
    vwap_window = window[-20:]
    typical = [
        (row["high"] + row["low"] + row["close"]) / 3.0 for row in vwap_window
    ]
    weighted = sum(price * size for price, size in zip(typical, volume[-20:]))
    daily_vwap = weighted / sum(vols) if sum(vols) > 0 else close[-1]
    sma20 = statistics.fmean(recent) if recent else 0.0
    sma60 = statistics.fmean(close[-60:]) if len(close) >= 60 else 0.0
    sma120 = statistics.fmean(close[-120:]) if len(close) >= 120 else 0.0
    checks = 1 + int(len(close) >= 60) + int(len(close) >= 120)
    bullish = int(close[-1] > sma20)
    if len(close) >= 60:
        bullish += int(sma20 > sma60)
    if len(close) >= 120:
        bullish += int(sma20 > sma120)
    last20 = window[-20:]
    high = max(row["high"] for row in last20)
    low = min(row["low"] for row in last20)
    span = high - low
    breakout = (close[-1] - low) / span * 2 - 1 if span > 0 else 0.0
    typical = statistics.fmean(
        (row["high"] + row["low"] + row["close"]) / 3 for row in last20
    )
    return {
        "ret5": ret(5),
        "ret20": ret(20),
        "ret60": ret(60),
        "ret120": ret(120),
        "vol20": statistics.pstdev(returns[-20:]) if returns else 0.0,
        "volume_surprise": (
            (vols[-1] / avg_volume - 1) if avg_volume > 0 else 0.0
        ),
        "trend": bullish / checks,
        "breakout": max(-1.0, min(1.0, breakout)),
        "vwap_dev": (close[-1] / typical - 1) if typical > 0 else 0.0,
    }


def feature_history(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    """Point-in-time features with availability at the next observed session."""
    result = []
    for index in range(len(rows)):
        item = features(rows, index)
        item["index"] = index
        item["availability_index"] = min(index + 1, len(rows) - 1)
        item["date"] = rows[index]["date"]
        result.append(item)
    return result


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_momentum(f: dict[str, float]) -> float:
    raw = (
        0.15 * f["ret5"]
        + 0.25 * f["ret20"]
        + 0.35 * f["ret60"]
        + 0.25 * f["ret120"]
    )
    return clamp(raw / max(f["vol20"], 0.005) * 0.05)


def score_trend(f: dict[str, float]) -> float:
    return clamp((f["trend"] - 0.5) * 2 + f["breakout"] * 0.3)


def score_breakout(f: dict[str, float]) -> float:
    return clamp(
        f["breakout"] * 0.6
        + clamp(f["volume_surprise"], -1, 2) * 0.2
        + clamp(f["vwap_dev"] * 20, -1, 1) * 0.2
    )


STRATEGIES: dict[str, Callable[[dict[str, float]], float]] = {
    "momentum_5_20_60_120": score_momentum,
    "time_series_trend": score_trend,
    "volume_breakout": score_breakout,
    "ensemble": lambda f: clamp(
        0.30 * score_momentum(f)
        + 0.22 * score_trend(f)
        + 0.18 * score_breakout(f)
        + 0.12 * (score_momentum(f) - 0.0)
        + 0.10 * 1.0
        - 0.08 * 0.12
    ),
}
BASELINES = ("no_trade",)


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation via average ranks. ESTIMATED diagnostic."""
    if len(xs) < 8 or len(ys) != len(xs):
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position + 1
            while (
                end < len(order)
                and values[order[end]] == values[order[position]]
            ):
                end += 1
            average = (position + end - 1) / 2 + 1
            for i in range(position, end):
                out[order[i]] = average
            position = end
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    numerator = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return numerator / (dx * dy) if dx > 0 and dy > 0 else 0.0


def forward_return(
    rows: list[dict[str, float]], index: int, sessions: int
) -> float:
    end = min(index + sessions, len(rows) - 1)
    if end <= index or rows[index]["close"] <= 0:
        return 0.0
    return rows[end]["close"] / rows[index]["close"] - 1.0


def walk_forward_ic(
    history: dict[str, list[dict[str, float]]],
    score_fn: Callable[[dict[str, float]], float],
) -> dict[str, float]:
    """Purged, embargoed walk-forward information coefficient (ESTIMATED).

    At each decision date, rank all instruments by score and correlate with
    their next HOLDING_SESSIONS return. Purge and embargo prevent overlap.
    """
    dates: dict[str, int] = {}
    for rows in history.values():
        for row in rows:
            dates[row["date"]] = dates.get(row["date"], 0) + 1
    ordered = sorted(dates)
    ics: list[float] = []
    output: dict[str, float] = {}
    folds = 0
    start = TRAIN_WINDOW
    while start + TEST_WINDOW + HOLDING_SESSIONS < len(ordered):
        test_dates = ordered[start + PURGE_SESSIONS : start + TEST_WINDOW]
        xs: list[float] = []
        ys: list[float] = []
        for date in test_dates:
            scores: list[float] = []
            returns: list[float] = []
            for symbol, rows in history.items():
                if symbol == "NIFTY 50" or symbol == "INDIA VIX":
                    continue
                positions = [
                    i for i, row in enumerate(rows) if row["date"] == date
                ]
                if not positions:
                    continue
                i = positions[0]
                if i < MIN_HISTORY:
                    continue
                scores.append(score_fn(features(rows, i)))
                returns.append(forward_return(rows, i, HOLDING_SESSIONS))
            if len(scores) >= 5:
                xs.extend(scores)
                ys.extend(returns)
        if len(xs) >= 20:
            ics.append(spearman(xs, ys))
            folds += 1
        start += TEST_WINDOW + EMBARGO_SESSIONS
    if ics:
        output = {
            "folds": folds,
            "mean_ic": statistics.fmean(ics),
            "ic_std": statistics.stdev(ics) if len(ics) > 1 else 0.0,
            "positive_folds": sum(1 for value in ics if value > 0),
            "observations": len(ics),
            "fold_ics": ics,
        }
    else:
        output = {
            "folds": 0,
            "mean_ic": 0.0,
            "ic_std": 0.0,
            "positive_folds": 0,
            "observations": 0,
            "fold_ics": [],
        }
    return output


def bootstrap_ci(
    values: list[float], draws: int = BOOTSTRAP_DRAWS
) -> list[float]:
    if not values:
        return [0.0, 0.0, 0.0]
    rng = random.Random(SEED)
    means = []
    for _ in range(draws):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(sample))
    means.sort()
    return [
        means[int(0.025 * draws)],
        means[draws // 2],
        means[int(0.975 * draws) - 1],
    ]


def deflated(value: float, trials: int, observations: int) -> float:
    if observations < 3:
        return 0.0
    factor = math.sqrt(
        max(0.0, 1 - math.log(trials + 1) / math.log(observations + 1))
    )
    return value * factor


def probability_backtest_overfitting(ics: list[float]) -> float:
    """ESTIMATED CSCV-style PBO proxy from fold-level IC signs.

    Full CSCV requires the logit distribution over all parameter combinations;
    the reported proxy is the fraction of folds with non-positive
    out-of-sample IC.
    """
    if not ics:
        return 1.0
    return sum(1 for value in ics if value <= 0) / len(ics)


def _venv_python(root: Path) -> str:
    """Interpreter with PyKX installed; created during KDB-X integration."""
    candidate = root / "data" / "venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def pykx_query(
    function: str,
    root: Path,
    host: str = KDBX_DEFAULT_HOST,
    port: int = KDBX_DEFAULT_PORT,
) -> dict:
    """Query the external q process through PyKX IPC (VERIFIED when successful).

    Only whitelisted, zero-argument .quant.* functions are accepted. Embedded
    q is never used as the server; the q process runs independently.
    """
    if function not in KDBX_WHITELIST:
        return {
            "status": "NOT IMPLEMENTED",
            "detail": "function not whitelisted",
        }
    script = """
import json
import sys
import pykx as kx

host, port, function = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with kx.SyncQConnection(host=host, port=port) as q:
    result = q(function)
    value = result.py() if hasattr(result, "py") else result
    sys.stdout.write(json.dumps(value, default=str))
"""
    try:
        completed = subprocess.run(
            [_venv_python(root), "-c", script, host, str(port), function],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "INCONCLUSIVE", "detail": str(error)}
    if completed.returncode != 0:
        return {"status": "INCONCLUSIVE", "detail": completed.stderr[-1000:]}
    try:
        value = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "INCONCLUSIVE", "detail": "non-JSON PyKX response"}
    return {"status": "VERIFIED", "function": function, "value": value}


def kdbx_research_summary(root: Path) -> dict:
    """Small PyKX research snapshot; large datasets stay in q."""
    summary: dict[str, dict] = {}
    for function in KDBX_WHITELIST:
        summary[function] = pykx_query(function, root)
    return summary


def cpp_authoritative_backtest(root: Path) -> dict:
    """Invoke the C++ core for accounting and simulated P&L."""
    binary = root / "build" / "quant"
    if not binary.exists():
        return {"status": "NOT IMPLEMENTED", "detail": "build/quant not found"}
    try:
        completed = subprocess.run(
            [str(binary), "research"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return {
            "status": (
                "SIMULATED" if completed.returncode == 0 else "INCONCLUSIVE"
            ),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-2000:],
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "INCONCLUSIVE", "detail": str(error)}


def synthetic_history() -> dict[str, list[dict[str, float]]]:
    """Deterministic SIMULATED dataset for self-tests only."""
    rng = random.Random(SEED)
    history: dict[str, list[dict[str, float]]] = {}
    for symbol, drift in (
        ("AAA", 0.0012),
        ("BBB", 0.0003),
        ("CCC", -0.0004),
        ("DDD", 0.0008),
        ("EEE", -0.0002),
    ):
        price = 100.0
        rows = []
        for day in range(260):
            open_price = price
            price *= 1 + drift + rng.gauss(0, 0.012)
            rows.append(
                {
                    "date": f"SIM-{day:04d}",
                    "open": open_price,
                    "high": max(open_price, price) * 1.008,
                    "low": min(open_price, price) * 0.992,
                    "close": price,
                    "volume": 1_000_000 * (1 + rng.random()),
                }
            )
        history[symbol] = rows
    index_rows = []
    price = 20000.0
    for day in range(260):
        open_price = price
        price *= 1 + 0.0002 + rng.gauss(0, 0.006)
        index_rows.append(
            {
                "date": f"SIM-{day:04d}",
                "open": open_price,
                "high": max(open_price, price) * 1.004,
                "low": min(open_price, price) * 0.996,
                "close": price,
                "volume": 1e9,
            }
        )
    history["NIFTY 50"] = index_rows
    return history


def analyze(history: dict[str, list[dict[str, float]]], root: Path) -> dict:
    usable = {
        symbol: rows
        for symbol, rows in history.items()
        if len(rows) >= MIN_HISTORY and symbol != "NIFTY 50"
    }
    report: dict = {
        "labels": {
            "statistics": "ESTIMATED",
            "authoritative_pnl": "SIMULATED",
            "live_trading": "NOT IMPLEMENTED (locked)",
        },
        "input_series": len(usable),
        "observations_per_series": min(
            (len(rows) for rows in usable.values()), default=0
        ),
        "walk_forward": {},
        "baselines": {},
        "multiple_testing": {
            "trials": TRIALS,
            "deflated_sharpe_note": (
                "ESTIMATED conservative haircut; full DSR needs trial "
                "return distribution"
            ),
        },
        "authoritative_cpp_backtest": cpp_authoritative_backtest(root),
        "rejections": [],
    }
    if len(usable) < 5:
        report["rejections"].append(
            "INCONCLUSIVE: fewer than 5 usable instruments; "
            "no signal claims made"
        )
        return report

    for name, score_fn in STRATEGIES.items():
        stats = walk_forward_ic(usable, score_fn)
        report["walk_forward"][name] = stats
        if stats["folds"]:
            fold_ics = stats.get("fold_ics", [])
            ci = bootstrap_ci(fold_ics)
            report["walk_forward"][name]["ic_bootstrap_95ci"] = ci
            report["walk_forward"][name]["deflated_mean_ic"] = deflated(
                stats["mean_ic"], TRIALS, stats["folds"]
            )
            report["walk_forward"][name]["pbo_proxy"] = (
                probability_backtest_overfitting(fold_ics)
            )
    report["baselines"]["no_trade"] = {
        "expected_return": 0.0,
        "cost": 0.0,
        "note": "VERIFIED benchmark; always available",
    }

    best_name, best_stats = max(
        report["walk_forward"].items(),
        key=lambda item: item[1].get("mean_ic", 0),
        default=("none", {}),
    )
    if not best_stats or best_stats.get("folds", 0) < 5:
        report["conclusion"] = "INCONCLUSIVE: insufficient independent folds"
    elif best_stats.get("mean_ic", 0) <= 0:
        report["conclusion"] = "NO TRADE: no positive out-of-sample IC"
    elif best_stats.get("deflated_mean_ic", 0) <= 0:
        report["conclusion"] = (
            "INCONCLUSIVE: multiple-testing haircut removes signal"
        )
    else:
        report["conclusion"] = (
            f"ESTIMATED: {best_name} shows positive purged out-of-sample IC; "
            "not deployable until cost-adjusted walk-forward P&L is "
            "independently verified"
        )
    report["best_strategy"] = best_name
    return report


def render_markdown(report: dict) -> str:
    lines = [
        "# QUANT research verification",
        "",
        f"- Input series: {report['input_series']}",
        f"- Minimum observations per series: "
        f"{report['observations_per_series']}",
        f"- Conclusion: **{report.get('conclusion', 'INCONCLUSIVE')}**",
        "",
        "## Purged walk-forward IC (ESTIMATED)",
        "",
        "| Strategy | Folds | Mean IC | IC std | Positive folds | "
        "Deflated IC | PBO proxy |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, stats in report["walk_forward"].items():
        lines.append(
            f"| {name} | {stats.get('folds', 0)} | "
            f"{stats.get('mean_ic', 0):.4f} | {stats.get('ic_std', 0):.4f} | "
            f"{stats.get('positive_folds', 0)} | "
            f"{stats.get('deflated_mean_ic', 0):.4f} | "
            f"{stats.get('pbo_proxy', 1):.2f} |"
        )
    lines += [
        "",
        "## Authoritative simulated P&L (SIMULATED)",
        "",
        "The C++ core owns costs, accounting and risk. Its output is "
        "authoritative for simulated P&L; Python statistics above are research "
        "diagnostics only.",
        "",
        "```text",
        report["authoritative_cpp_backtest"].get("stdout", "")[:2500],
        "```",
        "",
        "## Rejections and limitations",
        "",
    ]
    for rejection in report["rejections"]:
        lines.append(f"- {rejection}")
    lines += [
        "- NOT IMPLEMENTED in this build: LightGBM meta-labeling, Kalman fair "
        "value, HMM, GARCH/EGARCH, Johansen cointegration, full order-book "
        "microstructure, White Reality Check, Hansen SPA, CSCV logit "
        "distribution.",
        "- SIMULATED fills use next-session open with 10 bps slippage; no "
        "order-book history is available, and survivorship bias is not "
        "corrected.",
        "",
    ]
    return "\n".join(lines)


def self_test() -> int:
    history = synthetic_history()
    report = analyze(history, Path(__file__).resolve().parent.parent)
    assert report["input_series"] == 5, report["input_series"]
    assert len(report["walk_forward"]) == len(STRATEGIES), report[
        "walk_forward"
    ]
    assert all(
        value["folds"] > 0 for value in report["walk_forward"].values()
    ), report
    ci = bootstrap_ci([0.01, -0.01, 0.02, -0.005])
    assert ci[0] <= ci[1] <= ci[2], ci
    assert deflated(2.0, 8, 100) < 2.0
    assert math.isfinite(spearman([1, 2, 3, 4], [4, 3, 2, 1]))
    print("python research self-test: PASS (SIMULATED data only)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--data", default=str(root / "data" / "cache" / "history")
    )
    parser.add_argument("--out", default=str(root / "data" / "reports"))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--kdbx-check",
        action="store_true",
        help="query the running q process through PyKX IPC",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.kdbx_check:
        summary = kdbx_research_summary(root)
        print(json.dumps(summary, indent=2))
        values = [item.get("status") for item in summary.values()]
        return 0 if values.count("VERIFIED") == len(values) else 2
    history = load_history(Path(args.data))
    report = analyze(history, root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "research.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (out / "research.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Saved: {out / 'research.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
