#!/usr/bin/env python3
"""Fail-closed vocabulary and provenance helpers for research evidence.

This module only describes data. It performs no I/O and claims no live
qualification: classification is a declaration that callers must substantiate
with an artifact digest before any real-source research registration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
_UNKNOWN = "UNKNOWN"
UNKNOWN = _UNKNOWN


class DataClassification(StrEnum):
    """Vocabulary describing where research input data came from."""

    SYNTHETIC_TEST_FIXTURE = "SYNTHETIC_TEST_FIXTURE"
    SIMULATED_MARKET_DATA = "SIMULATED_MARKET_DATA"
    OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT = "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT"
    PROVIDER_API_CAPTURE = "PROVIDER_API_CAPTURE"
    PROVIDER_STREAM_CAPTURE = "PROVIDER_STREAM_CAPTURE"
    EXCHANGE_OFFICIAL_CAPTURE = "EXCHANGE_OFFICIAL_CAPTURE"
    DERIVED_DATASET = "DERIVED_DATASET"
    RESEARCH_OUTPUT = "RESEARCH_OUTPUT"
    UNVERIFIED = "UNVERIFIED"


SIMULATED_CLASSES = frozenset(
    {
        DataClassification.SYNTHETIC_TEST_FIXTURE.value,
        DataClassification.SIMULATED_MARKET_DATA.value,
    }
)
REAL_SOURCE_CLASSES = frozenset(
    {
        DataClassification.PROVIDER_API_CAPTURE.value,
        DataClassification.PROVIDER_STREAM_CAPTURE.value,
        DataClassification.EXCHANGE_OFFICIAL_CAPTURE.value,
        DataClassification.DERIVED_DATASET.value,
    }
)
REFERENCE_ONLY_CLASSES = frozenset(
    {DataClassification.OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT.value}
)

MANDATORY_PROVENANCE_FIELDS: tuple[str, ...] = (
    "artifact_id",
    "data_classification",
    "provider_or_exchange",
    "source_url",
    "capture_timestamp_utc",
    "provider_updated_timestamp",
    "market_date",
    "request_parameters",
    "http_status",
    "content_type",
    "content_length",
    "etag",
    "last_modified",
    "raw_sha256",
    "decoded_sha256",
    "schema_version",
    "fetcher_commit",
    "transformation_version",
    "parent_artifact_digests",
    "licensing_and_retention_status",
)

_SHA_FIELDS = frozenset({"raw_sha256", "decoded_sha256"})


def _classification_value(classification: Any) -> str:
    """Normalize an enum member or string to its plain string value."""
    value = getattr(classification, "value", classification)
    return value if isinstance(value, str) else str(value)


def is_sha256_hex(value: Any) -> bool:
    """Return True only for a 64-character hexadecimal digest."""
    return isinstance(value, str) and _HEX_64.fullmatch(value) is not None


def _validated_sha(value: Any) -> str:
    """Return a valid digest unchanged, otherwise the UNKNOWN sentinel."""
    return value if is_sha256_hex(value) else _UNKNOWN


def canonical_provenance(**fields: Any) -> dict[str, Any]:
    """Build a complete provenance record with explicit unknowns.

    Every mandatory field appears exactly once. Missing/None values and
    malformed SHA-256 digests become ``"UNKNOWN"`` rather than being inferred.
    The returned mapping is key-sorted so serialized output is deterministic.
    """
    result: dict[str, Any] = {}
    for key in MANDATORY_PROVENANCE_FIELDS:
        value = fields.get(key)
        if value is None:
            value = _UNKNOWN
        if key in _SHA_FIELDS:
            value = _validated_sha(value)
        elif key == "parent_artifact_digests" and isinstance(
            value, (list, tuple)
        ):
            value = [_validated_sha(item) for item in value]
        result[key] = value
    return dict(sorted(result.items()))


def configuration_digest(record: dict) -> str:
    """Canonical SHA-256 of a configuration record, without volatile fields."""
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExperimentAuthorization:
    """Explicit human authorization for one experiment registration sink."""

    classification: str
    approved_by: str
    reason: str

    def __post_init__(self) -> None:
        """Reject unverifiable classifications and blank attestations."""
        allowed = SIMULATED_CLASSES | REAL_SOURCE_CLASSES
        if _classification_value(self.classification) not in allowed:
            raise ValueError(
                "authorization classification must be in "
                "SIMULATED_CLASSES | REAL_SOURCE_CLASSES"
            )
        if (
            not isinstance(self.approved_by, str)
            or not self.approved_by.strip()
        ):
            raise ValueError("approved_by must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


def require_real_source(classification: str, source_digest: str) -> None:
    """Fail closed unless a real source class has a valid artifact digest."""
    value = _classification_value(classification)
    if value in SIMULATED_CLASSES or value == DataClassification.UNVERIFIED:
        raise ValueError(
            f"simulated/unverified data is not a real source: {value}"
        )
    if value in REFERENCE_ONLY_CLASSES:
        raise ValueError(f"reference-only data is not a real source: {value}")
    if value not in REAL_SOURCE_CLASSES:
        raise ValueError(f"unknown data classification: {value}")
    if not is_sha256_hex(source_digest):
        raise ValueError("real-source research requires a 64-hex source digest")
