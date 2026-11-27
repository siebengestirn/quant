#!/usr/bin/env python3
"""Classify advisory terminology findings into explicit dispositions.

Usage:
  python3 scripts/audit_disposition.py classify [--input PATH] [--output PATH]
      [--review PATH] [--force]
  python3 scripts/audit_disposition.py verify [--input PATH] [--path PATH]
      [--review PATH]
  python3 scripts/audit_disposition.py summary [--path PATH]

The audit findings are never edited or suppressed. This tool only reads the
JSON produced by the assurance terminology audit and records a disposition and
a short reason for every advisory finding. Binding categories stay separate.

Every record carries a stable ``finding_id`` derived from the audit fields
(source, line, category and phrase). The generated report and its machine-
readable review are exact evidence: ``verify`` checks the bundled files against
the raw audit bytes without regenerating or rewriting them. A ``classify`` run
that would replace an existing report whose source digest is stale is rejected
unless ``--force`` is given explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from quant_common import Finding, run_cli

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "assurance" / "english-audit.json"
DEFAULT_OUTPUT = ROOT / "data" / "assurance" / "audit-dispositions.json"
DEFAULT_REVIEW = ROOT / "data" / "assurance" / "audit-disposition-review.json"
SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
BINDING_CATEGORIES = frozenset({"tautology", "audit_ignore"})
DISPOSITIONS = (
    "CORRECT_WORDING",
    "ACCEPTED_DOMAIN_TERMINOLOGY",
    "HISTORICAL_STATEMENT",
    "AMBIGUOUS_REQUIREMENT",
    "POTENTIALLY_MISLEADING_CLAIM",
    "DOCUMENTATION_DEFECT",
)
OPEN_OWNER = "maintainer/documentation"
# Named owner class and explicit resolution state for each disposition
# category. Only the three resolved classes are closed; open wording needs a
# documentation maintainer decision and is never presented as accepted.
DISPOSITION_OWNERS = {
    "ACCEPTED_DOMAIN_TERMINOLOGY": {
        "owner_class": "domain-terminology",
        "resolution_status": "resolved",
    },
    "CORRECT_WORDING": {
        "owner_class": "terminology-audit",
        "resolution_status": "resolved",
    },
    "HISTORICAL_STATEMENT": {
        "owner_class": "historical-record",
        "resolution_status": "resolved",
    },
    "AMBIGUOUS_REQUIREMENT": {
        "owner_class": OPEN_OWNER,
        "resolution_status": "OPEN",
    },
    "POTENTIALLY_MISLEADING_CLAIM": {
        "owner_class": OPEN_OWNER,
        "resolution_status": "OPEN",
    },
    "DOCUMENTATION_DEFECT": {
        "owner_class": OPEN_OWNER,
        "resolution_status": "OPEN",
    },
}

INFORMAL = re.compile(
    r"\b(?:do the needful|make sure|works fine|goes wrong|fix it|"
    r"handle it|stuff|things|nice|good|bad|huge|tiny)\b"
)
HISTORY_MARKER = re.compile(
    r"\b(?:pr\s*#?\d+|prs\s*#?\d+|was later merged|were also merged|"
    r"at capture time|point[- ]in[- ]time capture|"
    r"historical (?:value|values|snapshot|record|commit|commits))\b"
)
DOMAIN_TERM = re.compile(
    r"\b(?:zero[- ]loss|large[- ]timestamp|large notional|retry later|"
    r"later (?:surveillance|delisting|record|head|heads|point|by)|"
    r"created later|notional|bar|tick)\b"
)
VAGUE_SCOPE = re.compile(
    r"\b(?:some|many|few|several|various|numerous|often|usually|"
    r"sometimes|soon|fast|slow|small|enough|reasonable|appropriate|"
    r"significant|substantial|high[- ]risk|routine|as needed|"
    r"where possible|where applicable)\b"
)
REQUIREMENT = re.compile(
    r"\b(?:must|should|required|requirement|mandatory|gate|policy|"
    r"only|accept|allow|claim|explicit|explicitly)\b"
)
CAPABILITY = re.compile(
    r"\b(?:never (?:contacts?|contacted|requires?|reaches?|downloads?|"
    r"attempted|fabricates?|refreshes?|mutates?|recomputed)|"
    r"always available|never fails?|guarantees?|ensures?|lossless|"
    r"exactly once|production[- ]ready|zero downtime|100%)\b"
)
SECURITY_TERMS = re.compile(
    r"\b(?:secrets?|credentials?|tokens?|passwords?|keychain|"
    r"api keys?|authorization)\b"
)
NEGATION = re.compile(
    r"\b(?:never|not|no|must|do not|cannot|explicit|explicitly|"
    r"absence|invalid|unknown)\b"
)
FAIL_CLOSED = re.compile(
    r"\b(?:fail[- ]closed|gated[_ -]not[_ -]run|always return false|"
    r"return false|explicit absence|invalid (?:money|amount|input)|"
    r"never (?:a valid zero|silently|reported as passed|as passed|passed|"
    r"promoted|inferred|guessed|converted|replaced|accepted|mutates|"
    r"enters logs|satisfy a financial guard)|"
    r"(?:is|are) unknown|missing (?:provider time|data)|"
    r"never a genuine zero|never used as|never from)\b"
)
EXPLICIT_EXCLUSION = re.compile(
    r"\b(?:never (?:tags|embedded|documentation|execution[- ]day|shell|"
    r"interpolation|evaluates|treated|used for|by a|as a|sources of)|"
    r"only when explicitly requested)\b"
)
PROHIBITION = re.compile(
    r"\b(?:never|must not|shall not|do not)\b.{0,90}?"
    r"\b(?:print|printed|log|logged|persist|persisted|commit|committed|"
    r"transmit|sent|store|stored|hardcode|force[- ]push|rewrite|merge|"
    r"delete|track|tracked|infer|inferred|interpret|interpreted|"
    r"accept|accepted|reported|reach|recomputed|download|downloaded|"
    r"attempted|fabricate|fabricates|refresh|refreshes|guess|guessed|"
    r"promote|promoted|add|added|adds|mutates|contact|contacts|"
    r"contacted|require|requires|inspected|converted|replaced|"
    r"claimed|means)\b"
)
AUTHORITATIVE = re.compile(
    r"\b(?:authoritative|authority|governed|policy|contract|invariant|"
    r"mandatory|required|requirement|rule|specification|definition|"
    r"complete|completion|verified|proof|valid|genuine|identity|"
    r"gate|claimed)\b"
)
HISTORICAL_SOURCE = re.compile(
    r"(?:historical|history|baseline|release-status|correctness-fixes|"
    r"acceptance-register)"
)


@dataclass(frozen=True)
class Rule:
    """An ordered, pattern-based classification rule."""

    disposition: str
    reason: str
    patterns: tuple[re.Pattern[str], ...]
    require_all: bool = False

    def matches(self, text: str) -> bool:
        """Report whether the rule matches the supplied text."""
        if self.require_all:
            return all(pattern.search(text) for pattern in self.patterns)
        return any(pattern.search(text) for pattern in self.patterns)


CONTEXT_RULES = (
    Rule(
        "DOCUMENTATION_DEFECT",
        "informal wording in a code comment or test literal",
        (INFORMAL,),
    ),
    Rule(
        "HISTORICAL_STATEMENT",
        "line records PR history or a point-in-time capture",
        (HISTORY_MARKER,),
    ),
    Rule(
        "ACCEPTED_DOMAIN_TERMINOLOGY",
        "established quant or finance terminology, not a capability claim",
        (DOMAIN_TERM,),
    ),
    Rule(
        "AMBIGUOUS_REQUIREMENT",
        "vague scope appears in requirement wording",
        (VAGUE_SCOPE, REQUIREMENT),
        require_all=True,
    ),
    Rule(
        "POTENTIALLY_MISLEADING_CLAIM",
        "absolute claim about an operational capability",
        (CAPABILITY,),
    ),
    Rule(
        "CORRECT_WORDING",
        "security invariant protects credentials or secrets",
        (SECURITY_TERMS, NEGATION),
        require_all=True,
    ),
    Rule(
        "CORRECT_WORDING",
        "fail-closed rule forbids an unsafe fallback",
        (FAIL_CLOSED,),
    ),
    Rule(
        "CORRECT_WORDING",
        "explicit prohibition stated in a policy or contract",
        (PROHIBITION,),
    ),
    Rule(
        "CORRECT_WORDING",
        "authoritative rule stated with explicit negation",
        (AUTHORITATIVE, NEGATION),
        require_all=True,
    ),
    Rule(
        "CORRECT_WORDING",
        "explicit exclusion of an invalid interpretation",
        (EXPLICIT_EXCLUSION,),
    ),
)
SOURCE_RULES = (
    Rule(
        "HISTORICAL_STATEMENT",
        "source is a historical-labelled document",
        (HISTORICAL_SOURCE,),
    ),
)
FALLBACK_REASON = (
    "no decisive rule matched; advisory wording requires human review"
)


def utc_now() -> str:
    """Return the current UTC instant in a stable Z-suffixed format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(raw: bytes) -> str:
    """Return the SHA-256 of the exact audit bytes."""
    return hashlib.sha256(raw).hexdigest()


