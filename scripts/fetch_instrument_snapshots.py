#!/usr/bin/env python3
"""Offline verification and explicit retrieval of pinned instrument snapshots.

Usage:
  python3 scripts/fetch_instrument_snapshots.py verify [--data-root DIR]
  python3 scripts/fetch_instrument_snapshots.py fetch [--output DIR]
  python3 scripts/fetch_instrument_snapshots.py extract \\
      --from-bundle PATH [--output DIR]
  python3 scripts/fetch_instrument_snapshots.py evidence-report \\
      [--output-report PATH] [--data-root DIR] [--evidence-root DIR] \\
      [--bundle PATH]

Offline evidence resolves in this order: verified local
``data/instrument-master`` bytes, a verified ``evidence/instrument-master``
directory at the repository root (an extracted bundle), then a release ZIP
named by ``QUANT_RELEASE_BUNDLE``, extracted with the offline ``extract``
command. ``verify``, ``extract`` and ``evidence-report`` are offline. Only
``fetch`` performs network I/O, and only when explicitly requested; it never
runs at import time. All URLs, byte counts and digests come from the pinned
provenance record. The download uses HTTPS without credentials or headers.

Gate wiring (exact command, no network):
  python3 scripts/fetch_instrument_snapshots.py evidence-report \\
      --output-report data/assurance/instrument-evidence.json
The report is written even when verification fails; a nonzero exit code means
the evidence did not verify. Tests never download snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
DEFAULT_DATA_ROOT = ROOT / "data" / "instrument-master"
TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1 << 16
MAX_ORIGINAL_BYTES = 20 * 1024 * 1024
MAX_DECODED_BYTES = 100 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"assets.upstox.com", "public.fyers.in"})
PROVIDERS = frozenset({"upstox", "fyers"})
SAFE_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]*")
SAFE_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SAFE_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
CONTENT_TYPES = {
    ".gz": frozenset(
        {"application/gzip", "application/x-gzip", "application/octet-stream"}
    ),
    ".json": frozenset(
        {"application/json", "text/json", "application/octet-stream"}
    ),
}
BUNDLE_PREFIX = "quant/"
BUNDLE_SNAPSHOT_DIR = "evidence/instrument-master"
MASTER_MODULE = "quant_instrument_master_fetcher"
DEFAULT_EVIDENCE_ROOT = ROOT / BUNDLE_SNAPSHOT_DIR
DEFAULT_EVIDENCE_REPORT = (
    ROOT / "data" / "assurance" / "instrument-evidence.json"
)
BUNDLE_ENV = "QUANT_RELEASE_BUNDLE"
SOURCE_DATA = "data/instrument-master"
SOURCE_EVIDENCE = "evidence/instrument-master"
SOURCE_BUNDLE = "QUANT_RELEASE_BUNDLE"
REPORT_SCHEMA_VERSION = 1
PASS = "PASS"
FAIL = "FAIL"


class EvidenceSource(NamedTuple):
    """The verified offline root and how its bytes were made available."""

    root: Path
    source: str
    extraction_command: str


class EvidenceUnavailable(ValueError):
    """No verified offline snapshot source is available."""


@lru_cache(maxsize=1)
def _master() -> Any:
    """Load the offline canonical module by absolute path."""
    path = ROOT / "python" / "instrument_master.py"
    spec = importlib.util.spec_from_file_location(MASTER_MODULE, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load instrument master: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MASTER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of overwriting them."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _provenance_entries(path: Path) -> list[dict]:
    """Read and shape-check the snapshots array from pinned provenance."""
    try:
        document = json.loads(
            path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable provenance {path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(
        document.get("snapshots"), list
    ):
        raise ValueError("provenance requires a snapshots array")
    entries = document["snapshots"]
    fields = _master().SNAPSHOT_FIELDS
    if not all(
        isinstance(entry, dict) and set(entry) == fields for entry in entries
    ):
        raise ValueError("unexpected snapshot provenance fields")
    return entries


def validate_url(url: str) -> str:
    """Require an HTTPS URL on an allowlisted host without credentials."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"non-HTTPS URL: {url!r}")
    if parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowlisted: {parts.hostname!r}")
    if parts.username or parts.password:
        raise ValueError(f"URL credentials are not allowed: {url!r}")
    if parts.port not in (None, 443):
        raise ValueError(f"unexpected URL port: {parts.port}")
    if parts.fragment or not parts.path:
        raise ValueError(f"unexpected URL form: {url!r}")
    return url


class AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave HTTPS or the pinned host allowlist."""

    def allowed(self, url: str) -> bool:
        """Return True when a redirect target passes the URL policy."""
        try:
            validate_url(url)
        except ValueError:
            return False
        return True

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Validate the redirect target before following it."""
        if not self.allowed(newurl):
            raise ValueError(f"disallowed redirect target: {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_filename(value: str) -> str:
    """Require a plain, relative snapshot filename with a known suffix."""
    if not SAFE_FILENAME_RE.fullmatch(value) or ".." in value:
        raise ValueError(f"unsafe snapshot filename: {value!r}")
    if not value.endswith((".gz", ".json")):
        raise ValueError(f"unsupported snapshot filename: {value!r}")
    return value


def safe_market_date(value: str) -> str:
    """Require a real ISO calendar date for the snapshot market_date."""
    if not SAFE_DATE_RE.fullmatch(value):
        raise ValueError(f"unsafe market_date: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid market_date: {value!r}") from exc
    return value


def _validate_size_digest(label: str, size: object, digest: object) -> None:
    """Check one pinned byte count and lowercase SHA-256 digest."""
    maximum = MAX_ORIGINAL_BYTES if label == "original" else MAX_DECODED_BYTES
    if isinstance(size, bool) or not isinstance(size, int):
        raise ValueError(f"invalid {label} byte count: {size!r}")
    if not 0 < size <= maximum:
        raise ValueError(f"{label} byte count exceeds {maximum}: {size}")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest.lower()):
        raise ValueError(f"invalid {label} SHA-256: {digest!r}")


def validate_meta(meta: Any) -> None:
    """Fail closed on unsafe provenance fields before deriving any path."""
    if meta.provider not in PROVIDERS or not SAFE_PROVIDER_RE.fullmatch(
        meta.provider
    ):
        raise ValueError(f"unsupported provider: {meta.provider!r}")
    safe_market_date(meta.market_date)
    safe_filename(meta.original_filename)
    safe_filename(_master().decoded_filename(meta.original_filename))
    validate_url(meta.url)
    _validate_size_digest("original", meta.original_bytes, meta.original_sha256)
    _validate_size_digest("decoded", meta.decoded_bytes, meta.decoded_sha256)


def ensure_contained(root: Path, path: Path) -> None:
    """Require a resolved path to stay strictly below the output root."""
    base = root.resolve()
    target = path.resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError(f"snapshot path escapes output directory: {path}")


def paths(meta: Any, root: Path) -> tuple[Path, Path]:
    """Resolve validated original and decoded byte paths under a root."""
    validate_meta(meta)
    original = _master().snapshot_path(root, meta, decoded=False)
    decoded = _master().snapshot_path(root, meta)
    ensure_contained(root, original)
    ensure_contained(root, decoded)
    return original, decoded


def load_pins(path: Path) -> tuple[Any, ...]:
    """Read, validate and return the unique AVAILABLE provenance entries."""
    metas = [
        _master().SnapshotMeta(**entry) for entry in _provenance_entries(path)
    ]
    for meta in metas:
        validate_meta(meta)
    providers = [meta.provider for meta in metas]
    if len(providers) != len(set(providers)):
        raise ValueError(f"duplicate provider in {path}")
    available = tuple(meta for meta in metas if meta.status == "AVAILABLE")
    if not available:
        raise ValueError(f"no AVAILABLE snapshots in {path}")
    relative = [
        entry.as_posix() for meta in available for entry in paths(meta, Path())
    ]
    if len(relative) != len(set(relative)):
        raise ValueError(f"duplicate snapshot paths in {path}")
    return available


