"""Hub + node daemon + CLI submit, all in-process over real sockets."""
import asyncio
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from cli.mesh import manifest_from_dir, submit
from hub.app import create_app
from hub.ledger import SqliteLedger
from node.daemon import NodeDaemon
from node.executor import sandbox
from shared.signing import load_or_create_key, node_id_for, public_b64, sign

ROOT = Path(__file__).parent.parent


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hub(tmp_path):
    port = _port()
    server = uvicorn.Server(uvicorn.Config(create_app(SqliteLedger(str(tmp_path / "m.db"))),
                                           port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


def test_register_rejects_tampered_and_mismatched(hub, tmp_path):
    key = load_or_create_key(tmp_path / "k.pem")
    pub = public_b64(key)
    p = {"node_id": node_id_for(pub), "public_key": pub, "name": "x", "capabilities": {}, "card": {}, "ts": time.time()}
    sig = sign(key, p)
    assert httpx.post(f"{hub}/register", json={"payload": {**p, "name": "evil"}, "signature": sig}).status_code == 401
    assert httpx.post(f"{hub}/register", json={"payload": {**p, "node_id": "deadbeef"}, "signature": sig}).status_code == 401
    stale = {**p, "ts": time.time() - 3600}
    assert httpx.post(f"{hub}/register", json={"payload": stale, "signature": sign(key, stale)}).status_code == 401
    assert httpx.post(f"{hub}/register", json={"payload": p, "signature": sig}).status_code == 200


def test_job_end_to_end_over_a2a_relay(hub, tmp_path, monkeypatch):
    if not sandbox.docker_available():
        monkeypatch.setenv(sandbox.UNSANDBOXED_ENV, "1")

    async def scenario():
        daemon = NodeDaemon(hub, "tester", tmp_path / "node")
        task = asyncio.create_task(daemon.run())
        async with httpx.AsyncClient() as h:
            for _ in range(100):
                nodes = (await h.get(f"{hub}/nodes")).json()
                if nodes and nodes[0]["online"]:
                    break
                await asyncio.sleep(0.1)
            assert nodes[0]["node_id"] == daemon.node_id

            job = manifest_from_dir(ROOT / "jobs/smoke", "python:3.12-slim", {"epochs": 3, "sleep": 0}, 60)
            events = []
            result = await submit(job, daemon.node_id, hub, on_event=events.append)
            assert result["status"] == "completed" and len(result["metrics"]) == 3
            assert sum("step" in e for e in events) == 3  # metrics streamed through the relay

            bad = job.model_copy(update={"image": "evil/miner", "job_id": "bad1"})
            rejected = await submit(bad, daemon.node_id, hub, on_event=events.append)
            assert rejected["status"] == "rejected" and "allowlist" in rejected["reason"]

            runs = (await h.get(f"{hub}/runs")).json()
            assert {r["status"] for r in runs} == {"completed", "rejected"}
        task.cancel()

    asyncio.run(scenario())
