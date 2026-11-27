"""Tests for the shared strict JSON duplicate-key hook."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "json_support", ROOT / "python" / "json_support.py"
)
assert SPEC is not None and SPEC.loader is not None
support = importlib.util.module_from_spec(SPEC)
sys.modules["json_support"] = support
SPEC.loader.exec_module(support)


def test_reject_duplicate_keys_reports_the_key() -> None:
    """Nested duplicate keys fail with the same diagnostic everywhere."""
    payload = '{"a": {"b": 1, "b": 2}}'
    with pytest.raises(ValueError, match="duplicate JSON key: b"):
        json.loads(payload, object_pairs_hook=support.reject_duplicate_keys)


def test_reject_duplicate_keys_keeps_unique_objects() -> None:
    """A valid object is returned unchanged."""
    result = json.loads(
        '{"a": 1, "b": [2, 3]}',
        object_pairs_hook=support.reject_duplicate_keys,
    )
    assert result == {"a": 1, "b": [2, 3]}
