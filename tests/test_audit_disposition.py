#!/usr/bin/env python3
"""Offline tests for advisory terminology disposition classification."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location(
    "audit_disposition", ROOT / "scripts" / "audit_disposition.py"
)
assert _SPEC is not None and _SPEC.loader is not None
AUDIT_DISPOSITION = importlib.util.module_from_spec(_SPEC)
# Python 3.13+ resolves dataclass annotations through sys.modules; the module
# must be registered before exec_module or class creation fails.
sys.modules["audit_disposition"] = AUDIT_DISPOSITION
_SPEC.loader.exec_module(AUDIT_DISPOSITION)

DISPOSITIONS = AUDIT_DISPOSITION.DISPOSITIONS
FIXED_TIME = "2026-01-02T03:04:05Z"
BASE = {
    "source": "docs/example.md",
    "line": 10,
    "category": "unsupported_absolute",
    "phrase": "never",
    "context": "Never print secrets.",
}


def make_finding(**overrides: str | int) -> object:
    """Build a synthetic finding using the audit record shape."""
    payload = {**BASE, **overrides}
    return AUDIT_DISPOSITION.Finding(**payload)


CASE_FINDINGS = (
    (
        "CORRECT_WORDING",
        make_finding(
            source="AGENTS.md",
            context="Keep credentials in Keychain. Never print secrets.",
        ),
    ),
    (
        "ACCEPTED_DOMAIN_TERMINOLOGY",
        make_finding(
            phrase="zero-loss",
            context="A q IPC response is not claimed as a zero-loss guarantee.",
        ),
    ),
    (
        "HISTORICAL_STATEMENT",
        make_finding(
            phrase="later",
            category="vague_scope",
            context="PR #1 was later merged into main.",
        ),
    ),
    (
        "AMBIGUOUS_REQUIREMENT",
        make_finding(
            source="docs/git-workflow.md",
            context=(
                "mandatory only at high-risk release gates, never for a "
                "routine PR."
            ),
        ),
    ),
    (
        "POTENTIALLY_MISLEADING_CLAIM",
        make_finding(
            source="include/quant.hpp",
            context="Paper execution never contacts a broker order endpoint.",
        ),
    ),
    (
        "DOCUMENTATION_DEFECT",
        make_finding(
            source="scripts/assurance.py",
            line=307,
            category="informal_verb",
            phrase="bad",
            context="bad = [path for path in tracked()]",
        ),
    ),
)
CASE_IDS = [disposition for disposition, _finding in CASE_FINDINGS]


@pytest.mark.parametrize(("expected", "item"), CASE_FINDINGS, ids=CASE_IDS)
def test_each_disposition_has_a_rule(expected: str, item: object) -> None:
    """Every disposition is reachable through a synthetic finding."""
    disposition, reason = AUDIT_DISPOSITION.classify(item)
    assert disposition == expected
    assert reason


def test_historical_source_path_is_classified() -> None:
    """A historical-labelled source alone is sufficient."""
    item = make_finding(
        source="governance/historical-baseline.md",
        context="the old wording stands",
    )
    disposition, reason = AUDIT_DISPOSITION.classify(item)
    assert disposition == "HISTORICAL_STATEMENT"
    assert reason


def test_every_advisory_finding_is_classified() -> None:
    """Each advisory finding gets exactly one disposition and a reason."""
    findings = [item for _expected, item in CASE_FINDINGS]
    findings.append(
        make_finding(
            source="docs/misc.md",
            context="Generated files are never documentation.",
        )
    )
    raw = json.dumps([item.as_dict() for item in findings]).encode()
    report = AUDIT_DISPOSITION.build_report(
        raw, findings, generated_at=FIXED_TIME
    )
    records = report["dispositions"]
    assert len(records) == len(findings)
    assert sum(report["counts"].values()) == len(findings)
    assert set(report["counts"]) == set(DISPOSITIONS)
    for record in records:
        assert record["disposition"] in DISPOSITIONS
        assert record["reason"]


def test_binding_categories_are_not_reclassified() -> None:
    """Binding findings stay separate and keep their original fields."""
    binding = (
        make_finding(
            source="governance/inventory.json",
            line=1,
            category="tautology",
            phrase="exact same",
            context="the exact same value",
        ),
        make_finding(
            source="governance/inventory.json",
            line=2,
            category="audit_ignore",
            phrase="bare marker",
            context="Never print secrets.",
        ),
    )
    advisory = make_finding(context="Never print secrets.")
    findings = [*binding, advisory]
    raw = json.dumps([item.as_dict() for item in findings]).encode()
    report = AUDIT_DISPOSITION.build_report(
        raw, findings, generated_at=FIXED_TIME
    )
    assert report["binding_findings"] == 2
    assert report["advisory_findings"] == 1
    assert sum(report["counts"].values()) == 1
    assert report["counts"]["CORRECT_WORDING"] == 1
    assert not [
        record
        for record in report["dispositions"]
        if record["category"] in AUDIT_DISPOSITION.BINDING_CATEGORIES
    ]
    assert report["dispositions"][0]["source"] == advisory.source


def test_source_digest_tracks_raw_input_bytes() -> None:
    """The digest changes when input bytes change, even for identical data."""
    item = make_finding()
    raw = json.dumps([item.as_dict()], indent=2).encode()
    report = AUDIT_DISPOSITION.build_report(
        raw, [item], generated_at=FIXED_TIME
    )
    assert report["source_digest"] == hashlib.sha256(raw).hexdigest()
    padded = AUDIT_DISPOSITION.build_report(
        raw + b"\n", [item], generated_at=FIXED_TIME
    )
    assert padded["source_digest"] != report["source_digest"]
    assert padded["generated_at"] == report["generated_at"]


MALFORMED = (
    b'{"findings": []}',
    b"[42]",
    b'[{"source": "docs/example.md"}]',
    b"[] trailing",
    b"null",
)


@pytest.mark.parametrize(
    "payload",
    MALFORMED,
    ids=["object", "scalar", "partial", "trailing", "null"],
)
def test_malformed_input_is_rejected(tmp_path: Path, payload: bytes) -> None:
    """Malformed audit JSON fails closed with a readable error."""
    source = tmp_path / "bad-audit.json"
    source.write_bytes(payload)
    with pytest.raises(ValueError):
        AUDIT_DISPOSITION.classify_file(source, tmp_path / "dispositions.json")


def test_invalid_field_type_is_rejected() -> None:
    """A finding with a non-integer line is not accepted."""
    record = dict(BASE)
    record["line"] = True
    with pytest.raises(ValueError, match="line"):
        AUDIT_DISPOSITION.parse_findings([record])


def test_empty_input_produces_zero_counts(tmp_path: Path) -> None:
    """An empty audit array is valid and yields an empty disposition set."""
    source = tmp_path / "empty-audit.json"
    source.write_bytes(b"[]\n")
    output = tmp_path / "empty-dispositions.json"
    report = AUDIT_DISPOSITION.classify_file(source, output)
    assert report["advisory_findings"] == 0
    assert report["binding_findings"] == 0
    assert report["dispositions"] == []
    assert all(count == 0 for count in report["counts"].values())
    assert (
        output.read_text(encoding="utf-8")
        == json.dumps(report, indent=2, sort_keys=True) + "\n"
    )


def test_input_audit_findings_are_not_modified(tmp_path: Path) -> None:
    """Classification never writes back to the audit file."""
    source = tmp_path / "audit.json"
    records = [item.as_dict() for _expected, item in CASE_FINDINGS]
    original = json.dumps(records, indent=2) + "\n"
    source.write_text(original, encoding="utf-8")
    AUDIT_DISPOSITION.classify_file(source, tmp_path / "out.json")
    assert source.read_text(encoding="utf-8") == original
    assert json.loads(source.read_text(encoding="utf-8")) == records


def test_cli_classify_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI commands write canonical JSON and print all disposition counts."""
    source = tmp_path / "audit.json"
    output = tmp_path / "dispositions.json"
    records = [item.as_dict() for _expected, item in CASE_FINDINGS]
    source.write_text(json.dumps(records), encoding="utf-8")
    assert (
        AUDIT_DISPOSITION.main(
            ["classify", "--input", str(source), "--output", str(output)]
        )
        == 0
    )
    written = json.loads(output.read_text(encoding="utf-8"))
    assert (
        written["source_digest"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert AUDIT_DISPOSITION.main(["summary", "--path", str(output)]) == 0
    text = capsys.readouterr().out
    for name in DISPOSITIONS:
        assert f"{name}: {written['counts'][name]}" in text


def case_findings() -> list:
    """Return the synthetic advisory findings used across the evidence tests."""
    return [item for _expected, item in CASE_FINDINGS]


def write_evidence(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """Write a source audit and its freshly generated disposition report."""
    findings = case_findings()
    raw = json.dumps([item.as_dict() for item in findings], indent=2).encode()
    source = tmp_path / "audit.json"
    source.write_bytes(raw)
    path = tmp_path / "dispositions.json"
    report = AUDIT_DISPOSITION.build_report(
        raw, findings, generated_at=FIXED_TIME
    )
    AUDIT_DISPOSITION.write_report(report, path)
    return source, path, raw


def mutating_verify(source: Path, path: Path, mutate) -> None:
    """Mutate a saved report, leaving verification to the calling test."""
    report = AUDIT_DISPOSITION.load_json(path, "disposition report")
    mutate(report)
    AUDIT_DISPOSITION.write_report(report, path)
    AUDIT_DISPOSITION.verify_bundle(source, path)


def test_finding_id_is_stable_unique_and_hex() -> None:
    """A finding ID is reproducible from the four source fields only."""
    item = make_finding()
    identifier = AUDIT_DISPOSITION.finding_id(item)
    assert identifier == AUDIT_DISPOSITION.finding_id(make_finding())
    assert (
        identifier
        == hashlib.sha256(
            b"docs/example.md:10:unsupported_absolute:never"
        ).hexdigest()
    )
    assert len(identifier) == 64
    assert set(identifier) <= set("0123456789abcdef")
    assert identifier != AUDIT_DISPOSITION.finding_id(make_finding(line=11))
    assert identifier != AUDIT_DISPOSITION.finding_id(
        make_finding(phrase="always")
    )


def test_findings_and_dispositions_have_exactly_matching_ids() -> None:
    """One-to-one coverage pairs every audit record with one disposition."""
    findings = case_findings()
    raw = json.dumps([item.as_dict() for item in findings]).encode()
    report = AUDIT_DISPOSITION.build_report(
        raw, findings, generated_at=FIXED_TIME
    )
    finding_ids = [item["finding_id"] for item in report["findings"]]
    disposition_ids = [item["finding_id"] for item in report["dispositions"]]
    assert finding_ids == disposition_ids
    assert len(set(finding_ids)) == len(findings)
    for record in report["dispositions"]:
        assert record["finding_id"] in {
            AUDIT_DISPOSITION.finding_id(item) for item in findings
        }
        assert record["disposition"] in DISPOSITIONS
    AUDIT_DISPOSITION.validate_report(raw, findings, report)


def test_verify_accepts_evidence_without_regenerating(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI verify command checks bundled evidence and prints PASS."""
    source, path, _raw = write_evidence(tmp_path)
    original = path.read_bytes()
    assert (
        AUDIT_DISPOSITION.main(
            ["verify", "--input", str(source), "--path", str(path)]
        )
        == 0
    )
    assert path.read_bytes() == original
    assert "verify: PASS" in capsys.readouterr().out


def test_stale_digest_is_rejected_by_verify(tmp_path: Path) -> None:
    """A report tied to other audit bytes cannot be verified."""
    source, path, _raw = write_evidence(tmp_path)

    def mutate(report: dict) -> None:
        report["source_digest"] = "0" * 64

    with pytest.raises(ValueError, match="stale source digest"):
        mutating_verify(source, path, mutate)


def test_stale_output_requires_explicit_force(tmp_path: Path) -> None:
    """Classification refuses to overwrite a stale bundled report."""
    source, path, _raw = write_evidence(tmp_path)
    report = AUDIT_DISPOSITION.load_json(path, "disposition report")
    report["source_digest"] = "f" * 64
    AUDIT_DISPOSITION.write_report(report, path)
    with pytest.raises(ValueError, match="pass --force"):
        AUDIT_DISPOSITION.classify_file(source, path)
    assert AUDIT_DISPOSITION.classify_file(source, path, force=True)[
        "source_digest"
    ] == AUDIT_DISPOSITION.digest(source.read_bytes())


def test_missing_disposition_is_rejected(tmp_path: Path) -> None:
    """A finding without a disposition fails coverage validation."""
    source, path, _raw = write_evidence(tmp_path)

    def mutate(report: dict) -> None:
        report["dispositions"].pop()

    with pytest.raises(ValueError, match="missing disposition"):
        mutating_verify(source, path, mutate)


def test_duplicate_disposition_is_rejected(tmp_path: Path) -> None:
    """A finding disposed twice fails coverage validation."""
    source, path, _raw = write_evidence(tmp_path)

    def mutate(report: dict) -> None:
        record = dict(report["dispositions"][0])
        report["dispositions"].append(record)

    with pytest.raises(ValueError, match="duplicate finding_id"):
        mutating_verify(source, path, mutate)


def test_extra_disposition_is_rejected(tmp_path: Path) -> None:
    """A disposition for an unknown finding fails coverage validation."""
    source, path, _raw = write_evidence(tmp_path)

    def mutate(report: dict) -> None:
        record = dict(report["dispositions"][0])
        record["finding_id"] = "e" * 64
        report["dispositions"].append(record)

    with pytest.raises(ValueError, match="extra finding_id"):
        mutating_verify(source, path, mutate)


def test_missing_audit_record_is_rejected(tmp_path: Path) -> None:
    """A disposition list that outruns the audit records is rejected."""
    source, path, _raw = write_evidence(tmp_path)

    def mutate(report: dict) -> None:
        report["findings"].pop()

    with pytest.raises(ValueError, match="missing audit record"):
        mutating_verify(source, path, mutate)


def test_review_report_records_owners_statuses_and_counts(
    tmp_path: Path,
) -> None:
    """The review names an owner and resolution state for every category."""
    source, path, raw = write_evidence(tmp_path)
    report = AUDIT_DISPOSITION.load_json(path, "disposition report")
    review_path = tmp_path / "review.json"
    review = AUDIT_DISPOSITION.build_review_report(
        raw, report, source=str(source)
    )
    AUDIT_DISPOSITION.write_report(review, review_path)
    assert review["source_digest"] == hashlib.sha256(raw).hexdigest()
    assert review["counts"] == report["counts"]
    assert set(review["categories"]) == set(DISPOSITIONS)
    for name in DISPOSITIONS:
        entry = review["categories"][name]
        expected = AUDIT_DISPOSITION.DISPOSITION_OWNERS[name]
        assert entry["owner_class"] == expected["owner_class"]
        assert entry["resolution_status"] == expected["resolution_status"]
        assert entry["count"] == report["counts"][name]
    for name in (
        "AMBIGUOUS_REQUIREMENT",
        "POTENTIALLY_MISLEADING_CLAIM",
        "DOCUMENTATION_DEFECT",
    ):
        entry = review["categories"][name]
        assert entry["owner_class"] == "maintainer/documentation"
        assert entry["resolution_status"] == "OPEN"
    assert review["resolution_counts"]["OPEN"] == sum(
        report["counts"][name]
        for name in (
            "AMBIGUOUS_REQUIREMENT",
            "POTENTIALLY_MISLEADING_CLAIM",
            "DOCUMENTATION_DEFECT",
        )
    )
    assert AUDIT_DISPOSITION.verify_bundle(source, path, review_path) == report
    assert (
        AUDIT_DISPOSITION.build_review_report(raw, report, source=str(source))
        == review
    )


def test_review_wrong_status_is_rejected(tmp_path: Path) -> None:
    """An OPEN category cannot be presented as resolved in the review."""
    source, path, raw = write_evidence(tmp_path)
    report = AUDIT_DISPOSITION.load_json(path, "disposition report")
    review_path = tmp_path / "review.json"
    review = AUDIT_DISPOSITION.build_review_report(raw, report)
    review["categories"]["AMBIGUOUS_REQUIREMENT"][
        "resolution_status"
    ] = "resolved"
    AUDIT_DISPOSITION.write_report(review, review_path)
    with pytest.raises(ValueError, match="wrong resolution status"):
        AUDIT_DISPOSITION.verify_bundle(source, path, review_path)


def test_cli_verify_reports_failure_on_tampered_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing verification exits nonzero and explains the problem."""
    source, path, _raw = write_evidence(tmp_path)
    report = AUDIT_DISPOSITION.load_json(path, "disposition report")
    report["counts"]["CORRECT_WORDING"] += 1
    AUDIT_DISPOSITION.write_report(report, path)
    assert (
        AUDIT_DISPOSITION.main(
            ["verify", "--input", str(source), "--path", str(path)]
        )
        == 1
    )
    assert "counts disagree" in capsys.readouterr().err
