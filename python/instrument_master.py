"""Offline canonical instrument master for official NSE snapshot files.

The module consumes only decoded bytes that were captured under
``data/instrument-master/`` and described in
``governance/instrument-master-snapshots.json``. It performs no network I/O.
Provider keys are external identifiers: a cash-equity identity is derived only
from ISIN, exchange, segment and trading series, never from a symbol, name or
provider token.

Identity verification, contract conformance, trading status and eligibility
are reported separately. Matching an ISIN establishes identity only; it never
implies that comparable contract fields agree. The legacy crosswalk reports
the snapshot market_date as a partial, snapshot-derived observation date; the
bitemporal registry never infers a validity date from it and keeps unknown
effective_from and effective_to values unknown until a source establishes
them. Each value carries a validity assessment; unknown intervals are never
proven-valid. Queries separate market date and observation time, and aliases
obey their own market validity intervals.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parent))
_support = importlib.import_module("instrument_master_support")
ACTIVE = _support.ACTIVE
ALLOWED = _support.ALLOWED
AMBIGUOUS = _support.AMBIGUOUS
ATTR_CONFORMANCE_STATUS = _support.ATTR_CONFORMANCE_STATUS
ATTR_DATA_FRESHNESS = _support.ATTR_DATA_FRESHNESS
ATTR_EFFECTIVE_FROM = _support.ATTR_EFFECTIVE_FROM
ATTR_EFFECTIVE_TO = _support.ATTR_EFFECTIVE_TO
ATTR_EXCHANGE_ELIGIBILITY = _support.ATTR_EXCHANGE_ELIGIBILITY
ATTR_EXCHANGE_STATUS = _support.ATTR_EXCHANGE_STATUS
ATTR_IDENTITY_STATUS = _support.ATTR_IDENTITY_STATUS
ATTR_PROVIDER_TRADING_STATUS = _support.ATTR_PROVIDER_TRADING_STATUS
CASH_EQUITY = _support.CASH_EQUITY
CONFLICT = _support.CONFLICT
CONFLICT_EVIDENCE_KEY = _support.CONFLICT_EVIDENCE_KEY
CONFORMANCE_FIELDS = _support.CONFORMANCE_FIELDS
CONFORMANCE_STATUSES = _support.CONFORMANCE_STATUSES
DATA_FRESHNESS_STATUSES = _support.DATA_FRESHNESS_STATUSES
DIFF_FIELDS = _support.DIFF_FIELDS
DISALLOWED = _support.DISALLOWED
ELIGIBILITY_STATUSES = _support.ELIGIBILITY_STATUSES
ELIGIBLE = _support.ELIGIBLE
EXCHANGE_ELIGIBILITY_STATUSES = _support.EXCHANGE_ELIGIBILITY_STATUSES
EXCHANGE_NSE = _support.EXCHANGE_NSE
EXCHANGE_STATUSES = _support.EXCHANGE_STATUSES
EXPIRED = _support.EXPIRED
EXTENDED_PROVENANCE_FIELDS = _support.EXTENDED_PROVENANCE_FIELDS
FRESH = _support.FRESH
FUTURE = _support.FUTURE
FYERS_EXCHANGE_SEGMENTS = _support.FYERS_EXCHANGE_SEGMENTS
IDENTITY_COLLISION = _support.IDENTITY_COLLISION
IDENTITY_STATUSES = _support.IDENTITY_STATUSES
IDENTITY_VERSION = _support.IDENTITY_VERSION
INACTIVE = _support.INACTIVE
INCOMPLETE = _support.INCOMPLETE
INDEX = _support.INDEX
INVALID = _support.INVALID
INVALID_INTERVAL = _support.INVALID_INTERVAL
ISIN_RE = _support.ISIN_RE
KNOWN_INTERVAL = _support.KNOWN_INTERVAL
MISSING_ISIN = _support.MISSING_ISIN
MISSING_PROVIDER_KEY = _support.MISSING_PROVIDER_KEY
MISSING_SERIES = _support.MISSING_SERIES
MULTI_SOURCE = _support.MULTI_SOURCE
NOT_ELIGIBLE = _support.NOT_ELIGIBLE
NOT_FOUND = _support.NOT_FOUND
NOT_YET_VALID = _support.NOT_YET_VALID
OPEN = _support.OPEN
OPEN_ENDED = _support.OPEN_ENDED
OPTION = _support.OPTION
PASS = _support.PASS
PREOPEN = _support.PREOPEN
PROVEN_VALIDITY_STATES = _support.PROVEN_VALIDITY_STATES
PROVIDER_COVERAGE_STATUSES = _support.PROVIDER_COVERAGE_STATUSES
PROVIDER_FYERS = _support.PROVIDER_FYERS
PROVIDER_KEY_ATTRIBUTE = _support.PROVIDER_KEY_ATTRIBUTE
PROVIDER_PREFIX = _support.PROVIDER_PREFIX
PROVIDER_UPSTOX = _support.PROVIDER_UPSTOX
REGISTRY_VERSION = _support.REGISTRY_VERSION
RESOLVED = _support.RESOLVED
SCHEMA_VERSION = _support.SCHEMA_VERSION
SEGMENT_CASH = _support.SEGMENT_CASH
SINGLE_SOURCE = _support.SINGLE_SOURCE
SNAPSHOT_FIELDS = _support.SNAPSHOT_FIELDS
STALE = _support.STALE
SUSPENDED = _support.SUSPENDED
TOKEN_REUSE = _support.TOKEN_REUSE
TRADING_STATUSES = _support.TRADING_STATUSES
UNKNOWN = _support.UNKNOWN
UNKNOWN_INTERVAL = _support.UNKNOWN_INTERVAL
UNMAPPED = _support.UNMAPPED
UNSUPPORTED = _support.UNSUPPORTED
UNSUPPORTED_SEGMENT = _support.UNSUPPORTED_SEGMENT
VALIDITY_STATES = _support.VALIDITY_STATES
VERIFIED = _support.VERIFIED
AliasResolution = _support.AliasResolution
CanonicalInstrument = _support.CanonicalInstrument
ConformanceReport = _support.ConformanceReport
CrosswalkReport = _support.CrosswalkReport
DiffReport = _support.DiffReport
FieldChange = _support.FieldChange
FieldConformance = _support.FieldConformance
FyersSymbolRecord = _support.FyersSymbolRecord
InstrumentState = _support.InstrumentState
Observation = _support.Observation
ParseResult = _support.ParseResult
ProviderIdentifier = _support.ProviderIdentifier
ProviderInstrument = _support.ProviderInstrument
QuarantineEntry = _support.QuarantineEntry
QuarantineReport = _support.QuarantineReport
SnapshotContext = _support.SnapshotContext
SnapshotMeta = _support.SnapshotMeta
UpstoxRecord = _support.UpstoxRecord
# These two typing helpers were visible at module scope before the extraction.
NamedTuple = _support.NamedTuple
dataclass = _support.dataclass

reject_duplicate_keys = importlib.import_module(
    "json_support"
).reject_duplicate_keys

# Status vocabulary retained for consumers even when the registry itself
# never consults a value directly. Binding it here keeps this module's public
# API unchanged after the contracts moved to the support module.
_PUBLIC_VOCABULARY = (
    PREOPEN,
    DISALLOWED,
    STALE,
    INVALID,
    IDENTITY_STATUSES,
    ELIGIBILITY_STATUSES,
    PROVIDER_COVERAGE_STATUSES,
    EXCHANGE_STATUSES,
    EXCHANGE_ELIGIBILITY_STATUSES,
    DATA_FRESHNESS_STATUSES,
)


class InstrumentRegistry:
    """Immutable, append-only store of observations with two time axes."""

    def __init__(self, observations: Sequence[Observation]) -> None:
        rows = []
        for item in observations:
            if not isinstance(item, Observation):
                raise ValueError("registry requires Observation records")
            _validate_observation(item)
            rows.append(item)
        self._observations = tuple(sorted(rows, key=_order_key))
        self._ids = tuple(
            sorted({row.canonical_instrument_id for row in self._observations})
        )

    @property
    def observations(self) -> tuple[Observation, ...]:
        """Return the append-only observation rows in deterministic order."""
        return self._observations

    @property
    def canonical_ids(self) -> tuple[str, ...]:
        """Return every canonical identifier present in the registry."""
        return self._ids

    def as_of(
        self,
        market_date: str,
        observed_at: str,
        *,
        proven_valid_only: bool = False,
    ) -> dict[str, InstrumentState]:
        """Return states valid on a date and known by an observation time.

        Unknown intervals carry UNKNOWN_INTERVAL and are never proven-valid;
        proven_valid_only excludes them.
        """
        day = _required_date(market_date, "market_date")
        moment = _timestamp(observed_at)
        selected: dict[str, dict[str, list[Observation]]] = {}
        for row in self._observations:
            if _knowledge_time(row) > moment:
                continue
            validity = validity_status(row, day)
            if validity is None or (
                proven_valid_only and validity == UNKNOWN_INTERVAL
            ):
                continue
            bucket = selected.setdefault(row.canonical_instrument_id, {})
            bucket.setdefault(row.attribute_name, []).append(row)
        states: dict[str, InstrumentState] = {}
        for identity, bucket in selected.items():
            winners: dict[str, Observation] = {}
            conflicts: dict[str, tuple[str, ...]] = {}
            for name, rows in bucket.items():
                winner, identifiers = _resolve_attribute_rows(rows)
                winners[name] = winner
                if identifiers:
                    conflicts[name] = identifiers
            states[identity] = self._state_for(
                identity, day, observed_at, winners, conflicts
            )
        return states

    def resolve(
        self,
        identifier: str,
        market_date: str,
        observed_at: str | None = None,
    ) -> InstrumentState | None:
        """Resolve an ID or alias at a market date and observation time.

        Both axes are independent; a lone timestamp is the legacy wrapper.
        """
        if observed_at is None:
            moment = _timestamp(market_date)
            return self.resolve(
                identifier, moment.date().isoformat(), market_date
            )
        day = _required_date(market_date, "market_date")
        _timestamp(observed_at)
        if identifier in self._ids:
            return self.as_of(day, observed_at).get(identifier)
        resolution = self.resolve_alias(identifier, day, observed_at)
        if resolution.canonical_instrument_id is None:
            return None
        return self.as_of(day, observed_at).get(
            resolution.canonical_instrument_id
        )

    def canonical_id_for(
        self,
        identifier: str,
        *,
        as_of: str | None = None,
        market_date: str | None = None,
        observed_at: str | None = None,
    ) -> str | None:
        """Map an ID or alias to a canonical ID using both explicit axes."""
        if identifier in self._ids:
            return identifier
        if market_date is None and as_of is None:
            claimants = {
                row.canonical_instrument_id
                for row in self._alias_rows(identifier, None)
            }
            return next(iter(claimants)) if len(claimants) == 1 else None
        if as_of is not None and market_date is None:
            moment = _timestamp(as_of)
            market_date = moment.date().isoformat()
            observed_at = as_of
        return self.resolve_alias(
            identifier, market_date or "", observed_at
        ).canonical_instrument_id

    def resolve_alias(
        self,
        identifier: str,
        market_date: str,
        observed_at: str | None = None,
    ) -> AliasResolution:
        """Resolve an alias with explicit status, validity and claimants."""
        day = _required_date(market_date, "market_date")
        moment = _timestamp(observed_at) if observed_at is not None else None
        rows = self._alias_rows(identifier, moment)
        claimants = tuple(sorted({row.canonical_instrument_id for row in rows}))
        if not rows:
            return AliasResolution(
                identifier, NOT_FOUND, None, UNKNOWN_INTERVAL, claimants
            )
        covering = [
            (row, validity_status(row, day))
            for row in rows
            if validity_status(row, day) is not None
        ]
        if not covering:
            ended = any(
                row.valid_to is not None and day >= row.valid_to for row in rows
            )
            status = EXPIRED if ended else NOT_YET_VALID
            return AliasResolution(
                identifier, status, None, UNKNOWN_INTERVAL, claimants
            )
        covered = tuple(
            sorted({row.canonical_instrument_id for row, _ in covering})
        )
        if len(covered) > 1:
            return AliasResolution(
                identifier, AMBIGUOUS, None, UNKNOWN_INTERVAL, covered
            )
        if len(claimants) > 1:
            return AliasResolution(
                identifier, TOKEN_REUSE, None, UNKNOWN_INTERVAL, claimants
            )
        states = [validity for _, validity in covering]
        validity = max(states, key=VALIDITY_STATES.index)
        return AliasResolution(
            identifier, RESOLVED, covered[0], validity, claimants
        )

    def detect_token_reuse(
        self, *, observed_at: str | None = None
    ) -> tuple[str, ...]:
        """List aliases claimed by more than one canonical ID."""
        moment = _timestamp(observed_at) if observed_at is not None else None
        claims: dict[str, set[str]] = {}
        for row in self._observations:
            if moment is not None and _knowledge_time(row) > moment:
                continue
            parsed = split_provider_attribute(row.attribute_name)
            if parsed is None or parsed[1] != PROVIDER_KEY_ATTRIBUTE:
                continue
            alias = str(row.attribute_value)
            if ":" not in alias:
                alias = provider_alias(parsed[0], alias)
            claims.setdefault(alias, set()).add(row.canonical_instrument_id)
        return tuple(
            sorted(alias for alias, ids in claims.items() if len(ids) > 1)
        )

    def _alias_rows(
        self, identifier: str, moment: datetime | None
    ) -> list[Observation]:
        """Return known alias rows matching an ID, key or qualified alias."""
        rows = []
        for row in self._observations:
            if moment is not None and _knowledge_time(row) > moment:
                continue
            parsed = split_provider_attribute(row.attribute_name)
            if parsed is None or parsed[1] != PROVIDER_KEY_ATTRIBUTE:
                continue
            key = str(row.attribute_value)
            if identifier in (key, provider_alias(parsed[0], key)):
                rows.append(row)
        return rows

    def provider_coverage(
        self,
        instrument_id: str | None = None,
        *,
        market_date: str | None = None,
        observed_at: str | None = None,
    ) -> str | dict[str, str]:
        """Return coverage for one ID, or a mapping for all IDs."""
        if instrument_id is None:
            return {
                identity: self.provider_coverage(
                    identity, market_date=market_date, observed_at=observed_at
                )
                for identity in self._ids
            }
        if observed_at is None:
            known = [
                row.ingested_at
                for row in self._observations
                if row.canonical_instrument_id == instrument_id
            ]
            if not known:
                return UNKNOWN
            observed_at = max(known, key=_timestamp)
        day = market_date or _timestamp(observed_at).date().isoformat()
        state = self.as_of(day, observed_at).get(instrument_id)
        return state.provider_coverage if state is not None else UNKNOWN

    def _state_for(
        self,
        identity: str,
        day: str,
        observed_at: str,
        bucket: Mapping[str, Observation],
        conflicts: Mapping[str, tuple[str, ...]],
    ) -> InstrumentState:
        """Build one state from the observations selected for a query."""
        values = {name: row.attribute_value for name, row in bucket.items()}
        validities = {
            name: validity_status(row, day) or UNKNOWN_INTERVAL
            for name, row in bucket.items()
        }
        validity = max(
            validities.values(),
            key=VALIDITY_STATES.index,
            default=KNOWN_INTERVAL,
        )
        effective_from = _state_date(values.get(ATTR_EFFECTIVE_FROM))
        effective_to = _state_date(values.get(ATTR_EFFECTIVE_TO))
        if (
            effective_from is not None
            and effective_to is not None
            and effective_from >= effective_to
        ):
            validity = INVALID_INTERVAL
        providers = set()
        provider_keys = []
        for name, row in bucket.items():
            parsed = split_provider_attribute(name)
            if parsed is None:
                continue
            provider, attribute = parsed
            providers.add(provider)
            if attribute == PROVIDER_KEY_ATTRIBUTE:
                provider_keys.append(str(row.attribute_value))
        coverage = (
            MULTI_SOURCE
            if len(providers) > 1
            else SINGLE_SOURCE if providers else UNKNOWN
        )
        conformance = _state_conformance(values.get(ATTR_CONFORMANCE_STATUS))
        if conformance is None:
            conformance = conformance_from_observations(bucket, coverage)
        if conflicts:
            conformance = CONFLICT
        latest = max(bucket.values(), key=_order_key)
        provider_trading = _state_status(
            values.get(ATTR_PROVIDER_TRADING_STATUS)
        )
        exchange = _state_status(values.get(ATTR_EXCHANGE_STATUS))
        eligibility = _state_status(values.get(ATTR_EXCHANGE_ELIGIBILITY))
        freshness = _state_status(values.get(ATTR_DATA_FRESHNESS))
        identity_status = _state_status(values.get(ATTR_IDENTITY_STATUS))
        quant = fail_closed_eligibility(
            identity_status=identity_status,
            provider_coverage=coverage,
            conformance_status=conformance,
            provider_trading_status=provider_trading,
            exchange_status=exchange,
            exchange_eligibility=eligibility,
            data_freshness=freshness,
            validity=validity,
        )
        attributes = dict(values)
        if conflicts:
            attributes[CONFLICT_EVIDENCE_KEY] = MappingProxyType(
                dict(conflicts)
            )
        return InstrumentState(
            canonical_instrument_id=identity,
            market_date=day,
            observed_at=observed_at,
            effective_from=effective_from,
            effective_to=effective_to,
            provider_coverage=coverage,
            identity_status=identity_status,
            conformance_status=conformance,
            provider_trading_status=provider_trading,
            exchange_status=exchange,
            exchange_eligibility=eligibility,
            data_freshness=freshness,
            quant_eligibility=quant,
            provider_keys=tuple(sorted(provider_keys)),
            attributes=MappingProxyType(attributes),
            validity=validity,
            attribute_validity=MappingProxyType(validities),
            source_digest=latest.source_digest,
            source_version=latest.source_version,
            registry_version=latest.registry_version,
        )


def sha256_hex(payload: bytes) -> str:
    """Return the SHA-256 digest of raw bytes."""
    return hashlib.sha256(payload).hexdigest()


def _decode_json(payload: bytes) -> object:
    """Decode UTF-8 JSON with strict duplicate-key handling."""
    return json.loads(
        payload.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
    )


def _verify_digest(payload: bytes, expected: str) -> None:
    """Fail closed when supplied bytes do not match the provenance digest."""
    if sha256_hex(payload) != expected:
        raise ValueError("snapshot digest does not match provenance")


_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _date(value: object, label: str) -> str | None:
    """Validate an ISO calendar date, treating empty or None as unknown."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    return value


