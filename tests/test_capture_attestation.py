"""Capture attestation tests using only observed response metadata."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fetcher_test_support import StubResponse
from test_loader_support import load_test_module

ROOT = Path(__file__).resolve().parent.parent
fetcher = load_test_module(
    "quant_fetcher_attestation",
    ROOT / "scripts" / "fetch_instrument_snapshots.py",
)
PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
REFERENCE = ROOT / "reference-data" / "instrument-master"


@pytest.fixture(scope="session", autouse=True)
def _block_network() -> None:
    """Make any accidental network call in an ordinary test fail loudly."""

    def forbidden(*args, **kwargs) -> None:
        raise AssertionError("tests must never access the network")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fetcher.urllib.request, "urlopen", forbidden)
        yield


def test_download_with_metadata_captures_only_observed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata comes from the injected response, with no network access."""
    body = b'{"ok":true}'
    response = StubResponse("application/json", body)
    response.headers["ETag"] = '"etag-1"'
    response.headers["Last-Modified"] = "Thu, 26 Nov 2026 23:59:38 GMT"
    calls: list[tuple[str, int]] = []

    def open_stub(url: str, timeout: int) -> StubResponse:
        """Record the exact request and return the prepared response."""
        calls.append((url, timeout))
        return response

    monkeypatch.setattr(
        fetcher.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=open_stub),
    )
    meta = fetcher.load_pins(PROVENANCE)[0]
    url = f"{meta.url}?limit=1"
    payload, metadata = fetcher.download_with_metadata(url, 100, ".json")
    assert payload == body
    assert calls == [(url, fetcher.TIMEOUT_SECONDS)]
    assert metadata["source_url"] == url
    assert metadata["request_parameters"] == "limit=1"
    assert metadata["http_status"] == 200
    assert metadata["content_type"] == "application/json"
    assert metadata["content_length"] == len(body)
    assert metadata["etag"] == '"etag-1"'
    assert metadata["last_modified"] == "Thu, 26 Nov 2026 23:59:38 GMT"
    assert metadata["capture_timestamp_utc"].endswith("Z")

    bare = StubResponse("application/json", body)
    monkeypatch.setattr(
        fetcher.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=lambda url, timeout: bare),
    )
    _, minimal = fetcher.download_with_metadata(meta.url, 100, ".json")
    unknown = "UNKNOWN"
    assert minimal["request_parameters"] == unknown
    assert minimal["etag"] == unknown
    assert minimal["last_modified"] == unknown


def test_evidence_report_states_authenticity_with_relative_paths() -> None:
    """The report records digest pins as integrity-only, not origin-proven."""
    report = fetcher.build_evidence_report(
        fetcher.EvidenceSource(REFERENCE, fetcher.SOURCE_REFERENCE, ""),
        PROVENANCE,
    )
    assert report["data_classification"] == (
        "OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT"
    )
    assert report["origin_authenticated"] is False
    assert "not independently authenticated" in report["authenticity_note"]
    assert report["verification_status"] == fetcher.PASS
    assert "/Users/" not in json.dumps(report)
    for entry in report["snapshots"]:
        for kind in ("original", "decoded"):
            assert not Path(entry[kind]["path"]).is_absolute()
            assert not Path(entry[kind]["relative_path"]).is_absolute()