def classify(finding: Finding) -> tuple[str, str]:
    """Return one disposition and its reason for an advisory finding."""
    text = finding.context.lower()
    for rule in CONTEXT_RULES:
        if rule.matches(text):
            return rule.disposition, rule.reason
    source = finding.source.lower()
    for rule in SOURCE_RULES:
        if rule.matches(source):
            return rule.disposition, rule.reason
    return "AMBIGUOUS_REQUIREMENT", FALLBACK_REASON


def finding_id(finding: Finding) -> str:
    """Return a stable ID from source, line, category and phrase."""
    identity = ":".join(
        (
            finding.source,
            str(finding.line),
            finding.category,
            finding.phrase,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def audit_record(finding: Finding) -> dict:
    """Return one audit-derived record with its stable finding ID."""
    return {**finding.as_dict(), "finding_id": finding_id(finding)}


def advisory_findings(findings: list[Finding]) -> list[Finding]:
    """Return findings that require an explicit disposition."""
    return [
        finding
        for finding in findings
        if finding.category not in BINDING_CATEGORIES
    ]


def required_text(item: dict, key: str, index: int) -> str:
    """Validate and return one non-empty text field of a finding."""
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"finding {index}: {key} must be a non-empty string")
    return value


def parse_findings(payload: object) -> list[Finding]:
    """Validate the audit JSON payload and return its findings."""
    if not isinstance(payload, list):
        raise ValueError("audit findings must be a JSON array")
    findings = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"finding {index}: expected a JSON object")
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError(
                f"finding {index}: line must be a positive integer"
            )
        findings.append(
            Finding(
                source=required_text(item, "source", index),
                line=line,
                category=required_text(item, "category", index),
                phrase=required_text(item, "phrase", index),
                context=required_text(item, "context", index),
            )
        )
    return findings


