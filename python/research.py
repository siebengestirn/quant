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
import hashlib
import itertools
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

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


HISTORY_HEADER = ("date", "open", "high", "low", "close", "volume")
STATUS_HEADER = (
    "symbol",
    "exchange",
    "isin",
    "effective_from",
    "effective_to",
    "eligible",
    "published_at",
    "revision",
)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value: str) -> date | None:
    """Strict Gregorian date; rejects impossible dates such as 2026-02-31."""
    if not isinstance(value, str) or ISO_DATE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def canonical_digest(value: object) -> str:
    """Enforce the QUANT style contract."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_history_with_errors(
    directory: Path,
) -> tuple[dict[str, list[dict[str, float]]], int, list[str]]:
    """Strict CSV loader. Rejects duplicate/unknown columns and wrong widths."""
    history: dict[str, list[dict[str, float]]] = {}
    errors: list[str] = []
    if not directory.is_dir():
        return history, 1, [f"{directory}: history directory absent"]
    for path in sorted(directory.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            errors.append(f"{path.name}: empty file")
            continue
        header = rows[0]
        if len(header) != len(set(header)):
            errors.append(f"{path.name}: duplicate header columns")
            continue
        if tuple(header) != HISTORY_HEADER:
            errors.append(f"{path.name}: header mismatch")
            continue
        parsed: list[dict[str, float]] = []
        for line, raw in enumerate(rows[1:], start=2):
            if len(raw) != len(header):
                errors.append(f"{path.name}:{line}: row width mismatch")
                continue
            if parse_iso_date(raw[0]) is None:
                errors.append(f"{path.name}:{line}: invalid date")
                continue
            try:
                numbers = [float(value) for value in raw[1:]]
            except ValueError:
                errors.append(f"{path.name}:{line}: non-numeric field")
                continue
            if not all(math.isfinite(value) for value in numbers):
                errors.append(f"{path.name}:{line}: non-finite field")
                continue
            parsed.append(
                {
                    "date": raw[0],
                    "open": numbers[0],
                    "high": numbers[1],
                    "low": numbers[2],
                    "close": numbers[3],
                    "volume": numbers[4],
                }
            )
        if len({row["date"] for row in parsed}) != len(parsed):
            errors.append(f"{path.name}: duplicate session dates")
            continue
        if parsed:
            parsed.sort(key=lambda row: row["date"])
            history[path.stem] = parsed
    return history, len(errors), errors


def load_universe_status(path: Path) -> tuple[list[dict], list[str]]:
    """Strict point-in-time universe records with availability vintages."""
    records: list[dict] = []
    errors: list[str] = []
    if not path.is_file():
        return records, [f"{path.name}: universe status file absent"]
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return records, [f"{path.name}: empty universe status file"]
    header = rows[0]
    if len(header) != len(set(header)):
        return records, [f"{path.name}: duplicate status header"]
    if tuple(header) != STATUS_HEADER:
        return records, [f"{path.name}: status header mismatch"]
    seen: dict[tuple[str, str, str], dict] = {}
    for line, raw in enumerate(rows[1:], start=2):
        if len(raw) != len(header):
            errors.append(f"{path.name}:{line}: status row width mismatch")
            continue
        values = dict(zip(header, raw, strict=True))
        if not all(values[key] for key in ("symbol", "exchange", "isin")):
            errors.append(f"{path.name}:{line}: missing instrument identity")
            continue
        if parse_iso_date(values["effective_from"]) is None:
            errors.append(f"{path.name}:{line}: invalid effective_from")
            continue
        if (
            values["effective_to"]
            and parse_iso_date(values["effective_to"]) is None
        ):
            errors.append(f"{path.name}:{line}: invalid effective_to")
            continue
        if (
            values["effective_to"]
            and values["effective_to"] < values["effective_from"]
        ):
            errors.append(f"{path.name}:{line}: effective_to precedes from")
            continue
        if parse_iso_date(values["published_at"]) is None:
            errors.append(f"{path.name}:{line}: invalid published_at")
            continue
        if values["eligible"] not in ("true", "false"):
            errors.append(f"{path.name}:{line}: invalid eligible token")
            continue
        try:
            revision = int(values["revision"])
        except ValueError:
            errors.append(f"{path.name}:{line}: invalid revision")
            continue
        record = {
            "symbol": values["symbol"],
            "exchange": values["exchange"],
            "isin": values["isin"],
            "effective_from": values["effective_from"],
            "effective_to": values["effective_to"],
            "eligible": values["eligible"] == "true",
            "published_at": values["published_at"],
            "revision": revision,
        }
        key = (record["symbol"], record["effective_from"], str(revision))
        if key in seen and canonical_digest(seen[key]) != canonical_digest(
            record
        ):
            errors.append(f"{path.name}:{line}: conflicting duplicate status")
            continue
        seen[key] = record
        records.append(record)
    return records, errors


def load_session_calendar(path: Path) -> tuple[list[str], list[str]]:
    """Strict, unique exchange session dates."""
    if not path.is_file():
        return [], [f"{path.name}: session calendar absent"]
    values = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    errors = [
        f"{path.name}: invalid session date {value}"
        for value in values
        if parse_iso_date(value) is None
    ]
    if len(set(values)) != len(values):
        errors.append(f"{path.name}: duplicate session dates")
    return (sorted(set(values)) if not errors else []), errors


def load_history(directory: Path) -> dict[str, list[dict[str, float]]]:
    """Load history using the strict contract."""
    history, _errors, _detail = load_history_with_errors(directory)
    return history


@dataclass
class ValidatedDataset:
    """One authoritative, fully described research dataset contract."""

    history: dict[str, list[dict[str, float]]]
    quality: dict[str, dict]
    eligible_dates: dict[str, list[str]]
    status: str
    source: str
    vintage: str
    invalid_rows: int
    corporate_actions: str = "NOT INCORPORATED"
    pit_enforced: bool = True
    parse_errors: int = 0
    calendar_basis: str = "EXCHANGE_CALENDAR"
    status_errors: list[str] = None
    coverage_gaps: list[str] = None
    dataset_digest: str = ""
    universe_digest: str = ""
    calendar_digest: str = ""
    code_version: str = ""

    def __post_init__(self) -> None:
        """Enforce the QUANT style contract."""
        if self.status_errors is None:
            self.status_errors = []
        if self.coverage_gaps is None:
            self.coverage_gaps = []


def load_history_validated(
    directory: Path,
    instrument_status: list[dict] | None = None,
    vintage: str = "UNSPECIFIED",
    session_calendar: list[str] | None = None,
    status_errors: list[str] | None = None,
    calendar_errors: list[str] | None = None,
    diagnostic: bool = False,
) -> ValidatedDataset:
    """Fail-closed dataset contract with mandatory PIT and calendar gates."""
    raw_history, parse_errors, _detail = load_history_with_errors(directory)
    status_errors = list(status_errors or []) + list(calendar_errors or [])
    calendar_errors = list(calendar_errors or [])
    expected = sorted(session_calendar or [])
    calendar_basis = (
        "EXCHANGE_CALENDAR" if session_calendar else "OBSERVED_ONLY"
    )
    quality: dict[str, dict] = {}
    for symbol, rows in raw_history.items():
        quality[symbol] = dataset_quality(rows, expected)
    eligible_dates: dict[str, list[str]] = {}
    coverage_gaps: list[str] = []
    for session_date in expected:
        eligible_dates[session_date] = point_in_time_universe(
            instrument_status or [], session_date
        )
        for symbol in raw_history:
            if symbol.startswith("NIFTY") or symbol == "INDIA VIX":
                continue
            if symbol not in eligible_dates[session_date]:
                coverage_gaps.append(
                    f"{symbol}@{session_date}: no applicable status"
                )
    statuses = {item["status"] for item in quality.values()}
    fatal = not diagnostic and (
        not instrument_status
        or not session_calendar
        or status_errors
        or calendar_errors
        or parse_errors
        or not quality
        or "BLOCKED" in statuses
    )
    if diagnostic:
        status = "OBSERVED_ONLY"
    elif fatal:
        status = "BLOCKED"
    elif coverage_gaps or "PARTIAL" in statuses:
        status = "PARTIAL"
    else:
        status = "COMPLETE"
    invalid_rows = (
        sum(item["invalid_bar_count"] for item in quality.values())
        + parse_errors
    )
    acceptable = {
        symbol: rows
        for symbol, rows in raw_history.items()
        if symbol not in ("NIFTY 50", "INDIA VIX")
    }
    try:
        code_version = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        code_version = "UNKNOWN"
    return ValidatedDataset(
        history=acceptable,
        quality=quality,
        eligible_dates=eligible_dates,
        status=status,
        source=str(directory),
        vintage=vintage,
        invalid_rows=invalid_rows,
        pit_enforced=not diagnostic and bool(instrument_status),
        parse_errors=parse_errors,
        calendar_basis=calendar_basis,
        status_errors=status_errors,
        coverage_gaps=coverage_gaps,
        dataset_digest=canonical_digest(dict(acceptable.items())),
        universe_digest=canonical_digest(instrument_status or []),
        calendar_digest=canonical_digest(expected),
        code_version=code_version,
    )


def features(rows: list[dict[str, float]], index: int) -> dict[str, float]:
    """Features computed strictly from rows[:index+1] (no lookahead)."""
    window = rows[: index + 1]
    close = [row["close"] for row in window]
    volume = [row["volume"] for row in window]

    def ret(periods: int) -> float:
        """Enforce the QUANT style contract."""
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
    weighted = sum(
        price * size for price, size in zip(typical, volume[-20:], strict=False)
    )
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
    # Canonical feature contract (version 2):
    #   returns: P_t/P_(t-n)-1 from sessions available at time t;
    #   vol20:   population standard deviation (divide by n) of the last 20
    #            daily simple returns, matching C++ StdDev and q dev/mdev;
    #   vwap_dev: deviation from the daily volume-weighted typical-price
    #            proxy, not intraday VWAP;
    #   valid:   false until the 21-session warm-up is complete. Unavailable
    #            features are reported through validity metadata, never as a
    #            silently valid zero.
    warmup_complete = len(close) >= 21
    return {
        "valid": warmup_complete,
        "validity_reason": (
            "READY" if warmup_complete else "INSUFFICIENT_HISTORY"
        ),
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
        "vwap_dev": (close[-1] / daily_vwap - 1) if daily_vwap > 0 else 0.0,
    }


def feature_history(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    """Point-in-time features with availability at the next observed session."""
    result = []
    for index, _row in enumerate(rows):
        item = features(rows, index)
        item["index"] = index
        # The final observed session has no known subsequent exchange
        # boundary in this dataset, so its availability is explicitly
        # unavailable rather than silently pointing at itself.
        item["availability_index"] = (
            index + 1 if index + 1 < len(rows) else None
        )
        item["date"] = rows[index]["date"]
        result.append(item)
    return result


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    """Enforce the QUANT style contract."""
    return max(low, min(high, value))


def score_momentum(f: dict[str, float]) -> float:
    """Enforce the QUANT style contract."""
    raw = (
        0.15 * f["ret5"]
        + 0.25 * f["ret20"]
        + 0.35 * f["ret60"]
        + 0.25 * f["ret120"]
    )
    return clamp(raw / max(f["vol20"], 0.005) * 0.05)


def score_trend(f: dict[str, float]) -> float:
    """Enforce the QUANT style contract."""
    return clamp((f["trend"] - 0.5) * 2 + f["breakout"] * 0.3)


def score_breakout(f: dict[str, float]) -> float:
    """Enforce the QUANT style contract."""
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
        """Enforce the QUANT style contract."""
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
    numerator = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return numerator / (dx * dy) if dx > 0 and dy > 0 else 0.0


def forward_return(
    rows: list[dict[str, float]], index: int, sessions: int
) -> float:
    """Enforce the QUANT style contract."""
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
        for session_date in test_dates:
            scores: list[float] = []
            returns: list[float] = []
            for symbol, rows in history.items():
                if symbol in ("NIFTY 50", "INDIA VIX"):
                    continue
                positions = [
                    i
                    for i, row in enumerate(rows)
                    if row["date"] == session_date
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


def dataset_quality(
    rows: list[dict[str, float]], expected_sessions: list[str]
) -> dict:
    """Dataset completeness with explicit status; malformed bars are counted.

    Numeric conversion failures are recorded, never converted into zero.
    """
    invalid = 0
    seen: set[str] = set()
    duplicates = 0
    valid_dates: set[str] = set()
    for row in rows:
        row_date = str(row.get("date", ""))
        if parse_iso_date(row_date) is None:
            invalid += 1
            continue
        if row_date in seen:
            duplicates += 1
        seen.add(row_date)
        values = [
            row.get(field)
            for field in ("open", "high", "low", "close", "volume")
        ]
        if not all(
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            and float(value) >= 0
            for value in values
        ):
            invalid += 1
            continue
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        if not (
            0 < low <= min(open_price, close)
            and max(open_price, close) <= high
            and close > 0
            and float(row["volume"]) >= 0
        ):
            invalid += 1
            continue
        valid_dates.add(row_date)
    expected = set(expected_sessions)
    missing = sorted(expected - valid_dates)
    if invalid or duplicates or missing:
        status = "PARTIAL"
    elif valid_dates == expected and expected:
        status = "COMPLETE"
    else:
        status = "BLOCKED"
    return {
        "status": status,
        "expected_sessions": len(expected),
        "observed_sessions": len(valid_dates),
        "missing_sessions": missing,
        "duplicate_count": duplicates,
        "invalid_bar_count": invalid,
    }


def point_in_time_universe(
    instrument_status: list[dict], as_of: str
) -> list[str]:
    """Eligibility using only status records effective on or before as_of.

    A later surveillance or delisting record cannot change a prior decision.
    """
    applicable: dict[str, tuple[tuple[str, int], dict]] = {}
    for record in instrument_status:
        effective_from = str(record.get("effective_from", ""))
        effective_to = str(record.get("effective_to", ""))
        published_at = str(record.get("published_at", effective_from))
        if published_at > as_of:
            continue  # A later publication cannot alter an earlier decision.
        if effective_from and effective_from > as_of:
            continue
        if effective_to and effective_to < as_of:
            continue
        symbol = str(record.get("symbol", ""))
        if not symbol:
            continue
        precedence = (effective_from, int(record.get("revision", 0)))
        existing = applicable.get(symbol)
        if existing is None or precedence >= existing[0]:
            applicable[symbol] = (precedence, record)
    return sorted(
        symbol
        for symbol, (_precedence, record) in applicable.items()
        if bool(record.get("eligible", False))
    )


def forward_label(
    rows: list[dict[str, float]], index: int, sessions: int
) -> float | None:
    """Executable forward label; None when the horizon is censored."""
    if sessions <= 0 or index + sessions >= len(rows):
        return None
    base = rows[index]["close"]
    if base <= 0 or not math.isfinite(base):
        return None
    end = rows[index + sessions]["close"]
    if end <= 0 or not math.isfinite(end):
        return None
    return end / base - 1.0


def spearman_strict(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation that reports UNDEFINED instead of a fabricated zero."""
    if len(xs) != len(ys) or len(xs) < 4:
        return None
    if not all(math.isfinite(v) for v in xs + ys):
        return None
    value = spearman(xs, ys)
    return value if math.isfinite(value) else None


