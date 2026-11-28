#!/usr/bin/env python3
"""Read-only Git HEAD lookup shared by offline research provenance records."""

from __future__ import annotations

import subprocess
from pathlib import Path


def head_commit(root: Path) -> str:
    """Return the current HEAD commit, or UNKNOWN when unavailable."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
