#!/usr/bin/env python3
"""QUANT dataset snapshot contract v2 (language-neutral implementation).

This module and the C++ implementation in src/dataset.cpp follow the single
specification in docs/dataset-contract-v2.md. SHA-256 is the only digest
algorithm, so manifests are interchangeable across the two languages.

Usage:
    python3 python/dataset_contract.py write <dir> <spec.json>
    python3 python/dataset_contract.py verify <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA_VERSION = 2
DIGEST_ALGORITHM = "sha256"
HEADER = ("date", "open", "high", "low", "close", "volume")
CONTRACT_FILE = "contract.json"
MANIFEST_FILE = "manifest.json"
CONTRACT_PREFIX = "QUANT-DATASET-CONTRACT-V2"

_SYMBOL_RE = re.compile(r"^[A-Z0-9 .&/_-]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CONTRACT_FIELDS = {
    "schema_version",
    "snapshot_id",
    "calendar_id",
    "generated_at_ms",
    "provenance",
    "symbols",
    "sessions",
    "first_date",
    "last_date",
    "min_rows",
    "instrument_ids",
}
MANIFEST_FIELDS = {
    "schema_version",
    "digest_algorithm",
    "contract_digest",
    "contract_sha256",
    "expectations",
    "files",
}
FILE_FIELDS = {
    "digest",
    "rows",
    "first_date",
    "last_date",
    "instrument_id",
    "raw_digest",
}


@dataclass
class DatasetResult:
    """Structured outcome of one dataset verification."""

    status: str = "FAILED"
    contract_digest: str = ""
    file_digests: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)


def sha256_hex(content: bytes) -> str:
    """Return the lowercase hex SHA-256 of exact bytes."""
    return hashlib.sha256(content).hexdigest()


def is_canonical_date(value: object) -> bool:
    """Return True for a real ISO calendar date, never a merely shaped one."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_uint(value: object) -> bool:
    """Return True only for a non-negative integer (excluding booleans)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def canonical_expectations(expectations: dict) -> str:
    """Build the exact digest input defined by the v2 specification."""
    symbols = sorted(expectations["symbols"])
    sessions = sorted(expectations["sessions"])
    ids = expectations["instrument_ids"]
    pairs = ",".join(f"{symbol}:{ids[symbol]}" for symbol in symbols)
    lines = [
        CONTRACT_PREFIX,
        "schema_version=2",
        "digest_algorithm=sha256",
        f"snapshot_id={expectations['snapshot_id']}",
        f"calendar_id={expectations['calendar_id']}",
        f"generated_at_ms={expectations['generated_at_ms']}",
        f"provenance={expectations['provenance']}",
        f"symbols={','.join(symbols)}",
        f"sessions={','.join(sessions)}",
        f"first_date={expectations['first_date']}",
        f"last_date={expectations['last_date']}",
        f"min_rows={expectations['min_rows']}",
        f"instrument_ids={pairs}",
    ]
    return "".join(f"{line}\n" for line in lines)


def contract_digest(expectations: dict) -> str:
    """Hash the canonical expectations with the only accepted algorithm."""
    return sha256_hex(canonical_expectations(expectations).encode("utf-8"))


def _validate_identity(expectations: dict) -> list[str]:
    """Validate snapshot identity, provenance and ingestion metadata."""
    errors: list[str] = []
    if expectations["schema_version"] != SCHEMA_VERSION or isinstance(
        expectations["schema_version"], bool
    ):
        errors.append("unsupported expectations schema")
    for name in ("snapshot_id", "calendar_id", "provenance"):
        value = expectations[name]
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            errors.append(f"invalid {name}")
    if not is_uint(expectations["generated_at_ms"]):
        errors.append("invalid generated_at_ms")
    return errors


def _validate_lists(expectations: dict) -> list[str]:
    """Validate the sorted, unique symbol and session lists."""
    errors: list[str] = []
    symbols = expectations["symbols"]
    sessions = expectations["sessions"]
    if not isinstance(symbols, list) or not symbols:
        errors.append("symbols must be a non-empty array")
    elif any(
        not isinstance(s, str) or not _SYMBOL_RE.fullmatch(s) for s in symbols
    ):
        errors.append("invalid symbol grammar")
    elif len(set(symbols)) != len(symbols) or symbols != sorted(symbols):
        errors.append("symbols must be sorted and unique")
    if not isinstance(sessions, list) or not sessions:
        errors.append("sessions must be a non-empty array")
    elif any(not is_canonical_date(s) for s in sessions):
        errors.append("invalid session date")
    elif len(set(sessions)) != len(sessions) or sessions != sorted(sessions):
        errors.append("sessions must be sorted and unique")
    return errors


def _validate_range(expectations: dict) -> list[str]:
    """Validate the declared date range and minimum history."""
    errors: list[str] = []
    for name in ("first_date", "last_date"):
        if not is_canonical_date(expectations[name]):
            errors.append(f"invalid {name}")
    if not is_uint(expectations["min_rows"]) or expectations["min_rows"] < 1:
        errors.append("min_rows must be at least one")
    first = expectations["first_date"]
    last = expectations["last_date"]
    if is_canonical_date(first) and is_canonical_date(last) and first > last:
        errors.append("first_date is after last_date")
    return errors


def _validate_identities(expectations: dict, symbols: object) -> list[str]:
    """Validate that permanent identities cover exactly the symbols."""
    ids = expectations["instrument_ids"]
    if not isinstance(ids, dict):
        return ["instrument_ids must be an object"]
    errors: list[str] = []
    if isinstance(symbols, list) and set(ids) != set(symbols):
        errors.append("instrument_ids must cover exactly the symbols")
    if any(
        not isinstance(value, str) or not _ID_RE.fullmatch(value)
        for value in ids.values()
    ):
        errors.append("invalid instrument identity grammar")
    return errors


def validate_expectations(expectations: object) -> list[str]:
    """Validate an expectations object against the frozen v2 grammar."""
    if not isinstance(expectations, dict):
        return ["expectations must be an object"]
    unknown = sorted(set(expectations) - CONTRACT_FIELDS)
    missing = sorted(CONTRACT_FIELDS - set(expectations))
    errors: list[str] = []
    if unknown:
        errors.append(f"unknown expectations fields: {','.join(unknown)}")
    if missing:
        errors.append(f"missing expectations fields: {','.join(missing)}")
    if errors:
        return errors
    errors.extend(_validate_identity(expectations))
    errors.extend(_validate_lists(expectations))
    errors.extend(_validate_range(expectations))
    errors.extend(_validate_identities(expectations, expectations["symbols"]))
    return errors


def read_expectations(directory: Path) -> tuple[dict | None, list[str]]:
    """Read and validate the frozen contract from a dataset directory."""
    path = directory / CONTRACT_FILE
    if not path.is_file():
        return None, ["contract.json: snapshot contract absent"]
    try:
        expectations = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"contract.json: invalid JSON: {exc}"]
    errors = validate_expectations(expectations)
    first = (
        expectations.get("first_date")
        if isinstance(expectations, dict)
        else None
    )
    last = (
        expectations.get("last_date")
        if isinstance(expectations, dict)
        else None
    )
    if (
        isinstance(first, str)
        and isinstance(last, str)
        and is_canonical_date(first)
        and is_canonical_date(last)
        and first > last
    ):
        errors.append("first_date is after last_date")
    if errors:
        return None, errors
    return expectations, []


def write_contract(directory: Path, expectations: dict) -> list[str]:
    """Write a validated, deterministic contract.json."""
    errors = validate_expectations(expectations)
    if errors:
        return errors
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(expectations, indent=2, sort_keys=True) + "\n"
    (directory / CONTRACT_FILE).write_text(payload, encoding="utf-8")
    return []


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    """Parse one canonical OHLCV CSV with strict row and calendar checks."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [], [f"{path.name}: unreadable CSV: {exc}"]
    if not rows or tuple(rows[0]) != HEADER:
        return [], [f"{path.name}: invalid canonical header"]
    errors: list[str] = []
    candles: list[dict] = []
    seen: set[str] = set()
    for number, fields in enumerate(rows[1:], start=2):
        if len(fields) != len(HEADER):
            errors.append(f"{path.name}:{number}: row width mismatch")
            continue
        session = fields[0]
        if not is_canonical_date(session) or session in seen:
            errors.append(f"{path.name}:{number}: invalid or duplicate date")
            continue
        seen.add(session)
        try:
            values = [float(value) for value in fields[1:]]
        except ValueError:
            errors.append(f"{path.name}:{number}: non-numeric value")
            continue
        opening, high, low, close, volume = values
        if not all(math.isfinite(value) for value in values):
            errors.append(f"{path.name}:{number}: non-finite value")
        elif (
            low <= 0
            or close <= 0
            or low > min(opening, close)
            or max(opening, close) > high
            or volume < 0
        ):
            errors.append(f"{path.name}:{number}: invalid OHLCV relationship")
        candles.append(
            {
                "date": session,
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    candles.sort(key=lambda row: row["date"])
    return candles, errors


def write_manifest(
    directory: Path,
    expectations: dict,
    raw_digests: dict[str, str] | None = None,
) -> list[str]:
    """Derive and persist a v2 manifest from a validated snapshot contract."""
    errors = validate_expectations(expectations)
    if errors:
        return errors
    raw = raw_digests or {}
    files: dict[str, dict] = {}
    for symbol in expectations["symbols"]:
        path = directory / f"{symbol}.csv"
        if not path.is_file():
            errors.append(f"{symbol}: missing series")
            continue
        content = path.read_bytes()
        candles, parse_errors = _read_csv(path)
        errors.extend(parse_errors)
        sessions = [row["date"] for row in candles]
        if sessions != expectations["sessions"]:
            errors.append(f"{symbol}: coverage differs from declared sessions")
        digest = sha256_hex(content)
        files[symbol] = {
            "digest": digest,
            "rows": len(candles),
            "first_date": sessions[0] if sessions else "",
            "last_date": sessions[-1] if sessions else "",
            "instrument_id": expectations["instrument_ids"][symbol],
            "raw_digest": raw.get(symbol, ""),
        }
        if len(candles) < expectations["min_rows"]:
            errors.append(f"{symbol}: insufficient rows")
    if errors:
        return errors
    contract_bytes = (directory / CONTRACT_FILE).read_bytes()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "contract_digest": contract_digest(expectations),
        "contract_sha256": sha256_hex(contract_bytes),
        "expectations": expectations,
        "files": files,
    }
    (directory / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return []


def _verify_structure(manifest: object) -> tuple[dict | None, list[str]]:
    """Check manifest grammar before trusting any of its values."""
    if not isinstance(manifest, dict):
        return None, ["manifest.json: manifest must be an object"]
    errors: list[str] = []
    unknown = sorted(set(manifest) - MANIFEST_FIELDS)
    missing = sorted(MANIFEST_FIELDS - set(manifest))
    if unknown:
        errors.append(f"unknown manifest fields: {','.join(unknown)}")
    if missing:
        errors.append(f"missing manifest fields: {','.join(missing)}")
    if errors:
        return None, errors
    if manifest["schema_version"] != SCHEMA_VERSION or isinstance(
        manifest["schema_version"], bool
    ):
        errors.append("unsupported manifest schema")
    if manifest["digest_algorithm"] != DIGEST_ALGORITHM:
        errors.append("unsupported digest algorithm")
    for name in ("contract_digest", "contract_sha256"):
        value = manifest[name]
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{64}", value
        ):
            errors.append(f"invalid {name}")
    if not isinstance(manifest["files"], dict):
        errors.append("files must be an object")
    return (manifest if not errors else None), errors


def verify_dataset(directory: Path) -> DatasetResult:
    """Verify a full snapshot: contract identity, file identity and coverage."""
    result = DatasetResult()
    expectations, errors = read_expectations(directory)
    if errors:
        result.status = "BLOCKED"
        result.errors.extend(errors)
        return result
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        result.status = "BLOCKED"
        result.errors.append("manifest.json: snapshot manifest absent")
        return result
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.status = "BLOCKED"
        result.errors.append(f"manifest.json: invalid JSON: {exc}")
        return result
    manifest, errors = _verify_structure(raw_manifest)
    if errors:
        result.status = "BLOCKED"
        result.errors.extend(errors)
        return result
    assert manifest is not None
    result.contract_digest = contract_digest(expectations)
    errors = _verify_manifest_binding(directory, manifest, expectations)
    if errors:
        result.status = "BLOCKED"
        result.errors.extend(errors)
        return result
    _verify_files(directory, manifest, expectations, result)
    if not result.errors:
        result.status = "COMPLETE"
    elif result.file_digests:
        result.status = "PARTIAL"
    else:
        result.status = "FAILED"
    return result


def _verify_manifest_binding(
    directory: Path, manifest: dict, expectations: dict
) -> list[str]:
    """Bind the manifest to the exact frozen contract bytes."""
    errors: list[str] = []
    if manifest["expectations"] != expectations:
        errors.append("manifest expectations differ from snapshot contract")
    if manifest["contract_digest"] != contract_digest(expectations):
        errors.append("contract digest mismatch")
    contract_bytes = (directory / CONTRACT_FILE).read_bytes()
    if manifest["contract_sha256"] != sha256_hex(contract_bytes):
        errors.append("contract byte digest mismatch")
    files = manifest["files"]
    symbols = expectations["symbols"]
    if set(files) != set(symbols):
        errors.append("manifest files differ from expected instruments")
    return errors


def _verify_files(
    directory: Path, manifest: dict, expectations: dict, result: DatasetResult
) -> None:
    """Verify every declared series and record structured errors."""
    for symbol in expectations["symbols"]:
        entry = manifest["files"].get(symbol)
        if not isinstance(entry, dict) or set(entry) != FILE_FIELDS:
            result.errors.append(f"{symbol}: malformed file identity")
            continue
        raw_digest = entry["raw_digest"]
        if not isinstance(raw_digest, str) or not re.fullmatch(
            r"[0-9a-f]{0,64}", raw_digest
        ):
            result.errors.append(f"{symbol}: malformed raw digest")
            continue
        path = directory / f"{symbol}.csv"
        if not path.is_file():
            result.errors.append(f"{symbol}: series absent")
            continue
        content = path.read_bytes()
        digest = sha256_hex(content)
        if digest != entry["digest"]:
            result.errors.append(f"{symbol}: dataset digest mismatch")
            continue
        candles, parse_errors = _read_csv(path)
        if parse_errors:
            result.errors.extend(parse_errors)
            continue
        sessions = [row["date"] for row in candles]
        if sessions != expectations["sessions"]:
            result.errors.append(f"{symbol}: session coverage mismatch")
            continue
        if not is_uint(entry["rows"]) or entry["rows"] != len(candles):
            result.errors.append(f"{symbol}: dataset row count mismatch")
            continue
        if len(candles) < expectations["min_rows"]:
            result.errors.append(f"{symbol}: insufficient history")
            continue
        if (
            entry["first_date"] != sessions[0]
            or entry["last_date"] != sessions[-1]
            or entry["instrument_id"] != expectations["instrument_ids"][symbol]
        ):
            result.errors.append(f"{symbol}: identity or range mismatch")
            continue
        result.file_digests[symbol] = digest
        result.rows[symbol] = len(candles)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by cross-language golden tests."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("write", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("spec", type=Path, nargs="?")
    args = parser.parse_args(argv)
    if args.action == "write":
        if args.spec is None:
            parser.error("write requires a spec.json path")
        try:
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"invalid spec: {exc}", file=sys.stderr)
            return 2
        errors = write_contract(args.directory, spec)
        errors.extend(write_manifest(args.directory, spec))
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0
    result = verify_dataset(args.directory)
    print(
        json.dumps(
            {
                "status": result.status,
                "contract_digest": result.contract_digest,
                "file_digests": result.file_digests,
                "errors": result.errors,
            },
            sort_keys=True,
        )
    )
    return 0 if result.status == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
