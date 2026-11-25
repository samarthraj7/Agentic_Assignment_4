from pathlib import Path

import pytest

from node.executor import sandbox
from shared.schemas import JobManifest

SMOKE = (Path(__file__).parent.parent / "jobs/smoke/train.py").read_text()
NET_PROBE = """
import socket
socket.create_connection(("1.1.1.1", 53), timeout=3)
print("NETWORK_REACHED")
"""
needs_docker = pytest.mark.skipif(not sandbox.docker_available(), reason="Docker not available")


def _job(code, **kw):
    return JobManifest(image="python:3.12-slim", entrypoint="train.py", files={"train.py": code}, **kw)


def test_parse_metric():
    m = sandbox.parse_metric('METRIC {"step": 2, "loss": 0.5}')
    assert m.step == 2 and m.metrics == {"loss": 0.5}
    assert sandbox.parse_metric("METRIC not-json") is None
    assert sandbox.parse_metric('METRIC {"loss": 1}') is None  # no step
    assert sandbox.parse_metric("hello") is None


def test_refuses_without_docker_or_dev_flag(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    monkeypatch.delenv(sandbox.UNSANDBOXED_ENV, raising=False)
    r = sandbox.run_job(_job(SMOKE), "n1")
    assert r.status == "rejected" and "refusing" in r.reason


def test_dev_executor_streams_metrics_and_times_out(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    monkeypatch.setenv(sandbox.UNSANDBOXED_ENV, "1")
    seen = []
    r = sandbox.run_job(_job(SMOKE, params={"epochs": 3, "sleep": 0}), "n1", seen.append)
    assert r.status == "completed" and len(r.metrics) == 3 == len(seen)
    r = sandbox.run_job(_job("import time; time.sleep(30)", timeout_s=1), "n1")
    assert r.status == "timeout"


@needs_docker
def test_docker_runs_job_and_blocks_network():
    r = sandbox.run_job(_job(SMOKE, params={"epochs": 3, "sleep": 0}), "n1")
    assert r.status == "completed" and r.sandbox == "docker" and len(r.metrics) == 3
    r = sandbox.run_job(_job(NET_PROBE), "n1")
    assert r.status == "failed" and "NETWORK_REACHED" not in r.log_tail


@needs_docker
def test_docker_timeout_kills():
    assert sandbox.run_job(_job("import time; time.sleep(60)", timeout_s=2), "n1").status == "timeout"