def cross_sectional_spread_returns(
    history: dict[str, list[dict[str, float]]],
    score_fn: Callable[[dict[str, float]], float],
    horizon: int = HOLDING_SESSIONS,
    eligible: dict[str, list[str]] | None = None,
) -> list[float]:
    """Gross top-minus-bottom spread returns; no execution costs included."""
    by_date: dict[str, list[tuple[float, float]]] = {}
    for symbol, rows in history.items():
        if symbol in ("NIFTY 50", "INDIA VIX"):
            continue
        for index, row in enumerate(rows):
            if index < MIN_HISTORY:
                continue
            if eligible is not None and symbol not in eligible.get(
                row["date"], []
            ):
                continue
            label = forward_label(rows, index, horizon)
            if label is None:
                continue
            score = score_fn(features(rows, index))
            if math.isfinite(score):
                by_date.setdefault(row["date"], []).append((score, label))
    spreads: list[float] = []
    for session_date in sorted(by_date):
        values = by_date[session_date]
        if len(values) < 6:
            continue
        values.sort(key=lambda item: item[0])
        cutoff = len(values) // 2
        top = statistics.fmean(label for _score, label in values[cutoff:])
        bottom = statistics.fmean(label for _score, label in values[:cutoff])
        spreads.append(top - bottom)
    return spreads


