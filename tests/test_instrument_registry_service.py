"""Offline tests for the canonical instrument identity service."""

from __future__ import annotations

import sys
from pathlib import Path

from test_loader_support import load_test_module

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
im = load_test_module(
    "instrument_master", ROOT / "python" / "instrument_master.py"
)
service = load_test_module(
    "instrument_registry_service",
    ROOT / "python" / "instrument_registry_service.py",
)

CID = "NSE-CASH-SERVICE-TEST"
OLD_KEY = "TOKEN_OLD"
NEW_KEY = "TOKEN_NEW"
UP_KEY = "NSE_EQ|INE001A01DD1"
FY_KEY = "10100000001"
JAN = "2026-01-01"
FEB = "2026-02-01"
MAR = "2026-03-01"
APR = "2026-04-01"
T0 = "2025-12-31T09:00:00Z"
T1 = "2026-01-16T09:00:00Z"
T2 = "2026-02-16T09:00:00Z"
T3 = "2026-03-16T09:00:00Z"
DAY = "2026-01-15"
NEXT = "2026-02-15"
LATE = "2026-03-15"


def _obs(
    attribute: str,
    value: object,
    *,
    identity: str = CID,
    provider: str = "",
    observed_at: str = T0,
    valid_from: str | None = JAN,
    valid_to: str | None = APR,
    digest: str = "a" * 64,
) -> im.Observation:
    """Build one observation with explicit validity and observation times."""
    name = im.provider_attribute(provider, attribute) if provider else attribute
    return im.observation(
        identity,
        name,
        value,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_to=valid_to,
        source_digest=digest,
    )


def _alias(
    provider: str,
    key: str,
    *,
    identity: str = CID,
    valid_from: str | None = JAN,
    valid_to: str | None = APR,
    observed_at: str = T0,
    digest: str = "b" * 64,
) -> im.Observation:
    """Build one provider-key alias observation with explicit bounds."""
    return _obs(
        im.PROVIDER_KEY_ATTRIBUTE,
        im.provider_alias(provider, key),
        identity=identity,
        provider=provider,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_to=valid_to,
        digest=digest,
    )


def _eligible_rows() -> list[im.Observation]:
    """Return affirming gates from both providers with attested intervals."""
    return [
        _obs(im.ATTR_IDENTITY_STATUS, im.VERIFIED),
        _obs(im.ATTR_CONFORMANCE_STATUS, im.PASS),
        _obs(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE),
        _obs(im.ATTR_EXCHANGE_STATUS, im.OPEN),
        _obs(im.ATTR_EXCHANGE_ELIGIBILITY, im.ALLOWED),
        _obs(im.ATTR_DATA_FRESHNESS, im.FRESH),
        _obs("isin", "INE001A01DD1", provider=im.PROVIDER_UPSTOX),
        _obs("isin", "INE001A01DD1", provider=im.PROVIDER_FYERS),
    ]


def _service(rows: list[im.Observation]) -> service.InstrumentRegistryService:
    """Build a service with an injected offline registry."""
    return service.InstrumentRegistryService(im.build_registry(rows))


def test_historical_symbol_mapping_resolves_on_both_sides() -> None:
    """A renamed symbol keeps one canonical identity across its aliases."""
    rows = _eligible_rows()
    rows.extend(
        [
            _alias(im.PROVIDER_FYERS, OLD_KEY, valid_to=FEB, digest="1" * 64),
            _alias(im.PROVIDER_FYERS, NEW_KEY, valid_from=FEB, digest="2" * 64),
        ]
    )
    registry = _service(rows)
    old = registry.resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    new = registry.resolve_for(im.PROVIDER_FYERS, NEW_KEY, NEXT, T2)
    assert old.status == service.RESOLVED
    assert old.canonical_instrument_id == CID
    assert old.validity == im.KNOWN_INTERVAL
    assert old.eligibility == im.ELIGIBLE
    assert old.established is True
    assert new.canonical_instrument_id == CID
    assert new.status == service.RESOLVED


def test_unknown_validity_is_refused() -> None:
    """An alias without an attested interval never yields a canonical ID."""
    rows = _eligible_rows()
    rows.append(
        _alias(
            im.PROVIDER_FYERS,
            OLD_KEY,
            valid_from=None,
            valid_to=None,
        )
    )
    result = _service(rows).resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    assert result.status == service.UNKNOWN_INTERVAL
    assert result.canonical_instrument_id is None
    assert result.established is False

    unknown_state = _service(
        [
            _alias(im.PROVIDER_FYERS, OLD_KEY),
            _obs(
                im.ATTR_DATA_FRESHNESS, im.FRESH, valid_from=None, valid_to=None
            ),
        ]
    ).resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    assert unknown_state.canonical_instrument_id is None
    assert unknown_state.established is False


