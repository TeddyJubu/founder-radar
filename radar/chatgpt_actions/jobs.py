"""Filesystem-backed async job store for long CLI commands (search / rescore)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from radar.chatgpt_actions.runner import RunResult, run_argv
from radar.render.digest import review_url

log = logging.getLogger(__name__)

JobStatus = str  # queued | running | done | failed


@dataclass
class Job:
    id: str
    command: str
    status: JobStatus
    argv: list[str]
    created_at: float
    updated_at: float
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    dashboard_url: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """JSON for GPT poll responses — CLI fidelity only, no invented scores."""
        body: dict[str, Any] = {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "dashboard_url": self.dashboard_url or review_url() or None,
            "exit_code": self.exit_code,
            "error": self.error,
        }
        if self.status in ("done", "failed"):
            body["stdout"] = self.stdout
            body["stderr"] = self.stderr
            if self.meta:
                body["meta"] = dict(self.meta)
        else:
            body["message"] = (
                "Job still running. Poll again. "
                "Open the Today dashboard for live results — do not invent scores."
            )
        return body


def jobs_dir() -> Path:
    override = (os.environ.get("RADAR_CHATGPT_JOBS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    root = (os.environ.get("RADAR_ROOT") or "/opt/founder-radar").rstrip("/")
    return Path(root) / "data" / "chatgpt_jobs"


def _job_path(job_id: str) -> Path:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    if safe != job_id or not safe:
        raise ValueError("invalid job id")
    return jobs_dir() / f"{safe}.json"


def save_job(job: Job) -> None:
    path = _job_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = asdict(job)
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def load_job(job_id: str) -> Job | None:
    try:
        path = _job_path(job_id)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return Job(
        id=str(raw["id"]),
        command=str(raw.get("command") or ""),
        status=str(raw.get("status") or "failed"),
        argv=list(raw.get("argv") or []),
        created_at=float(raw.get("created_at") or 0),
        updated_at=float(raw.get("updated_at") or 0),
        exit_code=raw.get("exit_code"),
        stdout=str(raw.get("stdout") or ""),
        stderr=str(raw.get("stderr") or ""),
        dashboard_url=raw.get("dashboard_url"),
        error=raw.get("error"),
        meta=dict(raw.get("meta") or {}),
    )


def create_job(command: str, argv: list[str], *, meta: dict[str, Any] | None = None) -> Job:
    now = time.time()
    job = Job(
        id=uuid.uuid4().hex,
        command=command,
        status="queued",
        argv=list(argv),
        created_at=now,
        updated_at=now,
        dashboard_url=review_url() or None,
        meta=dict(meta or {}),
    )
    save_job(job)
    return job


def _apply_result(job: Job, result: RunResult) -> None:
    job.updated_at = time.time()
    job.exit_code = result.exit_code
    job.stdout = result.stdout
    job.stderr = result.stderr
    job.status = "done" if result.exit_code == 0 else "failed"
    if result.exit_code != 0 and not job.error:
        job.error = f"exit {result.exit_code}"
    save_job(job)


def run_job_sync(job: Job, *, timeout: float | None = 3600.0) -> Job:
    job.status = "running"
    job.updated_at = time.time()
    save_job(job)
    result = run_argv(job.argv, timeout=timeout)
    _apply_result(job, result)
    return job


def spawn_job(
    job: Job,
    *,
    timeout: float | None = 3600.0,
    runner: Callable[[Job], Job] | None = None,
) -> Job:
    """Start the job on a daemon thread; return the queued/running job immediately."""

    def _target() -> None:
        try:
            (runner or (lambda j: run_job_sync(j, timeout=timeout)))(job)
        except Exception as exc:  # noqa: BLE001 - job must not die silent
            log.exception("chatgpt actions job %s failed", job.id)
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {exc}"
            job.updated_at = time.time()
            try:
                save_job(job)
            except OSError:
                log.exception("could not persist failed job %s", job.id)

    threading.Thread(target=_target, name=f"chatgpt-job-{job.id[:8]}", daemon=True).start()
    return job