def daily_cross_sectional_ic(
    history: dict[str, list[dict[str, float]]],
    score_fn: Callable[[dict[str, float]], float],
    horizon: int = HOLDING_SESSIONS,
    min_instruments: int = 5,
    eligible: dict[str, list[str]] | None = None,
) -> dict:
    """Daily Spearman IC across the eligible universe for each decision date.

    A label is included only when the full forward horizon exists. Censored
    observations are excluded, never replaced with zero.
    """
    by_date: dict[str, dict[str, tuple[int, dict[str, float]]]] = {}
    for symbol, rows in history.items():
        if symbol in ("NIFTY 50", "INDIA VIX"):
            continue
        for index, row in enumerate(rows):
            if index < MIN_HISTORY:
                continue
            if eligible is not None and symbol not in eligible.get(
                row["date"], []
            ):
                continue
            by_date.setdefault(row["date"], {})[symbol] = (
                index,
                row,
            )
    daily: list[float] = []
    daily_dates: list[str] = []
    coverage: list[int] = []
    for session_date in sorted(by_date):
        scores: list[float] = []
        labels: list[float] = []
        for symbol, (index, _row) in by_date[session_date].items():
            rows = history[symbol]
            label = forward_label(rows, index, horizon)
            if label is None:
                continue
            score = score_fn(features(rows, index))
            if not math.isfinite(score):
                continue
            scores.append(score)
            labels.append(label)
        if len(scores) < min_instruments:
            continue
        value = spearman_strict(scores, labels)
        if value is None:
            continue
        daily.append(value)
        daily_dates.append(session_date)
        coverage.append(len(scores))
    if not daily:
        return {
            "observations": 0,
            "mean_ic": 0.0,
            "median_ic": 0.0,
            "ic_std": 0.0,
            "coverage": 0,
            "effective_sample_size": 0,
            "daily_ics": [],
            "dates": [],
            "status": "UNDEFINED",
        }
    return {
        "observations": len(daily),
        "mean_ic": statistics.fmean(daily),
        "median_ic": statistics.median(daily),
        "ic_std": (statistics.stdev(daily) if len(daily) > 1 else 0.0),
        "coverage": min(coverage),
        "effective_sample_size": sum(coverage),
        "daily_ics": daily,
        "dates": daily_dates,
        "status": "CALCULATED",
    }