def matches(payload: bytes, size: int, digest: str) -> bool:
    """Return True when bytes match the pinned size and SHA-256 digest."""
    return (
        len(payload) == size and hashlib.sha256(payload).hexdigest() == digest
    )


def verify(payload: bytes, size: int, digest: str, label: str) -> None:
    """Fail loudly when bytes disagree with the pinned size or digest."""
    if len(payload) != size:
        raise ValueError(f"{label}: expected {size} bytes, got {len(payload)}")
    observed = hashlib.sha256(payload).hexdigest()
    if observed != digest:
        raise ValueError(f"{label}: expected SHA-256 {digest}, got {observed}")


def read_limited_file(path: Path, max_bytes: int) -> bytes:
    """Read one local file through the bounded streaming reader."""
    with path.open("rb") as handle:
        return read_limited(handle, f"local file {path}", max_bytes)


def verify_local(meta: Any, data_root: Path) -> bool:
    """Check local original and decoded bytes without any network access."""
    original_path, decoded_path = paths(meta, data_root)
    try:
        original = read_limited_file(original_path, meta.original_bytes + 1)
        decoded = read_limited_file(decoded_path, meta.decoded_bytes + 1)
    except (OSError, ValueError):
        return False
    return matches(
        original, meta.original_bytes, meta.original_sha256
    ) and matches(decoded, meta.decoded_bytes, meta.decoded_sha256)


