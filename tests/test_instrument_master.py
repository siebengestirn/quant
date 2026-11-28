"""Offline tests against the two pinned official instrument masters."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import itertools
import json
import os
import sys
import zipfile
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

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
REAL_DOWNLOAD = fetcher.download

PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
REFERENCE = ROOT / "reference-data" / "instrument-master"
DATA = ROOT / "data" / "instrument-master"
EVIDENCE = ROOT / fetcher.BUNDLE_SNAPSHOT_DIR
BUNDLE_ENV = fetcher.BUNDLE_ENV
METAS = {meta.provider: meta for meta in fetcher.load_pins(PROVENANCE)}
SOURCE_IS_REFERENCE = True
MARUTI_ISIN = "INE585B01010"
MARUTI_KEY = f"NSE_EQ|{MARUTI_ISIN}"
MARUTI_TICKER = "NSE:MARUTI-EQ"
MARUTI_FY_TOKEN = "101000000010999"
CID = "NSE-CASH-BITEMPORAL-TEST"
DAY = "2026-11-26"
NEXT_DAY = "2026-11-27"
T0 = "2026-11-26T09:00:00Z"
T1 = "2026-11-27T09:00:00Z"
T2 = "2026-11-28T09:00:00Z"
JAN = "2026-01-01"
FEB = "2026-02-01"
MAR = "2026-03-01"
APR = "2026-04-01"
EARLY = "2025-12-31T09:00:00Z"
MID = "2026-03-01T09:00:00Z"
LATER = "2026-04-02T09:00:00Z"


@pytest.fixture(scope="session", autouse=True)
def _block_network() -> None:
    """Make any accidental network call in an ordinary test fail loudly."""

    def forbidden(*args, **kwargs) -> None:
        raise AssertionError("tests must never access the network")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fetcher.urllib.request, "urlopen", forbidden)
        patch.setattr(fetcher, "download", forbidden)
        yield


def _prepare_snapshots(
    data_root: Path,
    evidence_root: Path,
    bundle_path: str,
    reference_root: Path = REFERENCE,
) -> tuple[Path, str]:
    """Resolve offline bytes or fail with the exact recovery command."""
    try:
        source = fetcher.resolve_evidence(
            data_root, evidence_root, PROVENANCE, bundle_path, reference_root
        )
    except fetcher.EvidenceUnavailable as exc:
        pytest.fail(str(exc))
    return source.root, source.source


def _obs(
    attribute: str,
    value: object,
    *,
    identity: str = CID,
    observed_at: str = T1,
    ingested_at: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    digest: str = "a" * 64,
) -> im.Observation:
    """Build one observation with explicit validity and observation times."""
    return im.observation(
        identity,
        attribute,
        value,
        observed_at=observed_at,
        ingested_at=ingested_at,
        valid_from=valid_from,
        valid_to=valid_to,
        source_digest=digest,
    )


def _alias_obs(
    alias: str,
    identity: str = CID,
    *,
    observed_at: str = EARLY,
    valid_from: str | None = JAN,
    valid_to: str | None = FEB,
    digest: str = "a" * 64,
) -> im.Observation:
    """Build one provider-key alias observation with explicit bounds."""
    return im.observation(
        identity,
        im.provider_attribute("fyers", im.PROVIDER_KEY_ATTRIBUTE),
        alias,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_to=valid_to,
        source_digest=digest,
    )


def _eligible_rows(
    valid_from: str | None = DAY,
) -> list[im.Observation]:
    """Return observations affirming every independent eligibility gate.

    Attested validity is itself a gate. Passing None builds the perfect-but
    unknown-validity shape used by the fail-closed regression test.
    """
    return [
        _obs(im.ATTR_IDENTITY_STATUS, im.VERIFIED, valid_from=valid_from),
        _obs(im.ATTR_CONFORMANCE_STATUS, im.PASS, valid_from=valid_from),
        _obs(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE, valid_from=valid_from),
        _obs(im.ATTR_EXCHANGE_STATUS, im.OPEN, valid_from=valid_from),
        _obs(im.ATTR_EXCHANGE_ELIGIBILITY, im.ALLOWED, valid_from=valid_from),
        _obs(im.ATTR_DATA_FRESHNESS, im.FRESH, valid_from=valid_from),
        _obs(
            im.provider_attribute("fyers", im.PROVIDER_KEY_ATTRIBUTE),
            im.provider_alias("fyers", MARUTI_FY_TOKEN),
            valid_from=valid_from,
            digest="b" * 64,
        ),
        _obs(
            im.provider_attribute("upstox", im.PROVIDER_KEY_ATTRIBUTE),
            im.provider_alias("upstox", MARUTI_KEY),
            valid_from=valid_from,
            digest="c" * 64,
        ),
    ]


def _replace_value(
    rows: list[im.Observation], attribute: str, value: object
) -> list[im.Observation]:
    """Return rows with one attribute's value replaced by a new value."""
    return [
        (
            row._replace(attribute_value=value)
            if row.attribute_name == attribute
            else row
        )
        for row in rows
    ]


@pytest.fixture(scope="session", autouse=True)
def pinned_snapshots() -> None:
    """Prefer tracked reference bytes, then local data, then a bundle."""
    global DATA, SOURCE_IS_REFERENCE
    DATA, source = _prepare_snapshots(
        DATA, EVIDENCE, os.environ.get(BUNDLE_ENV, "")
    )
    SOURCE_IS_REFERENCE = source == fetcher.SOURCE_REFERENCE


@pytest.fixture(scope="session", name="upstox_data")
def _upstox_fixture(pinned_snapshots):
    """Return the parsed pinned Upstox snapshot."""
    del pinned_snapshots
    return _parse("upstox")


@pytest.fixture(scope="session", name="fyers_data")
def _fyers_fixture(pinned_snapshots):
    """Return the parsed pinned FYERS snapshot."""
    del pinned_snapshots
    return _parse("fyers")


@pytest.fixture(scope="session", name="crosswalk_data")
def _crosswalk_fixture(upstox_data, fyers_data):
    """Return the cross-provider report for the pinned snapshots."""
    return im.crosswalk([upstox_data, fyers_data])


def _decoded_bytes(provider: str) -> bytes:
    """Read pinned decoded bytes from whichever offline source was resolved."""
    return fetcher.decoded_payload(
        METAS[provider], DATA, PROVENANCE, reference=SOURCE_IS_REFERENCE
    )


def _original_bytes(provider: str) -> bytes:
    """Read pinned original wire bytes from the resolved offline source."""
    meta = METAS[provider]
    if SOURCE_IS_REFERENCE:
        pair = fetcher.reference_paths(meta, DATA, PROVENANCE)
        assert pair is not None
        payload = pair[0].read_bytes()
        if meta.original_filename.endswith(".gz"):
            return payload
        return fetcher._gunzip(payload, fetcher.MAX_DECODED_BYTES)
    return fetcher.paths(meta, DATA)[0].read_bytes()


def _parse(provider: str):
    """Parse one captured snapshot using its provenance-derived context."""
    meta = METAS[provider]
    context = im.snapshot_context(meta)
    payload = _decoded_bytes(provider)
    if provider == "upstox":
        return im.parse_upstox(payload, context)
    if provider == "fyers":
        return im.parse_fyers(payload, context)
    raise AssertionError(f"unsupported provider: {provider}")


def _upstox_maruti():
    """Return the captured Upstox record for the known MARUTI ISIN."""
    return next(
        record
        for record in im.parse_upstox_records(_decoded_bytes("upstox"))
        if record.instrument_key == MARUTI_KEY
    )


def _fyers_maruti():
    """Return the captured FYERS record for the known MARUTI ticker."""
    return next(
        record
        for record in im.parse_fyers_symbols(_decoded_bytes("fyers"))
        if record.sym_ticker == MARUTI_TICKER
    )


def _maruti_pair(upstox_data, fyers_data):
    """Return normalized MARUTI provider observations from parsed snapshots."""
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
    return up, fy


