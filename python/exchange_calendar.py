"""Versioned, point-in-time exchange calendar contract.

This module is a data *contract*: it contains no NSE calendar values. Every
session, time window, halt and provenance record must be supplied by the
caller, normally from a JSON document captured from a named source. Tests use
small fixtures that are explicitly labelled SYNTHETIC.

A calendar document is immutable after validation. Digests are SHA-256 over a
canonical JSON encoding, so a supplied record can be checked byte-for-byte
against the provenance that accompanied it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
reject_duplicate_keys = importlib.import_module(
    "json_support"
).reject_duplicate_keys

SCHEMA_VERSION = "exchange-calendar-v1"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

NO_SESSION = "NO_SESSION_FOR_DATE"
SESSION_HOLIDAY = "SESSION_IS_HOLIDAY"
CALENDAR_NOT_OBSERVED = "CALENDAR_NOT_OBSERVED_BY_AS_OF"
INVALID_QUERY = "INVALID_CALENDAR_QUERY"

_SESSION_FIELDS = (
    "exchange",
    "segment",
    "session_date",
    "preopen_start",
    "preopen_end",
    "market_start",
    "market_end",
    "closing_session",
    "auction_sessions",
    "special_session",
    "early_closure",
    "holiday",
    "halts",
    "source_reference",
    "captured_at",
    "digest",
)
_DOCUMENT_FIELDS = (
    "schema_version",
    "source_reference",
    "captured_at",
    "digest",
    "sessions",
)


class TimeWindow(NamedTuple):
    """A named interval inside one trading session."""

    label: str
    start: str
    end: str


class Halt(NamedTuple):
    """A trading halt with an explicit reason and interval."""

    reason: str
    start: str
    end: str


class CalendarSession(NamedTuple):
    """One validated session record with its own provenance digest."""

    exchange: str
    segment: str
    session_date: str
    preopen_start: str | None
    preopen_end: str | None
    market_start: str | None
    market_end: str | None
    closing_session: TimeWindow | None
    auction_sessions: tuple[TimeWindow, ...]
    special_session: bool
    early_closure: bool
    holiday: bool
    halts: tuple[Halt, ...]
    source_reference: str
    captured_at: str
    digest: str


class SessionProvenance(NamedTuple):
    """Source and digests attached to a calendar query result."""

    source_reference: str
    captured_at: str
    session_digest: str
    document_digest: str
    schema_version: str


class CalendarQuery(NamedTuple):
    """A date query result; provenance is retained on negative results too."""

    found: bool
    session: CalendarSession | None
    provenance: SessionProvenance | None
    reasons: tuple[str, ...]


class CalendarDocument(NamedTuple):
    """Validated calendar sessions with document-level provenance."""

    schema_version: str
    source_reference: str
    captured_at: str
    digest: str
    sessions: tuple[CalendarSession, ...]


def sha256_hex(payload: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact bytes."""
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: object) -> bytes:
    """Serialize a value deterministically for digest computation."""
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return text.encode("utf-8")


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def parse_market_date(value: object) -> bool:
    """Return True for a real ISO calendar date, never a merely shaped one."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def parse_instant(value: object) -> datetime | None:
    """Parse a strict UTC `YYYY-MM-DDTHH:MM:SSZ` instant, else return None."""
    if not isinstance(value, str) or not _INSTANT_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def _window_payload(window: TimeWindow) -> dict[str, object]:
    """Return the canonical mapping for one time window."""
    return {"label": window.label, "start": window.start, "end": window.end}


def _halt_payload(halt: Halt) -> dict[str, object]:
    """Return the canonical mapping for one halt."""
    return {"reason": halt.reason, "start": halt.start, "end": halt.end}


def session_fields(session: CalendarSession) -> dict[str, object]:
    """Return all signed session fields, excluding the derived digest."""
    closing = (
        _window_payload(session.closing_session)
        if session.closing_session is not None
        else None
    )
    return {
        "exchange": session.exchange,
        "segment": session.segment,
        "session_date": session.session_date,
        "preopen_start": session.preopen_start,
        "preopen_end": session.preopen_end,
        "market_start": session.market_start,
        "market_end": session.market_end,
        "closing_session": closing,
        "auction_sessions": [
            _window_payload(item) for item in session.auction_sessions
        ],
        "special_session": session.special_session,
        "early_closure": session.early_closure,
        "holiday": session.holiday,
        "halts": [_halt_payload(item) for item in session.halts],
        "source_reference": session.source_reference,
        "captured_at": session.captured_at,
    }


def session_payload(session: CalendarSession) -> dict[str, object]:
    """Return the full signed session including its digest."""
    payload = session_fields(session)
    payload["digest"] = session.digest
    return payload


def session_digest(session: CalendarSession) -> str:
    """Compute the digest over the signed fields of one session."""
    return sha256_hex(canonical_json(session_fields(session)))


def session_key(session: CalendarSession) -> tuple[str, str, str]:
    """Return the unique lookup key of a session."""
    return (session.exchange, session.segment, session.session_date)


def document_fields(document: CalendarDocument) -> dict[str, object]:
    """Return all signed document fields, excluding the derived digest."""
    ordered = sorted(document.sessions, key=session_key)
    return {
        "schema_version": document.schema_version,
        "source_reference": document.source_reference,
        "captured_at": document.captured_at,
        "sessions": [session_payload(item) for item in ordered],
    }


def document_digest(document: CalendarDocument) -> str:
    """Compute the digest over the signed fields of a calendar document."""
    return sha256_hex(canonical_json(document_fields(document)))


def _optional_bool(value: object) -> bool:
    """Return a strict boolean flag, defaulting to False for absent values."""
    return value if isinstance(value, bool) else False


def _window_from_mapping(value: object) -> TimeWindow | None:
    """Build a time window from a mapping, or None when absent."""
    if not isinstance(value, Mapping):
        return None
    return TimeWindow(
        _text(value.get("label")),
        _text(value.get("start")),
        _text(value.get("end")),
    )


def _windows_from_mapping(value: object) -> tuple[TimeWindow, ...]:
    """Build a deterministic tuple of time windows from a JSON array."""
    if not isinstance(value, list):
        return ()
    windows = [_window_from_mapping(item) for item in value]
    return tuple(item for item in windows if item is not None)


def _halts_from_mapping(value: object) -> tuple[Halt, ...]:
    """Build a deterministic tuple of halts from a JSON array."""
    if not isinstance(value, list):
        return ()
    halts = []
    for item in value:
        if isinstance(item, Mapping):
            halts.append(
                Halt(
                    _text(item.get("reason")),
                    _text(item.get("start")),
                    _text(item.get("end")),
                )
            )
    return tuple(halts)


def session_from_mapping(data: Mapping[str, object]) -> CalendarSession:
    """Build a session from a mapping, deriving its digest when absent."""
    optional = ("preopen_start", "preopen_end", "market_start", "market_end")
    values = {name: _text(data.get(name)) or None for name in optional}
    supplied = _text(data.get("digest"))
    session = CalendarSession(
        exchange=_text(data.get("exchange")),
        segment=_text(data.get("segment")),
        session_date=_text(data.get("session_date")),
        preopen_start=values["preopen_start"],
        preopen_end=values["preopen_end"],
        market_start=values["market_start"],
        market_end=values["market_end"],
        closing_session=_window_from_mapping(data.get("closing_session")),
        auction_sessions=_windows_from_mapping(data.get("auction_sessions")),
        special_session=_optional_bool(data.get("special_session")),
        early_closure=_optional_bool(data.get("early_closure")),
        holiday=_optional_bool(data.get("holiday")),
        halts=_halts_from_mapping(data.get("halts")),
        source_reference=_text(data.get("source_reference")),
        captured_at=_text(data.get("captured_at")),
        digest=supplied,
    )
    return session._replace(digest=supplied or session_digest(session))


def document_from_mapping(data: Mapping[str, object]) -> CalendarDocument:
    """Build and digest a calendar document from a decoded JSON mapping."""
    sessions_value = data.get("sessions", [])
    sessions = (
        tuple(
            session_from_mapping(item)
            for item in sessions_value
            if isinstance(item, Mapping)
        )
        if isinstance(sessions_value, list)
        else ()
    )
    supplied = _text(data.get("digest"))
    document = CalendarDocument(
        schema_version=_text(data.get("schema_version")),
        source_reference=_text(data.get("source_reference")),
        captured_at=_text(data.get("captured_at")),
        digest=supplied,
        sessions=sessions,
    )
    return document._replace(digest=supplied or document_digest(document))


def _validate_window(
    window: TimeWindow, date: str, label: str, errors: list[str]
) -> None:
    """Check one window's grammar, label and date alignment."""
    if not window.label:
        errors.append(f"{label}: missing label")
    start = parse_instant(window.start)
    end = parse_instant(window.end)
    if start is None or end is None:
        errors.append(f"{label}: invalid instant")
        return
    if start.date().isoformat() != date or end.date().isoformat() != date:
        errors.append(f"{label}: instant outside session date")
    if start > end:
        errors.append(f"{label}: start after end")


