"""KDB-X integration tests with mandatory process isolation.

Each fixture starts a private q process on an OS-assigned loopback port with a
unique instance id and a temporary HDB directory. Production endpoints are
never contacted, reset, or stopped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QUANT = ROOT / "build" / "quant"
RESEARCH = ROOT / "python" / "research.py"
PYKX_PYTHON = ROOT / "data" / "venv" / "bin" / "python"


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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class IsolatedQ:
    port: int
    directory: Path
    instance_id: str
    process: subprocess.Popen
    owned: bool = True


def q_runtime_available() -> bool:
    qhome = os.environ.get("QHOME", "")
    return bool(qhome) and (Path(qhome) / "bin" / "q").exists()


@pytest.fixture(scope="module")
def isolated_q():
    qhome = os.environ.get("QHOME", "")
    if not qhome:
        pytest.skip("KDB-X q runtime not available")
    qbin = Path(qhome) / "bin" / "q"
    if not QUANT.exists():
        pytest.skip("build/quant not present; run cmake build first")
    if not qbin.exists():
        pytest.skip("KDB-X q runtime not available")
    if not PYKX_PYTHON.exists():
        pytest.skip("PyKX interpreter not available")

    port = free_port()
    directory = Path(
        tempfile.mkdtemp(prefix="quant-q-test-", dir=ROOT / "data")
    )
    instance_id = "pytest-" + uuid.uuid4().hex
    log = open(directory / "q.log", "wb")
    process = subprocess.Popen(
        [
            str(qbin),
            str(ROOT / "q" / "quant.q"),
            "-p",
            f"127.0.0.1:{port}",
            "-instanceId",
            instance_id,
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    log.close()
    server = IsolatedQ(port, directory, instance_id, process)

    probe = """
import pykx as kx
with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
    value = q(".quant.instanceId").py()
    print(value.decode() if isinstance(value, bytes) else value)
""".replace("PORT", str(port))
    deadline = time.time() + 90
    identity = ""
    while time.time() < deadline:
        if process.poll() is not None:
            break
        code, out = run([str(PYKX_PYTHON), "-c", probe], timeout=45)
        if code == 0:
            try:
                identity = out.strip().splitlines()[-1]
                break
            except (ValueError, KeyError, IndexError):
                pass
        time.sleep(0.2)
    if identity != instance_id:
        process.terminate()
        process.wait(timeout=10)
        shutil.rmtree(directory, ignore_errors=True)
        pytest.fail("could not verify isolated q instance identity")

    yield server

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    shutil.rmtree(directory, ignore_errors=True)


def test_cpp_verify_uses_isolated_instance():
    if not q_runtime_available():
        pytest.skip("KDB-X q runtime not available")
    code, out = run([str(QUANT), "db", "verify"], timeout=90)
    assert code == 0, out
    assert "isolated C++ to q round-trip" in out
    assert "instance=quant-test-" in out
    assert '"duplicates":1' in out


def test_pykx_reads_same_events(isolated_q):
    script = """
import json
import pykx as kx

def ingest(seq, seq_id, price):
    with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
        q(".quant.ingest", [seq, kx.SymbolAtom(seq_id),
                            kx.SymbolAtom("TEST"), kx.SymbolAtom("UPSTOX"),
                            946684800000000000, 946684800000000001,
                            946684800000000000, 123, price, price - 0.05,
                            price + 0.05, 1000.0, kx.SymbolAtom("ok")])

with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
    q(".quant.reset[]")
ingest(1, "e1", 100.0)
ingest(2, "e2", 101.0)
ingest(0, "e0", 99.0)
ingest(2, "e2", 101.0)
with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
    identity = q(".quant.instanceJson[]").py()
    if isinstance(identity, bytes):
        identity = identity.decode()
    print(json.dumps({"instance": identity,
                      "state": q(".quant.state[]").py(),
                      "counts": q(".quant.counts[]").py()}))
""".replace("PORT", str(isolated_q.port))
    code, out = run([str(PYKX_PYTHON), "-c", script])
    assert code == 0, out
    report = json.loads(out.strip().splitlines()[-1])
    assert (
        json.loads(report["instance"])["instanceId"] == isolated_q.instance_id
    )
    assert report["state"]["duplicates"] == 1
    assert report["state"]["outOfOrder"] == 1
    assert report["counts"]["quotes"] == 3


def test_q_feature_math_matches_fixture(isolated_q):
    script = """