def _identity(provider: str, key: str, upstox, fyers) -> str:
    """Return the canonical ID of one parsed provider record."""
    result = upstox if provider == "upstox" else fyers
    return im.canonical_id(
        next(record for record in result.records if record.provider_key == key)
    )


def _copy_pinned_bytes(data_root: Path) -> None:
    """Copy the verified pinned bytes into a temporary plain data root."""
    for meta in fetcher.load_pins(PROVENANCE):
        for decoded in (False, True):
            relative = fetcher.paths(meta, Path())[int(decoded)]
            payload = (
                _decoded_bytes(meta.provider)
                if decoded
                else _original_bytes(meta.provider)
            )
            destination = data_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)


def _copy_reference_tree(root: Path) -> Path:
    """Copy tracked reference files into a temporary reference root."""
    for meta in fetcher.load_pins(PROVENANCE):
        pair = fetcher.reference_paths(meta, REFERENCE, PROVENANCE)
        assert pair is not None
        for source in pair:
            destination = root / source.relative_to(REFERENCE)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    return root


def _mini_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """Build a small bundle with valid digests but tiny synthetic bytes."""
    entries = []
    pairs = []
    for provider in ("upstox", "fyers"):
        entry = METAS[provider]._asdict()
        original = json.dumps({"provider": provider}).encode("utf-8")
        decoded = json.dumps({"provider": provider, "decoded": True}).encode(
            "utf-8"
        )
        entry.update(
            original_bytes=len(original),
            original_sha256=hashlib.sha256(original).hexdigest(),
            decoded_bytes=len(decoded),
            decoded_sha256=hashlib.sha256(decoded).hexdigest(),
        )
        entries.append(entry)
        pairs.append((original, decoded))
    provenance = tmp_path / "mini-provenance.json"
    provenance.write_text(
        json.dumps({"snapshots": entries}, sort_keys=True), encoding="utf-8"
    )
    metas = fetcher.load_pins(provenance)
    bundle = tmp_path / "mini-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for meta, (original, decoded) in zip(metas, pairs, strict=True):
            archive.writestr(fetcher._zip_member_name(meta, False), original)
            archive.writestr(fetcher._zip_member_name(meta, True), decoded)
    return provenance, bundle


def _rewrite_bundle(
    source: Path, target: Path, replacements: dict[str, bytes]
) -> None:
    """Rewrite a ZIP with selected members replaced by supplied bytes."""
    with zipfile.ZipFile(source) as original:
        names = original.namelist()
        payloads = {name: original.read(name) for name in names}
    payloads.update(replacements)
    with zipfile.ZipFile(target, "w") as rewritten:
        for name, payload in payloads.items():
            rewritten.writestr(name, payload)


def test_provenance_is_canonical_and_digests_match_reference_files():
    """Recorded digests and byte counts must match the tracked bytes."""
    text = PROVENANCE.read_text(encoding="utf-8")
    document = json.loads(text)
    assert text == json.dumps(document, indent=2, sort_keys=True) + "\n"
    assert {"upstox", "fyers"} <= set(METAS)
    assert METAS["upstox"].status == "AVAILABLE"
    for entry in document["snapshots"]:
        assert entry["source_class"] == "immutable-reference-data"
        assert fetcher.verify_reference(
            METAS[entry["provider"]], REFERENCE, PROVENANCE
        )
        for kind in ("original", "decoded"):
            path = REFERENCE / fetcher._safe_reference_path(
                entry[f"reference_{kind}_path"], f"reference_{kind}_path"
            )
            payload = path.read_bytes()
            assert len(payload) == entry[f"reference_{kind}_bytes"]
            assert im.sha256_hex(payload) == entry[f"reference_{kind}_sha256"]
            assert path.name.endswith(".gz")
    for meta in METAS.values():
        original = _original_bytes(meta.provider)
        decoded = _decoded_bytes(meta.provider)
        assert len(original) == meta.original_bytes
        assert im.sha256_hex(original) == meta.original_sha256
        assert len(decoded) == meta.decoded_bytes
        assert im.sha256_hex(decoded) == meta.decoded_sha256
        assert meta.schema_version == im.SCHEMA_VERSION


def test_upstox_parser_extracts_actual_record_fields():
    """The Upstox parser must expose the fields in the captured JSON."""
    records = im.parse_upstox_records(_decoded_bytes("upstox"))
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
    assert maruti.tick_size == 100.0
    assert maruti.qty_multiplier == 1.0
    assert maruti.security_type == "NORMAL"


def test_fyers_parser_extracts_actual_symbol_master_fields():
    """The FYERS parser must expose every documented NSE_CM field."""
    records = im.parse_fyers_symbols(_decoded_bytes("fyers"))
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
    assert crosswalk_data.coverage_counts == {
        im.SINGLE_SOURCE: 174,
        im.MULTI_SOURCE: 9926,
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
    """A provider claiming one identity twice stays identity-ambiguous."""
    record = next(
        item for item in upstox_data.records if item.provider_key == MARUTI_KEY
    )
    result = im.ParseResult((record, record), (), 2)
    report = im.crosswalk([result])
    assert len(report.instruments) == 1
    assert report.status_counts[im.AMBIGUOUS] == 1
    instrument = report.instruments[0]
    assert instrument.crosswalk_status == im.AMBIGUOUS
    # One provider cannot supply cross-provider conformance, even twice.
    assert instrument.conformance_status == im.INCOMPLETE
    assert instrument.provider_coverage == im.SINGLE_SOURCE
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


def test_conformance_evaluates_every_field_independently(
    upstox_data, fyers_data
):
    """One conflict and one missing value must both remain visible."""
    up, fy = _maruti_pair(upstox_data, fyers_data)
    report = im.contract_conformance(
        (up, fy._replace(lot_size=2, listing_id=""))
    )
    assert report.status == im.CONFLICT
    assert len(report.fields) == len(im.CONFORMANCE_FIELDS)
    assert {item.field for item in report.fields} == set(im.CONFORMANCE_FIELDS)
    statuses = {item.field: item.status for item in report.fields}
    assert statuses["lot_size"] == im.CONFLICT
    assert statuses["listing_id"] == im.INCOMPLETE
    assert statuses["isin"] == im.PASS
    assert tuple(item.field for item in report.conflicts) == ("lot_size",)
    assert tuple(item.field for item in report.incomplete) == ("listing_id",)
    assert len(report.passed) == len(im.CONFORMANCE_FIELDS) - 2
    listing = next(item for item in report.fields if item.field == "listing_id")
    assert listing.missing == ("fyers",)
    assert dict(listing.observed)["fyers"] == ""
    assert dict(listing.observed)["upstox"] == "10999"


def test_conformance_conflict_survives_a_missing_value(upstox_data, fyers_data):
    """A disagreement must not be hidden by another provider's gap."""
    up, fy = _maruti_pair(upstox_data, fyers_data)
    third = up._replace(
        provider="third", provider_key="third", tick_size=None, lot_size=9
    )
    report = im.contract_conformance((up, fy, third))
    statuses = {item.field: item.status for item in report.fields}
    assert report.status == im.CONFLICT
    assert statuses["lot_size"] == im.CONFLICT
    assert statuses["tick_size"] == im.INCOMPLETE
    assert statuses["isin"] == im.PASS


def test_conformance_all_fields_agree_is_pass(upstox_data, fyers_data):
    """Agreement on every comparable field yields a full PASS report."""
    up, fy = _maruti_pair(upstox_data, fyers_data)
    report = im.contract_conformance((up, fy))
    assert report.status == im.PASS
    assert report.provider_coverage == im.MULTI_SOURCE
    assert report.conflicts == ()
    assert report.incomplete == ()
    assert report.fields == report.passed


def test_conformance_stays_separate_from_unmapped_identity(upstox_data):
    """A single-provider record can be field-conformant yet unmapped."""
    up = next(
        record
        for record in upstox_data.records
        if record.provider_key == MARUTI_KEY
    )
    report = im.contract_conformance((up,))
    assert report.provider_coverage == im.SINGLE_SOURCE
    assert report.status == im.INCOMPLETE
    assert report.fields[0].status == im.PASS
    instrument = im.crosswalk([im.ParseResult((up,), (), 1)]).instruments[0]
    assert instrument.identity_status == im.UNMAPPED
    assert instrument.conformance_status == im.INCOMPLETE
    assert instrument.provider_coverage == im.SINGLE_SOURCE
    assert instrument.conformance_report.status == im.INCOMPLETE
    assert not im.is_eligible(instrument)


def test_quarantine_report_counts_real_records(upstox_data, fyers_data):
    """The 77,233 withheld records are classified, not all called corrupt."""
    entries = [*upstox_data.quarantine, *fyers_data.quarantine]
    report = im.quarantine_report(entries)
    assert report.total == 77233
    assert len(entries) == 77233
    assert report.by_reason == {
        im.MISSING_ISIN: 130,
        im.UNSUPPORTED_SEGMENT: 77103,
    }
    assert report.by_provider == {"fyers": 130, "upstox": 77103}
    assert report.by_instrument_class == {
        im.CASH_EQUITY: 130,
        im.FUTURE: 912,
        im.INDEX: 139,
        im.OPTION: 76024,
        im.UNSUPPORTED: 28,
    }
    assert report.by_reason_provider_class == {
        im.MISSING_ISIN: {"fyers": {im.CASH_EQUITY: 130}},
        im.UNSUPPORTED_SEGMENT: {
            "upstox": {
                im.FUTURE: 912,
                im.INDEX: 139,
                im.OPTION: 76024,
                im.UNSUPPORTED: 28,
            }
        },
    }
    assert set(report.by_reason) <= {
        im.UNSUPPORTED_SEGMENT,
        im.MISSING_ISIN,
        im.MISSING_PROVIDER_KEY,
        im.IDENTITY_COLLISION,
    }


def test_digest_mismatch_is_rejected():
    """Parsing must fail closed when bytes disagree with provenance."""
    context = im.snapshot_context(METAS["upstox"])
    bad = context._replace(source_digest="0" * 64)
    payload = _decoded_bytes("upstox")
    with pytest.raises(ValueError, match="digest"):
        im.parse_upstox(payload, bad)


def test_corrupt_snapshot_bytes_are_rejected(tmp_path):
    """Both the fetch verifier and the parser must reject corrupt bytes."""
    meta = METAS["upstox"]
    payload = _decoded_bytes(meta.provider)
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
        item.canonical_instrument_id for item in crosswalk_data.instruments
    }
    report = im.diff_snapshots(
        previous,
        {
            item.canonical_instrument_id: item
            for item in crosswalk_data.instruments
        },
    )
    assert report.added == tuple(sorted(current - set(previous)))
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


