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
    script = """
import json
import pykx as kx

def ingest(seq, seq_id, price):
    with kx.SyncQConnection(host="127.0.0.1", port=5001) as q:
        q(".quant.ingest", [seq, kx.SymbolAtom(seq_id),
                            kx.SymbolAtom("TEST"), kx.SymbolAtom("UPSTOX"),
                            946684800000000000, 946684800000000001,
                            946684800000000000, 123, price, price - 0.05,
                            price + 0.05, 1000.0, kx.SymbolAtom("ok")])

with kx.SyncQConnection(host="127.0.0.1", port=5001) as q:
    q(".quant.reset[]")
ingest(1, "e1", 100.0)
ingest(2, "e2", 101.0)
ingest(0, "e0", 99.0)
ingest(2, "e2", 101.0)
with kx.SyncQConnection(host="127.0.0.1", port=5001) as q:
    print(json.dumps({"state": q(".quant.state[]").py(),
                      "counts": q(".quant.counts[]").py()}))
"""
    python = ROOT / "data" / "venv" / "bin" / "python"
    code, out = run([str(python), "-c", script])
    assert code == 0, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["state"]["duplicates"] == 1
    assert report["state"]["outOfOrder"] == 1
    assert report["counts"]["quotes"] == 3
    code, out = run(["python3", str(RESEARCH), "--kdbx-check"], timeout=90)
    assert code == 0, out
    statuses = json.loads(out[out.index("{") :])
    assert all(item["status"] == "VERIFIED" for item in statuses.values())


def test_q_feature_math_matches_fixture(q_server):
    script = """
import json
import pykx as kx

with kx.SyncQConnection(host="127.0.0.1", port=5001) as q:
    q(".quant.reset[]")
    for i in range(21):
        q(".quant.ingestBar", [9845 + i, kx.SymbolAtom("FIX"),
                               100.0 + i, 101.0 + i, 99.0 + i,
                               100.0 + i, 1000.0 + i])
    rows = q(".quant.featureTable[]").py()
    sys_out = {"ret20": rows["ret20"][0], "vwapDev": rows["vwapDev"][0]}
    print(json.dumps(sys_out))
"""
    python = ROOT / "data" / "venv" / "bin" / "python"
    code, out = run([str(python), "-c", script])
    assert code == 0, out
    values = json.loads(out.strip().splitlines()[-1])
    assert abs(values["ret20"] - 0.2) < 1e-9
    assert 0.0 <= values["vwapDev"] < 1.0


def test_benchmark_records_latency(q_server):
    code, out = run([str(QUANT), "db", "benchmark"], timeout=180)
    assert code == 0, out
    p99 = re.search(r"ingest_p99_ms=([0-9.]+)", out)
    assert p99 is not None and float(p99.group(1)) > 0.0
    assert "NOT IMPLEMENTED" in out  # async queue honestly disclosed
