"""Diagnose a live Founder Radar box and apply *safe* remediations.

This is the complete interface behind `founder-radar repair` and behind the
Hermes playbook on the VPS (FR-9.6, FR-9.7). Chat may orchestrate it; it may
not invent a parallel runbook.

What this module will do, on `--apply`:

- migrate an empty database
- prune `radar-*.db` backups older than the retention window when disk is low
- restart `founder-radar-web.service` if it is down (live box only)
- optionally start the daily scan if the last successful run is stale
  (`--run`, and never from an OnFailure of that same unit)

What it will not do:

- edit scoring, gates, thresholds, or any file under `radar/score/`
- print `.env`, tokens, or the Google service-account JSON
- `git reset`, force-push, or fast-forward the production checkout
  (that is `deploy/update-from-main.sh`)
- disable a source the sheet turned off — that is operator intent
- call the pipeline in-process (a 25-minute import inside a diagnostic)

Layout changes and parse bugs are `needs_agent=True`. Hermes follows
`hermes/skills/founder-radar/references/repair.md` for those; this module
only points at them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LIVE_ROOT = Path("/opt/founder-radar")
REQUIRED_ENV = ("COMPANIES_HOUSE_API_KEY",)
OPTIONAL_ENV = ("LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "GOOGLE_SA_JSON", "SHEET_ID", "RADAR_USER_AGENT")
DEFAULT_RETAIN_DAYS = 14
TIGHT_RETAIN_DAYS = 7
DISK_ALERT_GB = 5.0
DISK_FAIL_MB = 500
ERROR_LOG_TAIL = 40
UNITS = (
    "founder-radar.timer",
    "founder-radar-web.service",
    "founder-radar-heartbeat.timer",
    "founder-radar-update.timer",
)

# Never echo a value that looks like a credential, even from an error log.
_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)"
    r"\s*[:=]\s*\S+"
)


@dataclass
class Check:
    """One row of the repair table."""

    name: str
    ok: bool
    detail: str
    severity: str = "info"          # info | warn | fail
    fixable: str | None = None      # migrate | prune-backups | restart-web | start-scan
    needs_agent: bool = False


@dataclass
class RepairReport:
    """The verdict. `healthy` is everything ok; `needs_agent` is a code fix."""

    checks: list[Check] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    healthy: bool = True
    needs_agent: bool = False
    stale: bool = False
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    def as_text(self) -> str:
        lines = ["🔧 Founder Radar repair", ""]
        width = max((len(c.name) for c in self.checks), default=8)
        for check in self.checks:
            if check.ok:
                mark = "✅"
            elif check.severity == "warn":
                mark = "⚠️"
            else:
                mark = "❌"
            lines.append(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        if self.applied:
            lines.append("")
            lines.append("Applied: " + "; ".join(self.applied))
        if self.skipped:
            lines.append("Skipped: " + "; ".join(self.skipped))
        if self.needs_agent:
            lines.append("")
            lines.append(
                "Needs a code fix — Hermes should follow "
                "hermes/skills/founder-radar/references/repair.md"
            )
        elif all(c.ok for c in self.checks):
            lines.append("")
            lines.append("Healthy.")
        if self.summary:
            lines.append(self.summary)
        return "\n".join(lines).rstrip() + "\n"


def infer_root(db_path: Path) -> Path:
    """Production layout is `$ROOT/data/radar.db`. Tests override with RADAR_ROOT."""
    env = os.environ.get("RADAR_ROOT")
    if env:
        return Path(env)
    path = Path(db_path)
    if path.parent.name == "data":
        return path.parent.parent
    return path.parent if path.parent != Path() else Path(".")


def live_box(root: Path) -> bool:
    """True only on the deployed VPS checkout, never in the test suite's tmp dir."""
    try:
        return root.resolve() == LIVE_ROOT and (LIVE_ROOT / "app").is_dir()
    except OSError:
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redact(text: str) -> str:
    return _SECRETISH.sub(r"\1=***", text)


def _disk_free_mb(path: Path) -> tuple[int, str] | tuple[None, str]:
    target = path if path.exists() else Path(".")
    try:
        free_mb = shutil.disk_usage(target).free // (1024 * 1024)
        return free_mb, f"{free_mb} MB free"
    except OSError as exc:
        return None, str(exc)


