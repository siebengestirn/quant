#!/usr/bin/env python3
"""Fail-closed history dataset loading and validation for research.

This module owns the strict CSV readers, the shared v2 dataset contract
verification, and the immutable provenance attached to every validated
dataset. All work is offline; no live qualification is claimed.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import importlib.util
import json
import math
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_provenance = importlib.import_module("data_provenance")
_registry_service = importlib.import_module("instrument_registry_service")
_git = importlib.import_module("research_git")

REAL_SOURCE_CLASSES = _provenance.REAL_SOURCE_CLASSES
is_sha256_hex = _provenance.is_sha256_hex

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
    """Return the SHA-256 digest of canonical JSON bytes."""
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


def _dataset_contract_module():
    """Load the shared v2 dataset contract implementation by file path."""
    name = "quant_dataset_contract"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().with_name("dataset_contract.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify_history_manifest(
    directory: Path,
) -> tuple[dict[str, dict], list[str]]:
    """Verify the shared, language-neutral v2 dataset contract."""
    contract = _dataset_contract_module()
    result = contract.verify_dataset(directory)
    verified = {symbol: {} for symbol in result.file_digests}
    errors = list(result.errors)
    if result.status == "BLOCKED" and not errors:
        errors.append("dataset contract blocked")
    return verified, errors


def load_history(directory: Path) -> dict[str, list[dict[str, float]]]:
    """Load history using the strict contract."""
    history, _errors, _detail = load_history_with_errors(directory)
    return history


@dataclass(frozen=True)
class DatasetProvenance:
    """Immutable source classification and digests for one dataset."""

    data_classification: str = "UNVERIFIED"
    source_artifact_digest: str = ""
    corporate_action_digest: str = "UNKNOWN"
    feature_version_digest: str = "UNKNOWN"
    instrument_registry_digest: str = "UNKNOWN"


def _lineage_digest(lineage: dict | None, key: str) -> str:
    """Read a lineage digest without inventing a value for None/missing."""
    value = (lineage or {}).get(key, "UNKNOWN")
    return "UNKNOWN" if value is None else str(value)


def dataset_provenance(
    data_classification: str = "UNVERIFIED",
    source_artifact_digest: str = "",
    lineage: dict | None = None,
) -> DatasetProvenance:
    """Build fail-closed provenance from a classification and lineage dict."""
    return DatasetProvenance(
        data_classification=data_classification,
        source_artifact_digest=source_artifact_digest,
        corporate_action_digest=_lineage_digest(
            lineage, "corporate_action_digest"
        ),
        feature_version_digest=_lineage_digest(
            lineage, "feature_version_digest"
        ),
        instrument_registry_digest=_lineage_digest(
            lineage, "instrument_registry_digest"
        ),
    )


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
    provenance: DatasetProvenance = field(default_factory=DatasetProvenance)

    @property
    def data_classification(self) -> str:
        """Read the nested source classification."""
        return self.provenance.data_classification

    @property
    def source_artifact_digest(self) -> str:
        """Read the nested source artifact digest."""
        return self.provenance.source_artifact_digest

    @property
    def corporate_action_digest(self) -> str:
        """Read the nested corporate-action lineage digest."""
        return self.provenance.corporate_action_digest

    @property
    def feature_version_digest(self) -> str:
        """Read the nested feature-version lineage digest."""
        return self.provenance.feature_version_digest

    @property
    def instrument_registry_digest(self) -> str:
        """Read the nested instrument-registry lineage digest."""
        return self.provenance.instrument_registry_digest

    def __post_init__(self) -> None:
        """Validate the decoded dataset fields after construction."""
        if self.status_errors is None:
            self.status_errors = []
        if self.coverage_gaps is None:
            self.coverage_gaps = []
        if (
            self.provenance.data_classification in REAL_SOURCE_CLASSES
            and not is_sha256_hex(self.provenance.source_artifact_digest)
        ):
            self.status_errors.append(
                "real-source classification refused: source_artifact_digest "
                "is not a 64-hex digest; downgraded to UNVERIFIED"
            )
            self.provenance = replace(
                self.provenance, data_classification="UNVERIFIED"
            )


def load_history_validated(
    directory: Path,
    instrument_status: list[dict] | None = None,
    vintage: str = "UNSPECIFIED",
    session_calendar: list[str] | None = None,
    status_errors: list[str] | None = None,
    calendar_errors: list[str] | None = None,
    diagnostic: bool = False,
    manifest: bool = True,
    *,
    resolver: Callable[[str, str, str], object] | None = None,
    observed_at: str = "",
    provenance: DatasetProvenance | None = None,
) -> ValidatedDataset:
    """Fail-closed dataset contract with mandatory PIT and calendar gates."""
    raw_history, parse_errors, _detail = load_history_with_errors(directory)
    verified_files: dict[str, dict] = {}
    manifest_errors: list[str] = []
    if manifest:
        verified_files, manifest_errors = verify_history_manifest(directory)
    if manifest and not verified_files:
        manifest_errors.append("no verified dataset files")
    status_errors = (
        list(status_errors or [])
        + list(calendar_errors or [])
        + manifest_errors
    )
    calendar_errors = list(calendar_errors or [])
    expected = sorted(session_calendar or [])
    calendar_basis = (
        "EXCHANGE_CALENDAR" if session_calendar else "OBSERVED_ONLY"
    )
    quality: dict[str, dict] = {}
    if manifest:
        raw_history = {
            symbol: rows
            for symbol, rows in raw_history.items()
            if symbol in verified_files
        }
    coverage_gaps: list[str] = []
    if resolver is not None:
        resolved = _registry_service.resolve_history(
            raw_history, resolver, observed_at
        )
        raw_history = resolved.history
        coverage_gaps.extend(resolved.gaps)
    for symbol, rows in raw_history.items():
        quality[symbol] = dataset_quality(rows, expected)
    eligible_dates: dict[str, list[str]] = {}
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
    code_version = _git.head_commit(Path(__file__).resolve().parent.parent)
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
        provenance=(DatasetProvenance() if provenance is None else provenance),
    )


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
