"""Offline canonical instrument master for official NSE snapshot files.

The module consumes only decoded bytes that were captured under
``data/instrument-master/`` and described in
``governance/instrument-master-snapshots.json``. It performs no network I/O.
Provider keys are external identifiers: a cash-equity identity is derived only
from ISIN, exchange, segment and trading series, never from a symbol, name or
provider token.

Identity verification, contract conformance, trading status and eligibility
are reported separately. Matching an ISIN establishes identity only; it never
implies that comparable contract fields agree. Effective dating is partial:
effective_from is derived from the supplied snapshot market_date and
effective_to stays open because no validity interval is supplied.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = "instrument-master-v1"
IDENTITY_VERSION = "v1"

PROVIDER_UPSTOX = "upstox"
PROVIDER_FYERS = "fyers"

EXCHANGE_NSE = "NSE"
SEGMENT_CASH = "NSE_EQ"
CASH_EQUITY = "CASH_EQUITY"
FUTURE = "FUTURE"
OPTION = "OPTION"
INDEX = "INDEX"
UNSUPPORTED = "UNSUPPORTED"

VERIFIED = "VERIFIED"
UNMAPPED = "UNMAPPED"
AMBIGUOUS = "AMBIGUOUS"
PASS = "PASS"
CONFLICT = "CONFLICT"
INCOMPLETE = "INCOMPLETE"
INACTIVE = "INACTIVE"
SUSPENDED = "SUSPENDED"
ACTIVE = "ACTIVE"
UNKNOWN = "UNKNOWN"
ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

IDENTITY_STATUSES = (VERIFIED, UNMAPPED, AMBIGUOUS)
CONFORMANCE_STATUSES = (PASS, CONFLICT, INCOMPLETE)
TRADING_STATUSES = (ACTIVE, INACTIVE, SUSPENDED, UNKNOWN)
ELIGIBILITY_STATUSES = (ELIGIBLE, NOT_ELIGIBLE)

MISSING_PROVIDER_KEY = "MISSING_PROVIDER_KEY"
MISSING_ISIN = "MISSING_ISIN"
MISSING_SERIES = "MISSING_SERIES"
UNSUPPORTED_SEGMENT = "UNSUPPORTED_SEGMENT"
IDENTITY_COLLISION = "IDENTITY_COLLISION"

ISIN_RE = re.compile(r"^IN[0-9A-Z]{9}[0-9]$")
FYERS_EXCHANGE_SEGMENTS = {
    (10, 10): ("NSE", "NSE_EQ"),
    (10, 11): ("NSE", "NSE_FO"),
    (10, 12): ("NSE", "NSE_CD"),
    (11, 20): ("MCX", "MCX_COM"),
    (12, 10): ("BSE", "BSE_EQ"),
    (12, 11): ("BSE", "BSE_FO"),
}

SNAPSHOT_FIELDS = frozenset(
    {
        "provider",
        "url",
        "captured_at_utc",
        "market_date",
        "original_filename",
        "original_bytes",
        "original_sha256",
        "decoded_bytes",
        "decoded_sha256",
        "schema_version",
        "provider_update_timestamp",
        "status",
    }
)
DIFF_FIELDS = (
    "exchange",
    "segment",
    "instrument_class",
    "listing_id",
    "isin",
    "trading_series",
    "underlying_instrument_id",
    "provider_identifiers",
    "tick_size",
    "lot_size",
    "contract_multiplier",
    "expiry",
    "strike",
    "option_type",
    "effective_from",
    "effective_to",
    "trading_status",
    "crosswalk_status",
    "conformance_status",
    "eligibility_status",
)
# Provider-specific fields such as provider_symbol, listing tokens and
# trading_status are excluded; these are the comparable contract terms.
CONFORMANCE_FIELDS = (
    "exchange",
    "segment",
    "instrument_class",
    "trading_series",
    "isin",
    "listing_id",
    "tick_size",
    "lot_size",
    "contract_multiplier",
)


class SnapshotMeta(NamedTuple):
    """Verified provenance record for one captured provider file."""

    provider: str
    url: str
    captured_at_utc: str
    market_date: str
    original_filename: str
    original_bytes: int
    original_sha256: str
    decoded_bytes: int
    decoded_sha256: str
    schema_version: str
    provider_update_timestamp: str
    status: str


class SnapshotContext(NamedTuple):
    """Deterministic inputs supplied to a parser by a provenance record."""

    market_date: str
    observed_at_utc: str
    source_digest: str
    source_version: str
    provider_update_timestamp: str


class UpstoxRecord(NamedTuple):
    """Fields observed in the official Upstox NSE BOD JSON master."""

    segment: str = ""
    name: str = ""
    exchange: str = ""
    isin: str = ""
    instrument_type: str = ""
    instrument_key: str = ""
    lot_size: float | None = None
    freeze_quantity: float | None = None
    exchange_token: str = ""
    tick_size: float | None = None
    trading_symbol: str = ""
    qty_multiplier: float | None = None
    strike_price: float | None = None
    expiry: float | None = None
    asset_key: str = ""
    underlying_key: str = ""
    underlying_symbol: str = ""
    security_type: str = ""
    short_name: str = ""
    cas_eligible: bool | None = None


class FyersSymbolRecord(NamedTuple):
    """Fields documented for the official FYERS NSE_CM symbol master."""

    sym_ticker: str = ""
    fy_token: str = ""
    isin: str = ""
    ex_symbol: str = ""
    exchange: int | None = None
    segment: int | None = None
    ex_series: str = ""
    ex_token: int | None = None
    min_lot_size: int | None = None
    tick_size: float | None = None
    trading_session: str = ""
    last_update: str = ""
    trade_status: int | None = None
    qty_multiplier: float | None = None
    opt_type: str = ""
    strike_price: float | None = None
    expiry_date: str = ""
    under_sym: str = ""
    under_fy_tok: str = ""


class ProviderInstrument(NamedTuple):
    """Provider observation normalised to shared field names."""

    provider: str
    provider_key: str
    provider_symbol: str
    exchange: str
    segment: str
    trading_series: str
    instrument_class: str
    listing_id: str
    isin: str
    tick_size: float | None
    lot_size: int | None
    contract_multiplier: float | None
    expiry: str | None
    strike: float | None
    option_type: str | None
    underlying_provider_key: str
    trading_status: str
    market_date: str
    observed_at_utc: str
    provider_update_timestamp: str
    source_digest: str


class ProviderIdentifier(NamedTuple):
    """External provider key and symbol carried on a canonical instrument."""

    provider: str
    key: str
    symbol: str
    exchange_token: str
    provider_update_timestamp: str


class QuarantineEntry(NamedTuple):
    """Record withheld from the eligible universe and the reason why."""

    provider: str
    provider_key: str
    reason: str
    observed_at_utc: str
    source_digest: str


class CanonicalInstrument(NamedTuple):
    """Identity-resolved instrument; all provider keys remain external."""

    canonical_instrument_id: str
    exchange: str
    segment: str
    instrument_class: str
    listing_id: str
    isin: str
    trading_series: str
    underlying_instrument_id: str | None
    provider_identifiers: tuple[ProviderIdentifier, ...]
    tick_size: float | None
    lot_size: int | None
    contract_multiplier: float | None
    expiry: str | None
    strike: float | None
    option_type: str | None
    effective_from: str
    effective_to: str | None
    first_observed_at: str
    last_observed_at: str
    trading_status: str
    crosswalk_status: str
    conformance_status: str
    eligibility_status: str
    source_digest: str
    source_version: str

    @property
    def identity_status(self) -> str:
        """Return the identity-only status held in crosswalk_status."""
        return self.crosswalk_status


class ParseResult(NamedTuple):
    """Normalised records and quarantine entries from one snapshot."""

    records: tuple[ProviderInstrument, ...]
    quarantine: tuple[QuarantineEntry, ...]
    total_records: int


class FieldChange(NamedTuple):
    """One field difference for a canonical ID between two snapshots."""

    canonical_instrument_id: str
    field: str
    old_value: object
    new_value: object


class DiffReport(NamedTuple):
    """Deterministic daily diff keyed by canonical instrument ID."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[FieldChange, ...]