def load_findings(path: Path) -> tuple[bytes, list[Finding]]:
    """Read the audit JSON bytes and validate the finding records."""
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable audit findings at {path}: {exc}") from exc
    return raw, parse_findings(payload)


def load_json(path: Path, label: str) -> dict:
    """Read one evidence JSON object, failing closed on malformed input."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable {label} at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def build_report(
    raw: bytes,
    findings: list[Finding],
    *,
    source: str = "",
    generated_at: str | None = None,
) -> dict:
    """Build the disposition report without mutating the audit findings."""
    advisory = advisory_findings(findings)
    records = []
    for finding in advisory:
        disposition, reason = classify(finding)
        records.append(
            {
                **audit_record(finding),
                "disposition": disposition,
                "reason": reason,
            }
        )
    counts = {name: 0 for name in DISPOSITIONS}
    for record in records:
        counts[record["disposition"]] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or utc_now(),
        "source": source,
        "source_digest": digest(raw),
        "binding_categories": sorted(BINDING_CATEGORIES),
        "binding_findings": len(findings) - len(advisory),
        "advisory_findings": len(advisory),
        "findings": [audit_record(finding) for finding in advisory],
        "counts": counts,
        "dispositions": records,
    }


def build_review_report(
    raw: bytes,
    report: dict,
    *,
    source: str = "",
    generated_at: str | None = None,
) -> dict:
    """Build the machine-readable owner and resolution decision record."""
    records = report["dispositions"]
    categories = {}
    open_ids: list[str] = []
    for name in DISPOSITIONS:
        owner = DISPOSITION_OWNERS[name]
        ids = [
            record["finding_id"]
            for record in records
            if record["disposition"] == name
        ]
        categories[name] = {
            "owner_class": owner["owner_class"],
            "owner": owner["owner_class"],
            "resolution_status": owner["resolution_status"],
            "count": len(ids),
            "finding_ids": ids,
        }
        if owner["resolution_status"] == "OPEN":
            open_ids.extend(ids)
    resolved = sum(
        item["count"]
        for item in categories.values()
        if item["resolution_status"] == "resolved"
    )
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now(),
        "source": source,
        "source_digest": digest(raw),
        "total_findings": report["advisory_findings"],
        "counts": dict(report["counts"]),
        "categories": categories,
        "resolution_counts": {
            "resolved": resolved,
            "OPEN": len(open_ids),
        },
        "open_finding_ids": sorted(open_ids),
    }


def write_report(report: dict, path: Path) -> None:
    """Write canonical JSON, creating the parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check_ids(ids: list[str], label: str) -> None:
    """Reject duplicate IDs in either evidence list."""
    repeated = sorted({item for item in ids if ids.count(item) > 1})
    if repeated:
        raise ValueError(f"duplicate finding_id in {label}: {repeated[0]}")


