"""Offline contract tests for the Upstox V3 daily adapter.

Every HTTP response below is supplied by an in-memory fake client; no test
opens a socket. Live provider qualification remains PENDING: these tests
verify only the adapter contract against mocked official-shape responses.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_upstox_v3", ROOT / "python" / "upstox_v3.py"
)
assert SPEC is not None and SPEC.loader is not None
upstox = importlib.util.module_from_spec(SPEC)
sys.modules["quant_upstox_v3"] = upstox
SPEC.loader.exec_module(upstox)

CANONICAL_ID = "INE002A01018"
TOKEN = "unit-test-token"


class FakeClient:  # pylint: disable=too-few-public-methods
    """Deterministic in-memory HTTP client that never opens a socket."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def get(self, url: str, headers: dict[str, str]) -> object:
        """Return the next queued response and record the request."""
        self.calls.append(url)
        self.headers.append(dict(headers))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


def _resolve(canonical_id: str) -> str:
    """Map a canonical identity to a syntactically valid Upstox key."""
    return f"NSE_EQ|{canonical_id}"


def _row(
    stamp: str = "2024-01-02T00:00:00+05:30",
    opening: object = 100,
    high: object = 110,
    low: object = 95,
    close: object = 105,
    volume: object = 1000,
    oi: object = -1,
) -> list[object]:
    """Build one seven-field V3 candle."""
    return [stamp, opening, high, low, close, volume, oi]


def _success(rows: list[object]) -> object:
    """Build a successful official-shape V3 response."""
    body = json.dumps(
        {"status": "success", "data": {"candles": rows}}, sort_keys=True
    ).encode("utf-8")
    return upstox.HttpResponse(200, body, {})


def _fetch(
    rows: list[object] | None = None,
    *,
    client: FakeClient | None = None,
    config: object = None,
    start: str = "2024-01-02",
    end: str = "2024-01-02",
) -> object:
    """Run one opted-in fetch against a fake client."""
    if client is None:
        client = FakeClient([_success(rows if rows is not None else [_row()])])
    return upstox.fetch_daily(
        CANONICAL_ID,
        start,
        end,
        token=TOKEN,
        http_client=client,
        resolve_instrument_key=_resolve,
        config=config,
        enable_network=True,
    )


def test_module_declares_pending_live_qualification() -> None:
    """The adapter must never imply that a real provider was qualified."""
    assert "PENDING" in upstox.__doc__
    assert upstox.DEFAULT_BASE_URL == "https://api.upstox.com/v3"


def test_fetch_requires_explicit_network_opt_in() -> None:
    """The default enable_network=False refuses to run any request."""
    client = FakeClient([])
    with pytest.raises(upstox.NetworkDisabledError):
        upstox.fetch_daily(
            CANONICAL_ID,
            "2024-01-02",
            "2024-01-02",
            token=TOKEN,
            http_client=client,
            resolve_instrument_key=_resolve,
        )
    assert not client.calls


def test_valid_seven_field_candle_is_accepted() -> None:
    """A canonical seven-field candle validates with a raw SHA-256 digest."""
    response = _success([_row()])
    outcome = _fetch(client=FakeClient([response]))
    assert outcome.status == "OK"
    assert outcome.rejections == ()
    assert len(outcome.records) == 1
    candle = outcome.records[0]
    assert candle.session == "2024-01-02"
    assert (candle.open, candle.high, candle.low, candle.close) == (
        100.0,
        110.0,
        95.0,
        105.0,
    )
    assert candle.volume == 1000
    assert candle.volume_validity is upstox.VolumeValidity.VALID
    assert candle.open_interest == -1
    assert outcome.raw_digests == (upstox.sha256_hex(response.body),)


@pytest.mark.parametrize(
    "stamp",
    [
        "2024-01-02T00:00:00",
        "2024-01-02T00:00:00+05:00",
        "2024-01-02T10:30:00+05:30",
        "not-a-timestamp",
    ],
)
def test_invalid_timestamp_is_rejected(stamp: str) -> None:
    """Naive, wrong-offset, non-midnight and malformed stamps fail closed."""
    outcome = _fetch([_row(stamp=stamp)])
    assert outcome.status == "REJECTED"
    assert outcome.records == ()
    assert outcome.rejections[0].reason in {
        "invalid timestamp",
        "naive timestamp",
        "timestamp offset mismatch",
        "non-midnight timestamp",
    }