import json
import pykx as kx

with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
    q(".quant.reset[]")
    for i in range(21):
        q(".quant.ingestBar", [9845 + i, kx.SymbolAtom("FIX"),
                               100.0 + i, 101.0 + i, 99.0 + i,
                               100.0 + i, 1000.0 + i])
    rows = q(".quant.featureTable[]").py()
    print(json.dumps({"ret20": rows["ret20"][0],
                      "vwapDev": rows["vwapDev"][0]}))
""".replace("PORT", str(isolated_q.port))
    code, out = run([str(PYKX_PYTHON), "-c", script])
    assert code == 0, out
    values = json.loads(out.strip().splitlines()[-1])
    assert abs(values["ret20"] - 0.2) < 1e-9
    assert -1.0 < values["vwapDev"] < 1.0


def test_point_in_time_features_and_recovery(isolated_q):
    script = """
import json
import pykx as kx

def call(q, fn, *args):
    return q(fn, list(args)) if args else q(fn)

with kx.SyncQConnection(host="127.0.0.1", port=PORT) as q:
    q(".quant.reset[]")
    for i in range(21):
        q(".quant.ingestBar", [9845 + i, kx.SymbolAtom("FIX"),
                               100.0 + i, 101.0 + i, 99.0 + i,
                               100.0 + i, 1000.0 + i])
    q(".quant.ingestBar", [9865, kx.SymbolAtom("FIX"), 120.0, 121.0,
                           119.0, 120.0, 1000.0])
    ns = 9866 * 86400000000000
    q(".quant.ingest", [1, kx.SymbolAtom("e1"), kx.SymbolAtom("FIX"),
                        kx.SymbolAtom("UPSTOX"), ns, ns, ns, 123,
                        120.0, 119.9, 120.1, 1000.0, kx.SymbolAtom("ok")])
    hist = q(".quant.featureHistory[]").py()
    latest = q(".quant.featureTable[]").py()
    asof = q(".quant.asofFeatures[]").py()
    before = q(".quant.counts[]").py()["bars"]
    q(".quant.persist", "CHECKPOINT")
    q(".quant.reset[]")
    recovered = q(".quant.recover", "CHECKPOINT").py()
    after = q(".quant.counts[]").py()["bars"]
    data = {"history_rows": len(hist["date"]),
            "latest_ret20": latest["ret20"][0],
            "asof_ret20": asof["ret20"][0],
            "bars_before": before, "bars_after": after,
            "recovered": recovered.decode() if isinstance(recovered, bytes)
                         else recovered}
    print(json.dumps(data))
""".replace("PORT", str(isolated_q.port)).replace(
        "CHECKPOINT", str(isolated_q.directory / "hdb")
    )
    code, out = run([str(PYKX_PYTHON), "-c", script])
    assert code == 0, out
    data = json.loads(out.strip().splitlines()[-1])
    assert data["history_rows"] == 21
    assert abs(data["latest_ret20"] - 0.2) < 1e-9
    assert abs(data["asof_ret20"] - 0.2) < 1e-9
    assert data["bars_before"] == 21
    assert data["bars_after"] == 21
    assert data["recovered"] == "recovered"


def test_production_service_unchanged():
    if not q_runtime_available():
        pytest.skip("KDB-X q runtime not available")
    code, before = run([str(QUANT), "db", "status"], timeout=30)
    if code != 0 or "connected=false" in before:
        pytest.skip("no production q instance to compare")
    counts_before = next(
        (line for line in before.splitlines() if "connected=true" in line), ""
    )
    assert run([str(QUANT), "db", "verify"], timeout=90)[0] == 0
    assert run([str(QUANT), "db", "benchmark"], timeout=180)[0] == 0
    code, after = run([str(QUANT), "db", "status"], timeout=30)
    counts_after = next(
        (line for line in after.splitlines() if "connected=true" in line), ""
    )
    assert counts_before == counts_after


def test_benchmark_is_isolated():
    if not q_runtime_available():
        pytest.skip("KDB-X q runtime not available")
    code, out = run([str(QUANT), "db", "benchmark"], timeout=180)
    assert code == 0, out
    assert "instance=quant-test-" in out
    p99 = re.search(r"ingest_p99_ms=([0-9.]+)", out)
    assert p99 is not None and float(p99.group(1)) > 0.0
