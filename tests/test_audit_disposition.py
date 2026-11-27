#!/usr/bin/env python3
"""Offline tests for advisory terminology disposition classification."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "audit_disposition", ROOT / "scripts" / "audit_disposition.py"
)
assert _SPEC is not None and _SPEC.loader is not None
AUDIT_DISPOSITION = importlib.util.module_from_spec(_SPEC)
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
