"""Offline tests against the two pinned official instrument masters."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    """Load a standalone module from an absolute repository path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


im = _load_module(
    "quant_instrument_master", ROOT / "python" / "instrument_master.py"
)
fetcher = _load_module(
    "quant_fetch_instrument_snapshots",
    ROOT / "scripts" / "fetch_instrument_snapshots.py",
)

PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
DATA = ROOT / "data" / "instrument-master"
METAS = im.load_provenance(PROVENANCE)
MARUTI_ISIN = "INE585B01010"
MARUTI_KEY = f"NSE_EQ|{MARUTI_ISIN}"
MARUTI_TICKER = "NSE:MARUTI-EQ"
MARUTI_FY_TOKEN = "101000000010999"


def _available(provider: str) -> bool:
    """Return True when a provenance entry has readable captured bytes."""
    meta = METAS.get(provider)
    if meta is None or meta.status != "AVAILABLE":
        return False
    return (
        im.snapshot_path(DATA, meta).is_file()
        and im.snapshot_path(DATA, meta, decoded=False).is_file()
    )


@pytest.fixture(scope="session", autouse=True)
def pinned_snapshots() -> None:
    """Fetch pinned public bytes once when a clean checkout lacks them."""
    fetcher.fetch(PROVENANCE, DATA)
    assert _available("upstox") and _available("fyers")


@pytest.fixture(scope="session", name="upstox_data")
def _upstox_fixture():
    """Return the parsed pinned Upstox snapshot."""
    return _parse("upstox")


@pytest.fixture(scope="session", name="fyers_data")
def _fyers_fixture():
    """Return the parsed pinned FYERS snapshot."""
    return _parse("fyers")


@pytest.fixture(scope="session", name="crosswalk_data")
def _crosswalk_fixture(upstox_data, fyers_data):
    """Return the cross-provider report for the pinned snapshots."""
    return im.crosswalk([upstox_data, fyers_data])


def _parse(provider: str):
    """Parse one captured snapshot using its provenance-derived context."""
    meta = METAS[provider]
    return im.parse_snapshot(
        im.snapshot_path(DATA, meta), provider, im.snapshot_context(meta)
    )


def _upstox_maruti():
    """Return the captured Upstox record for the known MARUTI ISIN."""
    return next(
        record
        for record in im.parse_upstox_records(
            im.snapshot_path(DATA, METAS["upstox"]).read_bytes()
        )
        if record.instrument_key == MARUTI_KEY
    )


def _fyers_maruti():
    """Return the captured FYERS record for the known MARUTI ticker."""
    return next(
        record
        for record in im.parse_fyers_symbols(
            im.snapshot_path(DATA, METAS["fyers"]).read_bytes()
        )
        if record.sym_ticker == MARUTI_TICKER
    )


def _identity(provider: str, key: str, upstox, fyers) -> str:
    """Return the canonical ID of one parsed provider record."""
    result = upstox if provider == "upstox" else fyers
    return im.canonical_id(
        next(record for record in result.records if record.provider_key == key)
    )


def test_provenance_is_canonical_and_digests_match_snapshot_files():
    """Recorded digests and byte counts must match the captured bytes."""
    text = PROVENANCE.read_text(encoding="utf-8")
    document = json.loads(text)
    assert text == json.dumps(document, indent=2, sort_keys=True) + "\n"
    assert {"upstox", "fyers"} <= set(METAS)
    assert METAS["upstox"].status == "AVAILABLE"
    for meta in METAS.values():
        if meta.status != "AVAILABLE":
            continue
        original = im.snapshot_path(DATA, meta, decoded=False).read_bytes()
        decoded = im.snapshot_path(DATA, meta).read_bytes()
        assert len(original) == meta.original_bytes
        assert im.sha256_hex(original) == meta.original_sha256
        assert len(decoded) == meta.decoded_bytes
        assert im.sha256_hex(decoded) == meta.decoded_sha256
        assert meta.schema_version == im.SCHEMA_VERSION


def test_upstox_parser_extracts_actual_record_fields():
    """The Upstox parser must expose the fields in the captured JSON."""
    records = im.parse_upstox_records(
        im.snapshot_path(DATA, METAS["upstox"]).read_bytes()
    )
    assert len(records) == 87029
    maruti = _upstox_maruti()
    assert maruti.segment == "NSE_EQ"
    assert maruti.exchange == "NSE"
    assert maruti.isin == MARUTI_ISIN
    assert maruti.instrument_type == "EQ"
    assert maruti.instrument_key == MARUTI_KEY
    assert maruti.trading_symbol == "MARUTI"
    assert maruti.exchange_token == "10999"
    assert maruti.lot_size == 1
    # Upstox reports tick_size in paise; FYERS reports the same tick in rupees.
    assert maruti.tick_size == 100.0
    assert maruti.qty_multiplier == 1.0
    assert maruti.security_type == "NORMAL"


