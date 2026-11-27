"""Tests for the exchange calendar contract using SYNTHETIC fixtures only.

No NSE calendar value is asserted here. Every session below is a small
synthetic record constructed by this test module and labelled as such.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quant_exchange_calendar", ROOT / "python" / "exchange_calendar.py"
)
assert SPEC is not None and SPEC.loader is not None
cal = importlib.util.module_from_spec(SPEC)
sys.modules["quant_exchange_calendar"] = cal
SPEC.loader.exec_module(cal)

SOURCE = "SYNTHETIC-CALENDAR-FIXTURE-NOT-NSE"
CAPTURED = "2024-01-01T00:00:00Z"
EXCHANGE = "SYNTHETIC-EXCHANGE"
SEGMENT = "SYNTHETIC-EQ"


def _session(**overrides: object) -> dict[str, object]:
    """Build one signed SYNTHETIC session payload."""
    data: dict[str, object] = {
        "exchange": EXCHANGE,
        "segment": SEGMENT,
        "session_date": "2024-01-02",
        "preopen_start": "2024-01-02T03:45:00Z",
        "preopen_end": "2024-01-02T04:00:00Z",
        "market_start": "2024-01-02T04:00:00Z",
        "market_end": "2024-01-02T08:30:00Z",
        "closing_session": None,
        "auction_sessions": [],
        "special_session": False,
        "early_closure": False,
        "holiday": False,
        "halts": [],
        "source_reference": SOURCE,
        "captured_at": CAPTURED,
    }
    data.update(overrides)
    return cal.session_payload(cal.session_from_mapping(data))


def _document_bytes(sessions: list[dict[str, object]]) -> bytes:
    """Wrap SYNTHETIC sessions in a signed document."""
    data: dict[str, object] = {
        "schema_version": cal.SCHEMA_VERSION,
        "source_reference": SOURCE,
        "captured_at": CAPTURED,
        "sessions": sessions,
    }
    document = cal.document_from_mapping(data)
    data["digest"] = document.digest
    return json.dumps(data, sort_keys=True).encode("utf-8")


def _calendar(**overrides: object) -> cal.CalendarDocument:
    """Parse a one-session SYNTHETIC calendar document."""
    return cal.parse_calendar(_document_bytes([_session(**overrides)]))


def test_synthetic_calendar_loads_with_provenance() -> None:
    """A signed synthetic session is queryable with retained provenance."""
    document = _calendar()
    result = cal.query_session(document, "2024-01-02", EXCHANGE, SEGMENT)
    assert result.found
    assert result.session is not None
    assert result.session.market_start == "2024-01-02T04:00:00Z"
    assert result.provenance is not None
    assert result.provenance.source_reference == SOURCE
    assert result.provenance.session_digest == result.session.digest
    assert result.provenance.document_digest == document.digest
    assert cal.is_trading_session(document, "2024-01-02", EXCHANGE, SEGMENT)


def test_query_fails_closed_for_holiday_missing_and_unobserved() -> None:
    """Negative results keep provenance when the record itself exists."""
    holiday = _calendar(
        holiday=True,
        market_start=None,
        market_end=None,
        preopen_start=None,
        preopen_end=None,
    )
    result = cal.query_session(holiday, "2024-01-02", EXCHANGE, SEGMENT)
    assert not result.found
    assert result.reasons == (cal.SESSION_HOLIDAY,)
    assert result.provenance is not None

    absent = cal.query_session(holiday, "2024-01-03", EXCHANGE, SEGMENT)
    assert not absent.found
    assert absent.reasons == (cal.NO_SESSION,)
    assert absent.provenance is None

    future = cal.query_session(
        _calendar(),
        "2024-01-02",
        EXCHANGE,
        SEGMENT,
        "2023-12-31T23:59:59Z",
    )
    assert not future.found
    assert future.reasons == (cal.CALENDAR_NOT_OBSERVED,)
    assert future.provenance is not None


def test_windows_halts_and_special_session_are_preserved() -> None:
    """Rich synthetic session fields survive load and validation."""
    document = _calendar(
        special_session=True,
        early_closure=True,
        closing_session={
            "label": "SYNTHETIC-CLOSING",
            "start": "2024-01-02T08:30:00Z",
            "end": "2024-01-02T08:45:00Z",
        },
        auction_sessions=[
            {
                "label": "SYNTHETIC-OPENING",
                "start": "2024-01-02T04:00:00Z",
                "end": "2024-01-02T04:05:00Z",
            }
        ],
        halts=[
            {
                "reason": "SYNTHETIC-HALT",
                "start": "2024-01-02T06:00:00Z",
                "end": "2024-01-02T06:15:00Z",
            }
        ],
    )
    session = document.sessions[0]
    assert session.special_session
    assert session.early_closure
    assert session.closing_session is not None
    assert session.auction_sessions[0].label == "SYNTHETIC-OPENING"
    assert session.halts[0].reason == "SYNTHETIC-HALT"


def test_validation_rejects_bad_digest_and_bad_order() -> None:
    """Tampered provenance and inverted windows are rejected."""
    session = _session()
    session["digest"] = "0" * 64
    document = cal.document_from_mapping(
        {
            "schema_version": cal.SCHEMA_VERSION,
            "source_reference": SOURCE,
            "captured_at": CAPTURED,
            "sessions": [session],
        }
    )
    errors = cal.validate_calendar(document)
    assert any("digest mismatch" in item for item in errors)

    inverted = _session(market_start="2024-01-02T09:00:00Z")
    with pytest.raises(ValueError, match="market_start after market_end"):
        cal.parse_calendar(_document_bytes([inverted]))


def test_duplicate_fields_and_unknown_fields_are_rejected() -> None:
    """Strict JSON decoding rejects duplicate and unexpected fields."""
    payload = _document_bytes([_session()]).decode("utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        cal.parse_calendar(
            payload.replace(
                '"captured_at"', '"captured_at": "x", "captured_at"', 1
            ).encode("utf-8")
        )
    with pytest.raises(ValueError, match="unexpected calendar document"):
        cal.parse_calendar(
            json.dumps(
                {
                    **json.loads(payload),
                    "unexpected_synthetic_field": 1,
                }
            ).encode("utf-8")
        )


def test_load_calendar_from_path(tmp_path: Path) -> None:
    """The file loader parses the same signed synthetic document."""
    path = tmp_path / "synthetic-calendar.json"
    path.write_bytes(_document_bytes([_session()]))
    document = cal.load_calendar(path)
    assert document.digest == cal.document_digest(document)
    assert len(document.sessions) == 1
