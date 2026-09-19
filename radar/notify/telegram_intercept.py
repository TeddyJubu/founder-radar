"""Start a Telegram search as a background CLI job and ack with the Today URL.

The Hermes gateway plugin shells out to `founder-radar search --background`.
This module is the implementation of that flag: one flock/pid lock, spawn
`search --send`, return the dashboard ping immediately. The child process
holds the lock and texts the ping again when the pipeline finishes.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

def lock_path() -> Path:
    override = (os.environ.get("RADAR_SEARCH_LOCK") or "").strip()
    if override:
        return Path(override).expanduser()
    from radar.store.db import default_db_path

    return default_db_path().expanduser().resolve().parent / "search.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours to signal
    except OSError:
        return False
    return True


def search_in_progress() -> bool:
    path = lock_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not raw:
        return False
    try:
        pid = int(raw.split()[0])
    except (TypeError, ValueError):
        return False
    if _pid_alive(pid):
        return True
    try:
        path.unlink()
    except OSError:
        pass
    return False


def radar_bin() -> str:
    env = (os.environ.get("RADAR_BIN") or "").strip()
    if env:
        return env
    found = shutil.which("founder-radar")
    if found:
        return found
    opt = "/opt/founder-radar/venv/bin/founder-radar"
    if os.path.isfile(opt):
        return opt
    return "founder-radar"


def wrap_as_radar(argv: list[str]) -> list[str]:
    """Prefix with `sudo -n -u radar` when the gateway is not already radar."""
    if os.environ.get("RADAR_TELEGRAM_NO_SUDO") == "1":
        return list(argv)
    user = (os.environ.get("RADAR_SUDO_USER") or "radar").strip() or "radar"
    try:
        import pwd

        if pwd.getpwuid(os.geteuid()).pw_name == user:
            return list(argv)
    except Exception:  # noqa: BLE001 - identity probe must not break kickoff
        pass
    root = (os.environ.get("RADAR_ROOT") or "/opt/founder-radar").rstrip("/")
    env_file = shlex.quote(f"{root}/.env")
    hermes_env = shlex.quote(f"{root}/hermes.env")
    app = shlex.quote(f"{root}/app")
    inner = (
        "set -a; "
        f"[ -f {env_file} ] && . {env_file}; "
        f"[ -f {hermes_env} ] && . {hermes_env}; "
        "set +a; "
        f"cd {app} 2>/dev/null || true; "
        + " ".join(shlex.quote(part) for part in argv)
    )
    return ["sudo", "-n", "-u", user, "bash", "-lc", inner]


def dashboard_ping() -> str:
    from radar.render.digest import render_today_ping
    from radar.store.db import Db, default_db_path

    return render_today_ping(Db(default_db_path()))


def ack_text(*, already: bool = False, ping: str | None = None) -> str:
    body = (ping or "").strip() or _fallback_ping()
    extra = (
        "A scan is already running. Refresh the dashboard — that is the list."
        if already
        else "Scan started. I'll ping this chat again when it finishes."
    )
    return f"{body}\n\n{extra}"


def _fallback_ping() -> str:
    from radar.render.digest import review_url

    url = review_url()
    lines = [
        "📡 UK Founder Radar",
        "",
        "Today's companies are on the dashboard — not in this chat.",
    ]
    if url:
        lines.append(url)
    else:
        lines.append("Open the Today page on the review site.")
    return "\n".join(lines)


def search_argv(
    *,
    fund_key: str | None = None,
    source_key: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    no_llm: bool = False,
    send: bool = True,
) -> list[str]:
    argv = [radar_bin(), "search"]
    if fund_key:
        argv.extend(["--fund", fund_key])
    if source_key:
        argv.extend(["--source", source_key])
    if since:
        argv.extend(["--since", since])
    if dry_run:
        argv.append("--dry-run")
    if no_llm:
        argv.append("--no-llm")
    if send:
        argv.append("--send")
    return argv


def kickoff_search(
    *,
    fund_key: str | None = None,
    source_key: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    no_llm: bool = False,
    popen=subprocess.Popen,
) -> tuple[bool, str]:
    """Spawn `search --send` if idle. Returns ``(started, ack_text)``."""
    try:
        ping = dashboard_ping()
    except Exception:  # noqa: BLE001 - ack must still name the dashboard
        log.exception("dashboard ping failed before search kickoff")
        ping = _fallback_ping()

    if search_in_progress():
        return False, ack_text(already=True, ping=ping)

    lock = lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    log_path = lock.with_name("search-bg.log")
    argv = wrap_as_radar(
        search_argv(
            fund_key=fund_key,
            source_key=source_key,
            since=since,
            dry_run=dry_run,
            no_llm=no_llm,
            send=True,
        )
    )
    log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - child inherits
    try:
        proc = popen(
            argv,
            start_new_session=True,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        log_f.close()
        log.exception("failed to spawn founder-radar search")
        return False, ack_text(already=False, ping=ping) + (
            "\n\nCould not start the scan. Open the dashboard anyway."
        )

    try:
        lock.write_text(f"{proc.pid} {int(time.time())}\n", encoding="utf-8")
    except OSError:
        log.exception("could not write search lock pid")
    return True, ack_text(already=False, ping=ping)


def mark_search_done() -> None:
    """Drop the lock when this scan process finishes."""
    path = lock_path()
    try:
        path.unlink()
    except OSError:
        pass