def _validate_windows(
    windows: Sequence[TimeWindow], date: str, label: str, errors: list[str]
) -> None:
    """Check named windows for grammar, ordering and unique labels."""
    for index, window in enumerate(windows):
        _validate_window(window, date, f"{label}[{index}]", errors)
    labels = [item.label for item in windows]
    if len(set(labels)) != len(labels):
        errors.append(f"{label}: duplicate labels")
    if [item.start for item in windows] != sorted(
        item.start for item in windows
    ):
        errors.append(f"{label}: windows are not ordered")


def _validate_halts(session: CalendarSession, errors: list[str]) -> None:
    """Check halt intervals against the market window and each other."""
    for index, halt in enumerate(session.halts):
        label = f"halt[{index}]"
        if not halt.reason:
            errors.append(f"{label}: missing reason")
        _validate_window(
            TimeWindow(halt.reason, halt.start, halt.end),
            session.session_date,
            label,
            errors,
        )
    starts = [item.start for item in session.halts]
    ends = [item.end for item in session.halts]
    if starts != sorted(starts) or any(
        end > start for start, end in zip(starts, ends[1:], strict=False)
    ):
        errors.append("halts overlap or are unordered")


def _validate_times(session: CalendarSession, errors: list[str]) -> None:
    """Check that market times are complete, ordered and within the date."""
    fields = (
        "preopen_start",
        "preopen_end",
        "market_start",
        "market_end",
    )
    values = {}
    for name in fields:
        value = getattr(session, name)
        if value is None:
            continue
        moment = parse_instant(value)
        if moment is None:
            errors.append(f"{name}: invalid instant")
            continue
        if moment.date().isoformat() != session.session_date:
            errors.append(f"{name}: instant outside session date")
        values[name] = moment
    if session.holiday:
        activity = bool(
            values
            or session.halts
            or session.auction_sessions
            or session.closing_session
        )
        if activity:
            errors.append("holiday carries session activity")
        return
    if "market_start" not in values or "market_end" not in values:
        errors.append("non-holiday session has no market window")
        return
    if values["market_start"] > values["market_end"]:
        errors.append("market_start after market_end")
    for start_name, end_name in (
        ("preopen_start", "preopen_end"),
        ("preopen_end", "market_start"),
    ):
        if start_name in values and end_name in values:
            if values[start_name] > values[end_name]:
                errors.append(f"{start_name} after {end_name}")


