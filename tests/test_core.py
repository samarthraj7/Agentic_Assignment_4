import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from node.policy.grants import check_grant
from shared.schemas import Grant, JobManifest
from shared.signing import load_or_create_key, node_id_for, public_b64, sign, verify


def _job(**kw):
    base = dict(image="python:3.12-slim", entrypoint="train.py", files={"train.py": "print(1)"})
    return JobManifest(**{**base, **kw})


def test_signature_roundtrip_and_tamper(tmp_path):
    key = load_or_create_key(tmp_path / "k.pem")
    pub = public_b64(key)
    payload = {"a": 1, "nonce": "x"}
    sig = sign(key, payload)
    assert verify(pub, payload, sig)
    assert not verify(pub, {**payload, "a": 2}, sig)
    assert load_or_create_key(tmp_path / "k.pem").public_key() is not None  # reloads same file
    assert node_id_for(pub) == node_id_for(public_b64(load_or_create_key(tmp_path / "k.pem")))


@pytest.mark.parametrize("files,entry", [
    ({"../escape.py": "x"}, "../escape.py"),
    ({"/abs.py": "x"}, "/abs.py"),
    ({"train.py": "x"}, "missing.py"),
    ({"train.py": "x" * 300_000}, "train.py"),
])
def test_manifest_rejects_unsafe(files, entry):
    with pytest.raises(ValidationError):
        JobManifest(image="python:3.12-slim", entrypoint=entry, files=files)


GRANT = Grant(owner="o", allowed_images=["python:3.12-slim"], max_job_seconds=600, max_cpus=2,
              max_mem_gb=4, max_jobs_per_day=3)


@pytest.mark.parametrize("job,used,grant,reason", [
    (_job(image="evil/miner"), 0, GRANT, "allowlist"),
    (_job(timeout_s=9999), 0, GRANT, "timeout"),
    (_job(cpus=8), 0, GRANT, "cpus"),
    (_job(mem_gb=64), 0, GRANT, "mem"),
    (_job(), 3, GRANT, "quota"),
    (_job(), 0, GRANT.model_copy(update={"expires_at": time.time() - 1}), "expired"),
])
def test_grant_rejections(job, used, grant, reason):
    ok, why = check_grant(job, grant, used)
    assert not ok and reason in why


def test_grant_allowed_hours_wraps_midnight():
    g = GRANT.model_copy(update={"allowed_hours": (22, 6)})
    at = lambda h: time.mktime((2026, 1, 1, h, 0, 0, 0, 0, -1))
    assert check_grant(_job(), g, 0, now=at(23))[0]
    assert check_grant(_job(), g, 0, now=at(3))[0]
    assert not check_grant(_job(), g, 0, now=at(12))[0]
    assert check_grant(_job(), GRANT, 0)[0]
