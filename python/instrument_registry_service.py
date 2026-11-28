"""Canonical instrument identity service for offline research consumers.

This module wraps the W1 bitemporal registry in :mod:`instrument_master` and
offers one query: ``resolve_for(provider, identifier, market_date,
observed_at)``. A canonical ID is returned only when the provider alias is
established on the requested market date, was known by the requested
observation time, and the selected state is proven valid and fresh. Everything
else fails closed: unknown or invalid intervals, expired and not-yet-valid
aliases, ambiguous claimants, token reuse, stale or unattested data.

The registry is supplied by dependency injection for offline tests. The
default loader builds a registry from the pinned snapshots only; it never
contacts a provider. Results are cached under the complete query key,
including both time axes, so a different market date or observation time can
never inherit an earlier resolution.

C++/q and journal integration is FUTURE WORK. This is a Python-only consumer
of the offline W1 registry; it claims no live qualification, no exchange
status source and no authoritative accounting, risk or execution role.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, Protocol

RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"
UNKNOWN_INTERVAL = "UNKNOWN_INTERVAL"
KNOWN_INTERVAL = "KNOWN_INTERVAL"
OPEN_ENDED = "OPEN_ENDED"
ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
FRESH = "FRESH"
UNKNOWN = "UNKNOWN"
INVALID_QUERY = "INVALID_QUERY"
PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
NO_VALID_STATE = "NO_VALID_STATE"
STALE_DATA = "STALE_DATA"
PROVEN_VALIDITY = (KNOWN_INTERVAL, OPEN_ENDED)

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Resolution(NamedTuple):
    """Fail-closed outcome of one canonical identity query."""

    status: str
    canonical_instrument_id: str | None
    validity: str
    eligibility: str
    reasons: tuple[str, ...] = ()

    @property
    def established(self) -> bool:
        """Return True only for an established, fresh canonical identity."""
        return (
            self.status == RESOLVED
            and self.canonical_instrument_id is not None
            and self.validity in PROVEN_VALIDITY
        )


class RegistryQuery(Protocol):
    """Minimal bitemporal query surface required from an injected registry."""

    def resolve_alias(
        self, identifier: str, market_date: str, observed_at: str
    ) -> object:
        """Return the alias resolution at both requested time axes."""

    def as_of(self, market_date: str, observed_at: str) -> Mapping[str, object]:
        """Return states valid on a date and known by an observation time."""


def _master_module():
    """Return the offline instrument master module without network access."""
    for name in ("instrument_master", "quant_instrument_master"):
        module = sys.modules.get(name)
        if module is not None:
            return module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return importlib.import_module("instrument_master")


def _default_registry() -> RegistryQuery:
    """Build a registry from pinned snapshots; no live source is consulted."""
    master = _master_module()
    return master.build_registry(master.observations_from_snapshots())


def _text(value: object) -> str:
    """Return a stripped string for a string value, otherwise empty."""
    return value.strip() if isinstance(value, str) else ""


def _field(value: object, name: str, default: object) -> object:
    """Read a field from a mapping or an attribute-bearing result."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _valid_query(
    provider: str, identifier: str, market_date: str, observed_at: str
) -> bool:
    """Reject malformed queries before they reach the registry or cache."""
    return bool(
        isinstance(provider, str)
        and provider
        and ":" not in provider
        and isinstance(identifier, str)
        and identifier
        and isinstance(market_date, str)
        and _DATE_RE.fullmatch(market_date)
        and isinstance(observed_at, str)
        and observed_at
    )


def _alias(provider: str, identifier: str) -> str | None:
    """Qualify a provider key, refusing a token owned by another provider."""
    if ":" not in identifier:
        return f"{provider}:{identifier}"
    if identifier.startswith(f"{provider}:"):
        return identifier
    return None


def _failure(status: str, reason: str, eligibility: str = NOT_ELIGIBLE):
    """Build one fail-closed resolution with no canonical ID."""
    return Resolution(status, None, UNKNOWN_INTERVAL, eligibility, (reason,))


def _claim_reason(
    status: str, identity: str, claimants: object, validity: str
) -> str:
    """Return the first reason a provider claim cannot be accepted."""
    if status != RESOLVED:
        return status
    if not identity:
        return "NO_CANONICAL_ID"
    if isinstance(claimants, Sequence) and len(claimants) > 1:
        return "AMBIGUOUS_CLAIM"
    if validity not in PROVEN_VALIDITY:
        return validity
    return ""


