"""Hub: node registry, durable ledger, and an HTTP-over-WebSocket relay that carries raw A2A JSON-RPC
between clients and nodes behind NAT. The hub never rewrites A2A payloads; it is transport only.

Tunnel frames (JSON):
  hub->node  {"type":"challenge","nonce"} then {"type":"request","id","method","path","headers","body"(b64)}
  node->hub  {"type":"auth","signature"} then {"type":"start","id","status","headers"},
             {"type":"chunk","id","data"(b64)}, {"type":"end","id"}, {"type":"error","id","message"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from hub.ledger import make_ledger
from shared.schemas import RunResult
from shared.signing import node_id_for, verify

MAX_CLOCK_SKEW_S = 120
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "upgrade"}


class Tunnel:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.pending: dict[str, asyncio.Queue] = {}
        self._send_lock = asyncio.Lock()

    async def send(self, frame: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(frame))


def create_app(ledger=None) -> FastAPI:
    app = FastAPI(title="ExperimentMesh Hub")
    app.state.ledger = ledger or make_ledger()
    tunnels: dict[str, Tunnel] = {}

    def _verified(body: dict, pub: str) -> dict:
        payload, sig = body.get("payload"), body.get("signature", "")
        if not isinstance(payload, dict) or not verify(pub, payload, sig):
            raise HTTPException(401, "bad signature")
        if abs(time.time() - float(payload.get("ts", 0))) > MAX_CLOCK_SKEW_S:
            raise HTTPException(401, "stale or future timestamp (replay?)")
        return payload

    @app.post("/register")
    async def register(body: dict):
        payload = body.get("payload") or {}
        pub, node_id = payload.get("public_key", ""), payload.get("node_id", "")
        try:
            if node_id_for(pub) != node_id:
                raise HTTPException(401, "node_id does not match public key")
        except (ValueError, TypeError):
            raise HTTPException(401, "malformed public key")
        payload = _verified(body, pub)
        app.state.ledger.upsert_node(node_id, {
            "node_id": node_id, "name": payload["name"], "public_key": pub,
            "capabilities": payload["capabilities"], "card": payload["card"], "registered_at": payload["ts"]})
        return {"node_id": node_id}

    @app.get("/nodes")
    async def nodes():
        return [{**{k: v for k, v in n.items() if k != "card"}, "online": n["node_id"] in tunnels}
                for n in app.state.ledger.list_nodes()]

    @app.post("/runs")
    async def report_run(body: dict):
        node = app.state.ledger.get_node(body.get("node_id", ""))
        if not node:
            raise HTTPException(404, "unknown node")
        payload = _verified(body, node["public_key"])
        run = RunResult.model_validate(payload["run"])
        if run.node_id != node["node_id"]:
            raise HTTPException(403, "node may only report its own runs")
        app.state.ledger.add_run({**run.model_dump(), "reported_at": payload["ts"]})
        return {"ok": True}

    @app.get("/runs")
    async def runs(limit: int = 50):
        return app.state.ledger.list_runs(limit)

    @app.websocket("/ws/{node_id}")
    async def tunnel_ws(ws: WebSocket, node_id: str):
        await ws.accept()
        node = app.state.ledger.get_node(node_id)
        nonce = secrets.token_hex(16)
        await ws.send_text(json.dumps({"type": "challenge", "nonce": nonce}))
        try:
            auth = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
            await ws.close(code=4001)
            return
        if not node or not verify(node["public_key"], {"nonce": nonce, "node_id": node_id}, auth.get("signature", "")):
            await ws.close(code=4003)
            return
        tunnel = Tunnel(ws)
        tunnels[node_id] = tunnel
        await tunnel.send({"type": "ready"})
        try:
            while True:
                frame = json.loads(await ws.receive_text())
                q = tunnel.pending.get(frame.get("id", ""))
                if q:
                    await q.put(frame)
        except WebSocketDisconnect:
            pass
        finally:
            if tunnels.get(node_id) is tunnel:
                del tunnels[node_id]
            for q in tunnel.pending.values():
                await q.put({"type": "error", "message": "node disconnected"})

    @app.api_route("/nodes/{node_id}/{path:path}", methods=["GET", "POST"])
    async def relay(node_id: str, path: str, request: Request):
        tunnel = tunnels.get(node_id)
        if not tunnel:
            raise HTTPException(503, f"node {node_id} is offline")
        rid = uuid.uuid4().hex
        q: asyncio.Queue = asyncio.Queue()
        tunnel.pending[rid] = q
        await tunnel.send({
            "type": "request", "id": rid, "method": request.method,
            "path": "/" + path + (f"?{request.url.query}" if request.url.query else ""),
            "headers": {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS},
            "body": base64.b64encode(await request.body()).decode()})
        first = await q.get()
        if first["type"] != "start":
            tunnel.pending.pop(rid, None)
            raise HTTPException(502, first.get("message", "relay error"))

        async def body():
            try:
                while (frame := await q.get())["type"] == "chunk":
                    yield base64.b64decode(frame["data"])
            finally:
                tunnel.pending.pop(rid, None)

        headers = {k: v for k, v in first["headers"].items() if k.lower() not in HOP_HEADERS}
        return StreamingResponse(body(), status_code=first["status"], headers=headers)

    return app
# run: uvicorn hub.app:create_app --factory --host 0.0.0.0 --port 8080