def _validate_session(session: CalendarSession) -> list[str]:
    """Validate one session's grammar, ordering and digest."""
    errors: list[str] = []
    if not session.exchange or not session.segment:
        errors.append("missing exchange or segment")
    if not parse_market_date(session.session_date):
        errors.append("invalid session_date")
    if not session.source_reference:
        errors.append("missing source_reference")
    if parse_instant(session.captured_at) is None:
        errors.append("invalid captured_at")
    if not _DIGEST_RE.fullmatch(session.digest):
        errors.append("invalid session digest")
    elif session_digest(session) != session.digest:
        errors.append("session digest mismatch")
    _validate_times(session, errors)
    if session.closing_session is not None:
        _validate_window(
            session.closing_session,
            session.session_date,
            "closing_session",
            errors,
        )
    _validate_windows(
        session.auction_sessions,
        session.session_date,
        "auction_sessions",
        errors,
    )
    _validate_halts(session, errors)
    return errors


def validate_calendar(document: CalendarDocument) -> list[str]:
    """Return all contract violations for a calendar document."""
    errors: list[str] = []
    if document.schema_version != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if not document.source_reference:
        errors.append("missing document source_reference")
    if parse_instant(document.captured_at) is None:
        errors.append("invalid document captured_at")
    if not _DIGEST_RE.fullmatch(document.digest):
        errors.append("invalid document digest")
    elif document_digest(document) != document.digest:
        errors.append("document digest mismatch")
    keys = [session_key(item) for item in document.sessions]
    if len(set(keys)) != len(keys):
        errors.append("duplicate session keys")
    for index, session in enumerate(document.sessions):
        errors.extend(
            f"sessions[{index}]: {error}"
            for error in _validate_session(session)
        )
    return errors


