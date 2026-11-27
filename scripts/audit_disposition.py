#!/usr/bin/env python3
"""Classify advisory terminology findings into explicit dispositions.

Usage:
  python3 scripts/audit_disposition.py classify [--input PATH] [--output PATH]
  python3 scripts/audit_disposition.py summary [--path PATH]

The audit findings are never edited or suppressed. This tool only reads the
JSON produced by the assurance terminology audit and records a disposition and
a short reason for every advisory finding. Binding categories stay separate.
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "assurance" / "english-audit.json"
DEFAULT_OUTPUT = ROOT / "data" / "assurance" / "audit-dispositions.json"
SCHEMA_VERSION = 1
BINDING_CATEGORIES = frozenset({"tautology", "audit_ignore"})
DISPOSITIONS = (
    "CORRECT_WORDING",
    "ACCEPTED_DOMAIN_TERMINOLOGY",
    "HISTORICAL_STATEMENT",
    "AMBIGUOUS_REQUIREMENT",
    "POTENTIALLY_MISLEADING_CLAIM",
    "DOCUMENTATION_DEFECT",
)

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


@dataclass(frozen=True)
class Finding:
    """One advisory finding from the terminology audit."""

    source: str
    line: int
    category: str
    phrase: str
    context: str

    def as_dict(self) -> dict[str, str | int]:
        """Return the finding fields exactly as read from the audit."""
        return {
            "source": self.source,
            "line": self.line,
            "category": self.category,
            "phrase": self.phrase,
            "context": self.context,
        }


def utc_now() -> str:
    """Return the current UTC instant in a stable Z-suffixed format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def build_report(
    raw: bytes,
    findings: list[Finding],
    *,
    source: str = "",
    generated_at: str | None = None,
) -> dict:
    """Build the disposition report without mutating the audit findings."""
    advisory = [
        finding
        for finding in findings
        if finding.category not in BINDING_CATEGORIES
    ]
    records = []
    for finding in advisory:
        disposition, reason = classify(finding)
        records.append(
            {
                **finding.as_dict(),
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
        "source_digest": hashlib.sha256(raw).hexdigest(),
        "binding_categories": sorted(BINDING_CATEGORIES),
        "binding_findings": len(findings) - len(advisory),
        "advisory_findings": len(advisory),
        "counts": counts,
        "dispositions": records,
    }


def write_report(report: dict, path: Path) -> None:
    """Write canonical JSON, creating the parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def classify_file(input_path: Path, output_path: Path) -> dict:
    """Classify an audit file and write the generated disposition report."""
    raw, findings = load_findings(input_path)
    report = build_report(raw, findings, source=str(input_path))
    write_report(report, output_path)
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
    summary_parser = subparsers.add_parser("summary", help="print counts")
    summary_parser.add_argument(
        "--path", type=Path, default=DEFAULT_OUTPUT, help="report JSON path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested command."""
    args = parse_args(argv)
    try:
        if args.command == "classify":
            report = classify_file(args.input, args.output)
        else:
            report = json.loads(args.path.read_text(encoding="utf-8"))
        summarize(report)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
