#!/usr/bin/env python3
"""Versioned primitives shared by the QUANT assurance and release scripts.

An audit finding, the canonical contract binary coordinates, historical record
markers and command-line dispatch are defined once here. Every script that
reads or emits those values imports them instead of restating them, so a gate
cannot disagree with the evidence it validates.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

CANONICAL_BUILD_DIR = "build-ci"
CANONICAL_BINARY_PATH = f"{CANONICAL_BUILD_DIR}/quant_contract"
BINARY_PATH_KEY = "binary_path"
BINARY_DIGEST_KEY = "binary_sha256"
BINARY_BUILD_DIR_KEY = "build_dir"
BINARY_COMMIT_KEY = "binary_commit"

HISTORICAL_MARKERS = (
    "historical",
    "superseded",
    "baseline",
    "snapshot",
    "captured",
    "previous",
    "past",
    "release history",
    "archive",
)


@dataclass(frozen=True)
class Finding:
    """One immutable terminology candidate from a structured audit."""

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


def run_cli(
    handler: Callable[[list[str] | None], int],
    argv: list[str] | None = None,
    errors: tuple[type[Exception], ...] = (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
    ),
) -> int:
    """Dispatch a CLI handler and fail closed with a stderr diagnostic."""
    try:
        return handler(argv)
    except errors as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
