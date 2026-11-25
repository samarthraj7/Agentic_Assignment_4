"""Owner grant enforcement. Runs before any job touches the sandbox."""
from __future__ import annotations

import time
from datetime import datetime

from shared.schemas import Grant, JobManifest


def check_grant(job: JobManifest, grant: Grant, jobs_today: int, now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    if grant.expires_at is not None and now >= grant.expires_at:
        return False, "grant expired"
    if job.image not in grant.allowed_images:
        return False, f"image {job.image!r} not in owner allowlist"
    if job.timeout_s > grant.max_job_seconds:
        return False, f"timeout {job.timeout_s}s exceeds grant max {grant.max_job_seconds}s"
    if job.cpus > grant.max_cpus:
        return False, f"cpus {job.cpus} exceeds grant max {grant.max_cpus}"
    if job.mem_gb > grant.max_mem_gb:
        return False, f"mem {job.mem_gb}GB exceeds grant max {grant.max_mem_gb}GB"
    if jobs_today >= grant.max_jobs_per_day:
        return False, f"daily job quota ({grant.max_jobs_per_day}) used up"
    if grant.allowed_hours is not None:
        start, end = grant.allowed_hours
        hour = datetime.fromtimestamp(now).hour
        inside = start <= hour < end if start < end else (hour >= start or hour < end)  # wraps midnight
        if not inside:
            return False, f"outside owner's allowed hours {start}:00-{end}:00"
    return True, "ok"
