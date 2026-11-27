#!/usr/bin/env python3
"""QUANT dataset snapshot contract v2 (language-neutral implementation).

This module and the C++ implementation in src/dataset.cpp follow the single
specification in docs/dataset-contract.md. SHA-256 is the only digest
algorithm, so manifests are interchangeable across the two languages.

In canonical CSV schema v3, open interest >= 0 is an available value, -1 is
the unavailable sentinel, and every value < -1 is invalid. The same rule
applies to both the C++ and Python readers and writers.

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
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA_VERSION = 2
DIGEST_ALGORITHM = "sha256"
HEADER_V2 = ("date", "open", "high", "low", "close", "volume")
HEADER_V3 = HEADER_V2 + ("open_interest",)
MAX_OPEN_INTEREST = 2**63
MAX_EXACT_DOUBLE_INTEGER = 2**53
VALID_CSV_SCHEMAS = ("v2", "v3")
CONTRACT_FILE = "contract.json"
MANIFEST_FILE = "manifest.json"
CONTRACT_PREFIX = "QUANT-DATASET-CONTRACT-V2"

_SYMBOL_RE = re.compile(r"^[A-Z0-9 .&/_-]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_STORAGE_KEY_RE = re.compile(r"^s[0-9a-f]{31}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INTEGER_RE = re.compile(r"-?[0-9]+")

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
    "storage_keys",
    "csv_schema",
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
    "storage_key",
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


def strict_json_loads(text: str) -> object:
    """Parse JSON, rejecting duplicate object keys in every object."""

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=no_duplicates)


def storage_key(instrument_id: str) -> str:
    """Derive the sole physical filename from permanent instrument identity."""
    material = f"QUANT-DATASET-STORAGE-V1\n{instrument_id}".encode()
    return "s" + hashlib.sha256(material).hexdigest()[:31]


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
    pairs = ",".join(f"{symbol}:{ids.get(symbol, '')}" for symbol in symbols)
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
        f"csv_schema={expectations.get('csv_schema', 'v2')}",
        f"instrument_ids={pairs}",
        "storage_keys="
        + ",".join(
            f"{symbol}:{expectations.get('storage_keys', {}).get(symbol, '')}"
            for symbol in symbols
        ),
    ]
    return "".join(f"{line}\n" for line in lines)


def contract_digest(expectations: dict) -> str:
    """Hash the canonical expectations with the only accepted algorithm."""
    return sha256_hex(canonical_expectations(expectations).encode("utf-8"))


OBJECT_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
OBJECT_WRITE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
)


def open_snapshot_root(directory: Path) -> int:
    """Pin the dataset directory with a descriptor for relative access."""
    return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def read_object(root_fd: int, name: str) -> bytes:
    """Read one snapshot object without following links or reopening it."""
    fd = os.open(name, OBJECT_READ_FLAGS, dir_fd=root_fd)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{name}: snapshot object is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 16384)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def write_object(root_fd: int, name: str, content: bytes) -> None:
    """Write one snapshot object without following links."""
    fd = os.open(name, OBJECT_WRITE_FLAGS, 0o600, dir_fd=root_fd)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def valid_raw_digest(value: object) -> bool:
    """Accept only the empty marker or a full lowercase SHA-256 digest."""
    return isinstance(value, str) and (
        value == "" or re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


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
    sessions_list = expectations["sessions"]
    if isinstance(sessions_list, list) and sessions_list:
        if first != sessions_list[0] or last != sessions_list[-1]:
            errors.append("first_date and last_date must bound sessions")
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
    missing = sorted((CONTRACT_FIELDS - {"csv_schema"}) - set(expectations))
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
    schema = expectations.get("csv_schema", "v2")
    if schema not in VALID_CSV_SCHEMAS:
        errors.append("csv_schema must be v2 or v3")
    return errors


def read_expectations(directory: Path) -> tuple[dict | None, list[str]]:
    """Read and validate the frozen contract from a dataset directory."""
    try:
        root_fd = open_snapshot_root(directory)
    except OSError as exc:
        return None, [f"snapshot directory unavailable: {exc}"]
    try:
        try:
            raw = read_object(root_fd, CONTRACT_FILE)
        except (OSError, ValueError) as exc:
            return None, [f"contract.json: {exc}"]
        try:
            expectations = strict_json_loads(raw.decode("utf-8"))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            return None, [f"contract.json: invalid JSON: {exc}"]
    finally:
        os.close(root_fd)
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
    expectations.setdefault("csv_schema", "v2")
    errors = validate_expectations(expectations)
    if errors:
        return errors
    expectations.setdefault("csv_schema", "v2")
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(expectations, indent=2, sort_keys=True) + "\n"
    root_fd = open_snapshot_root(directory)
    try:
        write_object(root_fd, CONTRACT_FILE, payload.encode("utf-8"))
    finally:
        os.close(root_fd)
    return []


def _read_csv_content(
    content: bytes, name: str, csv_schema: str = "v2"
) -> tuple[list[dict], list[str]]:
    """Parse exact CSV bytes already read from a pinned snapshot object."""
    try:
        rows = list(csv.reader(content.decode("utf-8").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        return [], [f"{name}: unreadable CSV: {exc}"]
    expected_header = HEADER_V3 if csv_schema == "v3" else HEADER_V2
    if not rows or tuple(rows[0]) != expected_header:
        return [], [f"{name}: invalid canonical header for {csv_schema}"]
    errors: list[str] = []
    candles: list[dict] = []
    seen: set[str] = set()
    for number, fields in enumerate(rows[1:], start=2):
        if len(fields) != len(expected_header):
            errors.append(f"{name}:{number}: row width mismatch")
            continue
        session = fields[0]
        if not is_canonical_date(session) or session in seen:
            errors.append(f"{name}:{number}: invalid or duplicate date")
            continue
        seen.add(session)
        try:
            values = [float(value) for value in fields[1:6]]
        except ValueError:
            errors.append(f"{name}:{number}: non-numeric value")
            continue
        opening, high, low, close, volume = values
        open_interest: int | None = None
        if csv_schema == "v3":
            try:
                parsed_oi = int(fields[6])
                if (
                    _INTEGER_RE.fullmatch(fields[6]) is None
                    or parsed_oi < -1
                    or parsed_oi >= MAX_OPEN_INTEREST
                ):
                    raise ValueError
                open_interest = parsed_oi
            except (ValueError, OverflowError):
                errors.append(f"{name}:{number}: invalid open interest")
                continue
        if not all(math.isfinite(value) for value in values):
            errors.append(f"{name}:{number}: non-finite value")
        elif (
            low <= 0
            or close <= 0
            or low > min(opening, close)
            or max(opening, close) > high
            or volume < 0
            or volume > MAX_EXACT_DOUBLE_INTEGER
            or not volume.is_integer()
        ):
            errors.append(f"{name}:{number}: invalid OHLCV relationship")
        candles.append(
            {
                "date": session,
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "open_interest": open_interest,
            }
        )
    candles.sort(key=lambda row: row["date"])
    return candles, errors


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    """Parse a CSV file outside the pinned snapshot boundary."""
    return _read_csv_content(path.read_bytes(), path.name)


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
    root_fd = open_snapshot_root(directory)
    for symbol in expectations["symbols"]:
        name = f"{expectations['storage_keys'][symbol]}.csv"
        try:
            content = read_object(root_fd, name)
        except (OSError, ValueError) as exc:
            errors.append(f"{symbol}: {exc}")
            continue
        candles, parse_errors = _read_csv_content(
            content, name, expectations.get("csv_schema", "v2")
        )
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
            "storage_key": expectations["storage_keys"][symbol],
            "raw_digest": raw.get(symbol, ""),
        }
        if len(candles) < expectations["min_rows"]:
            errors.append(f"{symbol}: insufficient rows")
    if errors:
        return errors
    try:
        contract_bytes = read_object(root_fd, CONTRACT_FILE)
    finally:
        os.close(root_fd)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "contract_digest": contract_digest(expectations),
        "contract_sha256": sha256_hex(contract_bytes),
        "expectations": expectations,
        "files": files,
    }
    root_fd = open_snapshot_root(directory)
    try:
        write_object(
            root_fd,
            MANIFEST_FILE,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
    finally:
        os.close(root_fd)
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
    try:
        root_fd = open_snapshot_root(directory)
        raw_manifest_bytes = read_object(root_fd, MANIFEST_FILE)
    except (OSError, ValueError) as exc:
        result.status = "BLOCKED"
        result.errors.append(f"manifest.json: {exc}")
        return result
    try:
        raw_manifest = strict_json_loads(raw_manifest_bytes.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        result.status = "BLOCKED"
        result.errors.append(f"manifest.json: invalid JSON: {exc}")
        os.close(root_fd)
        return result
    manifest, errors = _verify_structure(raw_manifest)
    if errors:
        result.status = "BLOCKED"
        result.errors.extend(errors)
        os.close(root_fd)
        return result
    assert manifest is not None
    result.contract_digest = contract_digest(expectations)
    errors = _verify_manifest_binding(root_fd, manifest, expectations)
    if errors:
        result.status = "BLOCKED"
        result.errors.extend(errors)
        os.close(root_fd)
        return result
    _verify_files(root_fd, manifest, expectations, result)
    os.close(root_fd)
    if not result.errors:
        result.status = "COMPLETE"
    elif result.file_digests:
        result.status = "PARTIAL"
    else:
        result.status = "FAILED"
    return result


def _verify_manifest_binding(
    root_fd: int, manifest: dict, expectations: dict
) -> list[str]:
    """Bind the manifest to the exact frozen contract bytes."""
    errors: list[str] = []
    if manifest["expectations"] != expectations:
        errors.append("manifest expectations differ from snapshot contract")
    if manifest["contract_digest"] != contract_digest(expectations):
        errors.append("contract digest mismatch")
    contract_bytes = read_object(root_fd, CONTRACT_FILE)
    if manifest["contract_sha256"] != sha256_hex(contract_bytes):
        errors.append("contract byte digest mismatch")
    files = manifest["files"]
    symbols = expectations["symbols"]
    if set(files) != set(symbols):
        errors.append("manifest files differ from expected instruments")
    return errors


def _verify_files(
    root_fd: int, manifest: dict, expectations: dict, result: DatasetResult
) -> None:
    """Verify every declared series and record structured errors."""
    for symbol in expectations["symbols"]:
        entry = manifest["files"].get(symbol)
        if not isinstance(entry, dict) or set(entry) != FILE_FIELDS:
            result.errors.append(f"{symbol}: malformed file identity")
            continue
        raw_digest = entry["raw_digest"]
        if not valid_raw_digest(raw_digest):
            result.errors.append(f"{symbol}: malformed raw digest")
            continue
        if entry["storage_key"] != expectations["storage_keys"][symbol]:
            result.errors.append(f"{symbol}: storage key mismatch")
            continue
        name = f"{expectations['storage_keys'][symbol]}.csv"
        try:
            content = read_object(root_fd, name)
        except (OSError, ValueError) as exc:
            result.errors.append(f"{symbol}: {exc}")
            continue
        digest = sha256_hex(content)
        if digest != entry["digest"]:
            result.errors.append(f"{symbol}: dataset digest mismatch")
            continue
        candles, parse_errors = _read_csv_content(
            content, name, expectations.get("csv_schema", "v2")
        )
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