class InstrumentRegistryService:
    """Small canonical identity service over the offline W1 registry."""

    def __init__(
        self,
        registry: RegistryQuery | None = None,
        *,
        loader: Callable[[], RegistryQuery] | None = None,
    ) -> None:
        self._registry = registry
        self._loader = loader or _default_registry
        self._cache: dict[tuple[str, str, str, str], Resolution] = {}

    @property
    def cache_size(self) -> int:
        """Return the number of distinct full-query keys held in the cache."""
        return len(self._cache)

    def clear_cache(self) -> None:
        """Forget all cached resolutions."""
        self._cache.clear()

    def for_provider(
        self, provider: str
    ) -> Callable[[str, str, str], Resolution]:
        """Return a resolver bound to one provider for research consumers."""
        return lambda identifier, market_date, observed_at: self.resolve_for(
            provider, identifier, market_date, observed_at
        )

    def resolve_for(
        self,
        provider: str,
        identifier: str,
        market_date: str,
        observed_at: str,
    ) -> Resolution:
        """Resolve one provider identifier at both requested time axes."""
        key = (provider, identifier, market_date, observed_at)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._resolve_uncached(
            provider, identifier, market_date, observed_at
        )
        self._cache[key] = result
        return result

    def _registry_or_load(self) -> RegistryQuery:
        """Return the injected registry, loading pinned bytes only once."""
        if self._registry is None:
            self._registry = self._loader()
        return self._registry

    def _resolve_uncached(
        self,
        provider: str,
        identifier: str,
        market_date: str,
        observed_at: str,
    ) -> Resolution:
        """Perform the fail-closed resolution for a cache miss."""
        if not _valid_query(provider, identifier, market_date, observed_at):
            return _failure(UNRESOLVED, INVALID_QUERY)
        alias = _alias(provider, identifier)
        if alias is None:
            return _failure(UNRESOLVED, PROVIDER_MISMATCH)
        try:
            return self._query_alias(alias, market_date, observed_at)
        except (LookupError, TypeError, ValueError, AttributeError):
            return _failure(UNRESOLVED, INVALID_QUERY)

    def _query_alias(
        self, alias: str, market_date: str, observed_at: str
    ) -> Resolution:
        """Resolve an alias and validate its state against both time axes."""
        registry = self._registry_or_load()
        claim = registry.resolve_alias(alias, market_date, observed_at)
        status = _text(_field(claim, "status", UNRESOLVED)) or UNRESOLVED
        identity = _text(_field(claim, "canonical_instrument_id", ""))
        claimants = _field(claim, "claimants", ())
        validity = _text(_field(claim, "validity", UNKNOWN_INTERVAL))
        reason = _claim_reason(status, identity, claimants, validity)
        if reason:
            return _failure(reason, reason)
        state = registry.as_of(market_date, observed_at).get(identity)
        if state is None:
            return _failure(NO_VALID_STATE, NO_VALID_STATE)
        state_validity = _text(_field(state, "validity", UNKNOWN_INTERVAL))
        freshness = _text(_field(state, "data_freshness", UNKNOWN))
        eligibility = (
            _text(_field(state, "quant_eligibility", NOT_ELIGIBLE))
            or NOT_ELIGIBLE
        )
        if state_validity not in PROVEN_VALIDITY:
            return _failure(UNKNOWN_INTERVAL, state_validity, eligibility)
        if freshness != FRESH:
            return _failure(STALE_DATA, freshness, eligibility)
        return Resolution(RESOLVED, identity, state_validity, eligibility)


class IdentityRecord(NamedTuple):
    """Normalised identity from an injected consumer resolver result."""

    canonical_instrument_id: str | None
    status: str
    eligibility: str


def identity_from_result(result: object, market_date: str) -> IdentityRecord:
    """Accept a resolver result only with an established validity interval.

    Both mappings and attribute-bearing objects are accepted so research and
    data consumers can inject lightweight offline stubs. A bare string, an
    unknown interval or a failed status never yields a canonical ID.
    """
    if result is None:
        return IdentityRecord(None, UNRESOLVED, NOT_ELIGIBLE)
    status = _text(_field(result, "status", "")) or UNRESOLVED
    validity = _text(_field(result, "validity", "")) or UNKNOWN_INTERVAL
    identity = _text(_field(result, "canonical_instrument_id", ""))
    eligibility = _text(_field(result, "eligibility", "")) or NOT_ELIGIBLE
    refused = IdentityRecord(None, status, eligibility)
    if _field(result, "established", None) is False:
        return refused
    if status != RESOLVED or validity not in PROVEN_VALIDITY or not identity:
        return refused
    start = _field(result, "valid_from", None)
    end = _field(result, "valid_to", None)
    if isinstance(start, str) and market_date < start:
        return refused
    if isinstance(end, str) and market_date >= end:
        return refused
    return IdentityRecord(identity, status, eligibility)


def annotate_record(
    record: Mapping[str, object],
    resolver: Callable[[str, str, str], object],
    identifier: str,
    market_date: str,
    observed_at: str,
) -> dict[str, object]:
    """Return a copy carrying canonical identity metadata alongside symbol."""
    try:
        result = resolver(identifier, market_date, observed_at)
    except (LookupError, TypeError, ValueError, AttributeError):
        result = None
    identity = identity_from_result(result, market_date)
    enriched = dict(record)
    enriched["symbol"] = identifier
    enriched["canonical_instrument_id"] = identity.canonical_instrument_id
    enriched["resolution_status"] = identity.status
    enriched["identity_eligibility"] = identity.eligibility
    return enriched


class HistoryResolution(NamedTuple):
    """Dataset bars after canonical identity filtering."""

    history: dict[str, list[dict[str, object]]]
    canonical_ids: dict[str, str]
    errors: list[str]
    gaps: list[str]


def resolve_history(
    history: Mapping[str, Sequence[Mapping[str, object]]],
    resolver: Callable[[str, str, str], object],
    observed_at: str,
) -> HistoryResolution:
    """Annotate bars by symbol and date; refuse those without validity."""
    resolved: dict[str, list[dict[str, object]]] = {}
    identities: dict[str, str] = {}
    errors: list[str] = []
    gaps: list[str] = []
    for symbol, rows in history.items():
        kept: list[dict[str, object]] = []
        for row in rows:
            market_date = str(row["date"])
            enriched = annotate_record(
                row, resolver, symbol, market_date, observed_at
            )
            identity = enriched["canonical_instrument_id"]
            if identity is None:
                errors.append(
                    f"{symbol}@{market_date}: {enriched['resolution_status']}"
                )
                gaps.append(f"{symbol}@{market_date}: unresolved identity")
                continue
            kept.append(enriched)
            identities[symbol] = str(identity)
        if kept:
            resolved[symbol] = kept
    return HistoryResolution(resolved, identities, errors, gaps)
