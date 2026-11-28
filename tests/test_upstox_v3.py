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

# Bound the adapter's internal validators through public aliases so the
# contract tests stay explicit without reaching into protected attributes.
VALIDATE_BASE_URL = vars(upstox)["_validate_base_url"]
VALIDATE_REDIRECT = vars(upstox)["_validate_redirect"]
BUILD_URL = vars(upstox)["_build_url"]
REQUEST_ONCE = vars(upstox)["_request"]


class FakeClient:
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

    def seen(self) -> tuple[str, ...]:
        """Return the recorded URLs for assertions about redirect safety."""
        return tuple(self.calls)


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


def _redirect(location: str) -> object:
    """Build a redirect response carrying one Location header."""
    return upstox.HttpResponse(302, b"", {"Location": location})


def test_official_hosts_and_allowlists_are_explicit() -> None:
    """Production and sandbox hosts are pinned with no wildcard entries."""
    assert upstox.PRODUCTION_HOSTS == frozenset({"api.upstox.com"})
    assert upstox.SANDBOX_HOSTS == frozenset({"api-sandbox.upstox.com"})
    assert not upstox.PRODUCTION_HOSTS & upstox.SANDBOX_HOSTS
    assert upstox.DEFAULT_BASE_URL == "https://api.upstox.com/v3"
    assert upstox.SANDBOX_BASE_URL == "https://api-sandbox.upstox.com/v3"
    assert upstox.API_PATH_PREFIX == "/v3"
    assert VALIDATE_BASE_URL(upstox.SANDBOX_BASE_URL) == upstox.SANDBOX_BASE_URL


def test_evil_base_url_is_rejected_before_any_request() -> None:
    """A proxy host must never receive the bearer token."""
    with pytest.raises(upstox.ConfigError, match="allowlisted"):
        VALIDATE_BASE_URL("https://evil.example/proxy")
    client = FakeClient([])
    with pytest.raises(upstox.ConfigError):
        upstox.fetch_daily(
            CANONICAL_ID,
            "2024-01-02",
            "2024-01-02",
            token=TOKEN,
            http_client=client,
            resolve_instrument_key=_resolve,
            config=upstox.FetchConfig(base_url="https://evil.example/proxy"),
            enable_network=True,
        )
    assert not client.seen()


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://api.upstox.com/v3", "non-HTTPS"),
        ("https://u:p@api.upstox.com/v3", "credentials"),
        ("https://api.upstox.com:8443/v3", "port"),
        ("https://api.upstox.com/v3?x=1", "form"),
        ("https://api.upstox.com/v3#fragment", "form"),
        ("https://api.upstox.com/v3/../evil", "unsafe"),
        ("https://api.upstox.com/v3/%2e%2e/evil", "unsafe"),
        ("https://api.upstox.com/v2", "prefix"),
        ("https://api.upstox.com/v3/extra", "prefix"),
        ("https://api.upstox.com.evil.example/v3", "allowlisted"),
        ("https://api.upstox.com@evil.example/v3", "allowlisted"),
        ("https://sub.api.upstox.com/v3", "allowlisted"),
    ],
)
def test_unsafe_base_url_forms_are_rejected(url: str, message: str) -> None:
    """Non-HTTPS, credentials, traversal, ports and wrong paths fail closed."""
    with pytest.raises(upstox.ConfigError, match=message):
        VALIDATE_BASE_URL(url)


def test_environment_allowlists_are_separate() -> None:
    """A sandbox host is never accepted for a production fetch."""
    assert (
        VALIDATE_BASE_URL(upstox.SANDBOX_BASE_URL, "sandbox")
        == upstox.SANDBOX_BASE_URL
    )
    with pytest.raises(upstox.ConfigError, match="allowlisted"):
        VALIDATE_BASE_URL(upstox.SANDBOX_BASE_URL, "production")
    with pytest.raises(upstox.ConfigError, match="allowlisted"):
        VALIDATE_BASE_URL(upstox.PRODUCTION_BASE_URL, "sandbox")
    with pytest.raises(upstox.ConfigError):
        _fetch(config=upstox.FetchConfig(base_url=upstox.SANDBOX_BASE_URL))


def _request_url() -> str:
    """Return the validated URL used by the short single-date tests."""
    return BUILD_URL(
        upstox.FetchConfig(),
        "NSE_EQ|INE002A01018",
        upstox.DateRange("2024-01-02", "2024-01-02"),
    )