def parse_calendar(payload: bytes) -> CalendarDocument:
    """Decode, validate and digest a supplied JSON calendar document."""
    document_raw = json.loads(
        payload.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
    )
    if not isinstance(document_raw, dict):
        raise ValueError("calendar document must be a JSON object")
    if set(document_raw) - set(_DOCUMENT_FIELDS):
        raise ValueError("unexpected calendar document fields")
    sessions = document_raw.get("sessions", [])
    if isinstance(sessions, list):
        for index, item in enumerate(sessions):
            if isinstance(item, Mapping) and set(item) - set(_SESSION_FIELDS):
                raise ValueError(f"sessions[{index}]: unexpected fields")
    document = document_from_mapping(document_raw)
    errors = validate_calendar(document)
    if errors:
        raise ValueError("; ".join(errors))
    return document


def load_calendar(path: Path) -> CalendarDocument:
    """Load a validated calendar document from a JSON file path."""
    return parse_calendar(path.read_bytes())


def _provenance(
    document: CalendarDocument, session: CalendarSession
) -> SessionProvenance:
    """Attach both digests and the source to a session query result."""
    return SessionProvenance(
        session.source_reference,
        session.captured_at,
        session.digest,
        document.digest,
        document.schema_version,
    )


def _negative(
    reasons: tuple[str, ...],
    document: CalendarDocument,
    session: CalendarSession | None = None,
) -> CalendarQuery:
    """Build a fail-closed negative query result with retained provenance."""
    provenance = _provenance(document, session) if session is not None else None
    return CalendarQuery(False, session, provenance, reasons)


def query_session(
    document: CalendarDocument,
    session_date: str,
    exchange: str,
    segment: str,
    as_of_observation_time: str | None = None,
) -> CalendarQuery:
    """Query one session by date, retaining provenance on every result."""
    if (
        not parse_market_date(session_date)
        or not exchange
        or not segment
        or (
            as_of_observation_time is not None
            and parse_instant(as_of_observation_time) is None
        )
    ):
        return _negative((INVALID_QUERY,), document)
    for session in document.sessions:
        if session_key(session) != (exchange, segment, session_date):
            continue
        provenance = _provenance(document, session)
        if session.holiday:
            return CalendarQuery(False, session, provenance, (SESSION_HOLIDAY,))
        if as_of_observation_time is not None and (
            session.captured_at > as_of_observation_time
            or document.captured_at > as_of_observation_time
        ):
            return CalendarQuery(
                False, session, provenance, (CALENDAR_NOT_OBSERVED,)
            )
        return CalendarQuery(True, session, provenance, ())
    return _negative((NO_SESSION,), document)


def is_trading_session(
    document: CalendarDocument,
    session_date: str,
    exchange: str,
    segment: str,
) -> bool:
    """Return True only for an observed, non-holiday session."""
    return query_session(document, session_date, exchange, segment).found
