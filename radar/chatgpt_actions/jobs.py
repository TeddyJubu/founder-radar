"""Durable local async jobs; retries never replay a recorded operation.

POSIX advisory locks serialize mutations through this API only, not standalone
CLI/systemd runs. Keep the job directory on one local filesystem and retain job
records for as long as clients may retry their idempotency keys.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from radar.chatgpt_actions.runner import RunResult, _try_parse_json, run_argv
from radar.render.digest import review_url

log = logging.getLogger(__name__)
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"done", "partial", "failed", "interrupted"})
JobStatus = str


class JobStoreError(RuntimeError):
    """Persistent state is unreadable; fail closed rather than duplicate work."""


class IdempotencyConflict(ValueError):
    """A key already identifies a different operation."""


class MutationBusy(RuntimeError):
    def __init__(self, job_id: str | None = None):
        super().__init__("Another API mutation is active. Retry later with the same key.")
        self.job_id = job_id


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
    idempotency_hash: str | None = None
    request_hash: str | None = None

    def to_public(self) -> dict[str, Any]:
        """CLI fidelity only: parsed output is data, never a replacement gate."""
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
        if self.status in TERMINAL_STATUSES:
            body.update(stdout=self.stdout, stderr=self.stderr)
            parsed = _try_parse_json(self.stdout)
            if parsed is not None:
                body["result"] = parsed
            if self.meta:
                body["meta"] = dict(self.meta)
        else:
            body["message"] = "Job still running. Poll again; do not resubmit with a new key."
        return body


def jobs_dir() -> Path:
    override = (os.environ.get("RADAR_CHATGPT_JOBS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    root = (os.environ.get("RADAR_ROOT") or "/opt/founder-radar").rstrip("/")
    return Path(root) / "data" / "chatgpt_jobs"


def _job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise ValueError("invalid job id")
    return jobs_dir() / f"{job_id}.json"


def save_job(job: Job) -> None:
    """Atomic replace plus fsync: acceptance is persisted before execution."""
    path = _job_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{job.id}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(job), stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_job(job_id: str) -> Job | None:
    try:
        path = _job_path(job_id)
    except ValueError:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        job = Job(**raw)
        if job.id != job_id or job.status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise ValueError("invalid stored job")
        return job
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError) as exc:
        raise JobStoreError(f"Cannot read job {job_id}; operator review required") from exc


def _open_lock(name: str, *, blocking: bool) -> int | None:
    root = jobs_dir()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _admission_lock() -> Iterator[None]:
    fd = _open_lock(".admission.lock", blocking=True)
    try:
        yield
    finally:
        os.close(fd)


def _active_jobs() -> list[Job]:
    return [job for path in jobs_dir().glob("*.json")
            if (job := load_job(path.stem)) is not None and job.status in ACTIVE_STATUSES]


def _recover_locked() -> None:
    # Called only while holding both locks: no API worker or inherited CLI
    # lock remains. Outcomes/side effects may be unknown, so NEVER replay.
    for job in _active_jobs():
        job.status = "interrupted"
        job.updated_at = time.time()
        job.error = (
            "API worker interrupted; side effects may have completed. "
            "Review the CLI/database/Sheet/delivery state before a new operation. "
            "This job will not be replayed."
        )
        save_job(job)


def recover_interrupted_jobs() -> None:
    with _admission_lock():
        fd = _open_lock(".mutation.lock", blocking=False)
        if fd is not None:
            try:
                _recover_locked()
            finally:
                os.close(fd)


def _acquire_mutation() -> int:
    fd = _open_lock(".mutation.lock", blocking=False)
    if fd is None:
        active = _active_jobs()
        raise MutationBusy(active[0].id if active else None)
    try:
        _recover_locked()
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def mutation_guard() -> Iterator[int]:
    """One API mutation at a time, including synchronous legacy endpoints."""
    with _admission_lock():
        fd = _acquire_mutation()
    try:
        yield fd
    finally:
        # Close rather than LOCK_UN: a surviving CLI inherits this lease if
        # the API exits. Releasing it explicitly would unlock the child's fd.
        os.close(fd)


def create_job(
    command: str, argv: list[str], *, meta: dict[str, Any] | None = None,
    job_id: str | None = None, idempotency_hash: str | None = None,
    request_hash: str | None = None,
) -> Job:
    now = time.time()
    job = Job(
        id=job_id or uuid.uuid4().hex, command=command, status="queued", argv=list(argv),
        created_at=now, updated_at=now, dashboard_url=review_url() or None,
        meta=dict(meta or {}), idempotency_hash=idempotency_hash, request_hash=request_hash,
    )
    save_job(job)
    return job


def submit_job(
    command: str, argv: list[str], *, idempotency_key: str | None = None,
    meta: dict[str, Any] | None = None, timeout: float | None = 3600.0,
) -> tuple[Job, bool]:
    """Atomically deduplicate/admit and launch. No restart queue or retries."""
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise ValueError("Idempotency-Key must be 1..128 ASCII letters, digits, '.', '_', ':', or '-'")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
    # The configured executable path is not an operation parameter.
    request_hash = hashlib.sha256(json.dumps([command, argv[1:]], separators=(",", ":")).encode()).hexdigest()
    with _admission_lock():
        if key_hash:
            existing = load_job(key_hash[:32])
            if existing is not None:
                if existing.idempotency_hash != key_hash or existing.request_hash != request_hash:
                    raise IdempotencyConflict("Idempotency-Key already used for a different operation")
                fd = _open_lock(".mutation.lock", blocking=False)
                if fd is not None:
                    try:
                        _recover_locked()
                    finally:
                        os.close(fd)
                return load_job(existing.id), True
        fd = _acquire_mutation()
        try:
            job = create_job(command, argv, meta=meta, job_id=key_hash[:32] if key_hash else None,
                             idempotency_hash=key_hash, request_hash=request_hash)
            spawn_job(job, timeout=timeout, lock_fd=fd)
        except BaseException:
            os.close(fd)
            raise
    return job, False


def _is_partial_search(job: Job, result: RunResult) -> bool:
    # Python also exits 1 on an uncaught exception, so exit 1 alone is not a
    # partial scan: require the CLI's own JSON summary to say "partial".
    if job.command != "search" or result.exit_code != 1:
        return False
    parsed = _try_parse_json(result.stdout)
    run = parsed.get("run") if isinstance(parsed, dict) else None
    return isinstance(run, dict) and run.get("status") == "partial"


def _apply_result(job: Job, result: RunResult) -> None:
    job.updated_at = time.time()
    job.exit_code = result.exit_code
    job.stdout = result.stdout
    job.stderr = result.stderr
    job.status = "done" if result.exit_code == 0 else (
        "partial" if _is_partial_search(job, result) else "failed"
    )
    job.error = None
    if result.exit_code != 0:
        job.error = ("Search completed partially; review output before publishing" if job.status == "partial"
                     else f"exit {result.exit_code}; review side effects before starting a new operation")
    save_job(job)


def run_job_sync(job: Job, *, timeout: float | None = 3600.0, lock_fd: int | None = None) -> Job:
    if lock_fd is None:
        with mutation_guard() as fd:
            return run_job_sync(job, timeout=timeout, lock_fd=fd)
    job.status = "running"
    job.updated_at = time.time()
    save_job(job)
    result = run_argv(job.argv, timeout=timeout, pass_fds=(lock_fd,))
    _apply_result(job, result)
    return job


def spawn_job(
    job: Job, *, timeout: float | None = 3600.0,
    runner: Callable[[Job], Job] | None = None, lock_fd: int | None = None,
) -> Job:
    """A lease supplied by submit_job transfers to this worker on success."""
    def _target() -> None:
        try:
            if runner:
                runner(job)
            else:
                run_job_sync(job, timeout=timeout, lock_fd=lock_fd)
        except Exception as exc:  # noqa: BLE001 - job must not die silent
            log.exception("chatgpt actions job %s failed", job.id)
            # An exception might follow a successful external side effect.
            job.status = "interrupted"
            job.error = f"{exc.__class__.__name__}: worker interrupted; review state before retrying"
            job.updated_at = time.time()
            try:
                save_job(job)
            except OSError:
                log.exception("could not persist interrupted job %s", job.id)
        finally:
            if lock_fd is not None:
                os.close(lock_fd)

    threading.Thread(target=_target, name=f"chatgpt-job-{job.id[:8]}", daemon=True).start()
    return job
