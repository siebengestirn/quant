"""Shared constants, status vocabulary and immutable data contracts.

The instrument master keeps its public vocabulary and record shapes here so
the registry, parser and converter modules can share one definition without
an import cycle. Every name is re-exported by ``instrument_master`` so existing
consumers see an unchanged public API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple

SCHEMA_VERSION = "instrument-master-v1"
IDENTITY_VERSION = "v1"
REGISTRY_VERSION = "instrument-registry-v1"

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
SINGLE_SOURCE = "SINGLE_SOURCE"
MULTI_SOURCE = "MULTI_SOURCE"
OPEN = "OPEN"
PREOPEN = "PREOPEN"
ALLOWED = "ALLOWED"
DISALLOWED = "DISALLOWED"
FRESH = "FRESH"
STALE = "STALE"
INVALID = "INVALID"

IDENTITY_STATUSES = (VERIFIED, UNMAPPED, AMBIGUOUS)
CONFORMANCE_STATUSES = (PASS, CONFLICT, INCOMPLETE)
TRADING_STATUSES = (ACTIVE, INACTIVE, SUSPENDED, UNKNOWN)
ELIGIBILITY_STATUSES = (ELIGIBLE, NOT_ELIGIBLE)
PROVIDER_COVERAGE_STATUSES = (SINGLE_SOURCE, MULTI_SOURCE, UNKNOWN)
EXCHANGE_STATUSES = (OPEN, PREOPEN, SUSPENDED, UNKNOWN)
EXCHANGE_ELIGIBILITY_STATUSES = (ALLOWED, DISALLOWED, UNKNOWN)
DATA_FRESHNESS_STATUSES = (FRESH, STALE, INVALID, UNKNOWN)

KNOWN_INTERVAL = "KNOWN_INTERVAL"
OPEN_ENDED = "OPEN_ENDED"
UNKNOWN_INTERVAL = "UNKNOWN_INTERVAL"
INVALID_INTERVAL = "INVALID_INTERVAL"
VALIDITY_STATES = (
    KNOWN_INTERVAL,
    OPEN_ENDED,
    UNKNOWN_INTERVAL,
    INVALID_INTERVAL,
)
# Only attested intervals can pass the eligibility gate. An open-ended
# interval has a known start but no end, so it stays subject to the FRESH
# data gate; UNKNOWN_INTERVAL and INVALID_INTERVAL never pass.
PROVEN_VALIDITY_STATES = (KNOWN_INTERVAL, OPEN_ENDED)

RESOLVED = "RESOLVED"
NOT_FOUND = "NOT_FOUND"
NOT_YET_VALID = "NOT_YET_VALID"
EXPIRED = "EXPIRED"
TOKEN_REUSE = "TOKEN_REUSE"

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
        "captured_at_utc",
        "url",
        "market_date",
        "decoded_bytes",
        "decoded_sha256",
        "original_filename",
        "original_bytes",
        "original_sha256",
        "schema_version",
        "provider_update_timestamp",
        "status",
    }
)
# Non-authoritative provenance additions accepted from the canonical record.
EXTENDED_PROVENANCE_FIELDS = frozenset(
    {
        "source_class",
        "reference_original_path",
        "reference_decoded_path",
        "reference_original_bytes",
        "reference_decoded_bytes",
        "reference_original_sha256",
        "reference_decoded_sha256",
    }
)
# Observation attribute names. Provider-scoped names use
# ``provider:<provider>:<attribute>`` so one append-only stream can carry
# independent evidence from each provider without extra record fields.
PROVIDER_PREFIX = "provider:"
PROVIDER_KEY_ATTRIBUTE = "provider_key"
# Reserved attribute carrying conflict evidence on a selected state. It is
# evidence, not a comparable contract term, so it never enters conformance.
CONFLICT_EVIDENCE_KEY = "__conflicts__"
ATTR_EFFECTIVE_FROM = "effective_from"
ATTR_EFFECTIVE_TO = "effective_to"
ATTR_IDENTITY_STATUS = "identity_status"
ATTR_CONFORMANCE_STATUS = "conformance_status"
ATTR_PROVIDER_TRADING_STATUS = "provider_trading_status"
ATTR_EXCHANGE_STATUS = "exchange_status"
ATTR_EXCHANGE_ELIGIBILITY = "exchange_eligibility"
ATTR_DATA_FRESHNESS = "data_freshness"
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
    instrument_class: str = UNKNOWN


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
    conformance_report: ConformanceReport | None = None
    provider_coverage: str = MULTI_SOURCE
    provider_trading_status: str = UNKNOWN
    exchange_status: str = UNKNOWN
    exchange_eligibility: str = UNKNOWN
    data_freshness: str = UNKNOWN
    quant_eligibility: str = NOT_ELIGIBLE

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


class FieldConformance(NamedTuple):
    """One comparable field evaluated independently across providers."""

    field: str
    status: str
    observed: tuple[tuple[str, object], ...]
    missing: tuple[str, ...]


class ConformanceReport(NamedTuple):
    """Per-field contract conformance, separate from identity corroboration.

    provider_coverage is an independent dimension: agreement within one
    provider is never reported as cross-provider PASS.
    """

    status: str
    fields: tuple[FieldConformance, ...]
    provider_coverage: str = MULTI_SOURCE

    @property
    def conflicts(self) -> tuple[FieldConformance, ...]:
        """Return fields on which providers disagree."""
        return tuple(item for item in self.fields if item.status == CONFLICT)

    @property
    def incomplete(self) -> tuple[FieldConformance, ...]:
        """Return fields with at least one missing provider value."""
        return tuple(item for item in self.fields if item.status == INCOMPLETE)

    @property
    def passed(self) -> tuple[FieldConformance, ...]:
        """Return fields on which every provider agrees."""
        return tuple(item for item in self.fields if item.status == PASS)


class QuarantineReport(NamedTuple):
    """Counts of withheld records, never a claim that bytes are corrupt."""

    total: int
    by_reason: dict[str, int]
    by_provider: dict[str, int]
    by_instrument_class: dict[str, int]
    by_reason_provider_class: dict[str, dict[str, dict[str, int]]]


_EMPTY_COUNTS: Mapping[str, int] = MappingProxyType({})
_EMPTY_CONFLICTS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


class CrosswalkReport(NamedTuple):
    """Cross-provider result plus identities withheld from eligibility.

    status_counts holds identity counts for VERIFIED/UNMAPPED/AMBIGUOUS and
    trading counts for INACTIVE/SUSPENDED. conformance_counts and
    trading_counts report the two independent classifications explicitly.
    """

    instruments: tuple[CanonicalInstrument, ...]
    quarantine: tuple[QuarantineEntry, ...]
    status_counts: dict[str, int]
    conformance_counts: Mapping[str, int] = _EMPTY_COUNTS
    trading_counts: Mapping[str, int] = _EMPTY_COUNTS
    coverage_counts: Mapping[str, int] = _EMPTY_COUNTS


class AliasResolution(NamedTuple):
    """Alias outcome; ID is set only for a unique, unambiguous claim.

    claimants preserves every distinct ID that ever claimed the alias.
    """

    identifier: str
    status: str
    canonical_instrument_id: str | None
    validity: str
    claimants: tuple[str, ...]


class Observation(NamedTuple):
    """One append-only observation; corrections are new rows, not edits."""

    canonical_instrument_id: str
    attribute_name: str
    attribute_value: object
    valid_from: str | None
    valid_to: str | None
    observed_at: str
    ingested_at: str
    provider_updated_at: str | None
    source_digest: str
    source_version: str
    registry_version: str


@dataclass(frozen=True)
class InstrumentState:
    """Bitemporal view at one market date and observation time."""

    canonical_instrument_id: str
    market_date: str
    observed_at: str
    effective_from: str | None
    effective_to: str | None
    provider_coverage: str
    identity_status: str
    conformance_status: str
    provider_trading_status: str
    exchange_status: str
    exchange_eligibility: str
    data_freshness: str
    quant_eligibility: str
    provider_keys: tuple[str, ...]
    attributes: Mapping[str, object]
    validity: str
    attribute_validity: Mapping[str, str]
    source_digest: str
    source_version: str
    registry_version: str

    @property
    def validity_assessment(self) -> str:
        """Return the explicit validity state of the selected values."""
        return self.validity

    @property
    def proven_valid(self) -> bool:
        """Return True only when an established interval covers market_date."""
        return self.validity in (KNOWN_INTERVAL, OPEN_ENDED)

    @property
    def conflicts(self) -> Mapping[str, tuple[str, ...]]:
        """Return conflicting observation identifiers keyed by attribute."""
        evidence = self.attributes.get(CONFLICT_EVIDENCE_KEY)
        return evidence if isinstance(evidence, Mapping) else _EMPTY_CONFLICTS

    @property
    def conflict_status(self) -> str:
        """Report AMBIGUOUS when tied observations disagree on a value."""
        return AMBIGUOUS if self.conflicts else RESOLVED

    def as_dict(self) -> dict[str, object]:
        """Return a plain mapping, safe for JSON serialisation."""
        return {
            **self.__dict__,
            "attributes": dict(self.attributes),
            "conflicts": dict(self.conflicts),
        }

    def __getitem__(self, key: str) -> object:
        """Allow mapping-style access to every state field."""
        if key not in self.__dict__:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        """Return a state field with a mapping-style default."""
        return self.__dict__.get(key, default)
