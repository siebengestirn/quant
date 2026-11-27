"""Offline tests against the two pinned official instrument masters."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import zipfile
from email.message import Message
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
REAL_DOWNLOAD = fetcher.download

PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
DATA = ROOT / "data" / "instrument-master"
EVIDENCE = ROOT / fetcher.BUNDLE_SNAPSHOT_DIR
BUNDLE_ENV = fetcher.BUNDLE_ENV
METAS = im.load_provenance(PROVENANCE)
MARUTI_ISIN = "INE585B01010"
MARUTI_KEY = f"NSE_EQ|{MARUTI_ISIN}"
MARUTI_TICKER = "NSE:MARUTI-EQ"
MARUTI_FY_TOKEN = "101000000010999"


def _local_available() -> bool:
    """Return True when every pinned snapshot has verified local bytes."""
    return all(
        fetcher.verify_local(meta, DATA)
        for meta in fetcher.load_pins(PROVENANCE)
    )


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
    data_root: Path, evidence_root: Path, bundle_path: str
) -> Path:
    """Resolve offline bytes or fail with the exact recovery command."""
    try:
        source = fetcher.resolve_evidence(
            data_root, evidence_root, PROVENANCE, bundle_path
        )
    except fetcher.EvidenceUnavailable as exc:
        pytest.fail(str(exc))
    return source.root


@pytest.fixture(scope="session", autouse=True)
def pinned_snapshots() -> None:
    """Prefer local bytes, then extracted evidence, then an offline ZIP."""
    global DATA
    DATA = _prepare_snapshots(DATA, EVIDENCE, os.environ.get(BUNDLE_ENV, ""))


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
    """Copy the verified pinned bytes into a temporary data root."""
    for meta in fetcher.load_pins(PROVENANCE):
        for source in fetcher.paths(meta, DATA):
            destination = data_root / source.relative_to(DATA)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())


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
        im.PASS: 10100,
        im.CONFLICT: 0,
        im.INCOMPLETE: 0,
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
    # Conformance compares contract fields only; identical duplicates agree.
    assert instrument.conformance_status == im.PASS
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
    assert report.status == im.PASS
    instrument = im.crosswalk([im.ParseResult((up,), (), 1)]).instruments[0]
    assert instrument.identity_status == im.UNMAPPED
    assert instrument.conformance_status == im.PASS
    assert instrument.conformance_report.status == im.PASS
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


def test_verify_cli_checks_local_bytes_without_network(tmp_path):
    """The verify command exits 0 for valid bytes and 1 for corrupt bytes."""
    _copy_pinned_bytes(tmp_path)
    assert fetcher.main(["verify", "--data-root", str(tmp_path)]) == 0
    meta = METAS["upstox"]
    decoded = im.snapshot_path(tmp_path, meta)
    payload = decoded.read_bytes()
    decoded.write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    assert fetcher.main(["verify", "--data-root", str(tmp_path)]) == 1


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
        fetcher._check_content_type(Response("text/plain", "1"), url, ".json")
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
                source = fetcher.paths(meta, DATA)[int(decoded)]
                archive.writestr(
                    fetcher._zip_member_name(meta, decoded),
                    source.read_bytes(),
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


def test_evidence_resolution_prefers_verified_local_bytes(tmp_path):
    """Valid local bytes win over an evidence directory or a bundle."""
    source = fetcher.resolve_evidence(
        DATA, tmp_path / "evidence", PROVENANCE, ""
    )
    assert source.root == DATA
    assert source.source == fetcher.SOURCE_DATA
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
    assert _prepare_snapshots(tmp_path / "empty-data", evidence, "") == evidence
    source = fetcher.resolve_evidence(
        tmp_path / "empty-data", evidence, PROVENANCE, ""
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
    with pytest.raises(fetcher.EvidenceUnavailable) as exc:
        fetcher.resolve_evidence(
            tmp_path / "data", tmp_path / "evidence", PROVENANCE, ""
        )
    message = str(exc.value)
    for token in ("extract --from-bundle", "verify", "pytest", "network"):
        assert token in message
    with pytest.raises(pytest.fail.Exception) as failed:
        _prepare_snapshots(tmp_path / "data", tmp_path / "evidence", "")
    assert BUNDLE_ENV in str(failed.value)


def test_tampered_snapshot_fails_resolution_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modified snapshot is neither resolved nor reported as verified."""
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    data = tmp_path / "data"
    _copy_pinned_bytes(data)
    meta = fetcher.load_pins(PROVENANCE)[0]
    target = fetcher.paths(meta, data)[1]
    payload = target.read_bytes()
    target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    assert not fetcher.verify_local(meta, data)
    with pytest.raises(fetcher.EvidenceUnavailable):
        fetcher.resolve_evidence(data, tmp_path / "no-evidence", PROVENANCE, "")
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
        output, data, tmp_path / "no-evidence", PROVENANCE, ""
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
    """The JSON report records exact paths, byte counts and digests."""
    output = tmp_path / "assurance" / "instrument-evidence.json"
    report, code = fetcher.evidence_report(
        output, DATA, tmp_path / "evidence", PROVENANCE, ""
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert text == json.dumps(report, indent=2, sort_keys=True) + "\n"
    assert report["schema_version"] == fetcher.REPORT_SCHEMA_VERSION
    assert report["verification_status"] == fetcher.PASS
    assert report["provenance_source"] == fetcher.SOURCE_DATA
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
        assert entry["provenance_source"] == fetcher.SOURCE_DATA
        original, decoded = fetcher.paths(meta, DATA)
        for kind, path, size, digest in (
            (
                "original",
                original,
                meta.original_bytes,
                meta.original_sha256,
            ),
            ("decoded", decoded, meta.decoded_bytes, meta.decoded_sha256),
        ):
            record = entry[kind]
            assert Path(record["path"]) == path.resolve()
            assert (
                record["relative_path"]
                == path.resolve().relative_to(ROOT).as_posix()
            )
            assert record["bytes"] == size
            assert record["sha256"] == digest
            assert record["expected_bytes"] == size
            assert record["expected_sha256"] == digest
            assert record["verification"] == fetcher.PASS


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