def test_fractional_and_missing_volume_are_rejected() -> None:
    """Volume validity rejects fractional and absent counts explicitly."""
    fractional = _fetch([_row(volume=12.5)])
    assert fractional.status == "REJECTED"
    assert fractional.rejections[0].reason == "invalid volume"
    assert fractional.rejections[0].detail == "INVALID"

    missing = _fetch([_row(volume=None)])
    assert missing.status == "REJECTED"
    assert missing.rejections[0].reason == "volume missing"
    assert missing.rejections[0].detail == "MISSING"


def test_open_interest_sentinel_contract() -> None:
    """OI accepts -1, zero and large exact ints below 2**63 only."""
    for value in (-1, 0, 2**62 + 1):
        outcome = _fetch([_row(oi=value)])
        assert outcome.status == "OK"
        assert outcome.rejections == ()
        assert outcome.records[0].open_interest == value

    for value in (-2, 2**63, 1.5, float(2**53)):
        outcome = _fetch([_row(oi=value)])
        assert outcome.status == "REJECTED"
        assert outcome.rejections[0].reason == "invalid open interest"
        assert outcome.records == ()


def test_duplicate_date_is_rejected() -> None:
    """Two candles on one session never yield a partial accepted series."""
    outcome = _fetch([_row(), _row(volume=2000)])
    assert outcome.status == "REJECTED"
    assert outcome.records == ()
    assert [item.reason for item in outcome.rejections] == ["duplicate date"]


def test_out_of_order_rows_are_sorted_deterministically() -> None:
    """Descending provider rows are normalised to ascending sessions."""
    rows = [
        _row(stamp="2024-01-04T00:00:00+05:30"),
        _row(stamp="2024-01-02T00:00:00+05:30"),
        _row(stamp="2024-01-03T00:00:00+05:30"),
    ]
    first = _fetch(rows, start="2024-01-02", end="2024-01-04")
    second = _fetch(rows, start="2024-01-02", end="2024-01-04")
    assert first == second
    assert [row.session for row in first.records] == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]
    assert first.status == "OK"


def test_missing_session_gap_is_detected() -> None:
    """An injected expected calendar detects absent sessions explicitly."""
    config = upstox.FetchConfig(expected_sessions=["2024-01-02", "2024-01-03"])
    outcome = _fetch(config=config, end="2024-01-03")
    assert outcome.status == "REJECTED"
    assert outcome.missing_dates == ("2024-01-03",)
    assert outcome.rejections[0].reason == "missing sessions"


def test_rate_limit_error_is_classified() -> None:
    """HTTP 429 and provider rate-limit codes are distinct outcomes."""
    limited = _fetch(
        client=FakeClient([upstox.HttpResponse(429, b"", {"Retry-After": "7"})])
    )
    assert limited.status == "REJECTED"
    assert limited.rejections[0].reason == "rate limited"
    assert limited.rejections[0].failure_class is upstox.FailureClass.RATE_LIMIT
    assert limited.batches[0].retry_after == 7

    body = json.dumps(
        {
            "status": "error",
            "errors": {
                "error_code": "UDAPI100026",
                "message": "Too many requests",
            },
        }
    ).encode("utf-8")
    coded = _fetch(client=FakeClient([upstox.HttpResponse(200, body, {})]))
    assert coded.rejections[0].failure_class is upstox.FailureClass.RATE_LIMIT
    assert coded.status == "REJECTED"


def test_batching_and_authenticated_url_contract() -> None:
    """Ranges batch inclusively and use the official V3 URL shape."""
    stamps = [
        "2024-01-01T00:00:00+05:30",
        "2024-01-03T00:00:00+05:30",
        "2024-01-05T00:00:00+05:30",
    ]
    client = FakeClient([_success([_row(stamp=stamp)]) for stamp in stamps])
    config = upstox.FetchConfig(max_days_per_request=2)
    outcome = _fetch(
        client=client, config=config, start="2024-01-01", end="2024-01-05"
    )
    assert outcome.status == "OK"
    assert [batch.start for batch in outcome.batches] == [
        "2024-01-01",
        "2024-01-03",
        "2024-01-05",
    ]
    assert [batch.end for batch in outcome.batches] == [
        "2024-01-02",
        "2024-01-04",
        "2024-01-05",
    ]
    assert client.calls[0] == (
        "https://api.upstox.com/v3/historical-candle/"
        "NSE_EQ%7CINE002A01018/day/2024-01-02/2024-01-01"
    )
    assert client.headers[0]["Authorization"] == f"Bearer {TOKEN}"
    assert client.headers[0]["Accept"] == "application/json"


