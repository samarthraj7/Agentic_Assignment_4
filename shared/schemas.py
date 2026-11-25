"""Wire contracts shared by hub, node, and clients. Changing these is a cross-team decision."""
from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_CODE_BYTES = 256_000  # inline code cap; larger projects move to git refs later
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\-./]*$")


class GpuInfo(BaseModel):
    name: str
    vram_gb: float


class NodeCapabilities(BaseModel):
    platform: str
    cpu_count: int
    ram_gb: float
    accelerator: Literal["cpu", "cuda"] = "cpu"
    gpus: list[GpuInfo] = []
    docker: bool = False


class Grant(BaseModel):
    """Owner policy for what this node may run. Enforced by the node before any job starts."""
    owner: str
    allowed_images: list[str]
    max_job_seconds: int = 1800
    max_cpus: float = 2.0
    max_mem_gb: float = 4.0
    max_jobs_per_day: int = 50
    allowed_hours: tuple[int, int] | None = None  # local hours [start, end); None = any time
    expires_at: float | None = None  # unix ts


class JobManifest(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    image: str
    entrypoint: str
    files: dict[str, str]  # relative path -> file text
    params: dict[str, float | int | str | bool] = {}
    timeout_s: int = 600
    cpus: float = 1.0
    mem_gb: float = 1.0

    @field_validator("files")
    @classmethod
    def _safe_files(cls, files: dict[str, str]) -> dict[str, str]:
        if sum(len(v.encode()) for v in files.values()) > MAX_CODE_BYTES:
            raise ValueError(f"inline code exceeds {MAX_CODE_BYTES} bytes")
        for path in files:
            if not _SAFE_PATH.match(path) or ".." in path.split("/"):
                raise ValueError(f"unsafe file path: {path!r}")
        return files

    @model_validator(mode="after")
    def _entrypoint_exists(self) -> JobManifest:
        if not self.entrypoint.endswith(".py") or self.entrypoint not in self.files:
            raise ValueError("entrypoint must be a .py file included in files")
        return self


class MetricEvent(BaseModel):
    step: int
    metrics: dict[str, float]


class RunResult(BaseModel):
    job_id: str
    node_id: str
    status: Literal["completed", "failed", "rejected", "timeout"]
    exit_code: int | None = None
    metrics: list[MetricEvent] = []
    duration_s: float = 0.0
    log_tail: str = ""
    reason: str = ""
    sandbox: Literal["docker", "unsandboxed-dev", "none"] = "none"
