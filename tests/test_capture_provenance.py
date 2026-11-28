"""Offline tests for capture attestation fields and fail-closed validation."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    """Load a standalone module from an absolute repository path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


capture = _load_module(
    "quant_capture_provenance_test",
    ROOT / "python" / "capture_provenance.py",
)
UNKNOWN = capture.UNKNOWN
DIGEST = hashlib.sha256(b"captured bytes").hexdigest()


def _record(**overrides: object) -> dict:
    """Build a minimally observed record and apply explicit overrides."""
    record = capture.build_capture_attestation(
        data_classification="OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT",
        provider_or_exchange="upstox",
        market_date="2026-11-26",
        raw_sha256=DIGEST,
        decoded_sha256=DIGEST,
        http_status=200,
        content_length=42,
    )
    record.update(overrides)
    return record


def test_all_mandatory_capture_fields_are_present():
    """Every attestation carries the full mandatory vocabulary."""
    record = capture.build_capture_attestation()
    assert set(capture.CAPTURE_FIELDS) <= set(record)
    assert len(capture.CAPTURE_FIELDS) == 20
    assert set(capture.FEED_FIELDS) == {
        "connection_id",
        "subscription_set",
        "provider_sequence_if_available",
        "receive_sequence",
        "first_event_timestamp",
        "last_event_timestamp",
        "connection_gaps",
        "recovery_status",
    }
    assert capture.validate_attestation(record) == []


def test_unknown_fields_are_preserved_and_not_fabricated():
    """Missing or null observations stay UNKNOWN, never guessed."""
    record = capture.build_capture_attestation(
        etag=None,
        content_type="application/json",
    )
    for key in (
        "etag",
        "last_modified",
        "source_url",
        "capture_timestamp_utc",
        "provider_updated_timestamp",
        "request_parameters",
        "fetcher_commit",
        "transformation_version",
        "parent_artifact_digests",
        "licensing_and_retention_status",
    ):
        assert record[key] == UNKNOWN
    assert record["content_type"] == "application/json"
    assert record["artifact_id"] == UNKNOWN
    assert "/Users/" not in repr(record)


def test_digests_and_integers_fail_closed_on_bad_values():
    """Only 64-hex digests and non-boolean integers survive normalization."""
    record = capture.build_capture_attestation(
        raw_sha256=DIGEST,
        decoded_sha256=DIGEST.upper(),
        http_status="200",
        content_length=True,
    )
    assert record["raw_sha256"] == DIGEST
    assert record["decoded_sha256"] == UNKNOWN
    assert record["http_status"] == UNKNOWN
    assert record["content_length"] == UNKNOWN
    assert capture.validate_attestation(record) == []

    errors = capture.validate_attestation(_record(raw_sha256="not-a-digest"))
    assert any(
        "invalid digest format for raw_sha256" in item for item in errors
    )
    bad_int = _record(http_status=True, content_length="42")
    errors = capture.validate_attestation(bad_int)
    assert any("invalid integer for http_status" in item for item in errors)
    assert any("invalid integer for content_length" in item for item in errors)


def test_absolute_paths_and_unknown_classifications_are_rejected():
    """Machine-local paths and invented classifications never validate."""
    errors = capture.validate_attestation(
        _record(source_url="/Users/tourist/snapshot.json")
    )
    assert any("absolute filesystem path" in item for item in errors)
    errors = capture.validate_attestation(
        _record(request_parameters={"path": "x/Users/tourist/y"})
    )
    assert any("absolute filesystem path" in item for item in errors)
    errors = capture.validate_attestation(
        _record(data_classification="LIVE_AUTHENTICATED")
    )
    assert errors == ["unknown data_classification: 'LIVE_AUTHENTICATED'"]


def test_accepted_classifications_validate():
    """Exactly the nine declared classifications are accepted."""
    names = (
        "SYNTHETIC_TEST_FIXTURE",
        "SIMULATED_MARKET_DATA",
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT",
        "PROVIDER_API_CAPTURE",
        "PROVIDER_STREAM_CAPTURE",
        "EXCHANGE_OFFICIAL_CAPTURE",
        "DERIVED_DATASET",
        "RESEARCH_OUTPUT",
        "UNVERIFIED",
    )
    assert set(capture.DATA_CLASSIFICATIONS) == set(names)
    for name in names:
        assert (
            capture.validate_attestation(_record(data_classification=name))
            == []
        )


def test_missing_mandatory_keys_are_reported_sorted():
    """Validation reports each absent field with a stable ordering."""
    record = _record()
    del record["etag"]
    del record["fetcher_commit"]
    assert capture.validate_attestation(record) == [
        "missing mandatory field: etag",
        "missing mandatory field: fetcher_commit",
    ]


def test_artifact_id_is_deterministic_and_only_uses_observed_values():
    """The ID is stable and derived from provider, date and byte digests."""
    observed = {
        "provider_or_exchange": "fyers",
        "market_date": "2026-11-26",
        "raw_sha256": DIGEST,
        "decoded_sha256": DIGEST,
    }
    first = capture.build_capture_attestation(**observed)
    second = capture.build_capture_attestation(**observed)
    assert first["artifact_id"] == second["artifact_id"]
    assert len(first["artifact_id"]) == 64
    changed = capture.build_capture_attestation(
        **{**observed, "raw_sha256": "c" * 64}
    )
    assert changed["artifact_id"] != first["artifact_id"]
    assert (
        capture.build_capture_attestation(
            market_date="2026-11-26", raw_sha256=DIGEST
        )["artifact_id"]
        == UNKNOWN
    )


def test_archive_relative_path_is_contained_and_posix(tmp_path: Path):
    """Contained paths are relative; outside paths never leak."""
    root = tmp_path / "archive"
    (root / "upstox").mkdir(parents=True)
    inside = root / "upstox" / "NSE.json.gz"
    assert capture.archive_relative_path(inside, root) == "upstox/NSE.json.gz"
    assert capture.archive_relative_path(tmp_path / "outside", root) == UNKNOWN
    assert capture.archive_relative_path(root, root) == UNKNOWN
