"""KDB-X integration tests (C++ -> q -> Python round-trip).

These tests require an actual KDB-X runtime and the PyKX package. They are
skipped, not passed, when the runtime is unavailable. No broker credentials are
used; all fixtures are deterministic synthetic events.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QUANT = ROOT / "build" / "quant"
RESEARCH = ROOT / "python" / "research.py"


def run(args: list[str], timeout: int = 90) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


@pytest.fixture(scope="module")
def q_server():
    if not QUANT.exists():
        pytest.skip("build/quant not present; run cmake build first")
    qhome = os.environ.get("QHOME", "")
    if not qhome or not (Path(qhome) / "bin" / "q").exists():
        pytest.skip("KDB-X q runtime not available")
    code, out = run([str(QUANT), "db", "start"], timeout=60)
    if code != 0 and "already running" not in out:
        pytest.skip(f"q server unavailable: {out[-300:]}")
    yield
    run([str(QUANT), "db", "stop"], timeout=30)


def test_cpp_to_q_roundtrip(q_server):
    code, out = run([str(QUANT), "db", "verify"], timeout=90)
    assert code == 0, out
    assert "counts=" in out and '"quotes":3' in out
    assert '"duplicates":1' in out and '"outOfOrder":1' in out
    assert "historical_tables=" in out


def test_pykx_reads_same_events(q_server):
    code, out = run([str(QUANT), "db", "verify"], timeout=90)
    assert code == 0, out
    code, out = run(["python3", str(RESEARCH), "--kdbx-check"], timeout=90)
    assert code == 0, out
    report = json.loads(out[out.index("{") :])
    assert all(item["status"] == "VERIFIED" for item in report.values())
    state = report[".quant.state[]"]["value"]
    assert state["duplicates"] == 1 and state["outOfOrder"] == 1


def test_benchmark_records_latency(q_server):
    code, out = run([str(QUANT), "db", "benchmark"], timeout=180)
    assert code == 0, out
    p99 = re.search(r"ingest_p99_ms=([0-9.]+)", out)
    assert p99 is not None and float(p99.group(1)) > 0.0
    assert "NOT IMPLEMENTED" in out  # async queue honestly disclosed
