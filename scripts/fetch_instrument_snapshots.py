#!/usr/bin/env python3
"""Fetch the pinned public instrument masters used by the offline W1 tests.

Usage:
  python3 scripts/fetch_instrument_snapshots.py fetch [--provenance PATH]

Every URL, byte count and SHA-256 digest comes from the pinned provenance
record. The download uses urllib with no credentials or custom headers. No
network operation is performed at import time; callers decide when to fetch.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVENANCE = ROOT / "governance" / "instrument-master-snapshots.json"
DEFAULT_DATA_ROOT = ROOT / "data" / "instrument-master"
TIMEOUT_SECONDS = 60
MASTER_MODULE = "quant_instrument_master_fetcher"


@lru_cache(maxsize=1)
def _master() -> Any:
    """Load the offline canonical module by absolute path, without I/O."""
    path = ROOT / "python" / "instrument_master.py"
    spec = importlib.util.spec_from_file_location(MASTER_MODULE, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load instrument master: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MASTER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def load_pins(path: Path) -> tuple[Any, ...]:
    """Read the pinned provenance and return its AVAILABLE entries."""
    metas = tuple(_master().load_provenance(path).values())
    if not metas:
        raise ValueError(f"no snapshot provenance in {path}")
    return tuple(meta for meta in metas if meta.status == "AVAILABLE")


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


def paths(meta: Any, data_root: Path) -> tuple[Path, Path]:
    """Resolve original and decoded byte paths from pinned provenance."""
    master = _master()
    return (
        master.snapshot_path(data_root, meta, decoded=False),
        master.snapshot_path(data_root, meta),
    )


def verify_local(meta: Any, data_root: Path) -> bool:
    """Check local original and decoded bytes without any network access."""
    original_path, decoded_path = paths(meta, data_root)
    try:
        original = original_path.read_bytes()
        decoded = decoded_path.read_bytes()
    except OSError:
        return False
    return matches(
        original, meta.original_bytes, meta.original_sha256
    ) and matches(decoded, meta.decoded_bytes, meta.decoded_sha256)


def download(url: str) -> bytes:
    """Download the exact pinned URL with urllib and no auth headers."""
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def decode_original(payload: bytes, filename: str) -> bytes:
    """Return decoded snapshot bytes; gzip is decompressed when indicated."""
    if filename.endswith(".gz"):
        return gzip.decompress(payload)
    return payload


def stage(meta: Any, staging: Path) -> tuple[Any, Path, Path]:
    """Download, verify and stage one pinned snapshot in the temp directory."""
    original = download(meta.url)
    verify(
        original,
        meta.original_bytes,
        meta.original_sha256,
        f"{meta.provider} original",
    )
    decoded = decode_original(original, meta.original_filename)
    verify(
        decoded,
        meta.decoded_bytes,
        meta.decoded_sha256,
        f"{meta.provider} decoded",
    )
    original_path, decoded_path = paths(meta, staging)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(original)
    decoded_path.write_bytes(decoded)
    return meta, original_path, decoded_path


def publish(source: Path, destination: Path) -> None:
    """Replace one destination atomically using a sibling temporary file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".quant-", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def fetch(
    provenance: Path = DEFAULT_PROVENANCE,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> tuple[Any, ...]:
    """Ensure pinned bytes are present, fetching only absent or corrupt ones."""
    metas = load_pins(provenance)
    if not metas:
        raise ValueError(f"no AVAILABLE snapshots in {provenance}")
    pending = [meta for meta in metas if not verify_local(meta, data_root)]
    if not pending:
        return ()
    with tempfile.TemporaryDirectory(prefix="quant-") as name:
        staged = [stage(meta, Path(name)) for meta in pending]
        for meta, original_path, decoded_path in staged:
            final_original, final_decoded = paths(meta, data_root)
            publish(original_path, final_original)
            publish(decoded_path, final_decoded)
    for meta in pending:
        if not verify_local(meta, data_root):
            raise ValueError(
                f"published bytes failed verification: {meta.provider}"
            )
    return tuple(pending)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch_parser = subparsers.add_parser(
        "fetch", help="verify local pinned bytes, fetching missing ones"
    )
    fetch_parser.add_argument(
        "--provenance",
        type=Path,
        default=DEFAULT_PROVENANCE,
        help="pinned provenance JSON path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the fetch command, reporting verified digests."""
    args = parse_args(argv)
    try:
        fetched = fetch(args.provenance)
    except (OSError, ValueError, EOFError) as error:
        print(f"fetch failed: {error}", file=sys.stderr)
        return 1
    if not fetched:
        print("pinned instrument snapshots already present and verified")
        return 0
    for meta in fetched:
        print(
            f"verified {meta.provider}: original {meta.original_sha256}, "
            f"decoded {meta.decoded_sha256}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