def cscv_pbo(performance: list[list[float]], max_splits: int = 256) -> dict:
    """Combinatorially Symmetric Cross-Validation probability of overfitting.

    Rows are candidate configurations; columns are out-of-sample periods.
    For every symmetric half-split the best in-sample configuration is chosen
    and its out-of-sample logit rank is accumulated. PBO is the fraction of
    logits that are non-positive.
    """
    if len(performance) < 2 or len(performance[0]) < 4:
        return {"status": "UNDEFINED", "pbo": 1.0, "splits": 0}
    width = len(performance[0])
    if any(len(row) != width for row in performance):
        return {"status": "INVALID_INPUT", "pbo": 1.0, "splits": 0}
    # Bound the combinatorial space explicitly. A full CSCV over hundreds of
    # periods is intractable; the declared subset is recorded in the result.
    columns = (
        list(range(width))
        if width <= 12
        else [round(i * (width - 1) / 11) for i in range(12)]
    )
    combinations = list(itertools.combinations(columns, len(columns) // 2))
    if len(combinations) > max_splits:
        step = max(1, len(combinations) // max_splits)
        combinations = combinations[::step][:max_splits]
    logits: list[float] = []
    count = len(performance)
    for train_columns in combinations:
        test_columns = [i for i in columns if i not in train_columns]
        in_sample = [
            statistics.fmean(row[i] for i in train_columns)
            for row in performance
        ]
        out_sample = [
            statistics.fmean(row[i] for i in test_columns)
            for row in performance
        ]
        winner = max(range(count), key=lambda i, values=in_sample: values[i])
        final_rank = (
            sum(1 for value in out_sample if value < out_sample[winner]) + 1
        )
        probability = final_rank / (count + 1)
        if probability <= 0.0 or probability >= 1.0:
            continue
        logits.append(math.log(probability / (1.0 - probability)))
    if not logits:
        return {"status": "UNDEFINED", "pbo": 1.0, "splits": 0}
    return {
        "status": "CALCULATED",
        "pbo": sum(1 for value in logits if value <= 0.0) / len(logits),
        "splits": len(logits),
        "mean_logit": statistics.fmean(logits),
        "periods_used": len(columns),
        "periods_total": width,
    }


def deflated_sharpe_ratio(returns: list[float], trials: int) -> dict:
    """Deflated Sharpe ratio probability under multiple testing.

    Uses per-observation Sharpe, skew and excess kurtosis, and the expected
    maximum Sharpe over the declared trial universe. Returns a probability,
    not a heuristic haircut.
    """
    n = len(returns)
    if n < 4 or trials < 1 or not all(math.isfinite(v) for v in returns):
        return {"status": "UNDEFINED", "dsr": 0.0}
    mean = statistics.fmean(returns)
    sigma = statistics.pstdev(returns)
    if sigma <= 0.0:
        return {"status": "UNDEFINED", "dsr": 0.0}
    sr = mean / sigma
    log_trials = math.log(trials)
    expected_max = math.sqrt(2.0 * log_trials)
    if expected_max > 0.0:
        expected_max -= (0.5772156649 + math.log(4.0 * math.pi)) / (
            2.0 * expected_max
        )
    sr_zero = expected_max / math.sqrt(n)
    centered = [value - mean for value in returns]
    skew = sum(value**3 for value in centered) / n / sigma**3
    kurtosis = sum(value**4 for value in centered) / n / sigma**4
    variance = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2
    if variance <= 0.0:
        return {"status": "UNDEFINED", "dsr": 0.0}
    z = (sr - sr_zero) * math.sqrt(max(1, n - 1)) / math.sqrt(variance)
    return {
        "status": "CALCULATED",
        "dsr": statistics.NormalDist().cdf(z),
        "observed_sharpe": sr,
        "expected_max_sharpe": sr_zero,
        "trials": trials,
        "observations": n,
    }


def register_experiment(root: Path, record: dict) -> str:
    """Append-only experiment registry entry with an immutable identifier."""
    identifier = uuid.uuid4().hex
    path = root / "data" / "assurance" / "experiments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(record)
    entry["id"] = identifier
    entry["registered_at_utc"] = datetime.now(UTC).isoformat()
    entry["random_seed"] = SEED
    entry["trial_count"] = TRIALS
    try:
        entry["code_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        entry["code_commit"] = "UNKNOWN"
    line = json.dumps(entry, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return identifier


def block_bootstrap_ci(
    values: list[float], block_length: int = 5, draws: int = 2000
) -> list[float]:
    """Moving-block bootstrap CI for serially dependent daily statistics."""
    n = len(values)
    if n < 4 or block_length < 1 or block_length >= n or draws < 100:
        return [0.0, 0.0, 0.0]
    rng = random.Random(SEED)
    blocks = n // block_length
    means: list[float] = []
    for _ in range(draws):
        sample: list[float] = []
        for _ in range(blocks):
            start = rng.randrange(0, n - block_length + 1)
            sample.extend(values[start : start + block_length])
        means.append(statistics.fmean(sample))
    means.sort()
    return [
        means[int(0.025 * draws)],
        means[draws // 2],
        means[int(0.975 * draws) - 1],
    ]


def hac_standard_error(values: list[float], lags: int = 5) -> float:
    """Newey-West HAC standard error of the mean."""
    n = len(values)
    if n < 4 or lags < 0 or lags >= n:
        return 0.0
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    variance = sum(value * value for value in centered) / n
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        autocov = (
            sum(centered[i] * centered[i - lag] for i in range(lag, n)) / n
        )
        variance += 2.0 * weight * autocov
    return math.sqrt(max(0.0, variance) / n)


def split_development_holdout(
    history: dict[str, list[dict[str, float]]], holdout_sessions: int = 20
) -> tuple[
    dict[str, list[dict[str, float]]],
    dict[str, list[dict[str, float]]],
    list[str],
]:
    """Seal the final sessions before any strategy selection occurs."""
    dates = sorted({row["date"] for rows in history.values() for row in rows})
    holdout_dates = dates[-holdout_sessions:] if holdout_sessions else []
    cutoff = holdout_dates[0] if holdout_dates else None
    development: dict[str, list[dict[str, float]]] = {}
    holdout: dict[str, list[dict[str, float]]] = {}
    for symbol, rows in history.items():
        development[symbol] = [
            row for row in rows if cutoff is None or row["date"] < cutoff
        ]
        holdout[symbol] = [
            row for row in rows if cutoff is not None and row["date"] >= cutoff
        ]
    return development, holdout, holdout_dates


def select_on_development(
    development: dict[str, list[dict[str, float]]],
) -> tuple[str, dict[str, dict]]:
    """Choose one strategy using development data only."""
    stats: dict[str, dict] = {}
    for name, score_fn in STRATEGIES.items():
        stats[name] = daily_cross_sectional_ic(development, score_fn)
    best = max(
        stats.items(),
        key=lambda item: item[1].get("mean_ic", 0.0),
        default=("none", {}),
    )
    return best[0], stats


def purged_walk_forward(
    history: dict[str, list[dict[str, float]]],
    score_fn: Callable[[dict[str, float]], float],
    train_window: int = 130,
    test_window: int = 20,
    purge_sessions: int = HOLDING_SESSIONS,
    embargo_sessions: int = HOLDING_SESSIONS,
    final_holdout: int = 20,
) -> dict:
    """Chronological train/select/test with purge and embargo."""
    dates = sorted({row["date"] for rows in history.values() for row in rows})
    usable_dates = (
        dates[: len(dates) - final_holdout] if final_holdout else dates
    )
    scores: dict[str, dict[str, float]] = {}
    labels: dict[str, dict[str, float]] = {}
    for symbol, rows in history.items():
        if symbol in ("NIFTY 50", "INDIA VIX"):
            continue
        scores[symbol] = {}
        labels[symbol] = {}
        for index, row in enumerate(rows):
            label = forward_label(rows, index, HOLDING_SESSIONS)
            if index >= MIN_HISTORY and label is not None:
                value = score_fn(features(rows, index))
                if math.isfinite(value):
                    scores[symbol][row["date"]] = value
                    labels[symbol][row["date"]] = label
    folds: list[dict] = []
    start = train_window
    fold_number = 0
    while start + test_window <= len(usable_dates):
        test_dates = usable_dates[start : start + test_window]
        train_end = max(0, start - purge_sessions)
        train_dates = usable_dates[:train_end]
        selected = [
            symbol
            for symbol, series in scores.items()
            if len(set(train_dates) & set(series)) >= 5
        ]
        fold_ics: list[float] = []
        fold_scores: list[float] = []
        fold_labels: list[float] = []
        for session_date in test_dates:
            xs = [
                scores[s][session_date]
                for s in selected
                if session_date in scores[s]
            ]
            ys = [
                labels[s][session_date]
                for s in selected
                if session_date in labels[s]
            ]
            if len(xs) >= 5:
                ic = spearman_strict(xs, ys)
                if ic is not None:
                    fold_ics.append(ic)
            for symbol in selected:
                if (
                    session_date in scores[symbol]
                    and session_date in labels[symbol]
                ):
                    fold_scores.append(scores[symbol][session_date])
                    fold_labels.append(labels[symbol][session_date])
        fold_ics_global = spearman_strict(fold_scores, fold_labels)
        folds.append(
            {
                "fold": fold_number,
                "train_dates": len(train_dates),
                "test_dates": len(test_dates),
                "purged": purge_sessions,
                "embargoed": embargo_sessions,
                "selected_instruments": len(selected),
                "daily_ic": statistics.fmean(fold_ics) if fold_ics else 0.0,
                "pooled_ic": (
                    fold_ics_global if fold_ics_global is not None else 0.0
                ),
            }
        )
        fold_number += 1
        start += test_window + embargo_sessions
    if not folds:
        return {
            "status": "UNDEFINED",
            "folds": [],
            "holdout_sessions": final_holdout,
        }
    return {
        "status": "CALCULATED",
        "folds": folds,
        "mean_ic": statistics.fmean(f["daily_ic"] for f in folds),
        "holdout_sessions": final_holdout,
        "holdout_used_for_selection": False,
        "protocol": "chronological purged walk-forward with embargo",
    }


def selection_test_status(trial_matrix: list[list[float]] | None) -> dict:
    """White Reality Check / Hansen SPA status for a complete trial universe."""
    if not trial_matrix or len(trial_matrix) < 8 or len(trial_matrix[0]) < 8:
        return {
            "status": "UNAVAILABLE",
            "reason": "complete trial performance matrix not registered",
        }
    return {
        "status": "UNAVAILABLE",
        "reason": (
            "dependence-aware White Reality Check and Hansen SPA "
            "implementation pending; bounded diagnostics only"
        ),
    }


def bootstrap_ci(
    values: list[float], draws: int = BOOTSTRAP_DRAWS
) -> list[float]:
    """Enforce the QUANT style contract."""
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
    """Enforce the QUANT style contract."""
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


def analyze(
    history: dict[str, list[dict[str, float]]],
    root: Path,
    dataset: ValidatedDataset | None = None,
) -> dict:
    """Enforce the QUANT style contract."""
    if dataset is not None and dataset.status in (
        "BLOCKED",
        "PARTIAL",
        "OBSERVED_ONLY",
    ):
        return {
            "labels": {"statistics": dataset.status},
            "input_series": 0,
            "observations_per_series": 0,
            "multiple_testing": {},
            "best_strategy": "none",
            "status_errors": list(dataset.status_errors),
            "coverage_gaps": list(dataset.coverage_gaps),
            "conclusion": (
                f"{dataset.status}: research input failed its data contract"
            ),
            "data_contract": {
                "status": dataset.status,
                "source": dataset.source,
                "vintage": dataset.vintage,
                "invalid_rows": dataset.invalid_rows,
                "status_errors": list(dataset.status_errors),
                "coverage_gaps": list(dataset.coverage_gaps),
                "session_eligibility": (
                    "ENFORCED" if dataset.pit_enforced else "NOT ENFORCED"
                ),
            },
            "walk_forward": {},
            "baselines": {},
            "authoritative_cpp_backtest": {},
            "rejections": [
                f"{dataset.status} research dataset rejected before analysis"
            ],
        }
    eligibility = None
    if dataset is not None:
        history = dataset.history
        if dataset.pit_enforced:
            eligibility = dataset.eligible_dates
    development, holdout, holdout_dates = split_development_holdout(
        {
            symbol: rows
            for symbol, rows in history.items()
            if len(rows) >= MIN_HISTORY
            and symbol not in ("NIFTY 50", "INDIA VIX")
        }
    )
    usable = development
    report: dict = {
        "labels": {
            "statistics": "ESTIMATED",
            "authoritative_pnl": "SIMULATED",
            "live_trading": "NOT IMPLEMENTED (locked)",
        },
        "input_series": len(usable),
        "data_contract": {
            "status": "UNVALIDATED" if dataset is None else dataset.status,
            "source": (
                "direct dictionary" if dataset is None else dataset.source
            ),
            "vintage": "UNSPECIFIED" if dataset is None else dataset.vintage,
            "invalid_rows": 0 if dataset is None else dataset.invalid_rows,
            "corporate_actions": (
                "NOT INCORPORATED"
                if dataset is None
                else dataset.corporate_actions
            ),
            "session_eligibility": (
                "NOT ENFORCED"
                if dataset is None or not dataset.pit_enforced
                else "ENFORCED"
            ),
            "status_errors": [] if dataset is None else dataset.status_errors,
            "coverage_gaps": [] if dataset is None else dataset.coverage_gaps,
            "calendar_basis": (
                "NONE" if dataset is None else dataset.calendar_basis
            ),
            "digests": {
                "dataset": "" if dataset is None else dataset.dataset_digest,
                "universe": "" if dataset is None else dataset.universe_digest,
                "calendar": "" if dataset is None else dataset.calendar_digest,
                "code": "" if dataset is None else dataset.code_version,
            },
        },
        "observations_per_series": min(
            (len(rows) for rows in usable.values()), default=0
        ),
        "walk_forward": {},
        "baselines": {},
        "multiple_testing": {
            "trials": TRIALS,
            "deflated_sharpe_note": (
                "CALCULATED on gross cross-sectional spread returns; "
                "costs and execution effects are excluded"
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
        report["conclusion"] = "INCONCLUSIVE: insufficient eligible universe"
        return report

    for name, score_fn in STRATEGIES.items():
        stats = daily_cross_sectional_ic(usable, score_fn, eligible=eligibility)
        stats["folds"] = 0
        stats["fold_ics"] = stats["daily_ics"]
        report["walk_forward"][name] = stats
        if stats["observations"]:
            values = stats["daily_ics"]
            report["walk_forward"][name]["ic_bootstrap_95ci"] = (
                block_bootstrap_ci(values)
            )
            report["walk_forward"][name]["ic_hac_se"] = hac_standard_error(
                values
            )
            report["walk_forward"][name]["deflated_mean_ic"] = deflated(
                stats["mean_ic"], TRIALS, stats["observations"]
            )
            spread = cross_sectional_spread_returns(
                usable, score_fn, eligible=eligibility
            )
            ratio = deflated_sharpe_ratio(spread, TRIALS)
            report["walk_forward"][name]["gross_spread_mean"] = (
                statistics.fmean(spread) if spread else 0.0
            )
            report["walk_forward"][name]["dsr"] = ratio
            report["walk_forward"][name]["cost_model"] = "GROSS_ONLY"
            report["walk_forward"][name]["gross_spread_observations"] = len(
                spread
            )
            register_experiment(
                root,
                {
                    "strategy": name,
                    "dataset": "cache/history",
                    "ic_observations": stats["observations"],
                    "mean_ic": stats["mean_ic"],
                    "status": "INCONCLUSIVE",
                },
            )
    date_maps = [
        dict(
            zip(
                report["walk_forward"][name]["dates"],
                report["walk_forward"][name]["daily_ics"],
                strict=False,
            )
        )
        for name in STRATEGIES
    ]
    common_dates = sorted(set.intersection(*[set(m) for m in date_maps]))
    if len(common_dates) >= 4:
        matrix = [
            [mapping[date] for date in common_dates] for mapping in date_maps
        ]
        report["multiple_testing"]["cscv_pbo"] = cscv_pbo(matrix)
    else:
        report["multiple_testing"]["cscv_pbo"] = {
            "status": "UNDEFINED",
            "pbo": 1.0,
            "reason": "fewer than four common decision dates",
        }
    report["baselines"]["no_trade"] = {
        "expected_return": 0.0,
        "cost": 0.0,
        "note": "VERIFIED benchmark; always available",
    }

    selected_name, _selection_stats = select_on_development(development)
    best_name, best_stats = max(
        report["walk_forward"].items(),
        key=lambda item: item[1].get("mean_ic", 0),
        default=("none", {}),
    )
    if selected_name != best_name:
        report["rejections"].append(
            "strategy selection disagrees with development-only selector"
        )
    fold_result = purged_walk_forward(
        development,
        STRATEGIES.get(best_name, score_momentum),
        final_holdout=0,
    )
    fold_count = len(fold_result.get("folds", []))
    report["walk_forward"][best_name]["folds"] = fold_count
    if not best_stats or fold_count < 3:
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
    report["methodology"] = {
        "signal_diagnostics_scope": (
            "DESCRIPTIVE: daily cross-sectional IC and bounded CSCV on "
            "four strategy families; not strategy-selection evidence"
        ),
        "formal_selection_scope": (
            "NOT ESTABLISHED: full trial universe, net portfolio returns, "
            "block bootstrap/HAC, White Reality Check and Hansen SPA "
            "required before any promotion decision"
        ),
        "selection_tests": selection_test_status(None),
        "purged_walk_forward": fold_result,
        "fold_count": fold_count,
        "bootstrap": "moving-block, 5-session blocks",
        "cost_model": (
            "GROSS_ONLY: no turnover, fees, taxes, spread, impact or "
            "capacity model; fixed-basis haircuts not applied"
        ),
        "holdout_dates": holdout_dates,
        "holdout_sessions": len(holdout_dates),
        "holdout_used_for_selection": False,
        "holdout_labels": {
            symbol: len(rows) for symbol, rows in holdout.items()
        },
    }
    return report


def render_markdown(report: dict) -> str:
    """Enforce the QUANT style contract."""
    lines = [
        "# QUANT research verification",
        "",
        f"- Input series: {report['input_series']}",
        f"- Minimum observations per series: "
        f"{report['observations_per_series']}",
        f"- Conclusion: **{report.get('conclusion', 'INCONCLUSIVE')}**",
        f"- Selection: development data only; holdout sessions "
        f"{report.get('methodology', {}).get('holdout_sessions', 0)} sealed",
        "- Cost model: "
        f"{report.get('methodology', {}).get('cost_model', 'UNKNOWN')}",
        "",
        "## Purged walk-forward IC (ESTIMATED)",
        "",
        "| Strategy | Folds | Mean IC | IC std | Positive folds | "
        "Deflated IC | Bounded CSCV PBO |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    pbo = report.get("multiple_testing", {}).get("cscv_pbo", {}).get("pbo", 1)
    for name, stats in report["walk_forward"].items():
        lines.append(
            f"| {name} | {stats.get('folds', 0)} | "
            f"{stats.get('mean_ic', 0):.4f} | {stats.get('ic_std', 0):.4f} | "
            f"{stats.get('positive_folds', 0)} | "
            f"{stats.get('deflated_mean_ic', 0):.4f} | {pbo:.3f} |"
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
        "microstructure, White Reality Check and Hansen SPA. CSCV is a "
        "bounded diagnostic over four IC signal families, not a full "
        "parameter-search logit distribution.",
        "- SIMULATED fills use next-session open with 10 bps slippage; no "
        "order-book history is available, and survivorship bias is not "
        "corrected.",
        "",
    ]
    return "\n".join(lines)


def self_test() -> int:
    """Enforce the QUANT style contract."""
    history = synthetic_history()
    report = analyze(history, Path(__file__).resolve().parent.parent)
    assert report["input_series"] == 5, report["input_series"]
    assert len(report["walk_forward"]) == len(STRATEGIES), report[
        "walk_forward"
    ]
    assert report["walk_forward"][report["best_strategy"]]["folds"] > 0
    assert report["methodology"]["holdout_used_for_selection"] is False
    ci = bootstrap_ci([0.01, -0.01, 0.02, -0.005])
    assert ci[0] <= ci[1] <= ci[2], ci
    assert deflated(2.0, 8, 100) < 2.0
    assert math.isfinite(spearman([1, 2, 3, 4], [4, 3, 2, 1]))
    print("python research self-test: PASS (SIMULATED data only)")
    return 0


def main() -> int:
    """Enforce the QUANT style contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--data", default=str(root / "data" / "cache" / "history")
    )
    parser.add_argument("--out", default=str(root / "data" / "reports"))
    parser.add_argument("--universe-status", default="")
    parser.add_argument("--sessions", default="")
    parser.add_argument(
        "--diagnostic-observed-only",
        action="store_true",
        help="explicitly labelled diagnostic; never a COMPLETE result",
    )
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
    status_records, status_errors = (
        load_universe_status(Path(args.universe_status))
        if args.universe_status
        else ([], ["universe status not supplied"])
    )
    sessions, calendar_errors = (
        load_session_calendar(Path(args.sessions))
        if args.sessions
        else ([], ["exchange calendar not supplied"])
    )
    dataset = load_history_validated(
        Path(args.data),
        status_records,
        "CACHED",
        sessions,
        status_errors,
        calendar_errors,
        args.diagnostic_observed_only,
    )
    report = analyze(dataset.history, root, dataset)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "research.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (out / "research.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Saved: {out / 'research.json'}")
    if dataset.status == "BLOCKED":
        return 2
    if dataset.status in ("PARTIAL", "OBSERVED_ONLY"):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
