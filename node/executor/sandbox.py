"""Run a JobManifest in an isolated container and stream METRIC lines back.

Job contract: params arrive as JSON in $PARAMS, artifacts go to $OUT_DIR, and progress is reported by
printing lines of the form `METRIC {"step": 3, "loss": 0.41}` to stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from shared.schemas import JobManifest, MetricEvent, RunResult

OnMetric = Callable[[MetricEvent], None]
UNSANDBOXED_ENV = "MESH_ALLOW_UNSANDBOXED"


def parse_metric(line: str) -> MetricEvent | None:
    if not line.startswith("METRIC "):
        return None
    try:
        raw = json.loads(line[len("METRIC "):])
        step = int(raw.pop("step"))
        return MetricEvent(step=step, metrics={k: float(v) for k, v in raw.items()})
    except (ValueError, KeyError, TypeError, AttributeError):
        return None  # malformed metric lines are ignored, never executed or trusted


def _write_code(job: JobManifest, root: Path) -> tuple[Path, Path]:
    code, out = root / "code", root / "out"
    for rel, text in job.files.items():
        p = code / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    out.mkdir()
    out.chmod(0o777)  # container runs as nobody
    return code, out


def _consume(lines, on_metric: OnMetric | None, metrics: list, tail: deque) -> None:
    for line in lines:
        line = line.rstrip("\n")
        tail.append(line)
        if (m := parse_metric(line)) is not None:
            metrics.append(m)
            if on_metric:
                on_metric(m)


def docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


def run_job(job: JobManifest, node_id: str, on_metric: OnMetric | None = None) -> RunResult:
    if docker_available():
        return _run_docker(job, node_id, on_metric)
    if os.environ.get(UNSANDBOXED_ENV) == "1":
        return _run_unsandboxed_dev(job, node_id, on_metric)
    return RunResult(job_id=job.job_id, node_id=node_id, status="rejected",
                     reason=f"Docker unavailable and {UNSANDBOXED_ENV} not set; refusing to run unsandboxed")


def _run_docker(job: JobManifest, node_id: str, on_metric: OnMetric | None) -> RunResult:
    import docker
    client = docker.from_env()
    metrics: list[MetricEvent] = []
    tail: deque[str] = deque(maxlen=50)
    start = time.time()
    with tempfile.TemporaryDirectory(prefix="mesh-") as tmp:
        code, out = _write_code(job, Path(tmp))
        container = client.containers.run(
            job.image, ["python", f"/code/{job.entrypoint}"],
            detach=True,
            environment={"PARAMS": json.dumps(job.params), "OUT_DIR": "/out", "PYTHONUNBUFFERED": "1"},
            volumes={str(code): {"bind": "/code", "mode": "ro"}, str(out): {"bind": "/out", "mode": "rw"}},
            working_dir="/code",
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": "size=256m"},
            user="65534:65534",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=256,
            mem_limit=f"{int(job.mem_gb * 1024)}m",
            nano_cpus=int(job.cpus * 1e9),
        )
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            container.kill()

        timer = threading.Timer(job.timeout_s, _kill)
        timer.start()
        try:
            logs = (chunk.decode(errors="replace") for chunk in container.logs(stream=True, follow=True))
            _consume(_split_lines(logs), on_metric, metrics, tail)
            exit_code = container.wait().get("StatusCode")
        finally:
            timer.cancel()
            container.remove(force=True)
    return _result(job, node_id, exit_code, timed_out.is_set(), metrics, tail, start, "docker")


def _run_unsandboxed_dev(job: JobManifest, node_id: str, on_metric: OnMetric | None) -> RunResult:
    # ponytail: dev-only fallback for machines without Docker; no isolation. Never enable on a shared node.
    metrics: list[MetricEvent] = []
    tail: deque[str] = deque(maxlen=50)
    start = time.time()
    with tempfile.TemporaryDirectory(prefix="mesh-") as tmp:
        code, out = _write_code(job, Path(tmp))
        env = {"PARAMS": json.dumps(job.params), "OUT_DIR": str(out), "PYTHONUNBUFFERED": "1",
               "PATH": "/usr/bin:/bin"}
        proc = subprocess.Popen([sys.executable, "-I", job.entrypoint], cwd=code, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            proc.kill()

        timer = threading.Timer(job.timeout_s, _kill)
        timer.start()
        try:
            _consume(proc.stdout, on_metric, metrics, tail)
            exit_code = proc.wait()
        finally:
            timer.cancel()
    return _result(job, node_id, exit_code, timed_out.is_set(), metrics, tail, start, "unsandboxed-dev")


def _split_lines(chunks):
    buf = ""
    for chunk in chunks:
        buf += chunk
        *lines, buf = buf.split("\n")
        yield from lines
    if buf:
        yield buf


def _result(job, node_id, exit_code, timed_out, metrics, tail, start, sandbox) -> RunResult:
    status = "timeout" if timed_out else ("completed" if exit_code == 0 else "failed")
    return RunResult(job_id=job.job_id, node_id=node_id, status=status, exit_code=exit_code,
                     metrics=metrics, duration_s=round(time.time() - start, 3),
                     log_tail="\n".join(tail), sandbox=sandbox)
