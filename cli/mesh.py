"""mesh CLI: list nodes/runs and submit a job to a node over A2A (through the hub relay).

  python -m cli.mesh nodes
  python -m cli.mesh submit jobs/smoke --params '{"lr": 0.3}' [--node NODE_ID] [--image python:3.12-slim]
  python -m cli.mesh runs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import get_data_parts, new_data_part
from a2a.types import Message, Role, SendMessageRequest, TaskState

from shared.schemas import JobManifest

HUB = os.environ.get("HUB_URL", "http://127.0.0.1:8080").rstrip("/")
CODE_SUFFIXES = {".py", ".txt", ".json", ".yaml", ".yml", ".cfg"}


def manifest_from_dir(job_dir: Path, image: str, params: dict, timeout_s: int, entrypoint: str = "train.py") -> JobManifest:
    files = {str(p.relative_to(job_dir)): p.read_text() for p in sorted(job_dir.rglob("*"))
             if p.is_file() and p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts}
    return JobManifest(image=image, entrypoint=entrypoint, files=files, params=params, timeout_s=timeout_s)


async def pick_node(hub: str, node_id: str | None) -> str:
    async with httpx.AsyncClient() as h:
        nodes = (await h.get(f"{hub}/nodes")).json()
    online = [n["node_id"] for n in nodes if n["online"]]
    if node_id:
        if node_id not in online:
            raise SystemExit(f"node {node_id} is not online")
        return node_id
    if not online:
        raise SystemExit("no online nodes")
    return online[0]


async def submit(job: JobManifest, node_id: str, hub: str = HUB, on_event=print) -> dict | None:
    """Send the job over A2A; stream status/metrics; return the RunResult artifact (or rejection data)."""
    async with httpx.AsyncClient(timeout=None) as h:
        client = await create_client(f"{hub}/nodes/{node_id}", client_config=ClientConfig(httpx_client=h))
        req = SendMessageRequest(message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER,
                                                 parts=[new_data_part(job.model_dump())]))
        result = None
        async for ev in client.send_message(req):
            if ev.HasField("status_update"):
                st = ev.status_update.status
                data = get_data_parts(st.message.parts) if st.HasField("message") else []
                text = " ".join(p.text for p in st.message.parts if p.text) if st.HasField("message") else ""
                if data and "metric" in data[0]:
                    m = data[0]["metric"]
                    on_event(f"  step {int(m['step'])}: " + ", ".join(f"{k}={v:.4g}" for k, v in m["metrics"].items()))
                else:
                    on_event(f"[{TaskState.Name(st.state).removeprefix('TASK_STATE_').lower()}] {text}")
                    if data and st.state == TaskState.TASK_STATE_REJECTED:
                        result = data[0]
            elif ev.HasField("artifact_update"):
                result = get_data_parts(ev.artifact_update.artifact.parts)[0]
        return result


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mesh")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("nodes")
    sub.add_parser("runs")
    s = sub.add_parser("submit")
    s.add_argument("job_dir", type=Path)
    s.add_argument("--node")
    s.add_argument("--params", default="{}")
    s.add_argument("--image", default="python:3.12-slim")
    s.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)

    if args.cmd in ("nodes", "runs"):
        print(json.dumps(httpx.get(f"{HUB}/{args.cmd}").json(), indent=2))
        return
    job = manifest_from_dir(args.job_dir, args.image, json.loads(args.params), args.timeout)
    node_id = asyncio.run(pick_node(HUB, args.node))
    print(f"submitting job {job.job_id} to node {node_id} via {HUB}")
    result = asyncio.run(submit(job, node_id))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result and result.get("status") == "completed" else 1)


if __name__ == "__main__":
    main()
