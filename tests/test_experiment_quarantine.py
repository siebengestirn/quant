"""Offline tests for historical experiment registry quarantine and staging."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    """Load a script module without relying on the current working directory."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


QUARANTINE = load_module(
    "quarantine_experiments", SCRIPTS / "quarantine_experiments.py"
)
RELEASE_BUNDLE = load_module("release_bundle", SCRIPTS / "release_bundle.py")

SAMPLE_RECORDS = [
    {"id": "r1", "random_seed": 20261125, "status": "INCONCLUSIVE"},
    {"id": "r2", "random_seed": 20261126, "status": "INCONCLUSIVE"},
    {"id": "r3", "random_seed": 20261127, "status": "INCONCLUSIVE"},
]


def write_source(path: Path, records: list[dict]) -> bytes:
    """Write a canonical JSONL registry and return its exact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_preserves_every_record(tmp_path: Path) -> None:
    """All JSONL records survive quarantine unchanged, with no deletion."""
    source = tmp_path / "experiments.jsonl"
    raw = write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(source, output)

    source_lines = [json.loads(line) for line in raw.decode().splitlines()]
    assert document["record_count"] == len(source_lines)
    assert document["records"] == source_lines
    assert sorted(document["records"], key=lambda item: item["id"]) == sorted(
        source_lines, key=lambda item: item["id"]
    )
    assert document["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8")) == document


def test_records_digest_is_deterministic_and_record_only() -> None:
    """Canonical records digest ignores key order and other document fields."""
    digest = QUARANTINE.records_digest(SAMPLE_RECORDS)
    reordered = [
        {key: record[key] for key in reversed(record)}
        for record in SAMPLE_RECORDS
    ]
    assert digest == QUARANTINE.records_digest(
        [dict(r) for r in SAMPLE_RECORDS]
    )
    assert digest == QUARANTINE.records_digest(reordered)
    assert len(digest) == 64
    assert set(digest) <= frozenset("0123456789abcdef")
    assert digest != QUARANTINE.records_digest(list(reversed(SAMPLE_RECORDS)))
    assert digest != QUARANTINE.records_digest(SAMPLE_RECORDS[:-1])


def test_generated_records_digest_matches_source_records(
    tmp_path: Path,
) -> None:
    """The generated document binds the exact records read from the source."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(source, output)
    source_records = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert document["records"] == source_records
    assert document["records_digest"] == (
        QUARANTINE.records_digest(source_records)
    )
    assert QUARANTINE.validate_document(document) == []
    assert QUARANTINE.verify(source, output) == []


def test_labels_and_status(tmp_path: Path) -> None:
    """The quarantined form is explicitly historical and not research."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    document = QUARANTINE.generate(source, tmp_path / "quarantine.json")

    assert document["schema_version"] == 1
    assert document["label"] == "HISTORICAL_QUARANTINED_RESEARCH_EVIDENCE"
    assert document["research_evidence_status"] == (
        "SYNTHETIC_OR_UNVERIFIED_TEST_CONTAMINATED"
    )
    assert document["acceptance"] == "NOT_ACCEPTED_AS_RESEARCH"
    assert document["historical"] is True
    assert document["source_present"] is True
    assert "synthetic" in document["reason"].lower()
    assert "dataset digests" in document["reason"]
    assert "no record was deleted" in document["note"]


def test_canonical_deterministic_bytes(tmp_path: Path) -> None:
    """Two generations produce identical canonical bytes."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    QUARANTINE.generate(source, first)
    QUARANTINE.generate(source, second)
    payload = first.read_bytes()

    assert payload == second.read_bytes()
    assert payload.endswith(b"\n")
    assert payload.decode("utf-8") == (
        json.dumps(json.loads(payload), indent=2, sort_keys=True) + "\n"
    )


def test_missing_input_is_valid_empty_quarantine(tmp_path: Path) -> None:
    """A fresh checkout with no generated registry still produces a gate."""
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(tmp_path / "absent.jsonl", output)

    assert document["record_count"] == 0
    assert document["records"] == []
    assert document["source_sha256"] == "UNKNOWN"
    assert document["source_present"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == document


def test_cli_generate_and_verify(tmp_path: Path) -> None:
    """The CLI generates and verifies a quarantined registry successfully."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    script = SCRIPTS / "quarantine_experiments.py"

    generated = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    verdict = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(source),
            "--output",
            str(output),
            "--verify",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verdict.returncode == 0, verdict.stderr
    assert "VERIFY PASS" in verdict.stdout
    assert "/Users/" not in verdict.stdout + verdict.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("label", "RESEARCH_EVIDENCE", "label mismatch"),
        ("research_evidence_status", "VERIFIED", "status mismatch"),
        ("acceptance", "ACCEPTED", "acceptance mismatch"),
        ("record_count", 2, "record_count mismatch"),
        ("source_sha256", "0" * 64, "source_sha256 mismatch"),
        ("historical", False, "historical marker is missing"),
    ],
)
def test_cli_verify_rejects_mutation(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    """A tampered quarantine fails with a precise, non-secret message."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(source, output)
    document[field] = value
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verdict = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "quarantine_experiments.py"),
            "--input",
            str(source),
            "--output",
            str(output),
            "--verify",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verdict.returncode != 0
    assert "VERIFY FAIL" in verdict.stderr
    assert message in verdict.stderr
    assert "/Users/" not in verdict.stderr


