"""Control room backups: nightly DB snapshots and local code rollback.

Database restore and code rollback are separate. Restoring a snapshot does not
change the checkout; checking out an older commit does not change the database.

Confirmation is typed at the boundary. A wrong date or SHA refuses the action
and leaves the live database untouched. A successful restore writes a safety
copy first, then replaces the live file through SQLite's backup API. The Google
Sheet is not synced afterwards — a sync would pull Sheet edits back in and undo
restored decisions.

Code rollback goes through the root-owned ``rollback.sh`` helper so the five-
minute updater cannot fast-forward the checkout away. That helper also owns the
SSH ``release`` path when the Control room tab itself is rolled back.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from radar.store.db import RestoreError, now_iso, restore_database

SNAPSHOT_RE = re.compile(r"^radar-(\d{4}-\d{2}-\d{2})\.db$")
RELEASE_COMMAND = "sudo /usr/local/libexec/founder-radar/rollback.sh release"
DEFAULT_ROLLBACK = Path("/usr/local/libexec/founder-radar/rollback.sh")
DEFAULT_HOLD = Path("/etc/founder-radar/rollback-hold")
DEFAULT_ROOT = Path("/opt/founder-radar")
BUSY_SCAN_STATES = frozenset({"active", "activating", "reloading", "deactivating"})


class BackupError(ValueError):
    """A restore or rollback was refused. Nothing was changed."""


def backup_dir() -> Path:
    raw = os.environ.get("RADAR_BACKUP_DIR")
    if raw:
        return Path(raw).expanduser()
    root = os.environ.get("RADAR_ROOT", str(DEFAULT_ROOT))
    return Path(root).expanduser() / "backups"


def app_dir() -> Path:
    raw = os.environ.get("RADAR_APP_DIR")
    if raw:
        return Path(raw).expanduser()
    root = os.environ.get("RADAR_ROOT", str(DEFAULT_ROOT))
    return Path(root).expanduser() / "app"


def hold_path() -> Path:
    raw = os.environ.get("RADAR_ROLLBACK_HOLD")
    return Path(raw).expanduser() if raw else DEFAULT_HOLD


def rollback_script() -> Path:
    raw = os.environ.get("RADAR_ROLLBACK_SCRIPT")
    return Path(raw).expanduser() if raw else DEFAULT_ROLLBACK


def live_db_path(db: Any) -> Path:
    """The on-disk path behind an open Control room connection."""
    if hasattr(db, "path") and db.path:
        return Path(db.path)
    conn = getattr(db, "conn", db)
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    if not path:
        raise BackupError("the Control room needs a file database, not :memory:")
    return Path(path)


# ------------------------------------------------------------------- listing


@dataclass(frozen=True)
class Snapshot:
    date: str
    filename: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {"date": self.date, "filename": self.filename,
                "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short: str
    date: str
    subject: str
    current: bool

    def as_dict(self) -> dict[str, Any]:
        return {"sha": self.sha, "short": self.short, "date": self.date,
                "subject": self.subject, "current": self.current}


def list_snapshots(directory: Path | None = None) -> list[Snapshot]:
    root = directory or backup_dir()
    if not root.is_dir():
        return []
    out: list[Snapshot] = []
    for path in sorted(root.iterdir(), reverse=True):
        match = SNAPSHOT_RE.match(path.name)
        if not match or not path.is_file():
            continue
        out.append(Snapshot(date=match.group(1), filename=path.name,
                            size_bytes=path.stat().st_size))
    return out


def _git_cmd(app: Path, *args: str) -> list[str]:
    """Git against a root-owned checkout. The web service runs as `radar`."""
    return ["git", "-c", f"safe.directory={app.resolve()}", "-C", str(app), *args]


def _git(app: Path, *args: str) -> str:
    result = subprocess.run(
        _git_cmd(app, *args),
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise BackupError(
            (result.stderr or result.stdout or "git failed").strip()
            or f"git {' '.join(args)} failed"
        )
    return result.stdout


def list_commits(app: Path | None = None, *, limit: int = 30) -> list[CommitInfo]:
    root = app or app_dir()
    if not (root / ".git").exists():
        return []
    try:
        head = _git(root, "rev-parse", "HEAD").strip()
    except BackupError:
        return []
    # Prefer origin/main history; fall back to HEAD when fetch has not run.
    tip = "origin/main"
    probe = subprocess.run(
        _git_cmd(root, "rev-parse", "--verify", tip),
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        tip = "HEAD"
    fmt = "%H%x09%h%x09%ad%x09%s"
    try:
        log = _git(root, "log", f"-{int(limit)}",
                   "--date=short", f"--format={fmt}", tip)
    except BackupError:
        return []
    out: list[CommitInfo] = []
    for line in log.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        sha, short, date, subject = parts
        out.append(CommitInfo(
            sha=sha, short=short, date=date, subject=subject,
            current=(sha == head),
        ))
    return out


def held_sha() -> str | None:
    path = hold_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def list_view() -> dict[str, Any]:
    held = held_sha()
    return {
        "databases": [s.as_dict() for s in list_snapshots()],
        "commits": [c.as_dict() for c in list_commits()],
        "held_sha": held,
        "release_command": RELEASE_COMMAND,
    }


# ------------------------------------------------------------------- restore


def resolve_snapshot(filename: str, directory: Path | None = None) -> tuple[Path, str]:
    """Return (absolute path, YYYY-MM-DD) or raise BackupError."""
    name = Path(str(filename or "")).name
    match = SNAPSHOT_RE.match(name)
    if not match:
        raise BackupError("not a nightly snapshot filename")
    root = (directory or backup_dir()).resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BackupError("snapshot path is outside the backup directory") from exc
    if not path.is_file():
        raise BackupError(f"snapshot not found: {name}")
    return path, match.group(1)


def scan_is_running() -> bool:
    """True when the morning oneshot is mid-run (same states as the updater)."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "--property=ActiveState", "--value",
             "founder-radar.service"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip() in BUSY_SCAN_STATES


