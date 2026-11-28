"""Frozen synthetic golden payloads for the FYERS and Upstox adapters.

The payloads under ``reference-data/provider-golden`` are hand-constructed
from documented provider schemas. They are SYNTHETIC and are NOT REAL
PROVIDER DATA: no live endpoint was contacted and no captured record is
reproduced. Live qualification remains PENDING for both adapters.

The adapters are loaded as pure functions through an explicit importlib
loader. The loader registers each module in ``sys.modules`` before
``exec_module`` so dataclass annotation resolution also works on Python 3.13.
Every check below is offline; no socket is opened at import time.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import socket
import sys
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "reference-data" / "provider-golden"
PROVIDERS = ("fyers", "upstox")


def _load(name: str, filename: str):
    """Load one adapter from its absolute path under a private module name."""
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "python" / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fyers = _load("quant_golden_fyers_socket", "fyers_socket.py")
upstox = _load("quant_golden_upstox_v3", "upstox_v3.py")

VALIDATE_MESSAGE = vars(fyers)["_validate_message"]
STRICT_JSON_LOADS = vars(upstox)["_strict_json_loads"]
EXTRACT_CANDLES = vars(upstox)["_extract_candles"]
COLLECT_RECORDS = vars(upstox)["_collect_records"]

CONFIG = upstox.FetchConfig()
TIMEZONE = ZoneInfo("Asia/Kolkata")
BATCH = upstox.DateRange("2024-01-02", "2024-01-04")


def _manifest(provider: str) -> dict[str, object]:
    """Read one provider manifest from the frozen golden tree."""
    path = GOLDEN / provider / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _entry(provider: str, file_name: str) -> dict[str, object]:
    """Return the single manifest entry for one frozen payload."""
    entries = _manifest(provider)["payloads"]
    assert isinstance(entries, list)
    matches = [entry for entry in entries if entry["file"] == file_name]
    assert len(matches) == 1
    return matches[0]


def _raw(provider: str, file_name: str) -> bytes:
    """Read the exact bytes of one frozen payload."""
    return (GOLDEN / provider / file_name).read_bytes()


def _collect(raw: bytes) -> tuple[list[object], list[str], list[object]]:
    """Replay one raw response through the adapter's pure validator."""
    response = upstox.HttpResponse(200, raw, {})
    return COLLECT_RECORDS(((BATCH, response),), CONFIG, TIMEZONE)


def test_dynamic_load_registers_module_before_exec() -> None:
    """The loader registers sys.modules first and keeps dataclasses usable."""
    source = inspect.getsource(_load)
    registration = source.index("sys.modules[")
    execution = source.index(".exec_module(")
    assert registration < execution
    for module_name, module in (
        ("quant_golden_fyers_socket", fyers),
        ("quant_golden_upstox_v3", upstox),
    ):
        assert sys.modules[module_name] is module
    for cls in (
        fyers.CanonicalEvent,
        fyers.Subscription,
        upstox.DailyCandle,
        upstox.FetchOutcome,
    ):
        assert cls.__module__ in sys.modules
        assert get_type_hints(cls)