def test_expired_and_not_yet_valid_aliases_are_refused() -> None:
    """Alias validity is a market-date bound, not an observation-time bound."""
    rows = _eligible_rows()
    rows.append(_alias(im.PROVIDER_FYERS, OLD_KEY, valid_to=FEB))
    registry = _service(rows)
    expired = registry.resolve_for(im.PROVIDER_FYERS, OLD_KEY, LATE, T3)
    early = registry.resolve_for(im.PROVIDER_FYERS, OLD_KEY, "2025-12-15", T1)
    assert expired.status == im.EXPIRED
    assert expired.canonical_instrument_id is None
    assert early.status == im.NOT_YET_VALID
    assert early.canonical_instrument_id is None
    assert registry.resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1).established


def test_two_providers_agree_on_the_canonical_id() -> None:
    """Distinct provider tokens for the same instrument converge on one ID."""
    rows = _eligible_rows()
    rows.extend(
        [
            _alias(im.PROVIDER_UPSTOX, UP_KEY, digest="1" * 64),
            _alias(im.PROVIDER_FYERS, FY_KEY, digest="2" * 64),
        ]
    )
    registry = _service(rows)
    upstox = registry.resolve_for(im.PROVIDER_UPSTOX, UP_KEY, DAY, T1)
    fyers = registry.resolve_for(im.PROVIDER_FYERS, FY_KEY, DAY, T1)
    assert upstox.canonical_instrument_id == fyers.canonical_instrument_id
    assert upstox.canonical_instrument_id == CID
    assert upstox.established and fyers.established
    qualified = registry.resolve_for(
        im.PROVIDER_FYERS, f"{im.PROVIDER_FYERS}:{FY_KEY}", DAY, T1
    )
    assert qualified.canonical_instrument_id == CID
    mismatch = registry.resolve_for(im.PROVIDER_UPSTOX, FY_KEY, DAY, T1)
    assert mismatch.canonical_instrument_id is None
    assert mismatch.status == im.NOT_FOUND


def test_ambiguous_and_reused_aliases_are_refused() -> None:
    """Overlapping or sequential claimants never get an arbitrary ID."""
    overlap = _eligible_rows()
    overlap.extend(
        [
            _alias(
                im.PROVIDER_FYERS,
                OLD_KEY,
                identity=CID + "-A",
                digest="1" * 64,
            ),
            _alias(
                im.PROVIDER_FYERS,
                OLD_KEY,
                identity=CID + "-B",
                valid_from=FEB,
                digest="2" * 64,
            ),
        ]
    )
    ambiguous = _service(overlap).resolve_for(
        im.PROVIDER_FYERS, OLD_KEY, NEXT, T2
    )
    assert ambiguous.status == im.AMBIGUOUS
    assert ambiguous.canonical_instrument_id is None

    reused_rows = _eligible_rows()
    reused_rows.extend(
        [
            _alias(
                im.PROVIDER_FYERS,
                NEW_KEY,
                identity=CID + "-A",
                valid_to=FEB,
                digest="1" * 64,
            ),
            _alias(
                im.PROVIDER_FYERS,
                NEW_KEY,
                identity=CID + "-B",
                valid_from=FEB,
                digest="2" * 64,
            ),
        ]
    )
    reused = _service(reused_rows).resolve_for(
        im.PROVIDER_FYERS, NEW_KEY, NEXT, T2
    )
    assert reused.status == im.TOKEN_REUSE
    assert reused.canonical_instrument_id is None


def test_stale_data_is_refused() -> None:
    """Freshness is an independent gate even when the alias is established."""
    rows = _eligible_rows()
    rows.append(_alias(im.PROVIDER_FYERS, OLD_KEY))
    rows = [
        (
            row._replace(attribute_value=im.STALE)
            if row.attribute_name == im.ATTR_DATA_FRESHNESS
            else row
        )
        for row in rows
    ]
    result = _service(rows).resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    assert result.status == service.STALE_DATA
    assert result.eligibility == im.NOT_ELIGIBLE
    assert result.canonical_instrument_id is None


