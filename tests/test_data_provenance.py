"""Fail-closed provenance vocabulary tests (offline, no I/O)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_loader_support import load_test_module

ROOT = Path(__file__).resolve().parent.parent
provenance = load_test_module(
    "quant_data_provenance_test", ROOT / "python" / "data_provenance.py"
)


def test_enum_vocabulary_is_exact():
    """Every classification name and value is part of the public contract."""
    expected = {
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
    assert {item.name for item in provenance.DataClassification} == expected
    assert {item.value for item in provenance.DataClassification} == expected
    assert all(isinstance(item, str) for item in provenance.DataClassification)
    assert provenance.SIMULATED_CLASSES == {
        "SYNTHETIC_TEST_FIXTURE",
        "SIMULATED_MARKET_DATA",
    }
    assert provenance.REAL_SOURCE_CLASSES == {
        "PROVIDER_API_CAPTURE",
        "PROVIDER_STREAM_CAPTURE",
        "EXCHANGE_OFFICIAL_CAPTURE",
        "DERIVED_DATASET",
    }
    assert provenance.REFERENCE_ONLY_CLASSES == {
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT"
    }


def test_canonical_provenance_preserves_unknowns():
    """Missing, None and malformed fields never imply a real source."""
    record = provenance.canonical_provenance(
        artifact_id="artifact-1",
        raw_sha256="not-a-digest",
        decoded_sha256="a" * 64,
        parent_artifact_digests=["b" * 64, "bad"],
    )
    assert set(record) == set(provenance.MANDATORY_PROVENANCE_FIELDS)
    assert list(record) == sorted(list(provenance.MANDATORY_PROVENANCE_FIELDS))
    assert record["artifact_id"] == "artifact-1"
    assert record["data_classification"] == "UNKNOWN"
    assert record["source_url"] == "UNKNOWN"
    assert record["raw_sha256"] == "UNKNOWN"
    assert record["decoded_sha256"] == "a" * 64
    assert record["parent_artifact_digests"] == ["b" * 64, "UNKNOWN"]
    assert provenance.canonical_provenance() == record | {
        "artifact_id": "UNKNOWN",
        "decoded_sha256": "UNKNOWN",
        "parent_artifact_digests": "UNKNOWN",
    }


def test_sha_validation_requires_exactly_64_hex():
    """Uppercase digests are valid; lengths and non-hex characters are not."""
    assert provenance.is_sha256_hex("a" * 64)
    assert provenance.is_sha256_hex("A" * 64)
    for bad in ("", "a" * 63, "a" * 65, "g" * 64, None, 12):
        assert not provenance.is_sha256_hex(bad)
        assert (
            provenance.canonical_provenance(raw_sha256=bad)["raw_sha256"]
            == "UNKNOWN"
        )


def test_configuration_digest_is_deterministic():
    """Key order and volatile fields cannot change the configuration digest."""
    config = {"strategy": "momentum", "seed": 7, "trials": 8}
    first = provenance.configuration_digest(config)
    assert first == provenance.configuration_digest(
        {"trials": 8, "seed": 7, "strategy": "momentum"}
    )
    assert first == provenance.configuration_digest(
        dict(reversed(list(config.items())))
    )
    assert len(first) == 64
    assert first != provenance.configuration_digest(config | {"seed": 8})


def test_authorization_validation_fails_closed():
    """Only declared source classes and non-blank attestations are accepted."""
    auth = provenance.ExperimentAuthorization(
        classification=provenance.DataClassification.SIMULATED_MARKET_DATA,
        approved_by="tester",
        reason="offline synthetic fixture",
    )
    assert auth.classification == "SIMULATED_MARKET_DATA"
    assert auth.approved_by == "tester"
    for classification in (
        "UNVERIFIED",
        "RESEARCH_OUTPUT",
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT",
        "REAL_DATA_RESEARCH",
        "",
    ):
        with pytest.raises(ValueError):
            provenance.ExperimentAuthorization(
                classification=classification,
                approved_by="tester",
                reason="offline synthetic fixture",
            )
    for approved_by, reason in (
        ("", "offline synthetic fixture"),
        ("   ", "offline synthetic fixture"),
        ("tester", ""),
        ("tester", "   "),
        (None, "offline synthetic fixture"),
        ("tester", None),
    ):
        with pytest.raises(ValueError):
            provenance.ExperimentAuthorization(
                classification="SIMULATED_MARKET_DATA",
                approved_by=approved_by,
                reason=reason,
            )


def test_require_real_source_refuses_every_non_real_class():
    """Simulated, unverified, reference-only and bad digests are rejected."""
    for classification in (
        "SYNTHETIC_TEST_FIXTURE",
        "SIMULATED_MARKET_DATA",
        "UNVERIFIED",
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT",
        "RESEARCH_OUTPUT",
        "NOT_A_CLASS",
    ):
        with pytest.raises(ValueError):
            provenance.require_real_source(classification, "a" * 64)
    for digest in ("", "a" * 63, "a" * 65, "g" * 64, None):
        with pytest.raises(ValueError):
            provenance.require_real_source("PROVIDER_API_CAPTURE", digest)
    for classification in (
        "PROVIDER_API_CAPTURE",
        "PROVIDER_STREAM_CAPTURE",
        "EXCHANGE_OFFICIAL_CAPTURE",
        "DERIVED_DATASET",
    ):
        assert provenance.require_real_source(classification, "a" * 64) is None
