"""Honest capture attestation vocabulary and validation.

SHA-256 proves integrity, not origin: a matching digest shows only that
bytes are identical to an earlier copy. Digest-pinned public snapshots are
NOT independently authenticated, and no live qualification is claimed here.
Facts that were not observed stay ``UNKNOWN`` instead of being inferred.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
_provenance = importlib.import_module("data_provenance")

UNKNOWN = _provenance.UNKNOWN
SCHEMA_VERSION = "capture-attestation-v1"
ARTIFACT_VERSION = "capture-artifact-v1"

# One canonical vocabulary: capture attestations and research provenance
# must never drift into different mandatory field sets.
CAPTURE_FIELDS = _provenance.MANDATORY_PROVENANCE_FIELDS
FEED_FIELDS = (
    "connection_id",
    "subscription_set",
    "provider_sequence_if_available",
    "receive_sequence",
    "first_event_timestamp",
    "last_event_timestamp",
    "connection_gaps",
    "recovery_status",
)
DATA_CLASSIFICATIONS = frozenset(
    {
        "SYNTHETIC_TEST_FIXTURE",
        "SIMULATED_MARKET_DATA",
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT",
        "PROVIDER_API_CAPTURE",
        "PROVIDER_STREAM_CAPTURE",
        "EXCHANGE_OFFICIAL_CAPTURE",
        "DERIVED_DATASET",
        "RESEARCH_OUTPUT",
        "UNVERIFIED",
    }
)
DIGEST_FIELDS = frozenset({"raw_sha256", "decoded_sha256", "artifact_id"})
INTEGER_FIELDS = frozenset({"http_status", "content_length"})
DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def _missing(value: Any) -> bool:
    """Return True for a value that carries no observed information."""
    return value is None or value == UNKNOWN


def _digest(value: Any) -> str:
    """Keep a lowercase 64-hex SHA-256, otherwise return UNKNOWN."""
    if isinstance(value, str) and DIGEST_RE.fullmatch(value):
        return value
    return UNKNOWN


def _integer(value: Any) -> int | str:
    """Keep a genuine integer, rejecting booleans and strings."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return UNKNOWN


def _artifact_id(fields: Mapping[str, Any]) -> str:
    """Derive a stable ID from provider, date and available byte digests."""
    provider = fields["provider_or_exchange"]
    market_date = fields["market_date"]
    raw = fields["raw_sha256"]
    decoded = fields["decoded_sha256"]
    if _missing(provider) or _missing(market_date):
        return UNKNOWN
    if _missing(raw) and _missing(decoded):
        return UNKNOWN
    payload = json.dumps(
        [ARTIFACT_VERSION, provider, market_date, raw, decoded],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(key: str, value: Any) -> Any:
    """Coerce one observed field without inventing a substitute value."""
    if key in ("raw_sha256", "decoded_sha256"):
        return _digest(value)
    if key in INTEGER_FIELDS:
        return _integer(value)
    return UNKNOWN if value is None else value


def build_capture_attestation(**fields: Any) -> dict[str, Any]:
    """Build one complete capture record from observed fields only."""
    record = {key: _normalize(key, fields.get(key)) for key in CAPTURE_FIELDS}
    record["artifact_id"] = _artifact_id(record)
    return record


def _scalars(value: Any, label: str) -> Iterator[tuple[str, Any]]:
    """Yield labelled scalar values from nested JSON-shaped data."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _scalars(child, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _scalars(child, f"{label}[{index}]")
    else:
        yield label, value


def _absolute_path(value: str) -> bool:
    """Detect machine-local absolute paths without guessing other formats."""
    return value.startswith("/") or "/Users/" in value


def validate_attestation(record: dict[str, Any]) -> list[str]:
    """Return sorted fail-closed errors; an empty list means valid."""
    errors: list[str] = []
    for key in CAPTURE_FIELDS:
        if key not in record:
            errors.append(f"missing mandatory field: {key}")
    classification = record.get("data_classification", UNKNOWN)
    if classification != UNKNOWN and classification not in DATA_CLASSIFICATIONS:
        errors.append(f"unknown data_classification: {classification!r}")
    for key in DIGEST_FIELDS:
        value = record.get(key, UNKNOWN)
        if not _missing(value) and not (
            isinstance(value, str) and DIGEST_RE.fullmatch(value)
        ):
            errors.append(f"invalid digest format for {key}: {value!r}")
    for key in INTEGER_FIELDS:
        value = record.get(key, UNKNOWN)
        if not _missing(value) and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            errors.append(f"invalid integer for {key}: {value!r}")
    for label, value in _scalars(record, "record"):
        if (
            isinstance(value, str)
            and value != UNKNOWN
            and _absolute_path(value)
        ):
            errors.append(f"absolute filesystem path in {label}: {value!r}")
    return sorted(errors)


def archive_relative_path(path: Path, root: Path) -> str:
    """Return a POSIX relative path when contained, else UNKNOWN."""
    try:
        base = root.resolve()
        target = path.resolve()
        if target == base or not target.is_relative_to(base):
            return UNKNOWN
        return target.relative_to(base).as_posix()
    except (OSError, RuntimeError, ValueError):
        return UNKNOWN