def test_fyers_parser_extracts_actual_symbol_master_fields():
    """The FYERS parser must expose every documented NSE_CM field."""
    records = im.parse_fyers_symbols(
        im.snapshot_path(DATA, METAS["fyers"]).read_bytes()
    )
    assert len(records) == 10230
    maruti = _fyers_maruti()
    assert maruti.sym_ticker == MARUTI_TICKER
    assert maruti.fy_token == MARUTI_FY_TOKEN
    assert maruti.isin == MARUTI_ISIN
    assert maruti.ex_symbol == "MARUTI"
    assert maruti.exchange == 10
    assert maruti.segment == 10
    assert maruti.ex_series == "EQ"
    assert maruti.ex_token == 10999
    assert maruti.min_lot_size == 1
    assert maruti.tick_size == 1.0
    assert maruti.trading_session == "0915-1530|1815-1915:"
    assert maruti.last_update == "2026-11-26"
    assert maruti.trade_status == 1
    assert maruti.qty_multiplier == 1.0
    assert maruti.opt_type == "XX"
    assert maruti.under_sym == "MARUTI"


def test_canonical_identity_is_deterministic_and_symbol_independent(
    upstox_data,
):
    """Identity derives from ISIN, exchange, segment and series only."""
    up = next(
        record
        for record in upstox_data.records
        if record.provider_key == MARUTI_KEY
    )
    identity = im.canonical_id(up)
    assert identity is not None
    assert identity == im.canonical_id(up)
    renamed = up._replace(
        provider_key="NSE_EQ|999999",
        provider_symbol="DIFFERENT-SYMBOL",
        listing_id="1",
    )
    assert im.canonical_id(renamed) == identity
    assert im.canonical_id(up._replace(isin="INE0LLY01014")) != identity
    assert im.valid_isin(up.isin)


def test_crosswalk_matches_snapshot_identities_and_marks_eligibility(
    upstox_data, fyers_data, crosswalk_data
):
    """Crosswalk must match the two providers without symbol comparison."""
    identity = _identity("upstox", MARUTI_KEY, upstox_data, fyers_data)
    assert identity == _identity(
        "fyers", MARUTI_FY_TOKEN, upstox_data, fyers_data
    )
    instrument = next(
        item
        for item in crosswalk_data.instruments
        if item.canonical_instrument_id == identity
    )
    assert instrument.identity_status == im.VERIFIED
    assert instrument.crosswalk_status == im.VERIFIED
    assert instrument.conformance_status == im.PASS
    assert instrument.trading_status == im.ACTIVE
    # FYERS values are preferred; its tick_size unit is rupees, not paise.
    assert instrument.tick_size == 1.0
    assert im.is_eligible(instrument)
    assert {item.provider for item in instrument.provider_identifiers} == {
        "upstox",
        "fyers",
    }
    assert crosswalk_data.status_counts == {
        im.VERIFIED: 9926,
        im.UNMAPPED: 174,
        im.AMBIGUOUS: 0,
        im.INACTIVE: 0,
        im.SUSPENDED: 0,
    }
    assert crosswalk_data.conformance_counts == {
        im.PASS: 9926,
        im.CONFLICT: 0,
        im.INCOMPLETE: 174,
    }
    assert crosswalk_data.trading_counts == {
        im.ACTIVE: 10100,
        im.INACTIVE: 0,
        im.SUSPENDED: 0,
        im.UNKNOWN: 0,
    }


def test_missing_identity_is_quarantined_from_real_records(
    upstox_data, fyers_data
):
    """Derivatives and missing-ISIN records must not enter the universe."""
    keys = {entry.provider_key for entry in upstox_data.quarantine}
    assert "NSE_FO|59150" in keys
    assert all(
        entry.reason == im.UNSUPPORTED_SEGMENT
        for entry in upstox_data.quarantine
    )
    canonical = {im.canonical_id(record) for record in upstox_data.records}
    assert None not in canonical
    assert any(
        entry.reason == im.MISSING_ISIN for entry in fyers_data.quarantine
    )


def test_identity_collision_is_ambiguous_and_ineligible(upstox_data):
    """A provider claiming one identity twice must be quarantined."""
    record = next(
        item for item in upstox_data.records if item.provider_key == MARUTI_KEY
    )
    result = im.ParseResult((record, record), (), 2)
    report = im.crosswalk([result])
    assert len(report.instruments) == 1
    assert report.status_counts[im.AMBIGUOUS] == 1
    instrument = report.instruments[0]
    assert instrument.crosswalk_status == im.AMBIGUOUS
    assert instrument.conformance_status == im.INCOMPLETE
    assert not im.is_eligible(instrument)
    assert any(
        entry.reason == im.IDENTITY_COLLISION for entry in report.quarantine
    )