class CrosswalkReport(NamedTuple):
    """Cross-provider result plus identities withheld from eligibility.

    status_counts holds identity counts for VERIFIED/UNMAPPED/AMBIGUOUS and
    trading counts for INACTIVE/SUSPENDED. conformance_counts and
    trading_counts report the two independent classifications explicitly.
    """

    instruments: tuple[CanonicalInstrument, ...]
    quarantine: tuple[QuarantineEntry, ...]
    status_counts: dict[str, int]
    conformance_counts: dict[str, int] = {}
    trading_counts: dict[str, int] = {}


def sha256_hex(payload: bytes) -> str:
    """Return the SHA-256 digest of raw bytes."""
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of silently overwriting."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(payload: bytes) -> object:
    """Decode UTF-8 JSON with strict duplicate-key handling."""
    return json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)


def _verify_digest(payload: bytes, expected: str) -> None:
    """Fail closed when supplied bytes do not match the provenance digest."""
    if sha256_hex(payload) != expected:
        raise ValueError("snapshot digest does not match provenance")


def _text(value: object) -> str:
    """Return a stripped string for a JSON string, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    """Return a finite JSON number as float, otherwise None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _integer(value: object) -> int | None:
    """Return an integral JSON number, otherwise None."""
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _epoch_ms_date(value: object) -> str | None:
    """Convert a provider epoch-millisecond expiry into a UTC ISO date."""
    number = _number(value)
    if number is None:
        return None
    try:
        moment = datetime.fromtimestamp(number / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.date().isoformat()


def _provider_strike(value: object) -> float | None:
    """Return a strike, mapping the documented -1 sentinel to None."""
    number = _number(value)
    if number is None or number == -1:
        return None
    return number


def _load_list(payload: bytes, digest: str | None = None) -> list[dict]:
    """Decode an array-of-objects provider file, checking its digest."""
    if digest is not None:
        _verify_digest(payload, digest)
    document = _decode_json(payload)
    if not isinstance(document, list) or not all(
        isinstance(item, dict) for item in document
    ):
        raise ValueError("expected a JSON array of objects")
    return document


def _load_mapping(payload: bytes, digest: str | None = None) -> dict:
    """Decode a symbol-keyed provider file, checking its digest."""
    if digest is not None:
        _verify_digest(payload, digest)
    document = _decode_json(payload)
    if not isinstance(document, dict) or not all(
        isinstance(value, dict) for value in document.values()
    ):
        raise ValueError("expected a JSON object of objects")
    return document


def parse_upstox_records(payload: bytes) -> tuple[UpstoxRecord, ...]:
    """Extract observed Upstox NSE BOD fields from decoded snapshot bytes."""
    return tuple(
        UpstoxRecord(
            segment=_text(item.get("segment")),
            name=_text(item.get("name")),
            exchange=_text(item.get("exchange")),
            isin=_text(item.get("isin")),
            instrument_type=_text(item.get("instrument_type")),
            instrument_key=_text(item.get("instrument_key")),
            lot_size=_number(item.get("lot_size")),
            freeze_quantity=_number(item.get("freeze_quantity")),
            exchange_token=_text(item.get("exchange_token")),
            tick_size=_number(item.get("tick_size")),
            trading_symbol=_text(item.get("trading_symbol")),
            qty_multiplier=_number(item.get("qty_multiplier")),
            strike_price=_number(item.get("strike_price")),
            expiry=_number(item.get("expiry")),
            asset_key=_text(item.get("asset_key")),
            underlying_key=_text(item.get("underlying_key")),
            underlying_symbol=_text(item.get("underlying_symbol")),
            security_type=_text(item.get("security_type")),
            short_name=_text(item.get("short_name")),
            cas_eligible=(
                item.get("cas_eligible")
                if isinstance(item.get("cas_eligible"), bool)
                else None
            ),
        )
        for item in _load_list(payload)
    )


def parse_fyers_symbols(payload: bytes) -> tuple[FyersSymbolRecord, ...]:
    """Extract documented FYERS NSE_CM fields from decoded snapshot bytes."""
    document = _load_mapping(payload)
    symbols = []
    for ticker, item in document.items():
        symbols.append(
            FyersSymbolRecord(
                sym_ticker=_text(item.get("symTicker")) or ticker,
                fy_token=_text(item.get("fyToken")),
                isin=_text(item.get("isin")),
                ex_symbol=_text(item.get("exSymbol")),
                exchange=_integer(item.get("exchange")),
                segment=_integer(item.get("segment")),
                ex_series=_text(item.get("exSeries")),
                ex_token=_integer(item.get("exToken")),
                min_lot_size=_integer(item.get("minLotSize")),
                tick_size=_number(item.get("tickSize")),
                trading_session=_text(item.get("tradingSession")),
                last_update=_text(item.get("lastUpdate")),
                trade_status=_integer(item.get("tradeStatus")),
                qty_multiplier=_number(item.get("qtyMultiplier")),
                opt_type=_text(item.get("optType")),
                strike_price=_number(item.get("strikePrice")),
                expiry_date=_text(item.get("expiryDate")),
                under_sym=_text(item.get("underSym")),
                under_fy_tok=_text(item.get("underFyTok")),
            )
        )
    return tuple(symbols)


def _instrument_class(segment: str, instrument_type: str) -> str:
    """Classify a record without using company names or provider keys."""
    if segment == SEGMENT_CASH:
        return CASH_EQUITY
    if segment == "NSE_INDEX":
        return INDEX
    if instrument_type in ("CE", "PE"):
        return OPTION
    if instrument_type == "FUT":
        return FUTURE
    return UNSUPPORTED


def _fyers_trading_status(trade_status: int | None) -> str:
    """Map the documented FYERS tradeStatus flag to a canonical status."""
    if trade_status == 1:
        return ACTIVE
    if trade_status == 0:
        return INACTIVE
    return UNKNOWN


def _option_type(value: str) -> str | None:
    """Return CE or PE only; XX is the documented non-option value."""
    return value if value in ("CE", "PE") else None


def _quarantine(
    provider: str, key: str, reason: str, context: SnapshotContext
) -> QuarantineEntry:
    """Build a quarantine entry for a withheld provider record."""
    return QuarantineEntry(
        provider, key, reason, context.observed_at_utc, context.source_digest
    )


def _identity_reason(record: ProviderInstrument) -> str | None:
    """Return the identity defect for a record, or None when identifiable."""
    if record.exchange != EXCHANGE_NSE or record.segment != SEGMENT_CASH:
        return UNSUPPORTED_SEGMENT
    if not ISIN_RE.fullmatch(record.isin):
        return MISSING_ISIN
    if not record.trading_series:
        return MISSING_SERIES
    return None


def _upstox_provider(record: UpstoxRecord, context: SnapshotContext):
    """Normalise one Upstox record into a provider observation.

    The Upstox NSE BOD master has no tradeStatus field, so trading status is
    UNKNOWN until corroborated by another provider.
    """
    series = record.instrument_type if record.segment == SEGMENT_CASH else ""
    return ProviderInstrument(
        PROVIDER_UPSTOX,
        record.instrument_key,
        record.trading_symbol,
        record.exchange,
        record.segment,
        series,
        _instrument_class(record.segment, record.instrument_type),
        record.exchange_token,
        record.isin,
        record.tick_size,
        _integer(record.lot_size),
        record.qty_multiplier,
        _epoch_ms_date(record.expiry),
        _provider_strike(record.strike_price),
        (
            None
            if record.segment == SEGMENT_CASH
            else _option_type(record.instrument_type)
        ),
        record.underlying_key or record.asset_key,
        UNKNOWN,
        context.market_date,
        context.observed_at_utc,
        context.provider_update_timestamp,
        context.source_digest,
    )


def _fyers_provider(record: FyersSymbolRecord, context: SnapshotContext):
    """Normalise one FYERS record into a provider observation."""
    mapped = FYERS_EXCHANGE_SEGMENTS.get(
        (record.exchange, record.segment), ("", "")
    )
    series = record.ex_series if mapped[1] == SEGMENT_CASH else ""
    return ProviderInstrument(
        PROVIDER_FYERS,
        record.fy_token,
        record.sym_ticker,
        mapped[0],
        mapped[1],
        series,
        _instrument_class(mapped[1], record.opt_type),
        str(record.ex_token) if record.ex_token is not None else "",
        record.isin,
        record.tick_size,
        record.min_lot_size,
        record.qty_multiplier,
        _epoch_ms_date(record.expiry_date),
        _provider_strike(record.strike_price),
        _option_type(record.opt_type),
        record.under_fy_tok,
        _fyers_trading_status(record.trade_status),
        context.market_date,
        context.observed_at_utc,
        record.last_update or context.provider_update_timestamp,
        context.source_digest,
    )


def _admit(
    record: ProviderInstrument,
    context: SnapshotContext,
    records: list[ProviderInstrument],
    quarantine: list[QuarantineEntry],
) -> None:
    """Admit an identifiable record or quarantine it with a reason."""
    reason = _identity_reason(record)
    if reason is None:
        records.append(record)
        return
    quarantine.append(
        _quarantine(record.provider, record.provider_key, reason, context)
    )


def parse_upstox(payload: bytes, context: SnapshotContext) -> ParseResult:
    """Parse and quarantine one decoded Upstox snapshot for its context."""
    _verify_digest(payload, context.source_digest)
    items = parse_upstox_records(payload)
    records: list[ProviderInstrument] = []
    quarantine: list[QuarantineEntry] = []
    for item in items:
        record = _upstox_provider(item, context)
        if not record.provider_key:
            quarantine.append(
                _quarantine(PROVIDER_UPSTOX, "", MISSING_PROVIDER_KEY, context)
            )
            continue
        _admit(record, context, records, quarantine)
    return ParseResult(tuple(records), tuple(quarantine), len(items))


def parse_fyers(payload: bytes, context: SnapshotContext) -> ParseResult:
    """Parse and quarantine one decoded FYERS snapshot for its context."""
    _verify_digest(payload, context.source_digest)
    items = parse_fyers_symbols(payload)
    records: list[ProviderInstrument] = []
    quarantine: list[QuarantineEntry] = []
    for item in items:
        record = _fyers_provider(item, context)
        if not record.provider_key:
            quarantine.append(
                _quarantine(PROVIDER_FYERS, "", MISSING_PROVIDER_KEY, context)
            )
            continue
        _admit(record, context, records, quarantine)
    return ParseResult(tuple(records), tuple(quarantine), len(items))


def valid_isin(value: str) -> bool:
    """Return True for a well-formed 12-character Indian ISIN."""
    return bool(ISIN_RE.fullmatch(value))


def identity_components(
    record: ProviderInstrument,
) -> tuple[str, str, str, str, str] | None:
    """Return the cash-equity identity tuple for an identifiable record."""
    if _identity_reason(record) is not None:
        return None
    return (
        IDENTITY_VERSION,
        record.exchange,
        record.segment,
        record.isin,
        record.trading_series,
    )


def derive_canonical_id(components: Sequence[str]) -> str:
    """Derive a stable ID from identity components, never from a symbol."""
    payload = "\x1f".join(components).encode("utf-8")
    return "NSE-CASH-" + sha256_hex(payload)[:32]


def canonical_id(record: ProviderInstrument) -> str | None:
    """Return the canonical ID of a record, or None if unidentified."""
    components = identity_components(record)
    return derive_canonical_id(components) if components else None


def _merged_digest(group: Sequence[ProviderInstrument]) -> str:
    """Derive one digest from the provider digests backing an identity."""
    parts = sorted((item.provider, item.source_digest) for item in group)
    return sha256_hex(json.dumps(parts, separators=(",", ":")).encode("utf-8"))


def _first_value(group: Sequence[ProviderInstrument], attribute: str) -> object:
    """Prefer FYERS values for shared fields, then Upstox, deterministically.

    In the captured snapshots Upstox reports tick_size in paise and FYERS in
    rupees, so the canonical tick_size retains the FYERS unit rather than
    silently mixing or rescaling provider values.
    """
    for provider in (PROVIDER_FYERS, PROVIDER_UPSTOX):
        for item in sorted(group, key=lambda row: row.provider_key):
            if item.provider == provider:
                value = getattr(item, attribute)
                if value is not None and value != "":
                    return value
    return None


def _underlying_id(value: str) -> str | None:
    """Resolve an Upstox ISIN-based underlying key to a canonical ID."""
    prefix = f"{SEGMENT_CASH}|"
    if not value.startswith(prefix):
        return None
    isin = value[len(prefix) :]
    components = (
        IDENTITY_VERSION,
        EXCHANGE_NSE,
        SEGMENT_CASH,
        isin,
        "EQ",
    )
    return derive_canonical_id(components) if valid_isin(isin) else None


def _combined_status(
    group: Sequence[ProviderInstrument], identity: str, suspended: set[str]
) -> str:
    """Combine provider statuses without inventing a suspension signal."""
    if identity in suspended:
        return SUSPENDED
    if any(item.trading_status == INACTIVE for item in group):
        return INACTIVE
    if any(item.trading_status == ACTIVE for item in group):
        return ACTIVE
    return UNKNOWN


def _identity_status(group: Sequence[ProviderInstrument]) -> str:
    """Classify identity corroboration only, independent of contract fields."""
    providers = {item.provider for item in group}
    if len(group) != len(providers):
        return AMBIGUOUS
    if len(providers) > 1:
        return VERIFIED
    return UNMAPPED


def _conformance_value(item: ProviderInstrument, field: str) -> object:
    """Return a comparable field value, normalising the tick-size unit."""
    value = getattr(item, field)
    if field == "tick_size" and item.provider == PROVIDER_UPSTOX:
        # Upstox reports paise and FYERS rupees; rescale for comparison only.
        if value is not None:
            return float(value) / 100
    return value


def _values_agree(left: object, right: object) -> bool:
    """Compare two field values, tolerating harmless float rounding."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left), float(right), rel_tol=1e-12, abs_tol=1e-12
        )
    return left == right


def contract_conformance(
    group: Sequence[ProviderInstrument], identity_status: str
) -> str:
    """Compare contract terms; an ISIN match alone is not conformance."""
    if identity_status != VERIFIED:
        return INCOMPLETE
    ordered = sorted(group, key=lambda item: item.provider)
    for field in CONFORMANCE_FIELDS:
        values = [_conformance_value(item, field) for item in ordered]
        if any(value is None or value == "" for value in values):
            return INCOMPLETE
        if not all(_values_agree(values[0], value) for value in values[1:]):
            return CONFLICT
    return PASS


def eligibility_status(
    identity_status: str, conformance: str, trading_status: str
) -> str:
    """Grant eligibility only when identity, contract and trading all pass."""
    if (
        identity_status == VERIFIED
        and conformance == PASS
        and trading_status == ACTIVE
    ):
        return ELIGIBLE
    return NOT_ELIGIBLE


def _build_canonical(
    identity: str,
    group: Sequence[ProviderInstrument],
    identity_status: str,
    trading_status: str,
    conformance: str,
) -> CanonicalInstrument:
    """Build one canonical instrument from its provider observations."""
    identifiers = tuple(
        sorted(
            ProviderIdentifier(
                item.provider,
                item.provider_key,
                item.provider_symbol,
                item.listing_id,
                item.provider_update_timestamp,
            )
            for item in group
        )
    )
    listing_id = _first_value(group, "listing_id")
    isin = _first_value(group, "isin")
    series = _first_value(group, "trading_series")
    tick_size = _first_value(group, "tick_size")
    eligibility = eligibility_status(
        identity_status, conformance, trading_status
    )
    return CanonicalInstrument(
        canonical_instrument_id=identity,
        exchange=EXCHANGE_NSE,
        segment=SEGMENT_CASH,
        instrument_class=CASH_EQUITY,
        listing_id=str(listing_id) if listing_id is not None else "",
        isin=str(isin) if isin is not None else "",
        trading_series=str(series) if series is not None else "",
        underlying_instrument_id=_underlying_id(
            str(_first_value(group, "underlying_provider_key") or "")
        ),
        provider_identifiers=identifiers,
        tick_size=tick_size,
        lot_size=_first_value(group, "lot_size"),
        contract_multiplier=_first_value(group, "contract_multiplier"),
        # W1 identity is cash-only; derivative records stay quarantined, so
        # these contract terms remain unset instead of being inferred.
        expiry=None,
        strike=None,
        option_type=None,
        # Partial effective dating only: the start comes from the supplied
        # snapshot market_date and no end date is asserted.
        effective_from=min(item.market_date for item in group),
        effective_to=None,
        first_observed_at=min(item.observed_at_utc for item in group),
        last_observed_at=max(item.observed_at_utc for item in group),
        trading_status=trading_status,
        crosswalk_status=identity_status,
        conformance_status=conformance,
        eligibility_status=eligibility,
        source_digest=_merged_digest(group),
        source_version=SCHEMA_VERSION,
    )


def _group_records(
    results: Sequence[ParseResult],
) -> dict[str, list[ProviderInstrument]]:
    """Group all identifiable records by deterministic canonical ID."""
    groups: dict[str, list[ProviderInstrument]] = {}
    for result in results:
        for record in result.records:
            key = canonical_id(record)
            if key is not None:
                groups.setdefault(key, []).append(record)
    return groups


def _collision_entries(
    identity: str, group: Sequence[ProviderInstrument]
) -> list[QuarantineEntry]:
    """Quarantine all records of an identity claimed twice by one provider."""
    seen: set[str] = set()
    entries = []
    for item in group:
        if item.provider in seen:
            entries.append(
                QuarantineEntry(
                    "crosswalk",
                    identity,
                    IDENTITY_COLLISION,
                    min(row.observed_at_utc for row in group),
                    _merged_digest(group),
                )
            )
        seen.add(item.provider)
    return entries


def crosswalk(
    results: Sequence[ParseResult], suspended_ids: Sequence[str] = ()
) -> CrosswalkReport:
    """Crosswalk parsed snapshots by canonical ID and quarantine collisions."""
    suspended = set(suspended_ids)
    groups = _group_records(results)
    instruments = []
    quarantine = [entry for result in results for entry in result.quarantine]
    counts = {
        VERIFIED: 0,
        UNMAPPED: 0,
        AMBIGUOUS: 0,
        INACTIVE: 0,
        SUSPENDED: 0,
    }
    conformance_counts = {status: 0 for status in CONFORMANCE_STATUSES}
    trading_counts = {status: 0 for status in TRADING_STATUSES}
    for identity in sorted(groups):
        group = groups[identity]
        collisions = _collision_entries(identity, group)
        quarantine.extend(collisions)
        identity_status = AMBIGUOUS if collisions else _identity_status(group)
        trading_status = _combined_status(group, identity, suspended)
        conformance = contract_conformance(group, identity_status)
        instruments.append(
            _build_canonical(
                identity, group, identity_status, trading_status, conformance
            )
        )
        counts[identity_status] += 1
        conformance_counts[conformance] += 1
        trading_counts[trading_status] += 1
        if trading_status in counts:
            counts[trading_status] += 1
    return CrosswalkReport(
        tuple(instruments),
        tuple(quarantine),
        counts,
        conformance_counts,
        trading_counts,
    )


def is_eligible(instrument: CanonicalInstrument) -> bool:
    """Return True only for a VERIFIED, conforming, active instrument."""
    return instrument.eligibility_status == ELIGIBLE


def diff_snapshots(
    previous: Mapping[str, CanonicalInstrument],
    current: Mapping[str, CanonicalInstrument],
    fields: Sequence[str] = DIFF_FIELDS,
) -> DiffReport:
    """Compare two snapshot universes by canonical ID, not by symbol."""
    added = tuple(sorted(current.keys() - previous.keys()))
    removed = tuple(sorted(previous.keys() - current.keys()))
    changes = []
    for identity in sorted(previous.keys() & current.keys()):
        for field in fields:
            old_value = getattr(previous[identity], field)
            new_value = getattr(current[identity], field)
            if old_value != new_value:
                changes.append(
                    FieldChange(identity, field, old_value, new_value)
                )
    return DiffReport(added, removed, tuple(changes))


def load_provenance(path: Path) -> dict[str, SnapshotMeta]:
    """Load and validate the canonical snapshot provenance record."""
    document = _decode_json(path.read_bytes())
    if not isinstance(document, dict) or not isinstance(
        document.get("snapshots"), list
    ):
        raise ValueError("provenance requires a snapshots array")
    metas = {}
    for entry in document["snapshots"]:
        if not isinstance(entry, dict) or set(entry) != SNAPSHOT_FIELDS:
            raise ValueError("unexpected snapshot provenance fields")
        meta = SnapshotMeta(**entry)
        metas[meta.provider] = meta
    return metas


def snapshot_context(meta: SnapshotMeta) -> SnapshotContext:
    """Derive parser inputs from a snapshot provenance entry."""
    if meta.status != "AVAILABLE":
        raise ValueError(f"snapshot not available: {meta.provider}")
    return SnapshotContext(
        meta.market_date,
        meta.captured_at_utc,
        meta.decoded_sha256,
        meta.schema_version,
        meta.provider_update_timestamp,
    )


def decoded_filename(original_filename: str) -> str:
    """Return the storage name used for decoded snapshot bytes."""
    if original_filename.endswith(".gz"):
        return original_filename[:-3]
    return original_filename.removesuffix(".json") + ".decoded.json"


def snapshot_path(
    root: Path, meta: SnapshotMeta, *, decoded: bool = True
) -> Path:
    """Resolve a snapshot byte path from provenance and a data root."""
    filename = (
        decoded_filename(meta.original_filename)
        if decoded
        else meta.original_filename
    )
    return root / meta.provider / meta.market_date / filename


def parse_snapshot(
    path: Path, provider: str, context: SnapshotContext
) -> ParseResult:
    """Parse supplied decoded snapshot bytes for a known provider."""
    payload = path.read_bytes()
    if provider == PROVIDER_UPSTOX:
        return parse_upstox(payload, context)
    if provider == PROVIDER_FYERS:
        return parse_fyers(payload, context)
    raise ValueError(f"unsupported provider: {provider}")