def expected_findings(findings: list[Finding]) -> dict:
    """Map every advisory finding ID to its source and audit record."""
    expected = {}
    for finding in advisory_findings(findings):
        identifier = finding_id(finding)
        if identifier in expected:
            raise ValueError(f"duplicate finding_id in audit: {identifier}")
        expected[identifier] = (finding, audit_record(finding))
    return expected


def validate_audit_records(report: dict, expected: dict) -> None:
    """Require each audit-derived record exactly once, unaltered."""
    recorded = report.get("findings")
    if not isinstance(recorded, list):
        raise ValueError("disposition report is missing audit findings")
    finding_ids = []
    for record in recorded:
        if not isinstance(record, dict):
            raise ValueError("audit finding record must be a JSON object")
        identifier = record.get("finding_id")
        if identifier not in expected:
            raise ValueError(f"extra finding_id in audit records: {identifier}")
        if record != expected[identifier][1]:
            raise ValueError(f"altered audit record: {identifier}")
        finding_ids.append(identifier)
    check_ids(finding_ids, "audit records")
    missing = set(expected) - set(finding_ids)
    if missing:
        raise ValueError(f"missing audit record: {sorted(missing)[0]}")


def validate_disposition_records(report: dict, expected: dict) -> None:
    """Require one correct, unaltered disposition per advisory finding."""
    dispositions = report.get("dispositions")
    if not isinstance(dispositions, list):
        raise ValueError("disposition report has no dispositions list")
    seen: list[str] = []
    counts = {name: 0 for name in DISPOSITIONS}
    for record in dispositions:
        if not isinstance(record, dict):
            raise ValueError("disposition record must be a JSON object")
        identifier = record.get("finding_id")
        if identifier in seen:
            raise ValueError(
                f"duplicate finding_id in dispositions: {identifier}"
            )
        if identifier not in expected:
            raise ValueError(f"extra finding_id in dispositions: {identifier}")
        finding, source_record = expected[identifier]
        for key, value in source_record.items():
            if record.get(key) != value:
                raise ValueError(
                    f"altered disposition field {key}: {identifier}"
                )
        disposition, reason = classify(finding)
        if record.get("disposition") != disposition:
            raise ValueError(f"incorrect disposition: {identifier}")
        if record.get("reason") != reason:
            raise ValueError(f"incorrect disposition reason: {identifier}")
        seen.append(identifier)
        counts[disposition] += 1
    missing = set(expected) - set(seen)
    if missing:
        raise ValueError(f"missing disposition: {sorted(missing)[0]}")
    if report.get("counts") != counts:
        raise ValueError("disposition counts disagree with records")


def validate_report(
    raw: bytes, findings: list[Finding], report: object
) -> None:
    """Check bundled evidence against the exact audit bytes."""
    if not isinstance(report, dict):
        raise ValueError("disposition report must be a JSON object")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected disposition schema version")
    expected_digest = digest(raw)
    if report.get("source_digest") != expected_digest:
        raise ValueError(
            "stale source digest: "
            f"report={report.get('source_digest')!r} audit={expected_digest}"
        )
    advisory = advisory_findings(findings)
    if report.get("advisory_findings") != len(advisory):
        raise ValueError("advisory finding count disagrees with audit")
    if report.get("binding_findings") != len(findings) - len(advisory):
        raise ValueError("binding finding count disagrees with audit")
    expected = expected_findings(findings)
    validate_audit_records(report, expected)
    validate_disposition_records(report, expected)


def validate_review_categories(
    categories: dict, report: dict
) -> tuple[int, list[str]]:
    """Validate every category's owner, state and finding IDs."""
    open_ids: list[str] = []
    resolved = 0
    for name in DISPOSITIONS:
        entry = categories[name]
        owner = DISPOSITION_OWNERS[name]
        if not isinstance(entry, dict):
            raise ValueError(f"invalid review category: {name}")
        if entry.get("owner_class") != owner["owner_class"]:
            raise ValueError(f"wrong owner class: {name}")
        if entry.get("resolution_status") != owner["resolution_status"]:
            raise ValueError(f"wrong resolution status: {name}")
        ids = [
            record["finding_id"]
            for record in report["dispositions"]
            if record["disposition"] == name
        ]
        if entry.get("finding_ids") != ids or entry.get("count") != len(ids):
            raise ValueError(f"review finding IDs disagree: {name}")
        if owner["resolution_status"] == "resolved":
            resolved += len(ids)
        else:
            open_ids.extend(ids)
    return resolved, open_ids


