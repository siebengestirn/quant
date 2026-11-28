"""Session guard: the persistent experiment registry must stay untouched."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REGISTRY = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "assurance"
    / "experiments.jsonl"
)


def _snapshot() -> tuple[bytes | None, str | None]:
    """Read-only snapshot of the registry bytes and digest."""
    if not REGISTRY.is_file():
        return None, None
    content = REGISTRY.read_bytes()
    return content, hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def persistent_experiment_registry_is_immutable():
    """Assert the complete suite leaves the persistent registry unchanged."""
    before, digest_before = _snapshot()
    yield
    after, digest_after = _snapshot()
    assert after == before, "persistent experiment registry bytes changed"
    assert digest_after == digest_before, "registry digest changed"