def test_cross_host_redirect_is_rejected_before_a_second_request() -> None:
    """A redirect to an unrelated host never receives the token."""
    evil = (
        "https://evil.example/v3/historical-candle/x/day/2024-01-02/2024-01-02"
    )
    client = FakeClient([_redirect(evil)])
    outcome = _fetch(client=client)
    assert outcome.status == "REJECTED"
    assert outcome.rejections[0].failure_class is (
        upstox.FailureClass.TRANSPORT_ERROR
    )
    assert len(client.seen()) == 1
    assert TOKEN not in repr(outcome)
    with pytest.raises(upstox.TransportError, match="disallowed redirect"):
        VALIDATE_REDIRECT(evil, _request_url(), upstox.PRODUCTION_HOSTS)


@pytest.mark.parametrize(
    "location",
    [
        "http://api.upstox.com/v3/historical-candle/x",
        "https://api.upstox.com:8443/v3/historical-candle/x",
        "https://u:p@api.upstox.com/v3/historical-candle/x",
        "https://api.upstox.com/v3/../evil",
        "https://api.upstox.com/v2/historical-candle/x",
        "https://api-sandbox.upstox.com/v3/historical-candle/x",
        "https://evil.example/v3/historical-candle/x",
        "//evil.example/v3/historical-candle/x",
    ],
)
def test_unsafe_redirect_targets_are_rejected(location: str) -> None:
    """Redirects may not leave HTTPS, the pinned host or the V3 prefix."""
    with pytest.raises(upstox.ConfigError, match="disallowed redirect"):
        VALIDATE_REDIRECT(location, _request_url(), upstox.PRODUCTION_HOSTS)


@pytest.mark.parametrize(
    "location",
    [
        _request_url(),
        "/v3/historical-candle/NSE_EQ%7CINE002A01018/day/2024-01-02/2024-01-02",
    ],
)
def test_same_host_redirect_is_followed(location: str) -> None:
    """An allowlisted same-host HTTPS redirect is followed with the token."""
    client = FakeClient([_redirect(location), _success([_row()])])
    outcome = _fetch(client=client)
    assert outcome.status == "OK"
    assert outcome.records[0].session == "2024-01-02"
    assert len(client.seen()) == 2
    assert all(
        url.startswith("https://api.upstox.com/v3/historical-candle/")
        for url in client.seen()
    )
    assert client.headers[1]["Authorization"] == f"Bearer {TOKEN}"


def test_redirect_chain_is_bounded() -> None:
    """An endless redirect loop stops after the configured maximum."""
    same = _request_url()
    client = FakeClient(
        [_redirect(same) for _ in range(upstox.MAX_REDIRECTS + 1)]
    )
    outcome = _fetch(client=client)
    assert outcome.status == "REJECTED"
    assert outcome.rejections[0].failure_class is (
        upstox.FailureClass.TRANSPORT_ERROR
    )
    assert len(client.seen()) == upstox.MAX_REDIRECTS + 1
    looping = FakeClient(
        [_redirect(same) for _ in range(upstox.MAX_REDIRECTS + 1)]
    )
    with pytest.raises(upstox.TransportError, match="too many redirects"):
        REQUEST_ONCE(looping, _request_url(), TOKEN)


def test_oversize_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversize bodies and declared lengths fail before parsing."""
    monkeypatch.setattr(upstox, "MAX_RESPONSE_BYTES", 64)
    client = FakeClient([upstox.HttpResponse(200, b"x" * 65, {})])
    outcome = _fetch(client=client)
    assert outcome.status == "REJECTED"
    assert "exceeds 64 bytes" in outcome.rejections[0].detail
    assert TOKEN not in repr(outcome)

    declared = FakeClient(
        [upstox.HttpResponse(200, b"", {"Content-Length": "65"})]
    )
    assert _fetch(client=declared).rejections[0].failure_class is (
        upstox.FailureClass.TRANSPORT_ERROR
    )


def test_streaming_response_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A streaming client cannot supply unbounded bytes."""

    class EndlessStream:
        """Minimal stream that produces bytes without ever ending."""

        status = 200
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self.reads = 0

        def read(self, size: int) -> bytes:
            """Return one full chunk on every call."""
            self.reads += 1
            return b"y" * size

        def count(self) -> int:
            """Expose the number of reads performed by the adapter."""
            return self.reads

    monkeypatch.setattr(upstox, "MAX_RESPONSE_BYTES", 64)
    stream = EndlessStream()
    client = FakeClient([stream])
    with pytest.raises(upstox.TransportError, match="exceeds 64 bytes"):
        REQUEST_ONCE(client, _request_url(), TOKEN)
    assert stream.count() >= 1


