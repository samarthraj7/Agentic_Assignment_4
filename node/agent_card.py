"""Detect this machine's capabilities and publish them as an A2A Agent Card."""
from __future__ import annotations

import platform
import shutil
import subprocess

import psutil
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentSkill
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from node.executor.sandbox import docker_available
from shared.schemas import GpuInfo, NodeCapabilities

CAPS_EXTENSION_URI = "https://experimentmesh.dev/ext/node-capabilities/v1"


def detect_capabilities() -> NodeCapabilities:
    gpus: list[GpuInfo] = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True).stdout
            for line in out.strip().splitlines():
                name, mem = (s.strip() for s in line.split(","))
                gpus.append(GpuInfo(name=name, vram_gb=round(float(mem) / 1024, 1)))
        except (subprocess.SubprocessError, ValueError):
            pass
    return NodeCapabilities(
        platform=f"{platform.system()}-{platform.machine()}",
        cpu_count=psutil.cpu_count(logical=True) or 1,
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
        accelerator="cuda" if gpus else "cpu",
        gpus=gpus,
        docker=docker_available(),
    )


def build_agent_card(node_name: str, rpc_url: str, caps: NodeCapabilities) -> AgentCard:
    return AgentCard(
        name=f"mesh-node:{node_name}",
        description=f"ExperimentMesh compute node owned by {node_name}. Runs sandboxed ML jobs.",
        version="0.1.0",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=rpc_url)],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[AgentExtension(
                uri=CAPS_EXTENSION_URI, description="Hardware capabilities of this node",
                params=ParseDict(caps.model_dump(), struct_pb2.Struct()))],
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(
            id="run_experiment", name="Run experiment",
            description="Run a JobManifest in a sandbox and stream METRIC events; returns a RunResult.",
            tags=["ml", caps.accelerator, caps.platform])],
    )