def test_verify_cli_checks_tracked_reference_bytes_without_network(tmp_path):
    """Verify checks reference files, exiting 1 when they are tampered."""
    root = _copy_reference_tree(
        tmp_path / "reference-data" / "instrument-master"
    )
    assert fetcher.main(["verify", "--reference-root", str(root)]) == 0
    meta = METAS["upstox"]
    pair = fetcher.reference_paths(meta, root, PROVENANCE)
    assert pair is not None
    target = pair[1]
    payload = target.read_bytes()
    target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    assert fetcher.main(["verify", "--reference-root", str(root)]) == 1


def test_clean_checkout_uses_tracked_reference_data_without_network(
    tmp_path, monkeypatch
):
    """No data/ and no bundle still resolves the immutable tracked bytes."""
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    root, source = _prepare_snapshots(
        tmp_path / "absent-data",
        tmp_path / "absent-evidence",
        "",
        REFERENCE,
    )
    assert root == REFERENCE
    assert source == fetcher.SOURCE_REFERENCE
    assert fetcher.main(["verify"]) == 0
    assert all(
        fetcher.verify_reference(meta, REFERENCE, PROVENANCE)
        for meta in fetcher.load_pins(PROVENANCE)
    )


def test_tampered_tracked_bytes_fail_resolution(tmp_path, monkeypatch):
    """A modified reference file fails closed without falling back to data."""
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    root = _copy_reference_tree(
        tmp_path / "reference-data" / "instrument-master"
    )
    pair = fetcher.reference_paths(METAS["fyers"], root, PROVENANCE)
    assert pair is not None
    payload = pair[0].read_bytes()
    pair[0].write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    assert not fetcher.verify_reference(METAS["fyers"], root, PROVENANCE)
    with pytest.raises(
        fetcher.EvidenceUnavailable, match="failed verification"
    ):
        fetcher.resolve_evidence(
            DATA,
            tmp_path / "no-evidence",
            PROVENANCE,
            "",
            root,
        )


def test_fetch_command_is_explicit_in_argument_parser():
    """Retrieval and extraction require their own explicit subcommands."""
    fetch_args = fetcher.parse_args(["fetch", "--output", "/tmp/out"])
    assert fetch_args.command == "fetch"
    assert fetch_args.output == Path("/tmp/out")
    extract_args = fetcher.parse_args(
        ["extract", "--from-bundle", "/tmp/bundle.zip"]
    )
    assert extract_args.command == "extract"
    assert extract_args.from_bundle == Path("/tmp/bundle.zip")
    update_args = fetcher.parse_args(
        ["update-provenance", "--output", "/tmp/candidate.json"]
    )
    assert update_args.command == "update-provenance"
    assert update_args.output == Path("/tmp/candidate.json")
    with pytest.raises(ValueError, match="must not overwrite"):
        fetcher.update_provenance(PROVENANCE, PROVENANCE)
    with pytest.raises(SystemExit):
        fetcher.parse_args([])


def test_non_https_and_unlisted_hosts_are_rejected():
    """Neither a plain HTTP URL nor an unknown host can be retrieved."""
    url = METAS["upstox"].url
    with pytest.raises(ValueError, match="non-HTTPS"):
        fetcher.validate_url(url.replace("https://", "http://"))
    with pytest.raises(ValueError, match="allowlisted"):
        fetcher.validate_url("https://evil.example/NSE.json.gz")
    handler = fetcher.AllowlistedRedirect()
    with pytest.raises(ValueError, match="disallowed redirect"):
        handler.redirect_request(
            None, None, 302, "Found", {}, url.replace("https://", "http://")
        )
    with pytest.raises(ValueError, match="disallowed redirect"):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://evil.example/NSE.json.gz"
        )
    assert handler.allowed(url)


def test_bad_filename_market_date_and_path_are_rejected(tmp_path):
    """Provenance paths are validated before any filesystem use."""
    meta = METAS["upstox"]
    with pytest.raises(ValueError, match="unsafe snapshot filename"):
        fetcher.safe_filename("../NSE.json.gz")
    with pytest.raises(ValueError, match="unsafe snapshot filename"):
        fetcher.validate_meta(
            meta._replace(original_filename="../../NSE.json.gz")
        )
    with pytest.raises(ValueError, match="invalid market_date"):
        fetcher.safe_market_date("2026-99-99")
    with pytest.raises(ValueError, match="escapes output"):
        fetcher.ensure_contained(tmp_path, tmp_path.parent / "outside.json")
    with pytest.raises(ValueError, match="unsafe reference-data path"):
        fetcher._safe_reference_path(
            "../../NSE.json.gz", "reference_original_path"
        )


def test_oversize_and_unexpected_content_are_rejected():
    """Streaming limits and declared content types fail closed."""
    with pytest.raises(ValueError, match="exceeds 10 bytes"):
        fetcher.read_limited(io.BytesIO(b"x" * 11), "test", 10)
    payload = gzip.compress(b"x" * 100)
    with pytest.raises(ValueError, match="gzip declares"):
        fetcher.decode_original(payload, "NSE.json.gz", 50)
    with pytest.raises(ValueError, match="decoded data exceeds"):
        fetcher._append_decoded([], b"x" * 51, 0, 50)
    with pytest.raises(ValueError, match="SHA-256"):
        fetcher.verify(b"x" * 8, 8, "0" * 64, "test")

    class Response:
        """Minimal response stub carrying only HTTP headers."""

        def __init__(self, content_type: str, length: str) -> None:
            self.headers = Message()
            self.headers["Content-Length"] = length
            self.headers["Content-Type"] = content_type

    url = METAS["upstox"].url
    with pytest.raises(ValueError, match="unexpected content type"):
        fetcher._check_content_type(Response("text/html", "1"), url, ".json")
    with pytest.raises(ValueError, match="Content-Length"):
        fetcher._check_content_length(
            Response("application/json", "99"), url, 10
        )


