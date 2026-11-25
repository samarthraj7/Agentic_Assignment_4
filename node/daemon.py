"""Node daemon: serves the local A2A server, registers with the hub, and keeps an outbound,
authenticated WebSocket tunnel so the hub can relay A2A traffic to this machine behind NAT.

Run: python -m node.daemon   (env: HUB_URL, NODE_NAME, MESH_HOME)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import time
from pathlib import Path

import httpx
import uvicorn
import websockets
from google.protobuf.json_format import MessageToDict

from node.a2a_server import RPC_PATH, MeshNodeExecutor, build_app
from node.agent_card import build_agent_card, detect_capabilities
from shared.schemas import Grant, RunResult
from shared.signing import load_or_create_key, node_id_for, public_b64, sign

log = logging.getLogger("mesh.node")
DEFAULT_GRANT = {"owner": "", "allowed_images": ["python:3.12-slim", "experimentmesh/torch-cpu:0.1"],
                 "max_job_seconds": 1800, "max_cpus": 2.0, "max_mem_gb": 4.0, "max_jobs_per_day": 50}


def load_grant(home: Path, owner: str) -> Grant:
    path = home / "grant.json"
    if not path.exists():  # first run: write a conservative default the owner can edit
        path.write_text(json.dumps({**DEFAULT_GRANT, "owner": owner}, indent=2))
    return Grant.model_validate_json(path.read_text())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NodeDaemon:
    def __init__(self, hub_url: str, name: str, home: Path):
        home.mkdir(parents=True, exist_ok=True)
        self.hub_url, self.name = hub_url.rstrip("/"), name
        self.key = load_or_create_key(home / "node_key.pem")
        self.pub = public_b64(self.key)
        self.node_id = node_id_for(self.pub)
        self.grant = load_grant(home, name)
        self.caps = detect_capabilities()
        self.card = build_agent_card(name, f"{self.hub_url}/nodes/{self.node_id}{RPC_PATH}", self.caps)
        self.local_port = _free_port()
        self.http = httpx.AsyncClient(timeout=None)

    def _signed(self, payload: dict) -> dict:
        payload = {**payload, "ts": time.time()}
        return {"payload": payload, "signature": sign(self.key, payload)}

    async def register(self) -> None:
        body = self._signed({"node_id": self.node_id, "public_key": self.pub, "name": self.name,
                             "capabilities": self.caps.model_dump(), "card": MessageToDict(self.card)})
        r = await self.http.post(f"{self.hub_url}/register", json=body)
        r.raise_for_status()
        log.info("registered node %s (%s) with hub", self.node_id, self.name)

    async def report(self, run: RunResult) -> None:
        try:
            r = await self.http.post(f"{self.hub_url}/runs",
                                     json={"node_id": self.node_id, **self._signed({"run": run.model_dump()})})
            r.raise_for_status()
        except httpx.HTTPError as e:  # ponytail: drop on failure; a local outbox lands with failover (week 3)
            log.warning("could not report run %s: %s", run.job_id, e)

    async def _forward(self, frame: dict, send) -> None:
        rid = frame["id"]
        try:
            async with self.http.stream(frame["method"], f"http://127.0.0.1:{self.local_port}{frame['path']}",
                                        headers=frame["headers"], content=base64.b64decode(frame["body"])) as resp:
                await send({"type": "start", "id": rid, "status": resp.status_code, "headers": dict(resp.headers)})
                async for chunk in resp.aiter_raw():
                    await send({"type": "chunk", "id": rid, "data": base64.b64encode(chunk).decode()})
            await send({"type": "end", "id": rid})
        except Exception as e:
            await send({"type": "error", "id": rid, "message": str(e)})

    async def tunnel_once(self) -> None:
        ws_url = self.hub_url.replace("http", "ws", 1) + f"/ws/{self.node_id}"
        async with websockets.connect(ws_url, max_size=None) as ws:
            lock = asyncio.Lock()

            async def send(f: dict) -> None:
                async with lock:
                    await ws.send(json.dumps(f))

            challenge = json.loads(await ws.recv())
            await send({"type": "auth", "signature": sign(self.key, {"nonce": challenge["nonce"], "node_id": self.node_id})})
            if json.loads(await ws.recv()).get("type") != "ready":
                raise ConnectionError("hub rejected tunnel auth")
            log.info("tunnel up: %s", ws_url)
            tasks = set()
            async for raw in ws:
                frame = json.loads(raw)
                if frame.get("type") == "request":
                    t = asyncio.create_task(self._forward(frame, send))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)

    async def run(self) -> None:
        executor = MeshNodeExecutor(self.node_id, self.grant, report=self.report)
        server = uvicorn.Server(uvicorn.Config(build_app(self.card, executor), host="127.0.0.1",
                                               port=self.local_port, log_level="warning"))
        serve = asyncio.create_task(server.serve())
        backoff = 1
        try:
            while True:
                try:
                    await self.register()
                    await self.tunnel_once()
                    backoff = 1
                except (OSError, httpx.HTTPError, websockets.WebSocketException, ConnectionError) as e:
                    log.warning("hub unreachable (%s); retrying in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
        finally:
            server.should_exit = True
            await serve


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    daemon = NodeDaemon(os.environ.get("HUB_URL", "http://127.0.0.1:8080"),
                        os.environ.get("NODE_NAME", socket.gethostname().split(".")[0]),
                        Path(os.environ.get("MESH_HOME", Path.home() / ".experimentmesh")))
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