def test_importing_adapters_does_no_network_io() -> None:
    """A fresh adapter import must never open a connection."""
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("network")
        raise AssertionError("network call during import")

    first = "quant_golden_fyers_socket_import"
    second = "quant_golden_upstox_v3_import"
    try:
        with (
            patch.object(socket, "create_connection", forbidden),
            patch.object(socket.socket, "connect", forbidden),
            patch.object(socket.socket, "connect_ex", forbidden),
        ):
            imported_fyers = _load(first, "fyers_socket.py")
            imported_upstox = _load(second, "upstox_v3.py")
        assert not calls
        assert sys.modules[first] is imported_fyers
        assert sys.modules[second] is imported_upstox
    finally:
        sys.modules.pop(first, None)
        sys.modules.pop(second, None)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_manifest_labels_and_source_digests(provider: str) -> None:
    """Every frozen payload is labelled, canonical and digest-verified."""
    manifest = _manifest(provider)
    assert manifest["schema_version"] == 1
    assert manifest["provenance"] == "synthetic-documented-schema"
    assert manifest["disclaimer"] == "NOT REAL PROVIDER DATA"
    assert manifest["label"] == "SYNTHETIC GOLDEN PAYLOADS"
    assert manifest["digest_algorithm"] == "sha256"
    entries = manifest["payloads"]
    assert isinstance(entries, list)
    names = [entry["file"] for entry in entries]
    assert names == sorted(names)
    assert len(names) == len(set(names))
    on_disk = sorted(
        path.name
        for path in (GOLDEN / provider).glob("*.json")
        if path.name != "manifest.json"
    )
    assert names == on_disk
    for entry in entries:
        assert entry["label"] == "SYNTHETIC"
        raw = _raw(provider, entry["file"])
        expected = hashlib.sha256(raw).hexdigest()
        assert entry["source_digest"] == expected
        canonical = (
            json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        assert raw == canonical


@pytest.mark.parametrize(
    ("file_name", "message_type", "status"),
    [
        ("cn.json", "cn", fyers.VALID),
        ("dp.json", "dp", fyers.VALID),
        ("error.json", "error", fyers.ERROR),
        ("if.json", "if", fyers.VALID),
        ("sf.json", "sf", fyers.VALID),
        ("sub.json", "sub", fyers.VALID),
    ],
)
def test_fyers_golden_frames_parse(
    file_name: str, message_type: str, status: str
) -> None:
    """Documented FYERS control, quote, depth and error frames parse."""
    entry = _entry("fyers", file_name)
    assert entry["expectation"] == "accept"
    assert entry["message_type"] == message_type
    assert entry["status"] == status
    message = VALIDATE_MESSAGE(_raw("fyers", file_name), None)
    assert message.message_type == message_type
    assert message.status == status
    assert message.fields
    assert message.market is (message_type in fyers.MARKET_TYPES)


@pytest.mark.parametrize(
    ("file_name", "reason"),
    [
        (
            "malformed_sf_bad_timestamp.json",
            "invalid integer representation",
        ),
        (
            "malformed_sf_fractional_volume.json",
            "value is not an exact integer",
        ),
        (
            "malformed_unknown_type.json",
            "unsupported or missing message type",
        ),
    ],
)
def test_fyers_malformed_golden_frames_reject(
    file_name: str, reason: str
) -> None:
    """Bad FYERS frames fail validation with the recorded reason."""
    entry = _entry("fyers", file_name)
    assert entry["expectation"] == "reject"
    assert entry["reason"] == reason
    with pytest.raises(fyers.ValidationError, match=reason):
        VALIDATE_MESSAGE(_raw("fyers", file_name), None)


def test_upstox_daily_golden_response_accepts_seven_fields() -> None:
    """The synthetic V3 daily response yields exact 7-field candles."""
    raw = _raw("upstox", "daily.json")
    entry = _entry("upstox", "daily.json")
    assert entry["expectation"] == "accept"
    payload = STRICT_JSON_LOADS(raw.decode("utf-8"))
    candles, error = EXTRACT_CANDLES(payload)
    assert not error
    assert len(candles) == entry["row_count"] == 2
    assert all(len(row) == 7 for row in candles)
    records, sessions, rejections = _collect(raw)
    assert not rejections
    assert sessions == ["2024-01-02", "2024-01-03"]
    assert [record.session for record in records] == sessions
    assert records[0].volume == 1000
    assert records[0].open_interest == -1
    assert records[1].volume == 2500
    assert records[1].open_interest == 1234
    volume, validity = upstox.classify_volume(candles[0][5])
    assert volume == 1000
    assert validity is upstox.VolumeValidity.VALID
    oi, detail = upstox.classify_open_interest(candles[0][6])
    assert (oi, detail) == (-1, "")


@pytest.mark.parametrize(
    ("file_name", "reason", "detail"),
    [
        ("malformed_bad_timestamp.json", "invalid timestamp", ""),
        ("malformed_fractional_volume.json", "invalid volume", "INVALID"),
        ("malformed_oi_overflow.json", "invalid open interest", ""),
        ("malformed_oi_sentinel.json", "invalid open interest", ""),
    ],
)
def test_upstox_malformed_golden_rows_reject(
    file_name: str, reason: str, detail: str
) -> None:
    """Bad V3 rows are rejected explicitly, never coerced or dropped."""
    entry = _entry("upstox", file_name)
    assert entry["expectation"] == "reject"
    assert entry["reason"] == reason
    assert entry["row_count"] == 0
    records, _sessions, rejections = _collect(_raw("upstox", file_name))
    assert not records
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.reason == reason
    assert rejection.detail == detail
    assert rejection.row_index == 0


def test_upstox_volume_and_oi_sentinels_are_exact() -> None:
    """Fractional volume and out-of-range OI never become valid values."""
    assert upstox.classify_volume(1000.5) == (
        None,
        upstox.VolumeValidity.INVALID,
    )
    assert upstox.classify_volume(-1)[1] is upstox.VolumeValidity.INVALID
    assert upstox.classify_open_interest(-1) == (-1, "")
    assert upstox.classify_open_interest(-2)[0] is None
    assert upstox.classify_open_interest(2**63)[0] is None