def _timestamp(value: object) -> datetime:
    """Parse a timezone-aware ISO timestamp, failing closed otherwise."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid timestamp: {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    if moment.tzinfo is None:
        raise ValueError(f"timestamp requires a UTC offset: {value!r}")
    return moment.astimezone(UTC)


def _required_date(value: object, label: str) -> str:
    """Require a real ISO date rather than an inferred or empty value."""
    if (day := _date(value, label)) is None:
        raise ValueError(f"{label} is required")
    return day


def validity_status(row: Observation, day: str) -> str | None:
    """Assess an observation on a market date; None means not applicable."""
    if row.valid_from is None:
        if row.valid_to is not None and day >= row.valid_to:
            return None
        return UNKNOWN_INTERVAL
    if day < row.valid_from:
        return None
    if row.valid_to is not None and day >= row.valid_to:
        return None
    return OPEN_ENDED if row.valid_to is None else KNOWN_INTERVAL


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def _validate_observation(row: Observation) -> None:
    """Reject malformed identity, intervals, timestamps or provenance."""
    required = (
        ("canonical instrument ID", row.canonical_instrument_id),
        ("attribute name", row.attribute_name),
        ("source_version", row.source_version),
        ("registry_version", row.registry_version),
    )
    for label, value in required:
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid {label}: {value!r}")
    start = _date(row.valid_from, "valid_from")
    end = _date(row.valid_to, "valid_to")
    if start is not None and end is not None and start >= end:
        raise ValueError("valid_from must precede valid_to")
    _timestamp(row.observed_at)
    _timestamp(row.ingested_at)
    if row.provider_updated_at not in (None, ""):
        try:
            _timestamp(row.provider_updated_at)
        except ValueError:
            _date(row.provider_updated_at, "provider_updated_at")
    if not isinstance(row.source_digest, str) or not _DIGEST_RE.fullmatch(
        row.source_digest
    ):
        raise ValueError(f"invalid source_digest: {row.source_digest!r}")
    parsed = split_provider_attribute(row.attribute_name)
    if parsed is not None and parsed[1] == PROVIDER_KEY_ATTRIBUTE:
        if not isinstance(row.attribute_value, str) or not row.attribute_value:
            raise ValueError("provider key observation requires a key")


def provider_attribute(provider: str, attribute: str) -> str:
    """Return the provider-scoped observation attribute name."""
    if not provider or ":" in provider or not attribute:
        raise ValueError("provider and attribute must be non-empty tokens")
    return f"{PROVIDER_PREFIX}{provider}:{attribute}"


def split_provider_attribute(name: str) -> tuple[str, str] | None:
    """Return the provider and attribute of a provider-scoped name."""
    if not isinstance(name, str) or not name.startswith(PROVIDER_PREFIX):
        return None
    parts = name[len(PROVIDER_PREFIX) :].split(":", 1)
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def provider_alias(provider: str, key: str) -> str:
    """Return the stable alias token used for provider-key resolution."""
    if not provider or ":" in provider or not key:
        raise ValueError("provider and key must be non-empty tokens")
    return f"{provider}:{key}"


def observation(
    canonical_instrument_id: str,
    attribute_name: str,
    attribute_value: object,
    *,
    observed_at: str,
    ingested_at: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    provider_updated_at: str | None = None,
    source_digest: str = "",
    source_version: str = SCHEMA_VERSION,
    registry_version: str = REGISTRY_VERSION,
) -> Observation:
    """Build one observation with explicit validity and observation times."""
    row = Observation(
        canonical_instrument_id,
        attribute_name,
        attribute_value,
        valid_from,
        valid_to,
        observed_at,
        ingested_at or observed_at,
        provider_updated_at,
        source_digest,
        source_version,
        registry_version,
    )
    _validate_observation(row)
    return row


def _order_key(row: Observation) -> tuple:
    """Order rows by observation time, then ingestion time and digest."""
    return (
        _timestamp(row.observed_at),
        _timestamp(row.ingested_at),
        row.provider_updated_at or "",
        row.source_digest,
    )


def _value_key(value: object) -> str:
    """Return a comparable representation of an observation value."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def observation_id(row: Observation) -> str:
    """Return a stable identifier for an observation's evidence.

    The identifier is derived from the row's content, never from its position
    in an input sequence, so tied observations can be cited deterministically.
    """
    payload = json.dumps(
        (
            row.canonical_instrument_id,
            row.attribute_name,
            row.attribute_value,
            row.valid_from,
            row.valid_to,
            row.observed_at,
            row.ingested_at,
            row.provider_updated_at,
            row.source_digest,
            row.source_version,
            row.registry_version,
        ),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256_hex(payload)[:16]


def _resolve_attribute_rows(
    rows: Sequence[Observation],
) -> tuple[Observation, tuple[str, ...]]:
    """Select the latest rows and report value conflicts among ties.

    Only rows with equivalent precedence can conflict. Ties are broken by a
    content-derived identifier rather than input order, and every tied
    identifier is returned as evidence when the values disagree.
    """
    ordered = sorted(
        rows, key=lambda row: (_order_key(row), observation_id(row))
    )
    latest = _order_key(ordered[-1])
    tied = [row for row in ordered if _order_key(row) == latest]
    if len({_value_key(row.attribute_value) for row in tied}) == 1:
        return ordered[-1], ()
    identifiers = tuple(sorted({observation_id(row) for row in tied}))
    return ordered[-1], identifiers


def _knowledge_time(row: Observation) -> datetime:
    """Return the first time QUANT could know this observation."""
    return max(_timestamp(row.observed_at), _timestamp(row.ingested_at))


def _state_status(value: object, allowed: Sequence[str] = ()) -> str:
    """Return a known status token, otherwise the fail-closed UNKNOWN."""
    if isinstance(value, str) and (not allowed or value in allowed):
        return value
    return UNKNOWN


def _state_conformance(value: object) -> str | None:
    """Return an explicit conformance token or None when unattested."""
    if isinstance(value, str) and value in (*CONFORMANCE_STATUSES, UNKNOWN):
        return value
    return None


def _state_date(value: object) -> str | None:
    """Return an explicitly attested effective date, never an inferred one."""
    return _date(value, "effective date")


def conformance_from_observations(
    bucket: Mapping[str, Observation], coverage: str
) -> str:
    """Evaluate comparable fields from provider-scoped observations."""
    providers = {
        parsed[0]
        for name in bucket
        if (parsed := split_provider_attribute(name)) is not None
    }
    if not providers:
        return UNKNOWN
    conflict = False
    incomplete = coverage == SINGLE_SOURCE
    for field in CONFORMANCE_FIELDS:
        seen: list[tuple[str, object]] = []
        for name, row in bucket.items():
            parsed = split_provider_attribute(name)
            if parsed is None or parsed[1] != field:
                continue
            value = row.attribute_value
            if (
                field == "tick_size"
                and parsed[0] == PROVIDER_UPSTOX
                and value is not None
            ):
                value = float(value) / 100
            seen.append((parsed[0], value))
        if not seen:
            continue
        present = [
            value for _, value in seen if value is not None and value != ""
        ]
        if len(seen) < len(providers) or len(present) < len(providers):
            incomplete = True
        if present and any(
            not _values_agree(present[0], value) for value in present[1:]
        ):
            conflict = True
    if conflict:
        return CONFLICT
    if incomplete:
        return INCOMPLETE
    return PASS


def fail_closed_eligibility(
    *,
    identity_status: str,
    provider_coverage: str,
    conformance_status: str,
    provider_trading_status: str,
    exchange_status: str,
    exchange_eligibility: str,
    data_freshness: str,
    validity: str,
) -> str:
    """Grant eligibility only when every independent dimension is affirmed.

    Temporal validity is a gate of its own. KNOWN_INTERVAL may proceed.
    OPEN_ENDED has a known start but no end, so it may proceed only while the
    independent FRESH data gate still holds. UNKNOWN_INTERVAL and
    INVALID_INTERVAL never pass; malformed intervals are rejected before an
    observation enters the registry. Conflicts and unknown gates fail closed
    through the other dimensions.
    """
    if validity not in PROVEN_VALIDITY_STATES:
        return NOT_ELIGIBLE
    gates = (
        (identity_status, VERIFIED),
        (provider_coverage, MULTI_SOURCE),
        (conformance_status, PASS),
        (provider_trading_status, ACTIVE),
        (exchange_status, OPEN),
        (exchange_eligibility, ALLOWED),
        (data_freshness, FRESH),
    )
    return (
        ELIGIBLE
        if all(actual == required for actual, required in gates)
        else NOT_ELIGIBLE
    )


def build_registry(observations: Sequence[Observation]) -> InstrumentRegistry:
    """Build an immutable bitemporal registry from append-only rows."""
    return InstrumentRegistry(observations)


def _as_of(
    registry: InstrumentRegistry, market_date: str, observed_at: str
) -> dict[str, InstrumentState]:
    """Module-level form of the registry as-of query."""
    return registry.as_of(market_date, observed_at)


def resolve(
    registry: InstrumentRegistry,
    identifier: str,
    market_date: str,
    observed_at: str | None = None,
) -> InstrumentState | None:
    """Module-level two-axis resolution, with a legacy timestamp form."""
    return registry.resolve(identifier, market_date, observed_at)


def _provider_coverage(
    registry: InstrumentRegistry,
    instrument_id: str | None = None,
    *,
    market_date: str | None = None,
    observed_at: str | None = None,
) -> str | dict[str, str]:
    """Module-level form of the provider-coverage dimension."""
    return registry.provider_coverage(
        instrument_id, market_date=market_date, observed_at=observed_at
    )


_PUBLIC_QUERIES = {
    "as_of": _as_of,
    "provider_coverage": _provider_coverage,
}


def __getattr__(name: str) -> object:
    """Expose the module-level query aliases without shadowing parameters."""
    try:
        return _PUBLIC_QUERIES[name]
    except KeyError as exc:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from exc


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
    provider: str,
    key: str,
    reason: str,
    context: SnapshotContext,
    instrument_class: str = UNKNOWN,
) -> QuarantineEntry:
    """Build a quarantine entry for a withheld provider record."""
    return QuarantineEntry(
        provider,
        key,
        reason,
        context.observed_at_utc,
        context.source_digest,
        instrument_class,
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
        _quarantine(
            record.provider,
            record.provider_key,
            reason,
            context,
            record.instrument_class,
        )
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
                _quarantine(
                    PROVIDER_UPSTOX,
                    "",
                    MISSING_PROVIDER_KEY,
                    context,
                    record.instrument_class,
                )
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
                _quarantine(
                    PROVIDER_FYERS,
                    "",
                    MISSING_PROVIDER_KEY,
                    context,
                    record.instrument_class,
                )
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


def _field_conformance(
    field: str, group: Sequence[ProviderInstrument]
) -> FieldConformance:
    """Evaluate a field, preserving missing and differing values."""
    ordered = sorted(group, key=lambda item: item.provider)
    observed = tuple(
        (item.provider, _conformance_value(item, field)) for item in ordered
    )
    missing = tuple(
        provider for provider, value in observed if value is None or value == ""
    )
    present = [
        value for _, value in observed if value is not None and value != ""
    ]
    status = PASS
    if present and any(
        not _values_agree(present[0], value) for value in present[1:]
    ):
        status = CONFLICT
    if status == PASS and missing:
        status = INCOMPLETE
    return FieldConformance(field, status, observed, missing)


def contract_conformance(
    group: Sequence[ProviderInstrument],
) -> ConformanceReport:
    """Evaluate comparable fields with an independent coverage dimension."""
    fields = tuple(
        _field_conformance(field, group) for field in CONFORMANCE_FIELDS
    )
    statuses = {item.status for item in fields}
    providers = {item.provider for item in group}
    coverage = (
        MULTI_SOURCE
        if len(providers) > 1
        else SINGLE_SOURCE if providers else UNKNOWN
    )
    if CONFLICT in statuses:
        status = CONFLICT
    elif INCOMPLETE in statuses or coverage != MULTI_SOURCE:
        status = INCOMPLETE
    else:
        status = PASS
    return ConformanceReport(status, fields, coverage)


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
    conformance: ConformanceReport,
    suspended: bool = False,
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
        identity_status, conformance.status, trading_status
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
        # Legacy crosswalk field only: this is the snapshot observation date,
        # not an asserted contract validity start. The bitemporal registry
        # keeps unknown effective dates unknown instead of inferring them.
        effective_from=min(item.market_date for item in group),
        effective_to=None,
        first_observed_at=min(item.observed_at_utc for item in group),
        last_observed_at=max(item.observed_at_utc for item in group),
        trading_status=trading_status,
        crosswalk_status=identity_status,
        conformance_status=conformance.status,
        eligibility_status=eligibility,
        source_digest=_merged_digest(group),
        source_version=SCHEMA_VERSION,
        conformance_report=conformance,
        provider_coverage=conformance.provider_coverage,
        provider_trading_status=trading_status,
        exchange_status=SUSPENDED if suspended else UNKNOWN,
        exchange_eligibility=UNKNOWN,
        data_freshness=UNKNOWN,
        quant_eligibility=eligibility,
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
    instrument_class = group[0].instrument_class if group else UNKNOWN
    for item in group:
        if item.provider in seen:
            entries.append(
                QuarantineEntry(
                    "crosswalk",
                    identity,
                    IDENTITY_COLLISION,
                    min(row.observed_at_utc for row in group),
                    _merged_digest(group),
                    instrument_class,
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
    coverage_counts = {SINGLE_SOURCE: 0, MULTI_SOURCE: 0}
    for identity in sorted(groups):
        group = groups[identity]
        collisions = _collision_entries(identity, group)
        quarantine.extend(collisions)
        identity_status = AMBIGUOUS if collisions else _identity_status(group)
        trading_status = _combined_status(group, identity, suspended)
        conformance = contract_conformance(group)
        instruments.append(
            _build_canonical(
                identity,
                group,
                identity_status,
                trading_status,
                conformance,
                identity in suspended,
            )
        )
        counts[identity_status] += 1
        conformance_counts[conformance.status] += 1
        trading_counts[trading_status] += 1
        coverage_counts[conformance.provider_coverage] += 1
        if trading_status in counts:
            counts[trading_status] += 1
    return CrosswalkReport(
        tuple(instruments),
        tuple(quarantine),
        counts,
        conformance_counts,
        trading_counts,
        coverage_counts,
    )


def quarantine_report(
    entries: Sequence[QuarantineEntry],
) -> QuarantineReport:
    """Count withheld records by reason, provider and instrument class.

    Quarantine records are withheld for identity or contract reasons such as
    unsupported segments, missing ISINs or collisions. They are not a claim
    that snapshot bytes are corrupt or that a provider is dishonest.
    """
    by_reason: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    by_class: dict[str, int] = {}
    nested: dict[str, dict[str, dict[str, int]]] = {}
    for entry in entries:
        by_reason[entry.reason] = by_reason.get(entry.reason, 0) + 1
        by_provider[entry.provider] = by_provider.get(entry.provider, 0) + 1
        by_class[entry.instrument_class] = (
            by_class.get(entry.instrument_class, 0) + 1
        )
        providers = nested.setdefault(entry.reason, {})
        classes = providers.setdefault(entry.provider, {})
        classes[entry.instrument_class] = (
            classes.get(entry.instrument_class, 0) + 1
        )
    return QuarantineReport(
        len(entries),
        dict(sorted(by_reason.items())),
        dict(sorted(by_provider.items())),
        dict(sorted(by_class.items())),
        {
            reason: {
                provider: dict(sorted(class_counts.items()))
                for provider, class_counts in sorted(providers.items())
            }
            for reason, providers in sorted(nested.items())
        },
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
        if not isinstance(entry, dict) or not SNAPSHOT_FIELDS <= set(entry):
            raise ValueError("unexpected snapshot provenance fields")
        unknown = set(entry) - (SNAPSHOT_FIELDS | EXTENDED_PROVENANCE_FIELDS)
        if unknown:
            raise ValueError(
                f"unknown snapshot provenance fields: "
                f"{', '.join(sorted(unknown))}"
            )
        extra = set(entry) & EXTENDED_PROVENANCE_FIELDS
        if extra and not EXTENDED_PROVENANCE_FIELDS <= set(entry):
            raise ValueError("incomplete extended provenance fields")
        if extra and entry.get("source_class") != "immutable-reference-data":
            raise ValueError("unsupported provenance source_class")
        for key in ("reference_original_path", "reference_decoded_path"):
            if key in entry:
                value = entry[key]
                if (
                    not isinstance(value, str)
                    or Path(value).is_absolute()
                    or ".." in Path(value).parts
                    or not value.startswith("reference-data/")
                    or not value.endswith(".gz")
                ):
                    raise ValueError(f"unsafe provenance path: {key}")
        meta = SnapshotMeta(
            **{key: entry[key] for key in sorted(SNAPSHOT_FIELDS)}
        )
        if meta.provider in metas:
            raise ValueError(f"duplicate provider: {meta.provider}")
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


def observations_from_snapshots(
    provenance: Path | None = None,
    reference_root: Path | None = None,
    payloads: Mapping[str, bytes] | None = None,
) -> tuple[Observation, ...]:
    """Convert pinned snapshots to append-only observations.

    market_date is recorded as observation evidence only. No validity bound
    is inferred from it: effective dates stay unknown until a source asserts
    them.
    """
    root = Path(__file__).resolve().parent.parent
    provenance = provenance or (
        root / "governance" / "instrument-master-snapshots.json"
    )
    metas = load_provenance(provenance)
    rows: list[Observation] = []
    for provider, meta in sorted(metas.items()):
        context = snapshot_context(meta)
        if payloads is None:
            sys.path.insert(0, str(root / "scripts"))
            fetcher = importlib.import_module("fetch_instrument_snapshots")
            reference = reference_root or (
                root / "reference-data" / "instrument-master"
            )
            payload = fetcher.decoded_payload(
                meta, reference, provenance, reference=True
            )
        else:
            payload = payloads[provider]
        parser = parse_upstox if provider == PROVIDER_UPSTOX else parse_fyers
        for record in parser(payload, context).records:
            identity = canonical_id(record)
            if identity is None:
                continue
            fields = {
                provider_attribute(
                    provider, PROVIDER_KEY_ATTRIBUTE
                ): provider_alias(provider, record.provider_key),
                provider_attribute(provider, "market_date"): record.market_date,
                ATTR_EFFECTIVE_FROM: None,
                ATTR_EFFECTIVE_TO: None,
            }
            for name, value in fields.items():
                rows.append(
                    observation(
                        identity,
                        name,
                        value,
                        observed_at=context.observed_at_utc,
                        provider_updated_at=record.provider_update_timestamp,
                        source_digest=context.source_digest,
                        source_version=context.source_version,
                    )
                )
    return tuple(rows)