def test_raw_persistence_and_replay_parity(tmp_path: Path) -> None:
    """Replay of verified raw bytes reproduces the fetch outcome exactly."""
    config = upstox.FetchConfig(
        raw_dir=tmp_path, expected_sessions=["2024-01-02"]
    )
    response = _success([_row()])
    client = FakeClient([response])
    fetched = _fetch(client=client, config=config)
    replayed = upstox.replay_daily(tmp_path, canonical_id=CANONICAL_ID)
    assert fetched == replayed
    assert fetched.status == "OK"

    raw_path = tmp_path / fetched.batches[0].file_name
    assert raw_path.read_bytes() == response.body
    assert upstox.sha256_hex(raw_path.read_bytes()) == fetched.raw_digests[0]

    stem = f"{CANONICAL_ID}_{upstox.sha256_hex(CANONICAL_ID.encode())[:16]}"
    manifest_path = tmp_path / f"{stem}.manifest.json"
    assert manifest_path.exists()
    assert TOKEN not in manifest_path.read_text()
    for path in tmp_path.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_replay_detects_raw_mutation(tmp_path: Path) -> None:
    """A changed raw payload cannot be replayed as the same response."""
    config = upstox.FetchConfig(raw_dir=tmp_path)
    _fetch(config=config)
    raw_path = (
        tmp_path
        / upstox.replay_daily(tmp_path, canonical_id=CANONICAL_ID)
        .batches[0]
        .file_name
    )
    raw_path.write_bytes(b"tampered")
    with pytest.raises(upstox.ReplayError):
        upstox.replay_daily(tmp_path, canonical_id=CANONICAL_ID)


def test_replay_rejects_unsafe_manifest_filename(tmp_path: Path) -> None:
    """A manifest cannot point replay at a file outside the raw directory."""
    _fetch(config=upstox.FetchConfig(raw_dir=tmp_path))
    manifest_path = next(tmp_path.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["batches"][0]["file_name"] = "/etc/passwd"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(upstox.ReplayError):
        upstox.replay_daily(tmp_path, canonical_id=CANONICAL_ID)


def test_replay_requires_callable_calendar_override(tmp_path: Path) -> None:
    """A callable calendar must be supplied again for an exact replay."""
    config = upstox.FetchConfig(
        raw_dir=tmp_path, expected_sessions=lambda start, _end: [start]
    )
    _fetch(config=config)
    with pytest.raises(upstox.ReplayError):
        upstox.replay_daily(tmp_path, canonical_id=CANONICAL_ID)


def test_config_validation_is_fail_closed() -> None:
    """Invalid dates, ranges, URLs, intervals and zones raise up front."""
    bad_dates = [("2024-1-02", "2024-01-02"), ("2024-01-02", "2024-01-01")]
    for start, end in bad_dates:
        with pytest.raises(upstox.ConfigError):
            _fetch(start=start, end=end)

    bad_configs = [
        upstox.FetchConfig(base_url="http://api.upstox.com/v3"),
        upstox.FetchConfig(base_url="https://u:p@api.upstox.com/v3"),
        upstox.FetchConfig(interval="minute"),
        upstox.FetchConfig(exchange_timezone="Mars/Olympus"),
        upstox.FetchConfig(max_days_per_request=0),
    ]
    for config in bad_configs:
        with pytest.raises(upstox.ConfigError):
            _fetch(config=config)


def test_instrument_key_resolution_is_fail_closed() -> None:
    """A malformed canonical mapping is rejected before any request."""
    client = FakeClient([])
    with pytest.raises(upstox.InstrumentResolutionError):
        upstox.fetch_daily(
            CANONICAL_ID,
            "2024-01-02",
            "2024-01-02",
            token=TOKEN,
            http_client=client,
            resolve_instrument_key=lambda _: "not-a-provider-key",
            enable_network=True,
        )


def test_transport_and_payload_errors_are_classified() -> None:
    """Non-rate failures are classified without silently accepting rows."""
    server_error = _fetch(
        client=FakeClient([upstox.HttpResponse(500, b"", {})])
    )
    assert server_error.rejections[0].failure_class is (
        upstox.FailureClass.HTTP_ERROR
    )

    broken = FakeClient([upstox.HttpResponse(200, b'{"status":"success"', {})])
    malformed = _fetch(client=broken)
    assert malformed.rejections[0].failure_class is (
        upstox.FailureClass.MALFORMED_RESPONSE
    )

    duplicate_keys = FakeClient(
        [
            upstox.HttpResponse(
                200,
                b'{"status":"success","status":"error","data":{}}',
                {},
            )
        ]
    )
    duplicate = _fetch(client=duplicate_keys)
    assert duplicate.rejections[0].failure_class is (
        upstox.FailureClass.MALFORMED_RESPONSE
    )