def _safety_copy(live: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = directory / f"radar-before-restore-{stamp}.db"
    tmp = dest.with_suffix(dest.suffix + ".partial")
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(f"{live.resolve().as_uri()}?mode=ro", uri=True)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    tmp.replace(dest)
    return dest


def restore_snapshot(db: Any, filename: str, confirm: str, *,
                     directory: Path | None = None) -> dict[str, Any]:
    """Restore a nightly snapshot after a typed date confirmation."""
    path, date = resolve_snapshot(filename, directory)
    if str(confirm or "").strip() != date:
        raise BackupError("confirmation must match the snapshot date")
    if scan_is_running():
        raise BackupError("the morning scan is running — try again when it finishes")

    live = live_db_path(db)
    root = (directory or backup_dir()).resolve()
    safety = _safety_copy(live, root)
    try:
        restore_database(path, live)
    except RestoreError as exc:
        raise BackupError(str(exc)) from exc

    # Audit on the restored database so the change is visible after reload.
    note = f"safety copy {safety.name}"
    try:
        db.execute(
            "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
            "VALUES (?, 'backup', ?, ?, ?, ?, 'skipped')",
            (now_iso(), path.name, live.name, path.name, note),
        )
        if hasattr(db, "commit"):
            db.commit()
    except Exception:  # noqa: BLE001 - pre-migration DB or closed handle
        pass

    return {
        "ok": True,
        "restored": path.name,
        "date": date,
        "safety_copy": safety.name,
        "sheet_sync": "skipped",
    }


# ------------------------------------------------------------------- code


def _resolve_commit(app: Path, sha: str) -> str:
    text = str(sha or "").strip()
    if not text or any(c in text for c in " \t\n\"';|&"):
        raise BackupError("invalid commit id")
    try:
        full = _git(app, "rev-parse", "--verify", f"{text}^{{commit}}").strip()
    except BackupError as exc:
        raise BackupError(f"unknown commit {text!r}") from exc
    ancestor = subprocess.run(
        _git_cmd(app, "merge-base", "--is-ancestor", full, "origin/main"),
        capture_output=True, text=True, check=False,
    )
    if ancestor.returncode != 0:
        tip = subprocess.run(
            _git_cmd(app, "rev-parse", "--verify", "origin/main"),
            capture_output=True, text=True, check=False,
        )
        if tip.returncode != 0:
            raise BackupError("origin/main is not available")
        raise BackupError("commit is not on origin/main")
    return full


def _run_rollback(*args: str) -> str:
    script = rollback_script()
    if not script.is_file():
        raise BackupError(f"rollback helper not installed: {script}")
    cmd = ["sudo", "-n", str(script), *args]
    # Tests may point RADAR_ROLLBACK_SCRIPT at a non-sudo stub.
    if os.environ.get("RADAR_ROLLBACK_NOSUDO") == "1":
        cmd = [str(script), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "rollback failed").strip()
        raise BackupError(detail)
    return (result.stdout or "").strip()


def rollback_code(sha: str, confirm: str, *, app: Path | None = None) -> dict[str, Any]:
    """Check out an ancestor of origin/main and hold the updater."""
    root = app or app_dir()
    full = _resolve_commit(root, sha)
    typed = str(confirm or "").strip()
    if not typed:
        raise BackupError("confirmation must match the commit id")
    try:
        confirmed = _resolve_commit(root, typed)
    except BackupError as exc:
        raise BackupError("confirmation must match the commit id") from exc
    if confirmed != full:
        raise BackupError("confirmation must match the commit id")

    _run_rollback("rollback-code", full)
    return {
        "ok": True,
        "sha": full,
        "short": full[:7],
        "reload": True,
        "release_command": RELEASE_COMMAND,
    }


def release_hold() -> dict[str, Any]:
    """Clear the updater hold and return the checkout to origin/main."""
    _run_rollback("release")
    return {"ok": True, "released": True}


def apply_body(db: Any, action: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch a POST body. Used by the HTTP layer."""
    if action == "restore":
        raw = body.get("filename") or body.get("date")
        if not isinstance(raw, str) or not raw.strip():
            raise BackupError("filename is required")
        # Basename only — never honour a client-supplied directory.
        name = Path(raw).name
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            name = f"radar-{name}.db"
        if not SNAPSHOT_RE.match(name):
            raise BackupError("not a nightly snapshot filename")
        return restore_snapshot(db, name, str(body.get("confirm") or ""))
    if action == "rollback":
        return rollback_code(str(body.get("sha") or ""), str(body.get("confirm") or ""))
    if action == "release":
        return release_hold()
    raise BackupError(f"unknown action {action!r}")