def test_cache_is_keyed_by_all_time_axes() -> None:
    """Neither the market date nor the observation time may leak across keys."""

    class CountingRegistry:
        """Count alias queries without changing registry semantics."""

        def __init__(self, rows: list[im.Observation]) -> None:
            self.inner = im.build_registry(rows)
            self.calls: list[tuple[str, str, str]] = []

        def resolve_alias(self, identifier, market_date, observed_at):
            """Record the alias query before delegating."""
            self.calls.append((identifier, market_date, observed_at))
            return self.inner.resolve_alias(
                identifier, market_date, observed_at
            )

        def as_of(self, market_date, observed_at):
            """Delegate the state query."""
            return self.inner.as_of(market_date, observed_at)

    rows = _eligible_rows()
    rows.append(_alias(im.PROVIDER_FYERS, OLD_KEY, valid_to=FEB))
    registry = CountingRegistry(rows)
    resolver = service.InstrumentRegistryService(registry)
    first = resolver.resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    assert resolver.resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1) == first
    assert registry.calls == [(f"fyers:{OLD_KEY}", DAY, T1)]
    assert resolver.cache_size == 1

    later_market = resolver.resolve_for(im.PROVIDER_FYERS, OLD_KEY, LATE, T1)
    later_observation = resolver.resolve_for(
        im.PROVIDER_FYERS, OLD_KEY, DAY, T2
    )
    assert first.established
    assert later_market.status == im.EXPIRED
    assert (
        later_observation.canonical_instrument_id
        == first.canonical_instrument_id
    )
    assert resolver.cache_size == 3
    assert len(registry.calls) == 3
    resolver.clear_cache()
    assert resolver.cache_size == 0


def test_loader_is_lazy_and_resolutions_are_replayable() -> None:
    """Injected loaders run once; cache misses replay the same offline data."""
    rows = _eligible_rows()
    rows.append(_alias(im.PROVIDER_UPSTOX, UP_KEY))
    calls: list[int] = []

    def loader():
        """Return the injected registry and count loader invocations."""
        calls.append(1)
        return im.build_registry(rows)

    resolver = service.InstrumentRegistryService(loader=loader)
    assert not calls
    first = resolver.resolve_for(im.PROVIDER_UPSTOX, UP_KEY, DAY, T1)
    resolver.resolve_for(im.PROVIDER_UPSTOX, UP_KEY, DAY, T1)
    assert len(calls) == 1
    resolver.clear_cache()
    assert resolver.resolve_for(im.PROVIDER_UPSTOX, UP_KEY, DAY, T1) == first
    assert len(calls) == 1


def test_pinned_snapshots_do_not_claim_live_validity() -> None:
    """Default loading is offline and never asserts live effective dates."""
    provenance = ROOT / "governance" / "instrument-master-snapshots.json"
    meta = im.load_provenance(provenance)[im.PROVIDER_FYERS]
    rows = im.observations_from_snapshots()
    alias_row = next(
        row
        for row in rows
        if row.attribute_name
        == im.provider_attribute(im.PROVIDER_FYERS, im.PROVIDER_KEY_ATTRIBUTE)
    )
    result = service.InstrumentRegistryService().resolve_for(
        im.PROVIDER_FYERS,
        str(alias_row.attribute_value),
        meta.market_date,
        meta.captured_at_utc,
    )
    assert result.established is False
    assert result.canonical_instrument_id is None


def test_malformed_queries_fail_closed() -> None:
    """Bad providers, identifiers and timestamps never resolve."""
    rows = _eligible_rows()
    rows.append(_alias(im.PROVIDER_FYERS, OLD_KEY))
    resolver = _service(rows)
    for provider, identifier, day, moment in (
        ("", OLD_KEY, DAY, T1),
        ("bad:provider", OLD_KEY, DAY, T1),
        (im.PROVIDER_FYERS, "", DAY, T1),
        (im.PROVIDER_FYERS, OLD_KEY, "2026-1-15", T1),
        (im.PROVIDER_FYERS, OLD_KEY, DAY, "not-a-time"),
    ):
        result = resolver.resolve_for(provider, identifier, day, moment)
        assert result.canonical_instrument_id is None
        assert result.established is False
    assert resolver.cache_size == 5


def test_mapping_based_results_are_supported() -> None:
    """Consumers may inject simple mapping-based registries and results."""

    class MappingRegistry:
        """Minimal injected registry returning mapping-shaped results."""

        def resolve_alias(self, identifier, market_date, observed_at):
            """Return a stable alias resolution for the requested axes."""
            assert market_date == DAY and observed_at == T1
            return {
                "status": service.RESOLVED,
                "canonical_instrument_id": CID,
                "validity": im.KNOWN_INTERVAL,
                "claimants": (identifier,),
            }

        def as_of(self, market_date, observed_at):
            """Return one fresh, proven-valid state for the requested axes."""
            assert market_date == DAY and observed_at == T1
            return {
                CID: {
                    "validity": im.KNOWN_INTERVAL,
                    "proven_valid": True,
                    "data_freshness": im.FRESH,
                    "quant_eligibility": im.ELIGIBLE,
                }
            }

    resolver = service.InstrumentRegistryService(MappingRegistry())
    result = resolver.resolve_for(im.PROVIDER_FYERS, OLD_KEY, DAY, T1)
    assert result.established is True
    assert result.canonical_instrument_id == CID
    bound = resolver.for_provider(im.PROVIDER_FYERS)
    assert bound(OLD_KEY, DAY, T1) == result