@pytest.mark.parametrize("tamper_all", [False, True])
def test_substituted_records_are_rejected(
    tmp_path: Path, tamper_all: bool
) -> None:
    """Fabricated records cannot retain a valid count or source digest."""
    source = tmp_path / "experiments.jsonl"
    raw = write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(source, output)
    original_count = document["record_count"]
    original_source_digest = document["source_sha256"]
    targets = document["records"] if tamper_all else document["records"][:1]
    for record in targets:
        record["status"] = "FABRICATED_RESULTS"
    assert document["record_count"] == original_count
    assert document["source_sha256"] == original_source_digest

    errors = QUARANTINE.validate_document(document)
    assert any("records_digest mismatch" in error for error in errors)

    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verify_errors = QUARANTINE.verify(source, output)
    assert any(
        "records content mismatch with source records" in error
        for error in verify_errors
    )
    assert not any("source_sha256 mismatch" in error for error in verify_errors)
    assert raw == source.read_bytes()

    staged = json.loads(output.read_text(encoding="utf-8"))
    assert QUARANTINE.validate_document(staged) == errors
    bundle = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(RELEASE_BUNDLE.QUARANTINE_ENTRY, output.read_bytes())
    bundle_errors: list[str] = []
    with zipfile.ZipFile(bundle) as archive:
        details = RELEASE_BUNDLE.check_quarantine_evidence(
            archive, bundle_errors, required=True
        )
    assert details["status"] == "FAIL"
    assert any("records_digest mismatch" in e for e in bundle_errors)


def test_verify_rejects_altered_records_digest(tmp_path: Path) -> None:
    """The declared records digest must also match the source records."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    document = QUARANTINE.generate(source, output)
    document["records_digest"] = "f" * 64
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert any(
        "records_digest mismatch" in error
        for error in QUARANTINE.validate_document(document)
    )
    errors = QUARANTINE.verify(source, output)
    assert any(
        "records_digest mismatch with source records" in error
        for error in errors
    )


def test_source_path_is_archive_relative(tmp_path: Path) -> None:
    """The source path is archive-relative, never a machine-local path."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    output = tmp_path / "quarantine.json"
    QUARANTINE.generate(source, output)
    payload = output.read_text(encoding="utf-8")

    assert not Path(QUARANTINE.SOURCE_PATH).is_absolute()
    assert "/Users/" not in payload
    assert str(tmp_path) not in payload
    assert json.loads(payload)["source_path"] == (
        "data/assurance/experiments.jsonl"
    )


def test_bundle_staging_excludes_raw_and_includes_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle staging never copies the raw registry verbatim."""
    repo = tmp_path / "repo"
    source = repo / "data" / "assurance" / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    monkeypatch.setattr(RELEASE_BUNDLE, "ROOT", repo)
    quant_root = tmp_path / "quant"

    RELEASE_BUNDLE.stage_optional_evidence(quant_root)

    assurance = quant_root / "evidence" / "assurance"
    assert not (assurance / "experiments.jsonl").exists()
    staged = assurance / "research-experiment-quarantine.json"
    assert staged.is_file()
    document = json.loads(staged.read_text(encoding="utf-8"))
    assert document["record_count"] == len(SAMPLE_RECORDS)
    assert document["records"] == SAMPLE_RECORDS
    assert document["acceptance"] == "NOT_ACCEPTED_AS_RESEARCH"


def test_bundle_staging_handles_missing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout without generated evidence still stages quarantine."""
    monkeypatch.setattr(RELEASE_BUNDLE, "ROOT", tmp_path / "repo")
    quant_root = tmp_path / "quant"

    RELEASE_BUNDLE.stage_optional_evidence(quant_root)

    staged = (
        quant_root
        / "evidence"
        / "assurance"
        / "research-experiment-quarantine.json"
    )
    document = json.loads(staged.read_text(encoding="utf-8"))
    assert document["record_count"] == 0
    assert document["source_present"] is False


def test_bundle_verify_rejects_raw_registry(tmp_path: Path) -> None:
    """An unquarantined registry in a bundle is always a verification error."""
    bundle = tmp_path / "raw.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(RELEASE_BUNDLE.RAW_EXPERIMENTS_ENTRY, "[]\n")

    errors: list[str] = []
    with zipfile.ZipFile(bundle) as archive:
        details = RELEASE_BUNDLE.check_quarantine_evidence(
            archive, errors, required=False
        )
    assert details["status"] == "FAIL"
    assert any("unquarantined experiment registry" in e for e in errors)


def test_bundle_verify_requires_quarantine(tmp_path: Path) -> None:
    """A built bundle cannot omit the labelled quarantine document."""
    bundle = tmp_path / "empty.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("quant/MANIFEST.md", "# manifest\n")

    errors: list[str] = []
    with zipfile.ZipFile(bundle) as archive:
        details = RELEASE_BUNDLE.check_quarantine_evidence(
            archive, errors, required=True
        )
    assert details["status"] == "FAIL"
    assert any("missing experiment quarantine" in e for e in errors)


def test_bundle_verify_accepts_quarantine(tmp_path: Path) -> None:
    """A labelled quarantine document passes the bundle evidence check."""
    source = tmp_path / "experiments.jsonl"
    write_source(source, SAMPLE_RECORDS)
    document_path = tmp_path / "quarantine.json"
    QUARANTINE.generate(source, document_path)
    bundle = tmp_path / "quarantined.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            RELEASE_BUNDLE.QUARANTINE_ENTRY, document_path.read_bytes()
        )

    errors: list[str] = []
    with zipfile.ZipFile(bundle) as archive:
        details = RELEASE_BUNDLE.check_quarantine_evidence(
            archive, errors, required=True
        )
    assert errors == []
    assert details["status"] == "PASS"
    assert details["record_count"] == len(SAMPLE_RECORDS)