def verify_all(
    provenance: Path = DEFAULT_PROVENANCE,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> tuple[Any, ...]:
    """Return the pinned snapshots whose local bytes fail verification."""
    return tuple(
        meta
        for meta in load_pins(provenance)
        if not verify_local(meta, data_root)
    )


def read_limited(handle: Any, label: str, max_bytes: int) -> bytes:
    """Stream bytes from a handle, failing before any unbounded read."""
    payload = bytearray()
    while chunk := handle.read(CHUNK_SIZE):
        if len(payload) + len(chunk) > max_bytes:
            raise ValueError(f"{label}: exceeds {max_bytes} bytes")
        payload.extend(chunk)
    return bytes(payload)


def _check_content_length(response: Any, url: str, max_bytes: int) -> None:
    """Fail early on a declared body larger than the pinned limit."""
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise ValueError(f"{url}: Content-Length exceeds {max_bytes} bytes")


def _check_content_type(response: Any, url: str, suffix: str) -> None:
    """Fail on a missing or unexpected provider content type."""
    observed = response.headers.get_content_type().lower()
    allowed = CONTENT_TYPES.get(suffix, frozenset())
    if observed not in allowed:
        raise ValueError(f"{url}: unexpected content type {observed!r}")


def download(
    url: str,
    max_bytes: int = MAX_ORIGINAL_BYTES,
    suffix: str = ".json",
) -> bytes:
    """Stream one pinned URL over HTTPS with host and size limits."""
    validate_url(url)
    opener = urllib.request.build_opener(AllowlistedRedirect())
    try:
        with opener.open(url, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ValueError(f"{url}: unexpected HTTP status {status}")
            _check_content_length(response, url, max_bytes)
            _check_content_type(response, url, suffix)
            return read_limited(response, url, max_bytes)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"{url}: HTTP status {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"{url}: network error: {exc.reason}") from exc


def _gzip_isize(payload: bytes) -> int:
    """Read the declared uncompressed size from the gzip trailer."""
    if len(payload) < 18 or payload[:2] != b"\x1f\x8b":
        raise ValueError("original bytes are not gzip data")
    return int.from_bytes(payload[-4:], "little")


def _append_decoded(
    chunks: list[bytes], output: bytes, total: int, max_bytes: int
) -> int:
    """Accumulate decoded bytes while enforcing the decoded byte limit."""
    total += len(output)
    if total > max_bytes:
        raise ValueError(f"decoded data exceeds {max_bytes} bytes")
    if output:
        chunks.append(output)
    return total


def _gunzip(payload: bytes, max_bytes: int) -> bytes:
    """Decompress gzip bytes incrementally with a hard decoded byte limit."""
    declared = _gzip_isize(payload)
    if declared > max_bytes:
        raise ValueError(
            f"gzip declares {declared} decoded bytes, limit {max_bytes}"
        )
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks: list[bytes] = []
    total = 0
    for start in range(0, len(payload), CHUNK_SIZE):
        chunk = payload[start : start + CHUNK_SIZE]
        output = decompressor.decompress(chunk, max_bytes - total + 1)
        total = _append_decoded(chunks, output, total, max_bytes)
        while decompressor.unconsumed_tail:
            output = decompressor.decompress(
                decompressor.unconsumed_tail, max_bytes - total + 1
            )
            total = _append_decoded(chunks, output, total, max_bytes)
    output = decompressor.flush(max_bytes - total + 1)
    total = _append_decoded(chunks, output, total, max_bytes)
    if not decompressor.eof or decompressor.unused_data:
        raise ValueError("gzip stream is incomplete or has trailing data")
    return b"".join(chunks)


def decode_original(payload: bytes, filename: str, max_bytes: int) -> bytes:
    """Return decoded snapshot bytes with bounded decompression."""
    if not filename.endswith(".gz"):
        if len(payload) > max_bytes:
            raise ValueError(f"original bytes exceed {max_bytes} bytes")
        return payload
    return _gunzip(payload, max_bytes)


def _write_atomic(destination: Path, payload: bytes) -> None:
    """Write one byte payload through a sibling temporary file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".quant-", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with temporary.open("wb") as output:
            output.write(payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def stage(meta: Any, staging: Path) -> tuple[Any, Path, Path]:
    """Download, verify and stage one pinned snapshot in a temp directory."""
    suffix = Path(meta.original_filename).suffix
    original = download(meta.url, MAX_ORIGINAL_BYTES, suffix)
    verify(
        original,
        meta.original_bytes,
        meta.original_sha256,
        f"{meta.provider} original",
    )
    decoded = decode_original(
        original,
        meta.original_filename,
        min(meta.decoded_bytes, MAX_DECODED_BYTES),
    )
    verify(
        decoded,
        meta.decoded_bytes,
        meta.decoded_sha256,
        f"{meta.provider} decoded",
    )
    original_path, decoded_path = paths(meta, staging)
    _write_atomic(original_path, original)
    _write_atomic(decoded_path, decoded)
    return meta, original_path, decoded_path


def publish(source: Path, destination: Path) -> None:
    """Replace one destination atomically using a sibling temporary file."""
    _write_atomic(destination, source.read_bytes())


def _commit_staged(
    staged: list[tuple[Any, Path, Path]],
    data_root: Path,
) -> None:
    """Publish staged snapshots and re-verify their final bytes."""
    for meta, original_path, decoded_path in staged:
        final_original, final_decoded = paths(meta, data_root)
        publish(original_path, final_original)
        publish(decoded_path, final_decoded)
    for meta, _, _ in staged:
        if not verify_local(meta, data_root):
            raise ValueError(
                f"published bytes failed verification: {meta.provider}"
            )


def fetch(
    provenance: Path = DEFAULT_PROVENANCE,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> tuple[Any, ...]:
    """Fetch missing or corrupt pinned bytes; the only network entry point."""
    metas = load_pins(provenance)
    pending = [meta for meta in metas if not verify_local(meta, data_root)]
    if not pending:
        return ()
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        staged = [stage(meta, Path(name)) for meta in pending]
        _commit_staged(staged, data_root)
    return tuple(pending)


def _zip_member_name(meta: Any, decoded: bool) -> str:
    """Return the exact bundle member name for one pinned snapshot."""
    relative = paths(meta, Path(BUNDLE_SNAPSHOT_DIR))[int(decoded)]
    return f"{BUNDLE_PREFIX}{relative.as_posix()}"


def _read_zip_member(
    archive: zipfile.ZipFile, name: str, max_bytes: int
) -> bytes:
    """Read one exact ZIP member with a validated name and size limit."""
    relative = name.removeprefix(BUNDLE_PREFIX)
    if (
        not name.startswith(BUNDLE_PREFIX)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError(f"unsafe bundle member: {name!r}")
    info = archive.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"{name}: member exceeds {max_bytes} bytes")
    with archive.open(info) as handle:
        return read_limited(handle, name, max_bytes)


def extract_from_bundle(
    bundle: Path,
    data_root: Path = DEFAULT_DATA_ROOT,
    provenance: Path = DEFAULT_PROVENANCE,
) -> tuple[Any, ...]:
    """Extract the four pinned snapshots from a release ZIP, offline."""
    metas = load_pins(provenance)
    with zipfile.ZipFile(bundle) as archive:
        with tempfile.TemporaryDirectory(prefix="quant-") as name:
            staging = Path(name)
            staged = []
            for meta in metas:
                names = (
                    _zip_member_name(meta, False),
                    _zip_member_name(meta, True),
                )
                original = _read_zip_member(
                    archive, names[0], MAX_ORIGINAL_BYTES
                )
                decoded = _read_zip_member(archive, names[1], MAX_DECODED_BYTES)
                verify(
                    original,
                    meta.original_bytes,
                    meta.original_sha256,
                    f"{meta.provider} original",
                )
                verify(
                    decoded,
                    meta.decoded_bytes,
                    meta.decoded_sha256,
                    f"{meta.provider} decoded",
                )
                original_path, decoded_path = paths(meta, staging)
                _write_atomic(original_path, original)
                _write_atomic(decoded_path, decoded)
                staged.append((meta, original_path, decoded_path))
            _commit_staged(staged, data_root)
    return metas


def extract_argv(bundle: Path, data_root: Path, provenance: Path) -> list[str]:
    """Return the arguments of the offline bundle-extraction command."""
    return [
        "extract",
        "--from-bundle",
        str(bundle),
        "--output",
        str(data_root),
        "--provenance",
        str(provenance),
    ]


def extract_command(bundle: Path, data_root: Path, provenance: Path) -> str:
    """Render the exact, reproducible offline extraction command."""
    argv = ["python3", "scripts/fetch_instrument_snapshots.py"]
    return shlex.join([*argv, *extract_argv(bundle, data_root, provenance)])


def evidence_unavailable_message(detail: str = "") -> str:
    """Return the precise command sequence that restores offline evidence."""
    example = " ".join(
        [
            "python3",
            "scripts/fetch_instrument_snapshots.py",
            "extract",
            "--from-bundle",
            "/path/to/quant-final-release.zip",
            "--output",
            SOURCE_DATA,
        ]
    )
    prefix = f"{detail} " if detail else ""
    return (
        f"{prefix}No verified pinned instrument snapshots are available in "
        f"{SOURCE_DATA} or {SOURCE_EVIDENCE}, and {BUNDLE_ENV} is unset or "
        "does not name a usable release ZIP. Restore the evidence offline, "
        "then verify and run the tests:\n"
        f"  {example}\n"
        "  python3 scripts/fetch_instrument_snapshots.py verify\n"
        "  python3 -m pytest tests/test_instrument_master.py "
        "-o addopts='' -q\n"
        "Tests never use the network. Retrieval is a separate explicit "
        "command: python3 scripts/fetch_instrument_snapshots.py fetch"
    )


def _verified_at(root: Path, metas: tuple[Any, ...]) -> bool:
    """Return True when every pinned snapshot verifies below one root."""
    return all(verify_local(meta, root) for meta in metas)


def resolve_evidence(
    data_root: Path = DEFAULT_DATA_ROOT,
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    provenance: Path = DEFAULT_PROVENANCE,
    bundle_path: str | Path | None = None,
) -> EvidenceSource:
    """Resolve verified offline bytes, never contacting a provider."""
    metas = load_pins(provenance)
    if _verified_at(data_root, metas):
        return EvidenceSource(data_root, SOURCE_DATA, "")
    if _verified_at(evidence_root, metas):
        return EvidenceSource(evidence_root, SOURCE_EVIDENCE, "")
    value = (
        bundle_path
        if bundle_path is not None
        else os.environ.get(BUNDLE_ENV, "")
    )
    if not value:
        raise EvidenceUnavailable(evidence_unavailable_message())
    bundle = Path(value).expanduser()
    if not bundle.is_absolute():
        bundle = Path.cwd() / bundle
    if not bundle.is_file():
        raise EvidenceUnavailable(
            evidence_unavailable_message(f"bundle not found: {bundle}")
        )
    command = extract_command(bundle, data_root, provenance)
    if main(extract_argv(bundle, data_root, provenance)) != 0:
        raise EvidenceUnavailable(
            evidence_unavailable_message(f"extraction failed: {command}")
        )
    if not _verified_at(data_root, metas):
        raise EvidenceUnavailable(
            evidence_unavailable_message(
                f"extracted bytes failed verification: {command}"
            )
        )
    return EvidenceSource(data_root, SOURCE_BUNDLE, command)


def _relative(path: Path) -> str:
    """Return a repository-relative path, or an empty string when outside."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return ""


def _byte_record(path: Path, size: int, digest: str) -> dict:
    """Return exact path, byte count and SHA-256 for one snapshot file."""
    record = {
        "path": str(path.resolve()),
        "relative_path": _relative(path),
        "bytes": 0,
        "sha256": "",
        "expected_bytes": size,
        "expected_sha256": digest,
        "verification": FAIL,
    }
    try:
        payload = read_limited_file(path, size + 1)
    except (OSError, ValueError) as exc:
        record["error"] = str(exc)
        return record
    record["bytes"] = len(payload)
    record["sha256"] = hashlib.sha256(payload).hexdigest()
    record["verification"] = PASS if matches(payload, size, digest) else FAIL
    return record


def build_evidence_report(source: EvidenceSource, provenance: Path) -> dict:
    """Assemble the machine-readable evidence report for one offline source."""
    snapshots = []
    for meta in load_pins(provenance):
        original, decoded = paths(meta, source.root)
        entry = {
            "provider": meta.provider,
            "market_date": meta.market_date,
            "provenance_source": source.source,
            "original": _byte_record(
                original, meta.original_bytes, meta.original_sha256
            ),
            "decoded": _byte_record(
                decoded, meta.decoded_bytes, meta.decoded_sha256
            ),
        }
        entry["verification_status"] = (
            PASS
            if entry["original"]["verification"] == PASS
            and entry["decoded"]["verification"] == PASS
            else FAIL
        )
        snapshots.append(entry)
    status = (
        PASS
        if all(item["verification_status"] == PASS for item in snapshots)
        else FAIL
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": _relative(provenance) or str(provenance),
        "provenance_source": source.source,
        "source_root": str(source.root.resolve()),
        "extraction_command": source.extraction_command,
        "verification_status": status,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }


def evidence_report(
    output: Path = DEFAULT_EVIDENCE_REPORT,
    data_root: Path = DEFAULT_DATA_ROOT,
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    provenance: Path = DEFAULT_PROVENANCE,
    bundle_path: str | Path | None = None,
) -> tuple[dict, int]:
    """Write the offline evidence report and return it with an exit code."""
    source = resolve_evidence(data_root, evidence_root, provenance, bundle_path)
    report = build_evidence_report(source, provenance)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    _write_atomic(output, text.encode("utf-8"))
    return report, 0 if report["verification_status"] == PASS else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments with explicit offline/network commands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser(
        "verify", help="verify local pinned bytes without network access"
    )
    verify_parser.add_argument(
        "--provenance", type=Path, default=DEFAULT_PROVENANCE
    )
    verify_parser.add_argument(
        "--data-root", type=Path, default=DEFAULT_DATA_ROOT
    )
    fetch_parser = subparsers.add_parser(
        "fetch", help="explicitly fetch missing pinned bytes over HTTPS"
    )
    fetch_parser.add_argument(
        "--provenance", type=Path, default=DEFAULT_PROVENANCE
    )
    fetch_parser.add_argument("--output", type=Path, default=DEFAULT_DATA_ROOT)
    extract_parser = subparsers.add_parser(
        "extract", help="extract pinned bytes from a release ZIP, offline"
    )
    extract_parser.add_argument(
        "--from-bundle", type=Path, required=True, help="release bundle ZIP"
    )
    extract_parser.add_argument(
        "--output", type=Path, default=DEFAULT_DATA_ROOT
    )
    extract_parser.add_argument(
        "--provenance", type=Path, default=DEFAULT_PROVENANCE
    )
    report_parser = subparsers.add_parser(
        "evidence-report", help="write the offline snapshot evidence report"
    )
    report_parser.add_argument(
        "--output-report", type=Path, default=DEFAULT_EVIDENCE_REPORT
    )
    report_parser.add_argument(
        "--data-root", type=Path, default=DEFAULT_DATA_ROOT
    )
    report_parser.add_argument(
        "--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT
    )
    report_parser.add_argument(
        "--provenance", type=Path, default=DEFAULT_PROVENANCE
    )
    report_parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help=f"release ZIP, overriding ${BUNDLE_ENV}",
    )
    return parser.parse_args(argv)


def _print_verified(meta: Any) -> None:
    """Print the exact byte counts and digests for one pinned snapshot."""
    print(
        f"{meta.provider}: original {meta.original_bytes} bytes "
        f"{meta.original_sha256}, decoded {meta.decoded_bytes} bytes "
        f"{meta.decoded_sha256}"
    )


def _verify_command(args: argparse.Namespace) -> int:
    """Dispatch offline verification, reporting every pinned snapshot."""
    metas = load_pins(args.provenance)
    failures = verify_all(args.provenance, args.data_root)
    for meta in metas:
        status = "FAIL" if meta in failures else "PASS"
        print(f"{status} {meta.provider}")
        _print_verified(meta)
    if failures:
        print(f"verification failed for {len(failures)} snapshot(s)")
        return 1
    print(f"verified {len(metas)} pinned snapshots (offline)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch verify, fetch, extract or evidence-report offline."""
    args = parse_args(argv)
    try:
        if args.command == "verify":
            return _verify_command(args)
        if args.command == "fetch":
            fetched = fetch(args.provenance, args.output)
            if not fetched:
                print("pinned instrument snapshots already present")
                return 0
            for meta in fetched:
                print(f"fetched {meta.provider}")
                _print_verified(meta)
            return 0
        if args.command == "evidence-report":
            report, code = evidence_report(
                args.output_report,
                args.data_root,
                args.evidence_root,
                args.provenance,
                args.bundle,
            )
            print(f"evidence report: {report['verification_status']}")
            print(f"Source: {report['provenance_source']}")
            print(f"Report: {args.output_report}")
            return code
        extracted = extract_from_bundle(
            args.from_bundle, args.output, args.provenance
        )
        for meta in extracted:
            print(f"extracted {meta.provider}")
            _print_verified(meta)
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        zipfile.BadZipFile,
        KeyError,
    ) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
