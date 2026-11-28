#!/usr/bin/env python3
"""Fail-closed experiment registration sinks for offline research.

Registration is opt-in: sinks never derive their own path and never claim
live qualification. Real-source records require explicit human authorization
and verifiable source and dataset digests before any persistent write.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
_provenance = importlib.import_module("data_provenance")
_git = importlib.import_module("research_git")

ExperimentAuthorization = _provenance.ExperimentAuthorization
REAL_SOURCE_CLASSES = _provenance.REAL_SOURCE_CLASSES
require_real_source = _provenance.require_real_source
configuration_digest = _provenance.configuration_digest
is_sha256_hex = _provenance.is_sha256_hex

SEED = 20261125
TRIALS = 8


def classification_value(value: Any) -> str:
    """Return the plain string value of a classification (enum or str)."""
    return getattr(value, "value", value)


def register_experiment(
    path: Path, record: dict, *, authorization: ExperimentAuthorization
) -> str:
    """Append one authorized entry to the explicit registry FILE."""
    identifier = uuid.uuid4().hex
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(record)
    entry["id"] = identifier
    entry["registered_at_utc"] = datetime.now(UTC).isoformat()
    entry["random_seed"] = SEED
    entry["trial_count"] = TRIALS
    if not entry.get("code_commit"):
        entry["code_commit"] = _git.head_commit(
            Path(__file__).resolve().parent.parent
        )
    classification = classification_value(authorization.classification)
    entry["authorization_classification"] = classification
    entry["authorization_approved_by"] = authorization.approved_by
    entry["authorization_reason"] = authorization.reason
    entry["approved_by"] = authorization.approved_by
    entry["reason"] = authorization.reason
    entry["authorization"] = {
        "classification": classification,
        "approved_by": authorization.approved_by,
        "reason": authorization.reason,
    }
    line = json.dumps(entry, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return identifier


class ExperimentSink(Protocol):
    """Destination for experiment records; never derives its own path."""

    def append(self, record: dict) -> str:
        """Accept one record and return a registration outcome."""
        ...

    def records(self) -> tuple[dict, ...]:
        """Return an immutable snapshot of the records accepted so far."""
        ...


class NullExperimentSink:
    """Default sink: accepts records without performing any I/O."""

    def append(self, _record: dict) -> str:
        """Discard the record; registration is opt-in."""
        return "NOT_REGISTERED"

    def records(self) -> tuple[dict, ...]:
        """Report the always-empty immutable snapshot of a null sink."""
        return ()


class InMemoryExperimentSink:
    """Non-persistent sink for tests and offline inspection."""

    def __init__(self) -> None:
        """Start with an empty record list."""
        self._records: list[dict] = []

    def append(self, record: dict) -> str:
        """Copy the record into memory and return a local outcome."""
        self._records.append(dict(record))
        return f"IN_MEMORY_{len(self._records)}"

    def records(self) -> tuple[dict, ...]:
        """Return an immutable snapshot of the copied records."""
        return tuple(dict(item) for item in self._records)


class FileExperimentSink:
    """Explicit, authorized append-only JSONL registry sink."""

    def __init__(
        self, path: Path, authorization: ExperimentAuthorization
    ) -> None:
        """Bind the sink to one registry file and one authorization."""
        self.path = Path(path)
        self.authorization = authorization

    def append(self, record: dict) -> str:
        """Validate the experiment class before any persistent write."""
        experiment_class = record.get(
            "experiment_classification", "SYNTHETIC_OR_UNVERIFIED"
        )
        author_class = classification_value(self.authorization.classification)
        if (
            author_class == "REAL_DATA_RESEARCH"
            or author_class in REAL_SOURCE_CLASSES
        ) and experiment_class != "REAL_DATA_RESEARCH":
            raise ValueError(
                "real-data authorization cannot register a "
                "SYNTHETIC_OR_UNVERIFIED record"
            )
        if experiment_class == "REAL_DATA_RESEARCH":
            if author_class not in REAL_SOURCE_CLASSES:
                raise ValueError(
                    "REAL_DATA_RESEARCH requires a real-source authorization"
                )
            require_real_source(
                record.get("data_classification", "UNVERIFIED"),
                record.get("source_artifact_digest", ""),
            )
            for key in ("dataset_digest", "universe_digest", "calendar_digest"):
                if not is_sha256_hex(record.get(key)):
                    raise ValueError(f"real record requires 64-hex {key}")
        return register_experiment(
            self.path, record, authorization=self.authorization
        )

    def records(self) -> tuple[dict, ...]:
        """Return the immutable snapshot of already persisted entries."""
        if not self.path.is_file():
            return ()
        return tuple(
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )


def experiment_configuration(record: dict) -> dict:
    """Stable, digestible subset of an experiment record."""
    keys = (
        "strategy",
        "dataset_digest",
        "universe_digest",
        "calendar_digest",
        "source_artifact_digest",
        "corporate_action_digest",
        "feature_version_digest",
        "instrument_registry_digest",
        "code_commit",
        "data_classification",
        "experiment_classification",
        "random_seed",
        "trial_count",
        "holdout_status",
    )
    return {key: record.get(key, "UNKNOWN") for key in keys}


def registration_summary(
    sink: ExperimentSink, classifications: list[str]
) -> dict:
    """Audit summary for the sink and the records it was asked to accept."""
    if not classifications:
        aggregate = "NONE"
    elif all(item == "REAL_DATA_RESEARCH" for item in classifications):
        aggregate = "REAL_DATA_RESEARCH"
    else:
        aggregate = "SYNTHETIC_OR_UNVERIFIED"
    return {
        "sink": type(sink).__name__,
        "classification": aggregate,
        "records": len(classifications),
    }


def experiment_record(
    name: str,
    dataset: Any,
    stats: dict,
    data_classification: str,
    experiment_classification: str,
) -> dict:
    """Build one fully attributed, digestible experiment record."""
    provenance = None if dataset is None else dataset.provenance
    record: dict = {
        "strategy": name,
        "dataset": "cache/history" if dataset is None else dataset.source,
        "dataset_digest": "" if dataset is None else dataset.dataset_digest,
        "universe_digest": "" if dataset is None else dataset.universe_digest,
        "calendar_digest": "" if dataset is None else dataset.calendar_digest,
        "source_artifact_digest": (
            "" if provenance is None else provenance.source_artifact_digest
        ),
        "corporate_action_digest": (
            "UNKNOWN"
            if provenance is None
            else provenance.corporate_action_digest
        ),
        "feature_version_digest": (
            "UNKNOWN"
            if provenance is None
            else provenance.feature_version_digest
        ),
        "instrument_registry_digest": (
            "UNKNOWN"
            if provenance is None
            else provenance.instrument_registry_digest
        ),
        "code_commit": ("UNKNOWN" if dataset is None else dataset.code_version),
        "random_seed": SEED,
        "trial_count": TRIALS,
        "mean_ic": stats["mean_ic"],
        "ic_observations": stats["observations"],
        "holdout_status": "SEALED_NOT_EVALUATED",
        "status": "INCONCLUSIVE",
        "data_classification": data_classification,
        "experiment_classification": experiment_classification,
    }
    record["configuration_digest"] = configuration_digest(
        experiment_configuration(record)
    )
    return record