def validate_review_report(review: object, report: dict, raw: bytes) -> None:
    """Check owner classes, resolution states and counts of the review."""
    if not isinstance(review, dict):
        raise ValueError("review report must be a JSON object")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("unexpected review schema version")
    if review.get("source_digest") != digest(raw):
        raise ValueError("stale review source digest")
    if review.get("counts") != report.get("counts"):
        raise ValueError("review counts disagree with dispositions")
    categories = review.get("categories")
    if not isinstance(categories, dict) or set(categories) != set(DISPOSITIONS):
        raise ValueError("review must cover every disposition category")
    resolved, open_ids = validate_review_categories(categories, report)
    if review.get("resolution_counts") != {
        "resolved": resolved,
        "OPEN": len(open_ids),
    }:
        raise ValueError("review resolution counts disagree")
    if review.get("open_finding_ids") != sorted(open_ids):
        raise ValueError("review open finding IDs disagree")
    if review.get("total_findings") != report["advisory_findings"]:
        raise ValueError("review total disagrees with dispositions")


def output_is_stale(output: Path, raw: bytes) -> bool:
    """Report whether an existing output refers to different audit bytes."""
    if not output.exists():
        return False
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    return (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("source_digest") != digest(raw)
    )


def classify_file(
    input_path: Path,
    output_path: Path,
    *,
    review_path: Path | None = None,
    force: bool = False,
) -> dict:
    """Classify an audit file and write the generated disposition report."""
    raw, findings = load_findings(input_path)
    if not force and output_is_stale(output_path, raw):
        raise ValueError(
            f"stale disposition report at {output_path}; "
            "pass --force to overwrite"
        )
    report = build_report(raw, findings, source=str(input_path))
    write_report(report, output_path)
    if review_path is not None:
        review = build_review_report(raw, report, source=str(input_path))
        write_report(review, review_path)
    return report


def verify_bundle(
    input_path: Path, path: Path, review_path: Path | None = None
) -> dict:
    """Validate bundled evidence without regenerating it."""
    raw, findings = load_findings(input_path)
    report = load_json(path, "disposition report")
    validate_report(raw, findings, report)
    if review_path is not None and review_path.is_file():
        review = load_json(review_path, "review report")
        validate_review_report(review, report, raw)
    return report


def summarize(report: dict) -> None:
    """Print the disposition counts for a generated report."""
    print(f"schema version: {report['schema_version']}")
    print(f"source digest: {report['source_digest']}")
    print(f"binding findings (unchanged): {report['binding_findings']}")
    print(f"advisory findings: {report['advisory_findings']}")
    for name in DISPOSITIONS:
        print(f"{name}: {report['counts'][name]}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify_parser = subparsers.add_parser(
        "classify", help="write dispositions"
    )
    classify_parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT, help="audit JSON path"
    )
    classify_parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    classify_parser.add_argument(
        "--review", type=Path, help="review report JSON path"
    )
    classify_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a report whose source digest is stale",
    )
    verify_parser = subparsers.add_parser(
        "verify", help="check bundled evidence without regenerating"
    )
    verify_parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT, help="audit JSON path"
    )
    verify_parser.add_argument(
        "--path", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    verify_parser.add_argument(
        "--review", type=Path, help="review report JSON path"
    )
    summary_parser = subparsers.add_parser("summary", help="print counts")
    summary_parser.add_argument(
        "--path", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    return parser.parse_args(argv)


def dispatch(argv: list[str] | None) -> int:
    """Run the requested classification, verification or summary command."""
    args = parse_args(argv)
    if args.command == "classify":
        review = args.review or (
            DEFAULT_REVIEW if args.output == DEFAULT_OUTPUT else None
        )
        report = classify_file(
            args.input,
            args.output,
            review_path=review,
            force=args.force,
        )
    elif args.command == "verify":
        review = args.review or (
            DEFAULT_REVIEW if args.path == DEFAULT_OUTPUT else None
        )
        report = verify_bundle(args.input, args.path, review)
        print("verify: PASS")
    else:
        report = load_json(args.path, "disposition report")
    summarize(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested command, failing closed on malformed input."""
    return run_cli(
        dispatch,
        argv,
        (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError),
    )


if __name__ == "__main__":
    sys.exit(main())