def test_download_rejects_bad_status_and_streams_with_limits(monkeypatch):
    """Retrieval fails on a bad status and succeeds only with valid headers."""

    class Response:
        """In-memory response with a status, headers and chunked body."""

        def __init__(self, status: int) -> None:
            self.status = status
            self.headers = Message()
            body = b'{"ok":true}'
            self.headers["Content-Type"] = "application/json"
            self.headers["Content-Length"] = str(len(body))
            self._body = io.BytesIO(body)

        def read(self, count: int) -> bytes:
            """Return the next body chunk."""
            return self._body.read(count)

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    class Opener:
        """Stub opener returning a chosen status for a pinned URL."""

        def open(self, url: str, timeout: int) -> Response:
            return Response(404 if "bad" in url else 200)

    monkeypatch.setattr(
        fetcher.urllib.request, "build_opener", lambda *args: Opener()
    )
    monkeypatch.setattr(fetcher, "download", REAL_DOWNLOAD)
    url = METAS["fyers"].url
    with pytest.raises(ValueError, match="unexpected HTTP status 404"):
        fetcher.download(f"{url}?bad", 100, ".json")
    assert fetcher.download(url, 100, ".json") == b'{"ok":true}'


class _StubResponse:
    """In-memory response with declared headers and a synthetic body."""

    def __init__(
        self, content_type: str, body: bytes, status: int = 200
    ) -> None:
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        self._body = io.BytesIO(body)

    def read(self, count: int) -> bytes:
        """Return the next body chunk."""
        return self._body.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _stub_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    body: bytes,
    status: int = 200,
) -> list[tuple[str, int]]:
    """Restore real retrieval and serve one synthetic response offline."""
    response = _StubResponse(content_type, body, status)
    calls: list[tuple[str, int]] = []

    def open_stub(url: str, timeout: int) -> _StubResponse:
        """Record the URL and timeout, then return the prepared response."""
        calls.append((url, timeout))
        return response

    opener = SimpleNamespace(open=open_stub)
    monkeypatch.setattr(
        fetcher.urllib.request, "build_opener", lambda *args: opener
    )
    monkeypatch.setattr(fetcher, "download", REAL_DOWNLOAD)
    return calls