def test_timeout_and_client_exceptions_are_sanitised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timeout and arbitrary client failures never expose the token."""

    class ExplodingClient:
        """Injected client that raises a token-bearing exception."""

        def __init__(self, error: Exception) -> None:
            self.error = error
            self.calls: list[str] = []

        def get(self, url: str, _headers: dict[str, str]) -> object:
            """Record the attempt, then fail with the configured exception."""
            self.calls.append(url)
            raise self.error

        def seen(self) -> tuple[str, ...]:
            """Return the attempted URLs."""
            return tuple(self.calls)

    for error in (
        TimeoutError(f"timed out carrying Bearer {TOKEN}"),
        RuntimeError(f"Authorization header was Bearer {TOKEN}"),
    ):
        client = ExplodingClient(error)
        outcome = _fetch(client=client)
        assert outcome.status == "REJECTED"
        rejection = outcome.rejections[0]
        assert rejection.failure_class is upstox.FailureClass.TRANSPORT_ERROR
        expected = (
            "request timed out"
            if isinstance(error, TimeoutError)
            else "transport failure"
        )
        assert rejection.detail == expected
        assert TOKEN not in repr(outcome)
        assert TOKEN not in caplog.text
        with pytest.raises(upstox.TransportError) as caught:
            REQUEST_ONCE(client, _request_url(), TOKEN)
        assert caught.value.__cause__ is None
        assert TOKEN not in str(caught.value)
        if isinstance(error, TimeoutError):
            assert "timed out" in str(caught.value)
        assert len(client.seen()) == 2


def test_bounded_timeout_is_passed_to_supporting_clients() -> None:
    """Injected clients receive a positive timeout within the hard bound."""

    class TimeoutClient:
        """Client that records the timeout it was given."""

        def __init__(self) -> None:
            self.timeouts: list[float] = []
            self.calls: list[str] = []

        def get(
            self, url: str, _headers: dict[str, str], timeout: float = 0
        ) -> object:
            """Return a valid response and record the timeout."""
            self.calls.append(url)
            self.timeouts.append(timeout)
            return _success([_row()])

        def seen(self) -> tuple[str, ...]:
            """Return the attempted URLs."""
            return tuple(self.calls)

    client = TimeoutClient()
    assert _fetch(client=client).status == "OK"
    assert client.timeouts == [upstox.DEFAULT_TIMEOUT_SECONDS]
    assert 0 < client.timeouts[0] <= upstox.MAX_TIMEOUT_SECONDS
    assert len(client.seen()) == 1

    configured = TimeoutClient()
    config = upstox.FetchConfig(timeout_seconds=2.5)
    assert _fetch(client=configured, config=config).status == "OK"
    assert configured.timeouts == [2.5]
    for timeout in (0, -1, upstox.MAX_TIMEOUT_SECONDS + 1):
        with pytest.raises(upstox.ConfigError):
            _fetch(config=upstox.FetchConfig(timeout_seconds=timeout))


def test_rate_limit_retries_are_bounded_and_classified() -> None:
    """Only rate limits are retried, and never beyond the hard bound."""
    limited = upstox.HttpResponse(
        429, b"", {"Retry-After": "1", "content-length": "0"}
    )
    config = upstox.FetchConfig(
        max_rate_limit_retries=upstox.MAX_RATE_LIMIT_RETRIES
    )
    recovered = FakeClient([limited, limited, limited, _success([_row()])])
    assert _fetch(client=recovered, config=config).status == "OK"
    assert len(recovered.seen()) == 4

    exhausted = FakeClient([limited] * (upstox.MAX_RATE_LIMIT_RETRIES + 1))
    outcome = _fetch(client=exhausted, config=config)
    assert outcome.rejections[0].failure_class is (
        upstox.FailureClass.RATE_LIMIT
    )
    assert len(exhausted.seen()) == upstox.MAX_RATE_LIMIT_RETRIES + 1
    with pytest.raises(upstox.ConfigError):
        _fetch(
            config=upstox.FetchConfig(
                max_rate_limit_retries=upstox.MAX_RATE_LIMIT_RETRIES + 1
            )
        )


def test_module_never_imports_network_primitives() -> None:
    """Importing the adapter cannot open a socket or an HTTP connection."""
    source = (ROOT / "python" / "upstox_v3.py").read_text(encoding="utf-8")
    assert "import socket" not in source
    assert "urlopen" not in source
    assert "requests.get" not in source
