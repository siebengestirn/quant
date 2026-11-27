"""Adversarial cross-language dataset contract corpus (SIMULATED fixtures).

The canonical C++ binary comes from ``QUANT_CONTRACT_BIN`` when that variable
is set, and otherwise falls back to ``build/quant_contract``
(``CONTRACT_TOOL``). ``scripts/verify_report.py`` always sets the variable to
the freshly built ``build-ci/quant_contract`` so a stale, ignored ``build/``
binary cannot satisfy these tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_TOOL = Path(
    os.environ.get("QUANT_CONTRACT_BIN", str(ROOT / "build" / "quant_contract"))
)
FIXTURES = ROOT / "data" / "review-fixtures" / "dataset-adversarial"

CSV = (
    "date,open,high,low,close,volume\n"
    "2026-01-01,100,103,99,102,1000\n"
    "2026-01-02,102,105,101,104,1200\n"
)


def _module(name: str):
    """Load the shared dataset contract module under a unique name."""
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "python" / "dataset_contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _base() -> dict:
    """Return a valid v2 expectations object with empty storage keys."""
    return {
        "schema_version": 2,
        "snapshot_id": "SNAP-ADVERSARIAL",
        "calendar_id": "NSE_CASH_DAILY_OBSERVED",
        "generated_at_ms": 1769000000000,
        "provenance": "REVIEW_FIXTURE",
        "symbols": ["AAA"],
        "sessions": ["2026-01-01", "2026-01-02"],
        "first_date": "2026-01-01",
        "last_date": "2026-01-02",
        "min_rows": 1,
        "instrument_ids": {"AAA": "ID-AAA"},
        "storage_keys": {},
    }


def _fixture(name: str, module) -> tuple[Path, dict]:
    """Write a valid snapshot fixture and return its directory and manifest."""
    expectations = _base()
    expectations["storage_keys"] = {
        symbol: module.storage_key(expectations["instrument_ids"][symbol])
        for symbol in expectations["symbols"]
    }
    directory = FIXTURES / name
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.iterdir():
        old.unlink()
    for key in expectations["storage_keys"].values():
        (directory / f"{key}.csv").write_text(CSV)
    assert module.write_contract(directory, expectations) == []
    assert module.write_manifest(directory, expectations) == []
    manifest = json.loads((directory / "manifest.json").read_text())
    return directory, manifest


def _commit(directory: Path, contract: dict, manifest: dict, module) -> None:
    """Persist mutated objects with recomputed identity digests."""
    (directory / "contract.json").write_text(json.dumps(contract))
    manifest["contract_digest"] = module.contract_digest(contract)
    manifest["contract_sha256"] = module.sha256_hex(
        (directory / "contract.json").read_bytes()
    )
    (directory / "manifest.json").write_text(json.dumps(manifest))


def _statuses(directory: Path, module) -> tuple[str, str]:
    """Return Python and C++ verifier statuses for one fixture."""
    result = subprocess.run(
        [str(CONTRACT_TOOL), "verify", str(directory)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    cpp = json.loads(result.stdout)["status"]
    return module.verify_dataset(directory).status, cpp


def test_adversarial_corpus_has_cross_language_parity():
    """Both verifiers reject every malformed category identically."""
    module = _module("adv_contract")
    corpus: list[str] = []

    directory, manifest = _fixture("manifest-expectations-mismatch", module)
    manifest["expectations"]["snapshot_id"] = "TAMPERED"
    _commit(
        directory,
        json.loads((directory / "contract.json").read_text()),
        manifest,
        module,
    )
    corpus.append(directory.name)

    directory, manifest = _fixture("unsorted-symbols", module)
    contract = json.loads((directory / "contract.json").read_text())
    contract["symbols"] = ["ZZZ", "AAA"]
    contract["instrument_ids"]["ZZZ"] = "ID-ZZZ"
    contract["storage_keys"]["ZZZ"] = module.storage_key("ID-ZZZ")
    manifest["expectations"] = contract
    manifest["files"]["ZZZ"] = dict(manifest["files"]["AAA"])
    (directory / f"{contract['storage_keys']['ZZZ']}.csv").write_text(CSV)
    _commit(directory, contract, manifest, module)
    corpus.append(directory.name)

    directory, manifest = _fixture("range-mismatch", module)
    contract = json.loads((directory / "contract.json").read_text())
    contract["first_date"] = "2025-12-31"
    manifest["expectations"] = contract
    _commit(directory, contract, manifest, module)
    corpus.append(directory.name)

    directory, _ = _fixture("duplicate-json-key", module)
    path = directory / "contract.json"
    path.write_text(
        path.read_text().replace(
            '"snapshot_id": "SNAP-ADVERSARIAL"',
            '"snapshot_id": "SNAP-ADVERSARIAL",' '"snapshot_id": "OTHER"',
        )
    )
    corpus.append(directory.name)

    directory, manifest = _fixture("path-traversal-symbol", module)
    contract = json.loads((directory / "contract.json").read_text())
    contract["symbols"] = ["../AAA"]
    contract["instrument_ids"] = {"../AAA": "ID-AAA"}
    manifest["expectations"] = contract
    _commit(directory, contract, manifest, module)
    corpus.append(directory.name)

    for name in corpus:
        directory = FIXTURES / name
        python_status, cpp_status = _statuses(directory, module)
        assert python_status == cpp_status, name
        assert python_status != "COMPLETE", name


def test_symbol_never_becomes_a_filesystem_path(tmp_path: Path):
    """An external symbol cannot name or escape a storage object."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "path_module", ROOT / "python" / "dataset_contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["path_module"] = module
    spec.loader.exec_module(module)
    parent = tmp_path
    snapshot = parent / "snapshot"
    snapshot.mkdir()
    symbol = "../AAA"
    expectations = _base()
    expectations["symbols"] = [symbol]
    expectations["instrument_ids"] = {symbol: "INSTRUMENT-AAA"}
    key = module.storage_key("INSTRUMENT-AAA")
    expectations["storage_keys"] = {symbol: key}
    (parent / "AAA.csv").write_text(CSV)
    (snapshot / f"{key}.csv").write_text(CSV)
    assert module.write_contract(snapshot, expectations) == []
    assert module.write_manifest(snapshot, expectations) == []
    assert module.verify_dataset(snapshot).status == "COMPLETE"
    assert not (snapshot / "AAA.csv").exists()
    assert (snapshot / ".." / "AAA.csv").exists() is not False
    assert key not in ("AAA", "../AAA")