def test_content_type_allowlist_is_explicit_and_provider_scoped() -> None:
    """Upstox stays gzip/binary; FYERS allows text/plain for its JSON."""
    assert fetcher.CONTENT_TYPES[".gz"] == frozenset(
        {"application/gzip", "application/x-gzip", "application/octet-stream"}
    )
    assert fetcher.CONTENT_TYPES[".json"] == frozenset(
        {"application/json", "text/plain", "application/octet-stream"}
    )


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/json", "application/octet-stream"],
)
def test_fyers_json_download_accepts_allowlisted_content_types(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    """A text/plain label must not reject the valid FYERS JSON body."""
    body = b'[{"symbol":"NSE:MARUTI-EQ"}]'
    calls = _stub_retrieval(monkeypatch, content_type, body)
    url = METAS["fyers"].url
    assert fetcher.download(url, 100, ".json") == body
    assert calls == [(url, fetcher.TIMEOUT_SECONDS)]


def test_fyers_html_content_type_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTML error page is never accepted as a pinned JSON snapshot."""
    _stub_retrieval(monkeypatch, "text/html", b'{"ok":true}')
    with pytest.raises(ValueError, match="unexpected content type"):
        fetcher.download(METAS["fyers"].url, 100, ".json")


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/json", "application/octet-stream"],
)
def test_malformed_json_is_rejected_with_an_allowed_content_type(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    """An allowed header cannot make non-JSON bytes an acceptable body."""
    _stub_retrieval(monkeypatch, content_type, b"<html>not json</html>")
    with pytest.raises(ValueError, match="not valid JSON"):
        fetcher.download(METAS["fyers"].url, 100, ".json")


def test_bundle_extraction_is_offline_and_digest_checked(tmp_path):
    """The four pinned snapshots can be restored from a ZIP without network."""
    provenance, bundle = _mini_bundle(tmp_path)
    output = tmp_path / "data"
    extracted = fetcher.extract_from_bundle(bundle, output, provenance)
    assert len(extracted) == 2
    assert all(
        fetcher.verify_local(meta, output)
        for meta in fetcher.load_pins(provenance)
    )
    metas = fetcher.load_pins(provenance)
    first = fetcher._zip_member_name(metas[0], False)
    tampered = tmp_path / "tampered.zip"
    original = b'{"provider": "upstox"}'
    _rewrite_bundle(bundle, tampered, {first: original[:-2] + b" }"})
    with pytest.raises(ValueError, match="SHA-256"):
        fetcher.extract_from_bundle(tampered, tmp_path / "other", provenance)


def test_bundle_rejects_duplicate_providers_and_unsafe_members(
    tmp_path: Path,
) -> None:
    """Duplicate provenance and traversal member names fail closed."""
    provenance, bundle = _mini_bundle(tmp_path)
    document = json.loads(provenance.read_text(encoding="utf-8"))
    document["snapshots"].append(document["snapshots"][0])
    duplicated = tmp_path / "duplicated.json"
    duplicated.write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate provider"):
        fetcher.load_pins(duplicated)
    with zipfile.ZipFile(bundle) as archive:
        with pytest.raises(ValueError, match="unsafe bundle member"):
            fetcher._read_zip_member(archive, "quant/../outside.json", 100)


def test_real_bundle_extraction_restores_all_four_snapshots(tmp_path):
    """A clean checkout can recover every pinned byte from the release ZIP."""
    metas = fetcher.load_pins(PROVENANCE)
    bundle = tmp_path / "real-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for meta in metas:
            for decoded in (False, True):
                payload = (
                    _decoded_bytes(meta.provider)
                    if decoded
                    else _original_bytes(meta.provider)
                )
                archive.writestr(
                    fetcher._zip_member_name(meta, decoded),
                    payload,
                )
    output = tmp_path / "clean" / "data"
    result = fetcher.main(
        [
            "extract",
            "--from-bundle",
            str(bundle),
            "--output",
            str(output),
        ]
    )
    assert result == 0
    assert all(fetcher.verify_local(meta, output) for meta in metas)


def test_evidence_resolution_prefers_tracked_reference_data(tmp_path):
    """Tracked reference bytes win over local data, evidence and a bundle."""
    source = fetcher.resolve_evidence(
        DATA, tmp_path / "evidence", PROVENANCE, ""
    )
    assert source.root == REFERENCE
    assert source.source == fetcher.SOURCE_REFERENCE
    assert source.extraction_command == ""


def test_extracted_evidence_needs_no_env_var_and_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean checkout with extracted bundle evidence resolves offline."""
    evidence = tmp_path / "evidence" / "instrument-master"
    _copy_pinned_bytes(evidence)
    monkeypatch.delenv(BUNDLE_ENV, raising=False)

    def forbidden(*args, **kwargs) -> None:
        raise AssertionError("extraction must not run with valid evidence")

    monkeypatch.setattr(fetcher, "main", forbidden)
    absent = tmp_path / "absent-reference"
    assert _prepare_snapshots(
        tmp_path / "empty-data", evidence, "", absent
    ) == (evidence, fetcher.SOURCE_EVIDENCE)
    source = fetcher.resolve_evidence(
        tmp_path / "empty-data", evidence, PROVENANCE, "", absent
    )
    assert source.root == evidence
    assert source.source == fetcher.SOURCE_EVIDENCE
    assert source.extraction_command == ""
    assert all(
        fetcher.verify_local(meta, evidence)
        for meta in fetcher.load_pins(PROVENANCE)
    )


def test_bundle_env_var_is_extracted_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release ZIP named by the env var is extracted by the CLI command."""
    provenance, bundle = _mini_bundle(tmp_path)
    data = tmp_path / "env-data"
    real_main = fetcher.main
    calls: list[list[str]] = []

    def recording_main(argv: list[str]) -> int:
        calls.append(list(argv))
        assert argv[0] == "extract"
        return real_main(argv)

    monkeypatch.setattr(fetcher, "main", recording_main)
    monkeypatch.setenv(BUNDLE_ENV, str(bundle))
    source = fetcher.resolve_evidence(
        data, tmp_path / "no-evidence", provenance, None
    )
    assert source.root == data
    assert source.source == fetcher.SOURCE_BUNDLE
    assert "extract --from-bundle" in source.extraction_command
    assert calls == [fetcher.extract_argv(bundle, data, provenance)]
    assert all(
        fetcher.verify_local(meta, data)
        for meta in fetcher.load_pins(provenance)
    )


def test_missing_evidence_fails_with_precise_recovery_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No usable evidence fails with the exact restore-and-verify commands."""
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    absent = tmp_path / "absent-reference"
    with pytest.raises(fetcher.EvidenceUnavailable) as exc:
        fetcher.resolve_evidence(
            tmp_path / "data", tmp_path / "evidence", PROVENANCE, "", absent
        )
    message = str(exc.value)
    for token in ("extract --from-bundle", "verify", "pytest", "network"):
        assert token in message
    with pytest.raises(pytest.fail.Exception) as failed:
        _prepare_snapshots(tmp_path / "data", tmp_path / "evidence", "", absent)
    assert BUNDLE_ENV in str(failed.value)


def test_tampered_snapshot_fails_resolution_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modified snapshot is neither resolved nor reported as verified."""
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    data = tmp_path / "data"
    _copy_pinned_bytes(data)
    absent = tmp_path / "absent-reference"
    meta = fetcher.load_pins(PROVENANCE)[0]
    target = fetcher.paths(meta, data)[1]
    payload = target.read_bytes()
    target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    assert not fetcher.verify_local(meta, data)
    with pytest.raises(fetcher.EvidenceUnavailable):
        fetcher.resolve_evidence(
            data, tmp_path / "no-evidence", PROVENANCE, "", absent
        )
    output = tmp_path / "tampered.json"
    assert (
        fetcher.main(
            [
                "evidence-report",
                "--output-report",
                str(output),
                "--data-root",
                str(data),
                "--evidence-root",
                str(tmp_path / "no-evidence"),
                "--reference-root",
                str(absent),
                "--provenance",
                str(PROVENANCE),
            ]
        )
        == 1
    )
    # A report forced over the known root must still expose the failure.
    monkeypatch.setattr(
        fetcher,
        "resolve_evidence",
        lambda *args, **kwargs: fetcher.EvidenceSource(
            data, fetcher.SOURCE_DATA, ""
        ),
    )
    report, code = fetcher.evidence_report(
        output, data, tmp_path / "no-evidence", PROVENANCE, "", absent
    )
    assert code == 1
    assert report["verification_status"] == fetcher.FAIL
    entry = next(
        item
        for item in report["snapshots"]
        if item["provider"] == meta.provider
    )
    assert entry["verification_status"] == fetcher.FAIL
    assert entry["decoded"]["verification"] == fetcher.FAIL
    assert entry["decoded"]["bytes"] == meta.decoded_bytes


def test_evidence_report_contents_match_pinned_digests(tmp_path: Path) -> None:
    """The report records tracked wrapper bytes and unwrapped digests."""
    output = tmp_path / "assurance" / "instrument-evidence.json"
    report, code = fetcher.evidence_report(
        output, DATA, tmp_path / "evidence", PROVENANCE, ""
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert text == json.dumps(report, indent=2, sort_keys=True) + "\n"
    assert report["schema_version"] == fetcher.REPORT_SCHEMA_VERSION
    assert report["verification_status"] == fetcher.PASS
    assert report["provenance_source"] == fetcher.SOURCE_REFERENCE
    assert report["source_root"] == fetcher.SOURCE_REFERENCE
    assert not Path(report["source_root"]).is_absolute()
    assert str(ROOT) not in json.dumps(report)
    assert report["extraction_command"] == ""
    assert report["snapshot_count"] == 2
    assert {item["provider"] for item in report["snapshots"]} == {
        "upstox",
        "fyers",
    }
    for meta in fetcher.load_pins(PROVENANCE):
        entry = next(
            item
            for item in report["snapshots"]
            if item["provider"] == meta.provider
        )
        assert entry["market_date"] == meta.market_date
        assert entry["provenance_source"] == fetcher.SOURCE_REFERENCE
        pair = fetcher.reference_paths(meta, REFERENCE, PROVENANCE)
        assert pair is not None
        spec = fetcher.reference_specs(PROVENANCE)[meta.provider]
        for kind, path in zip(("original", "decoded"), pair, strict=True):
            record = entry[kind]
            size = spec[f"reference_{kind}_bytes"]
            digest = spec[f"reference_{kind}_sha256"]
            unwrapped_size = (
                meta.original_bytes
                if kind == "original"
                and not meta.original_filename.endswith(".gz")
                else meta.decoded_bytes
            )
            unwrapped_digest = (
                meta.original_sha256
                if kind == "original"
                and not meta.original_filename.endswith(".gz")
                else meta.decoded_sha256
            )
            relative = path.resolve().relative_to(ROOT).as_posix()
            assert record["path"] == relative
            assert record["relative_path"] == relative
            assert not Path(record["path"]).is_absolute()
            assert record["bytes"] == size
            assert record["sha256"] == digest
            assert record["expected_bytes"] == size
            assert record["expected_sha256"] == digest
            assert record["verification"] == fetcher.PASS
            assert record["unwrapped_verification"] == fetcher.PASS
            assert record["unwrapped_bytes"] == unwrapped_size
            assert record["unwrapped_sha256"] == unwrapped_digest


def test_evidence_report_cli_uses_bundle_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence-report CLI works through the env-var bundle path."""
    provenance, bundle = _mini_bundle(tmp_path)
    monkeypatch.setenv(BUNDLE_ENV, str(bundle))
    output = tmp_path / "assurance" / "instrument-evidence.json"
    code = fetcher.main(
        [
            "evidence-report",
            "--output-report",
            str(output),
            "--data-root",
            str(tmp_path / "cli-data"),
            "--evidence-root",
            str(tmp_path / "no-evidence"),
            "--provenance",
            str(provenance),
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verification_status"] == fetcher.PASS
    assert report["provenance_source"] == fetcher.SOURCE_BUNDLE
    assert "extract --from-bundle" in report["extraction_command"]


def test_evidence_report_command_is_registered() -> None:
    """The report command has an explicit, overridable output path."""
    args = fetcher.parse_args(
        ["evidence-report", "--output-report", "/tmp/report.json"]
    )
    assert args.command == "evidence-report"
    assert args.output_report == Path("/tmp/report.json")
    assert args.bundle is None


def test_unknown_effective_date_stays_unknown() -> None:
    """No source establishes validity bounds, so they stay unknown."""
    rows = [
        _obs(im.ATTR_EFFECTIVE_FROM, None),
        _obs(im.ATTR_EFFECTIVE_TO, None),
        _obs(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE, observed_at=T0),
    ]
    state = im.build_registry(rows).as_of(DAY, T1)[CID]
    assert state.effective_from is None
    assert state.effective_to is None
    assert state.market_date == DAY
    assert im.ATTR_EFFECTIVE_FROM in state.attributes

    dated = im.build_registry(
        [_obs(im.ATTR_EFFECTIVE_FROM, NEXT_DAY, observed_at=T1)]
    )
    assert dated.as_of(DAY, T0) == {}
    assert dated.as_of(DAY, T1)[CID].effective_from == NEXT_DAY
    assert dated.as_of(NEXT_DAY, T1)[CID].effective_from == NEXT_DAY


def test_later_correction_does_not_overwrite_original() -> None:
    """A correction is appended; historical queries retain the old fact."""
    original = _obs(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE, observed_at=T1)
    correction = _obs(
        im.ATTR_PROVIDER_TRADING_STATUS, im.INACTIVE, observed_at=T2
    )
    registry = im.build_registry([correction, original])
    assert registry.observations == (original, correction)
    assert registry.as_of(DAY, T1)[CID].provider_trading_status == im.ACTIVE
    assert registry.as_of(DAY, T2)[CID].provider_trading_status == im.INACTIVE


def test_historical_as_of_separates_valid_and_observation_time() -> None:
    """Queries return what QUANT knew then and what was valid then."""
    late_valid = _obs("tick_size", 2.0, observed_at=T1, valid_from=NEXT_DAY)
    expiring = _obs("lot_size", 7, observed_at=T1, valid_to=NEXT_DAY)
    unmapped = _obs(im.ATTR_IDENTITY_STATUS, im.UNMAPPED, observed_at=T1)
    verified = _obs(im.ATTR_IDENTITY_STATUS, im.VERIFIED, observed_at=T2)
    registry = im.build_registry([late_valid, expiring, unmapped, verified])

    before = registry.as_of(DAY, T1)[CID]
    assert "tick_size" not in before.attributes
    assert before.attributes["lot_size"] == 7
    assert before.identity_status == im.UNMAPPED

    later = registry.as_of(NEXT_DAY, T1)[CID]
    assert later.attributes["tick_size"] == 2.0
    assert "lot_size" not in later.attributes
    assert later.identity_status == im.UNMAPPED
    assert registry.as_of(NEXT_DAY, T2)[CID].identity_status == im.VERIFIED


def test_single_source_never_reports_cross_provider_pass() -> None:
    """One provider's self-agreement is not cross-provider conformance."""
    up = _obs(
        im.provider_attribute("upstox", "isin"),
        MARUTI_ISIN,
        digest="b" * 64,
    )
    fy = _obs(
        im.provider_attribute("fyers", "isin"),
        MARUTI_ISIN,
        digest="c" * 64,
    )
    single = im.build_registry([up]).as_of(DAY, T1)[CID]
    multi = im.build_registry([up, fy]).as_of(DAY, T1)[CID]
    assert single.provider_coverage == im.SINGLE_SOURCE
    assert single.conformance_status == im.INCOMPLETE
    assert single.quant_eligibility == im.NOT_ELIGIBLE
    assert multi.provider_coverage == im.MULTI_SOURCE
    assert multi.conformance_status == im.PASS
    assert im.provider_coverage(im.build_registry([up]), CID) == (
        im.SINGLE_SOURCE
    )


def test_eligibility_fails_closed_on_stale_conflict_and_unknown() -> None:
    """Every independent gate must affirm eligibility explicitly."""
    rows = _eligible_rows()
    registry = im.build_registry(rows)
    assert registry.as_of(DAY, T1)[CID].quant_eligibility == im.ELIGIBLE
    mutations = (
        (im.ATTR_DATA_FRESHNESS, im.STALE),
        (im.ATTR_CONFORMANCE_STATUS, im.CONFLICT),
        (im.ATTR_EXCHANGE_STATUS, im.UNKNOWN),
        (im.ATTR_EXCHANGE_ELIGIBILITY, im.UNKNOWN),
        (im.ATTR_PROVIDER_TRADING_STATUS, im.INACTIVE),
        (im.ATTR_IDENTITY_STATUS, im.UNKNOWN),
    )
    for attribute, value in mutations:
        state = im.build_registry(_replace_value(rows, attribute, value)).as_of(
            DAY, T1
        )[CID]
        assert state.quant_eligibility == im.NOT_ELIGIBLE
    uncovered = im.build_registry(rows[:6]).as_of(DAY, T1)[CID]
    assert uncovered.provider_coverage == im.UNKNOWN
    assert uncovered.quant_eligibility == im.NOT_ELIGIBLE


def test_provider_alias_resolves_only_when_known() -> None:
    """Canonical IDs and provider alias tokens resolve at the query time."""
    alias = im.provider_alias("fyers", MARUTI_FY_TOKEN)
    rows = [
        _obs(im.ATTR_IDENTITY_STATUS, im.VERIFIED, observed_at=T1),
        _obs(
            im.provider_attribute("fyers", im.PROVIDER_KEY_ATTRIBUTE),
            alias,
            observed_at=T2,
            digest="d" * 64,
        ),
    ]
    registry = im.build_registry(rows)
    assert registry.resolve(alias, T1) is None
    assert registry.resolve(MARUTI_FY_TOKEN, T1) is None
    resolved = registry.resolve(alias, T2)
    assert resolved is not None
    assert resolved.canonical_instrument_id == CID
    assert registry.resolve(CID, T2) is not None
    assert registry.canonical_id_for(alias, as_of=T1) is None
    assert registry.canonical_id_for(alias, as_of=T2) == CID


def test_load_provenance_accepts_extended_fields(tmp_path: Path) -> None:
    """The canonical provenance with reference-data fields is accepted."""
    metas = im.load_provenance(PROVENANCE)
    assert set(metas) == {"upstox", "fyers"}
    assert metas["upstox"].market_date == "2026-11-26"
    assert metas["fyers"].schema_version == im.SCHEMA_VERSION
    document = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    document["snapshots"][0]["unexpected"] = 1
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown snapshot provenance"):
        im.load_provenance(candidate)


def test_evidence_report_uses_relative_reference_paths(tmp_path: Path) -> None:
    """The default evidence report carries no machine-local path."""
    output = tmp_path / "instrument-evidence.json"
    report, code = fetcher.evidence_report(
        output,
        tmp_path / "absent-data",
        tmp_path / "absent-evidence",
        PROVENANCE,
        "",
    )
    assert code == 0
    assert report["provenance_source"] == fetcher.SOURCE_REFERENCE
    text = output.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert str(ROOT) not in text
    assert not Path(report["source_root"]).is_absolute()
    for entry in report["snapshots"]:
        for kind in ("original", "decoded"):
            record = entry[kind]
            path = record["path"]
            assert not Path(path).is_absolute()
            assert path.startswith("reference-data/instrument-master/")
            assert record["relative_path"] == path


def test_module_query_aliases_match_registry_methods() -> None:
    """Public module-level queries survive the shadowing-safe rename."""
    registry = im.build_registry(
        [_obs(im.ATTR_IDENTITY_STATUS, im.VERIFIED, observed_at=T1)]
    )
    assert im.as_of(registry, DAY, T1) == registry.as_of(DAY, T1)
    assert im.resolve(registry, CID, T1) == registry.resolve(CID, T1)
    assert im.provider_coverage(registry, CID) == registry.provider_coverage(
        CID
    )
    missing = "missing_query"
    with pytest.raises(AttributeError, match="no attribute"):
        getattr(im, missing)


def test_expired_alias_cannot_resolve_on_a_later_market_date() -> None:
    """An alias interval is a market-validity bound, not a knowledge bound."""
    alias = im.provider_alias("fyers", "TOKEN1")
    registry = im.build_registry([_alias_obs(alias)])
    inside = registry.resolve_alias(alias, JAN, MID)
    assert inside.status == im.RESOLVED
    assert inside.validity == im.KNOWN_INTERVAL
    assert inside.canonical_instrument_id == CID
    assert registry.resolve(alias, JAN, MID) is not None
    expired = registry.resolve_alias(alias, MAR, MID)
    assert expired.status == im.EXPIRED
    assert expired.canonical_instrument_id is None
    assert registry.resolve(alias, MAR, MID) is None
    assert (
        registry.canonical_id_for(alias, market_date=MAR, observed_at=MID)
        is None
    )
    assert registry.canonical_id_for(alias, as_of=MID) is None
    assert registry.resolve_alias(alias, "2025-12-15", MID).status == (
        im.NOT_YET_VALID
    )
    assert registry.resolve_alias("missing", MAR, MID).status == im.NOT_FOUND


def test_unknown_interval_is_exposed_but_never_proven_valid() -> None:
    """Unknown bounds stay visible as an assessment, not a validity claim."""
    rows = [
        _obs(im.ATTR_EFFECTIVE_FROM, None, observed_at=T0),
        _obs(im.ATTR_EFFECTIVE_TO, None, observed_at=T0),
        _obs(im.ATTR_PROVIDER_TRADING_STATUS, im.ACTIVE, observed_at=T0),
    ]
    registry = im.build_registry(rows)
    state = registry.as_of(DAY, T1)[CID]
    assert state.validity == im.UNKNOWN_INTERVAL
    assert state.validity_assessment == im.UNKNOWN_INTERVAL
    assert state.as_dict()["validity"] == im.UNKNOWN_INTERVAL
    assert state.attribute_validity[im.ATTR_EFFECTIVE_FROM] == (
        im.UNKNOWN_INTERVAL
    )
    assert state.proven_valid is False
    assert state.effective_from is None
    assert registry.as_of(DAY, T1, proven_valid_only=True) == {}
    open_ended = im.build_registry(
        [_obs("tick_size", 1.0, observed_at=T0, valid_from=DAY)]
    )
    assert open_ended.as_of(NEXT_DAY, T1)[CID].validity == im.OPEN_ENDED
    assert open_ended.as_of(NEXT_DAY, T1)[CID].proven_valid is True
    assert open_ended.as_of("2026-11-25", T1) == {}
    half_unknown = im.build_registry(
        [_obs("tick_size", 1.0, observed_at=T0, valid_to=NEXT_DAY)]
    )
    assert half_unknown.as_of(DAY, T1)[CID].validity == im.UNKNOWN_INTERVAL


def test_ambiguous_alias_has_no_arbitrary_selection() -> None:
    """Overlapping historical claimants return AMBIGUOUS with no chosen ID."""
    alias = im.provider_alias("fyers", "TOKEN2")
    registry = im.build_registry(
        [
            _alias_obs(
                alias,
                CID + "-A",
                valid_from=JAN,
                valid_to=MAR,
                digest="1" * 64,
            ),
            _alias_obs(
                alias,
                CID + "-B",
                valid_from=FEB,
                valid_to=APR,
                digest="2" * 64,
            ),
        ]
    )
    resolution = registry.resolve_alias(alias, FEB, MID)
    assert resolution.status == im.AMBIGUOUS
    assert resolution.canonical_instrument_id is None
    assert resolution.claimants == (CID + "-A", CID + "-B")
    assert registry.resolve(alias, FEB, MID) is None
    assert (
        registry.canonical_id_for(alias, market_date=FEB, observed_at=MID)
        is None
    )


def test_token_reuse_is_detected_without_arbitrary_selection() -> None:
    """Sequential claimants flag reuse even when only one is valid today."""
    alias = im.provider_alias("fyers", "TOKEN3")
    registry = im.build_registry(
        [
            _alias_obs(alias, CID + "-A", digest="1" * 64),
            _alias_obs(
                alias,
                CID + "-B",
                valid_from=FEB,
                valid_to=MAR,
                digest="2" * 64,
            ),
        ]
    )
    resolution = registry.resolve_alias(alias, FEB, MID)
    assert resolution.status == im.TOKEN_REUSE
    assert resolution.canonical_instrument_id is None
    assert resolution.claimants == (CID + "-A", CID + "-B")
    assert registry.resolve(alias, FEB, MID) is None
    assert registry.detect_token_reuse() == (alias,)


def test_retroactive_correction_keeps_the_original_fact() -> None:
    """A later observation corrects history without rewriting append rows."""
    original = _obs(
        im.ATTR_PROVIDER_TRADING_STATUS,
        im.ACTIVE,
        observed_at=T0,
        valid_from=DAY,
        valid_to=NEXT_DAY,
        digest="1" * 64,
    )
    correction = _obs(
        im.ATTR_PROVIDER_TRADING_STATUS,
        im.INACTIVE,
        observed_at=T2,
        valid_from=DAY,
        valid_to=NEXT_DAY,
        digest="2" * 64,
    )
    registry = im.build_registry([correction, original])
    assert registry.observations == (original, correction)
    assert registry.as_of(DAY, T1)[CID].provider_trading_status == im.ACTIVE
    assert registry.as_of(DAY, T2)[CID].provider_trading_status == im.INACTIVE
    assert registry.as_of(NEXT_DAY, T2) == {}


def test_wrong_order_intervals_fail_closed() -> None:
    """Reversed and zero-length intervals are malformed, not silent."""
    with pytest.raises(ValueError, match="valid_from must precede"):
        im.observation(
            CID,
            "tick_size",
            1.0,
            observed_at=T0,
            valid_from=NEXT_DAY,
            valid_to=DAY,
            source_digest="a" * 64,
        )
    with pytest.raises(ValueError, match="valid_from must precede"):
        im.build_registry([_obs("x", 1, valid_from=DAY, valid_to=DAY)])
    bad_row = im.Observation(
        CID,
        "x",
        1,
        NEXT_DAY,
        DAY,
        T0,
        T0,
        None,
        "a" * 64,
        im.SCHEMA_VERSION,
        im.REGISTRY_VERSION,
    )
    with pytest.raises(ValueError, match="valid_from must precede"):
        im.build_registry([bad_row])


def test_malformed_timestamps_and_provenance_are_rejected() -> None:
    """Only attested timestamps and provenance can enter the registry."""
    with pytest.raises(ValueError, match="UTC offset"):
        im.observation(
            CID,
            "x",
            1,
            observed_at="2026-11-26T09:00:00",
            source_digest="a" * 64,
        )
    with pytest.raises(ValueError, match="source_digest"):
        im.observation(
            CID, "x", 1, observed_at=T0, source_digest="not-a-digest"
        )
    with pytest.raises(ValueError, match="source_version"):
        im.observation(
            CID,
            "x",
            1,
            observed_at=T0,
            source_digest="a" * 64,
            source_version="",
        )
    with pytest.raises(ValueError, match="provider_updated_at"):
        im.observation(
            CID,
            "x",
            1,
            observed_at=T0,
            source_digest="a" * 64,
            provider_updated_at="not-a-time",
        )
    bad_row = _obs("x", 1)._replace(registry_version="")
    with pytest.raises(ValueError, match="registry_version"):
        im.build_registry([bad_row])
    dated = im.observation(
        CID,
        "x",
        1,
        observed_at=T0,
        source_digest="a" * 64,
        provider_updated_at=DAY,
    )
    assert im.build_registry([dated]).as_of(DAY, T1)[CID] is not None


def test_resolve_separates_market_date_from_observation_time() -> None:
    """The new resolve path never uses the observation date as market date."""
    alias = im.provider_alias("fyers", "TOKEN4")
    claim = _alias_obs(
        alias,
        observed_at=T0,
        valid_from=DAY,
        valid_to=NEXT_DAY,
        digest="1" * 64,
    )
    old = _obs(
        im.ATTR_PROVIDER_TRADING_STATUS,
        im.ACTIVE,
        observed_at=T0,
        valid_from=DAY,
        valid_to=NEXT_DAY,
        digest="1" * 64,
    )
    correction = _obs(
        im.ATTR_PROVIDER_TRADING_STATUS,
        im.INACTIVE,
        observed_at=T2,
        valid_from=DAY,
        valid_to=NEXT_DAY,
        digest="2" * 64,
    )
    registry = im.build_registry([claim, old, correction])
    first = registry.resolve(alias, DAY, T1)
    assert first is not None
    assert first.market_date == DAY
    assert first.observed_at == T1
    assert first.provider_trading_status == im.ACTIVE
    later = registry.resolve(alias, DAY, T2)
    assert later is not None
    assert later.provider_trading_status == im.INACTIVE
    assert registry.resolve(alias, NEXT_DAY, T2) is None
    assert im.resolve(registry, alias, DAY, T1) == first


def test_converter_preserves_unknown_effective_dates(
    upstox_data, fyers_data
) -> None:
    """Pinned snapshots give observation evidence, never validity bounds."""
    rows = im.observations_from_snapshots()
    assert rows
    assert all(row.valid_from is None and row.valid_to is None for row in rows)
    digests = {meta.decoded_sha256 for meta in METAS.values()}
    assert {row.source_digest for row in rows} <= digests
    markets = {
        row.attribute_value
        for row in rows
        if row.attribute_name.endswith(":market_date")
    }
    assert markets == {DAY}
    observed = METAS["fyers"].captured_at_utc
    identity = _identity("upstox", MARUTI_KEY, upstox_data, fyers_data)
    registry = im.build_registry(rows)
    state = registry.as_of(DAY, observed)[identity]
    assert state.effective_from is None
    assert state.effective_to is None
    assert state.validity == im.UNKNOWN_INTERVAL
    assert state.proven_valid is False
    assert im.provider_alias("upstox", MARUTI_KEY) in state.provider_keys
    assert registry.as_of(DAY, observed, proven_valid_only=True) == {}
    alias = im.provider_alias("upstox", MARUTI_KEY)
    resolution = registry.resolve_alias(alias, DAY, observed)
    assert resolution.status == im.RESOLVED
    assert resolution.validity == im.UNKNOWN_INTERVAL
    assert resolution.canonical_instrument_id == identity


def test_perfect_instrument_with_unknown_validity_is_not_eligible() -> None:
    """Every other gate can pass while unknown validity still refuses."""
    state = im.build_registry(_eligible_rows(valid_from=None)).as_of(DAY, T1)[
        CID
    ]
    assert state.identity_status == im.VERIFIED
    assert state.provider_coverage == im.MULTI_SOURCE
    assert state.conformance_status == im.PASS
    assert state.provider_trading_status == im.ACTIVE
    assert state.exchange_status == im.OPEN
    assert state.exchange_eligibility == im.ALLOWED
    assert state.data_freshness == im.FRESH
    assert state.validity == im.UNKNOWN_INTERVAL
    assert state.proven_valid is False
    assert state.conflict_status == im.RESOLVED
    assert state.quant_eligibility == im.NOT_ELIGIBLE


def test_open_ended_and_bounded_intervals_gate_eligibility() -> None:
    """A known start is proven valid; an end still needs FRESH data."""
    rows = _eligible_rows()
    open_ended = im.build_registry(rows).as_of(DAY, T1)[CID]
    assert open_ended.validity == im.OPEN_ENDED
    assert open_ended.proven_valid is True
    assert open_ended.quant_eligibility == im.ELIGIBLE

    stale = im.build_registry(
        _replace_value(rows, im.ATTR_DATA_FRESHNESS, im.STALE)
    ).as_of(DAY, T1)[CID]
    assert stale.validity == im.OPEN_ENDED
    assert stale.quant_eligibility == im.NOT_ELIGIBLE

    bounded = [row._replace(valid_to=NEXT_DAY) for row in rows]
    known = im.build_registry(bounded).as_of(DAY, T1)[CID]
    assert known.validity == im.KNOWN_INTERVAL
    assert known.quant_eligibility == im.ELIGIBLE


def test_expired_interval_leaves_no_eligible_state() -> None:
    """A bounded interval stops selecting rows at its exclusive end."""
    rows = [row._replace(valid_to=NEXT_DAY) for row in _eligible_rows()]
    registry = im.build_registry(rows)
    assert registry.as_of(DAY, T1)[CID].quant_eligibility == im.ELIGIBLE
    assert registry.as_of(NEXT_DAY, T1) == {}


def test_alias_outside_validity_is_not_resolved() -> None:
    """Neither side of an alias interval yields a canonical ID."""
    alias = im.provider_alias("fyers", "OUTSIDE")
    registry = im.build_registry(
        [_alias_obs(alias, valid_from=JAN, valid_to=FEB)]
    )
    early = registry.resolve_alias(alias, "2025-12-31", MID)
    assert early.status == im.NOT_YET_VALID
    assert early.canonical_instrument_id is None
    assert registry.resolve(alias, "2025-12-31", MID) is None
    late = registry.resolve_alias(alias, FEB, MID)
    assert late.status == im.EXPIRED
    assert late.canonical_instrument_id is None
    assert registry.resolve(alias, FEB, MID) is None


def test_token_reuse_with_equal_precedence_is_ambiguous() -> None:
    """Two tied claimants are ambiguous, never chosen by input order."""
    alias = im.provider_alias("fyers", "REUSE")
    first = _alias_obs(
        alias,
        CID + "-A",
        observed_at=EARLY,
        valid_from=JAN,
        valid_to=MAR,
        digest="1" * 64,
    )
    second = _alias_obs(
        alias,
        CID + "-B",
        observed_at=EARLY,
        valid_from=FEB,
        valid_to=APR,
        digest="1" * 64,
    )
    registry = im.build_registry([second, first])
    resolution = registry.resolve_alias(alias, FEB, MID)
    assert resolution.status == im.AMBIGUOUS
    assert resolution.canonical_instrument_id is None
    assert resolution.claimants == (CID + "-A", CID + "-B")
    assert registry.resolve(alias, FEB, MID) is None


def test_equivalent_precedence_conflicts_are_permutation_invariant() -> None:
    """Tied rows with different values report CONFLICT in every order."""
    left = _obs("tick_size", 0.05, digest="1" * 64)
    right = _obs("tick_size", 0.10, digest="1" * 64)
    other = _obs("lot_size", 7, digest="2" * 64)
    baseline = None
    for order in itertools.permutations([left, right, other]):
        state = im.build_registry(list(order)).as_of(DAY, T1)[CID]
        result = (
            state.conformance_status,
            state.quant_eligibility,
            state.validity,
            tuple(sorted(state.conflicts.items())),
            state.attributes.get("tick_size"),
            state.attributes.get("lot_size"),
        )
        if baseline is None:
            baseline = result
        assert result == baseline
    assert baseline is not None
    assert baseline[0] == im.CONFLICT
    assert baseline[1] == im.NOT_ELIGIBLE
    assert state.conflict_status == im.AMBIGUOUS
    assert set(state.conflicts) == {"tick_size"}
    assert len(state.conflicts["tick_size"]) == 2
    identifiers = set(state.conflicts["tick_size"])
    assert identifiers == {
        im.observation_id(left),
        im.observation_id(right),
    }
    assert state.as_dict()["conflicts"] == dict(state.conflicts)


def test_invalid_interval_is_quarantined_before_eligibility() -> None:
    """Malformed intervals never enter the registry or pass the gate."""
    with pytest.raises(ValueError, match="valid_from must precede"):
        im.observation(
            CID,
            "tick_size",
            1.0,
            observed_at=T0,
            valid_from=NEXT_DAY,
            valid_to=DAY,
            source_digest="a" * 64,
        )
    direct = im.Observation(
        CID,
        "tick_size",
        1.0,
        NEXT_DAY,
        DAY,
        T0,
        T0,
        None,
        "a" * 64,
        im.SCHEMA_VERSION,
        im.REGISTRY_VERSION,
    )
    with pytest.raises(ValueError, match="valid_from must precede"):
        im.build_registry([direct])
    denied = im.fail_closed_eligibility(
        identity_status=im.VERIFIED,
        provider_coverage=im.MULTI_SOURCE,
        conformance_status=im.PASS,
        provider_trading_status=im.ACTIVE,
        exchange_status=im.OPEN,
        exchange_eligibility=im.ALLOWED,
        data_freshness=im.FRESH,
        validity=im.INVALID_INTERVAL,
    )
    assert denied == im.NOT_ELIGIBLE

    rows = _eligible_rows() + [
        _obs(im.ATTR_EFFECTIVE_FROM, NEXT_DAY),
        _obs(im.ATTR_EFFECTIVE_TO, DAY),
    ]
    state = im.build_registry(rows).as_of(DAY, T1)[CID]
    assert state.validity == im.INVALID_INTERVAL
    assert state.quant_eligibility == im.NOT_ELIGIBLE