def _systemctl(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str] | None:
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["systemctl", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("systemctl %s failed: %s", args, type(exc).__name__)
        return None


def _prune_backups(backup_dir: Path, *, retain_days: int) -> int:
    """Delete `radar-*.db` older than `retain_days`. Same rule as backup.sh."""
    if retain_days <= 0 or not backup_dir.is_dir():
        return 0
    cutoff = time.time() - retain_days * 86_400
    pruned = 0
    snapshots = sorted(
        (p for p in backup_dir.glob("radar-*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    # Never delete the newest snapshot even if it is somehow older than the
    # window — a repair that empties backups is worse than a full disk.
    keep = snapshots[-1] if snapshots else None
    for old in snapshots:
        if old == keep:
            continue
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
                pruned += 1
        except OSError as exc:
            log.warning("could not prune %s: %s", old, type(exc).__name__)
    return pruned


def _tail_error_log(root: Path) -> str | None:
    log_path = root / "logs" / "error.log"
    if not log_path.is_file():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    snippet = "\n".join(lines[-ERROR_LOG_TAIL:])
    return _redact(snippet)


def diagnose(db, *, root: Path | None = None, now: datetime | None = None,
             db_path: Path | None = None) -> RepairReport:
    """Read-only picture of the box. Never raises — a diagnostic that crashes
    is useless, same rule as `founder-radar doctor`."""
    from radar.notify.heartbeat import (
        blocked_sources, last_successful_run, parse_duration, stale_sources,
    )

    db_path = Path(db_path or getattr(db, "path", ".") or ".")
    root = root or infer_root(db_path)
    moment = now or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    report = RepairReport()
    checks = report.checks

    for name in REQUIRED_ENV:
        present = bool(os.environ.get(name))
        checks.append(Check(
            name=f"env {name}", ok=present,
            detail="required" if present else "required — missing",
            severity="fail" if not present else "info",
        ))
    for name in OPTIONAL_ENV:
        present = bool(os.environ.get(name))
        checks.append(Check(
            name=f"env {name}", ok=True,
            detail="set" if present else "optional — degraded if missing",
            severity="info",
        ))

    try:
        tables = db.tables()
    except Exception as exc:                    # noqa: BLE001 - must not crash
        tables = set()
        checks.append(Check(
            name="database", ok=False, detail=str(exc), severity="fail",
            fixable="migrate",
        ))
    else:
        checks.append(Check(
            name="database", ok=bool(tables),
            detail=f"{len(tables)} tables at {db_path}" if tables
            else f"no tables at {db_path}",
            severity="fail" if not tables else "info",
            fixable="migrate" if not tables else None,
        ))
        try:
            version = db.get_meta("schema_version", None)
        except Exception:                        # noqa: BLE001
            version = None
        checks.append(Check(
            name="schema version", ok=version is not None,
            detail=str(version or "missing"),
            severity="fail" if version is None else "info",
            fixable="migrate" if version is None else None,
        ))

    free_mb, disk_detail = _disk_free_mb(
        db_path.parent if db_path.parent.exists() else root
    )
    if free_mb is None:
        checks.append(Check(name="disk space", ok=False, detail=disk_detail,
                            severity="fail"))
    else:
        free_gb = free_mb / 1024
        tight = free_gb < DISK_ALERT_GB
        failed = free_mb <= DISK_FAIL_MB
        checks.append(Check(
            name="disk space", ok=not failed,
            detail=f"{free_gb:.1f} GB free" + (" (below 5 GB)" if tight else ""),
            severity="fail" if failed else ("warn" if tight else "info"),
            fixable="prune-backups" if tight or failed else None,
        ))

    sa = os.environ.get("GOOGLE_SA_JSON")
    if sa:
        exists = Path(sa).is_file()
        checks.append(Check(
            name="google service account", ok=exists,
            detail=sa if exists else f"missing file {sa}",
            severity="fail" if not exists else "info",
        ))

    try:
        last = last_successful_run(db)
    except Exception:                            # noqa: BLE001
        last = None
    stale_after = parse_duration("26h")
    age = (moment - last) if last else None
    report.stale = last is None or (age is not None and age > stale_after)
    if report.stale:
        if last is None:
            detail = "no successful run recorded"
        else:
            hours = int(age.total_seconds() // 3600)
            detail = f"last good run {hours}h ago ({last:%a %d %b %H:%M} UTC)"
        checks.append(Check(
            name="last run", ok=False, detail=detail, severity="fail",
            fixable="start-scan",
        ))
    else:
        hours = int(age.total_seconds() // 3600) if age else 0
        checks.append(Check(
            name="last run", ok=True,
            detail=f"ok {hours}h ago",
        ))

    try:
        quiet = stale_sources(db)
    except Exception:                            # noqa: BLE001
        quiet = []
    try:
        blocked = blocked_sources(db)
    except Exception:                            # noqa: BLE001
        blocked = []
    if quiet:
        checks.append(Check(
            name="quiet sources", ok=False,
            detail=", ".join(quiet) + " — zero items for 7 days (layout change?)",
            severity="warn", needs_agent=True,
        ))
    if blocked:
        detail = "; ".join(f"{key} ({n} checks)" for key, n in blocked)
        checks.append(Check(
            name="blocked sources", ok=False,
            detail=detail + " — possible anti-bot block, not a code edit",
            severity="warn",
        ))
    if not quiet and not blocked:
        checks.append(Check(name="source health", ok=True, detail="no quiet or blocked streak"))

    snippet = _tail_error_log(root)
    if snippet:
        last_line = snippet.strip().splitlines()[-1][:200]
        checks.append(Check(
            name="error.log", ok=True,
            detail=f"last line: {last_line}",
            severity="info",
        ))

    if live_box(root):
        for unit in UNITS:
            proc = _systemctl("is-active", unit)
            active = proc is not None and proc.returncode == 0
            failed = False
            fail_proc = _systemctl("is-failed", unit)
            if fail_proc is not None and fail_proc.returncode == 0:
                failed = True
            ok = active and not failed
            if unit == "founder-radar-web.service" and not ok:
                checks.append(Check(
                    name=unit, ok=False,
                    detail="inactive or failed",
                    severity="fail", fixable="restart-web",
                ))
            else:
                checks.append(Check(
                    name=unit, ok=ok,
                    detail="active" if ok else "inactive",
                    severity="warn" if not ok else "info",
                ))

    report.needs_agent = any(c.needs_agent for c in checks)
    report.healthy = all(c.ok for c in checks)
    return report


def apply_remediations(
    db, report: RepairReport, *,
    root: Path,
    start_run: bool = False,
) -> RepairReport:
    """Mutate the box for every `fixable` check. Idempotent. Never raises."""
    wanted = {c.fixable for c in report.checks if c.fixable and not c.ok}

    if "migrate" in wanted:
        try:
            db.migrate()
            report.applied.append(f"migrated schema ({len(db.tables())} tables)")
        except Exception as exc:                # noqa: BLE001
            report.skipped.append(f"migrate failed: {type(exc).__name__}")

    if "prune-backups" in wanted:
        backup_dir = root / "backups"
        pruned = _prune_backups(backup_dir, retain_days=DEFAULT_RETAIN_DAYS)
        free_mb, _ = _disk_free_mb(backup_dir if backup_dir.exists() else root)
        if free_mb is not None and free_mb / 1024 < DISK_ALERT_GB:
            pruned += _prune_backups(backup_dir, retain_days=TIGHT_RETAIN_DAYS)
        if pruned:
            report.applied.append(f"pruned {pruned} old backup(s)")
        else:
            report.skipped.append("disk low but no old backups to prune")

    if "restart-web" in wanted:
        if not live_box(root):
            report.skipped.append("restart-web skipped (not the live box)")
        else:
            proc = _systemctl("restart", "founder-radar-web.service")
            if proc is not None and proc.returncode == 0:
                report.applied.append("restarted founder-radar-web.service")
            else:
                report.skipped.append("could not restart founder-radar-web.service")

    if "start-scan" in wanted and not start_run:
        report.skipped.append("stale run — pass --run to start a scan")
    elif start_run and "start-scan" not in wanted:
        report.skipped.append("last run is fresh — not starting a scan")
    elif start_run and "start-scan" in wanted:
        if not live_box(root):
            report.skipped.append("start-scan skipped (not the live box)")
        else:
            active = _systemctl("is-active", "founder-radar.service")
            if active is not None and active.returncode == 0:
                report.skipped.append("daily scan already running")
            else:
                proc = _systemctl("start", "founder-radar.service")
                if proc is not None and proc.returncode == 0:
                    report.applied.append("started founder-radar.service")
                else:
                    report.skipped.append("could not start founder-radar.service")

    return report


def run_repair(
    db, *,
    apply: bool = False,
    start_run: bool = False,
    root: Path | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
    request_file: Path | None = None,
) -> RepairReport:
    """Diagnose, optionally apply, optionally write a Hermes hand-off file."""
    db_path = Path(db_path or getattr(db, "path", ".") or ".")
    root = root or infer_root(db_path)
    report = diagnose(db, root=root, now=now, db_path=db_path)
    if apply:
        apply_remediations(db, report, root=root, start_run=start_run)
        # Re-read after remediations so the printed table is the new state,
        # but keep the applied/skipped lists from this pass.
        applied, skipped = report.applied, report.skipped
        report = diagnose(db, root=root, now=now, db_path=db_path)
        report.applied = applied
        report.skipped = skipped
        report.needs_agent = any(c.needs_agent for c in report.checks)
        report.healthy = all(c.ok for c in report.checks)

    if request_file is not None:
        request_file = Path(request_file)
        try:
            request_file.parent.mkdir(parents=True, exist_ok=True)
            if report.needs_agent:
                request_file.write_text(
                    json.dumps(report.as_dict(), indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
                report.applied.append(f"wrote Hermes request {request_file}")
            elif request_file.exists():
                request_file.unlink()
        except OSError as exc:
            report.skipped.append(f"could not write request file: {type(exc).__name__}")

    return report