def test_path_grammar_property(tmp_path: Path):
    """Storage keys are opaque and independent of symbol punctuation."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "grammar_module", ROOT / "python" / "dataset_contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["grammar_module"] = module
    spec.loader.exec_module(module)
    import re

    for symbol in ["../AAA", "AAA/BBB", "AAA..", "..\\AAA", "AAA BBB"]:
        key = module.storage_key("INSTRUMENT-" + symbol)
        assert re.fullmatch(r"s[0-9a-f]{31}", key)
        assert "/" not in key and ".." not in key and " " not in key


def test_symlinked_snapshot_objects_are_rejected():
    """No object outside the snapshot can contribute bytes to COMPLETE."""
    directory, manifest = _fixture("symlink-objects", _module("symlink_mod"))
    module = _module("symlink_verifier")
    outside = FIXTURES / "symlink-external.csv"
    outside.write_text(CSV)
    key = module.storage_key("ID-AAA")
    target = directory / f"{key}.csv"
    target.unlink()
    target.symlink_to(outside)
    assert module.verify_dataset(directory).status != "COMPLETE"
    cpp = subprocess.run(
        [str(CONTRACT_TOOL), "verify", str(directory)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(cpp.stdout)["status"] != "COMPLETE"
    target.unlink()


def test_symlinked_contract_and_manifest_are_rejected():
    """Contract and manifest symlinks follow the same no-follow rule."""
    directory, _ = _fixture("symlink-metadata", _module("symlink_meta"))
    module = _module("symlink_meta_verify")
    for name in ("contract.json", "manifest.json"):
        original = directory / name
        outside = FIXTURES / f"external-{name}"
        outside.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(outside)
        assert module.verify_dataset(directory).status == "BLOCKED"
        original.unlink()
        original.write_bytes(outside.read_bytes())


def test_raw_digest_grammar_is_exact():
    """raw_digest is empty or one full lowercase SHA-256 digest."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "raw_grammar", ROOT / "python" / "dataset_contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["raw_grammar"] = module
    spec.loader.exec_module(module)
    assert module.valid_raw_digest("")
    assert module.valid_raw_digest("a" * 64)
    for bad in ("a", "a" * 63, "a" * 65, "A" * 64, "g" * 64, None, 1):
        assert not module.valid_raw_digest(bad)
