"""Shared offline response stubs for instrument-master fetcher tests."""

from __future__ import annotations

import io
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest


class StubResponse:
    """In-memory response with declared headers and a synthetic body."""

    def __init__(
        self, content_type: str, body: bytes, status: int = 200
    ) -> None:
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        self._body = io.BytesIO(body)

    def read(self, count: int) -> bytes:
        """Return the next body chunk."""
        return self._body.read(count)

    def __enter__(self) -> StubResponse:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def stub_retrieval(
    fetcher: Any,
    real_download: Any,
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    body: bytes,
    status: int = 200,
) -> list[tuple[str, int]]:
    """Serve one synthetic response offline and return observed requests."""
    response = StubResponse(content_type, body, status)
    calls: list[tuple[str, int]] = []

    def open_stub(url: str, timeout: int) -> StubResponse:
        """Record the URL and timeout, then return the prepared response."""
        calls.append((url, timeout))
        return response

    opener = SimpleNamespace(open=open_stub)
    monkeypatch.setattr(
        fetcher.urllib.request, "build_opener", lambda *args: opener
    )
    monkeypatch.setattr(fetcher, "download", real_download)
    return calls