def test_inactive_provider_status_blocks_eligibility(upstox_data, fyers_data):
    """An inactive provider flag must make a matched identity ineligible."""
    up = next(
        record
        for record in upstox_data.records
        if record.provider_key == MARUTI_KEY
    )
    fy = next(
        record
        for record in fyers_data.records
        if record.provider_key == MARUTI_FY_TOKEN
    )
    report = im.crosswalk(
        [
            im.ParseResult((up,), (), 1),
            im.ParseResult((fy._replace(trading_status=im.INACTIVE),), (), 1),
        ]
    )
    instrument = report.instruments[0]
    assert report.status_counts[im.INACTIVE] == 1
    assert instrument.identity_status == im.VERIFIED
    assert instrument.conformance_status == im.PASS
    assert instrument.trading_status == im.INACTIVE
    assert not im.is_eligible(instrument)


def test_suspension_is_authoritative_and_blocks_eligibility(
    upstox_data, fyers_data
):
    """An external suspension set must override an otherwise verified match."""
    identity = _identity("upstox", MARUTI_KEY, upstox_data, fyers_data)
    report = im.crosswalk([upstox_data, fyers_data], suspended_ids=(identity,))
    instrument = next(
        item
        for item in report.instruments
        if item.canonical_instrument_id == identity
    )
    assert instrument.identity_status == im.VERIFIED
    assert instrument.conformance_status == im.PASS
    assert instrument.trading_status == im.SUSPENDED
    assert instrument.eligibility_status == im.NOT_ELIGIBLE
    assert not im.is_eligible(instrument)


def test_field_conflict_is_conformance_conflict_with_verified_identity(
    upstox_data, fyers_data
):
    """A contract field conflict must not manufacture an identity mismatch."""
    up = next(
        record
        for record in upstox_data.records
        if record.provider_key == MARUTI_KEY
    )
    fy = next(
        record
        for record in fyers_data.records
        if record.provider_key == MARUTI_FY_TOKEN
    )
    changed = fy._replace(lot_size=2, tick_size=2.0)
    report = im.crosswalk(
        [
            im.ParseResult((up,), (), 1),
            im.ParseResult((changed,), (), 1),
        ]
    )
    instrument = report.instruments[0]
    assert instrument.identity_status == im.VERIFIED
    assert instrument.crosswalk_status == im.VERIFIED
    assert instrument.conformance_status == im.CONFLICT
    assert report.conformance_counts[im.CONFLICT] == 1
    assert instrument.trading_status == im.ACTIVE
    assert not im.is_eligible(instrument)


def test_digest_mismatch_is_rejected():
    """Parsing must fail closed when bytes disagree with provenance."""
    context = im.snapshot_context(METAS["upstox"])
    bad = context._replace(source_digest="0" * 64)
    payload = im.snapshot_path(DATA, METAS["upstox"]).read_bytes()
    with pytest.raises(ValueError, match="digest"):
        im.parse_upstox(payload, bad)


def test_corrupt_snapshot_bytes_are_rejected(tmp_path):
    """Both the fetch verifier and the parser must reject corrupt bytes."""
    meta = METAS["upstox"]
    payload = im.snapshot_path(DATA, meta).read_bytes()
    corrupted = payload[:-1] + bytes([payload[-1] ^ 0x01])
    with pytest.raises(ValueError, match="SHA-256"):
        fetcher.verify(
            corrupted, meta.decoded_bytes, meta.decoded_sha256, "corrupt"
        )
    with pytest.raises(ValueError, match="expected 8 bytes"):
        fetcher.verify(b"short", 8, meta.decoded_sha256, "corrupt")
    with pytest.raises(ValueError, match="digest"):
        im.parse_upstox(corrupted, im.snapshot_context(meta))
    original_path, decoded_path = fetcher.paths(meta, tmp_path)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(corrupted)
    decoded_path.write_bytes(b"{}")
    assert not fetcher.verify_local(meta, tmp_path)


def test_daily_diff_compares_snapshots_by_canonical_id(
    upstox_data, crosswalk_data
):
    """Diff must report added IDs and changed fields from actual snapshots."""
    before = im.crosswalk([upstox_data])
    previous = {
        item.canonical_instrument_id: item for item in before.instruments
    }
    current = {
        item.canonical_instrument_id: item
        for item in crosswalk_data.instruments
    }
    report = im.diff_snapshots(previous, current)
    assert report.added == tuple(sorted(set(current) - set(previous)))
    assert len(report.added) == 174
    assert report.removed == ()
    fields = {change.field for change in report.changed}
    assert "crosswalk_status" in fields
    assert "provider_identifiers" in fields
    assert "source_digest" not in fields
    assert all(
        change.canonical_instrument_id in current for change in report.changed
    )


def test_effective_dating_is_partial_and_snapshot_derived(crosswalk_data):
    """Effective dates must not claim more than the supplied snapshots."""
    for instrument in crosswalk_data.instruments:
        assert instrument.effective_from == METAS["upstox"].market_date
        assert instrument.effective_to is None
