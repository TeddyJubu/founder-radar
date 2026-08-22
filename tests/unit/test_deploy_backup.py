"""FR-9.4 via `deploy/backup.sh` — the script systemd actually runs.

`tests/unit/test_operations.py` covers `founder-radar db backup`, the CLI path.
This file covers the shell script that `founder-radar-backup.service` invokes,
which is a different surface with its own way of going wrong: a missing
database, an unset `BACKUP_DIR`, a non-zero exit that the timer would swallow.

Both are real. Only the CLI test carries the canonical `test_backup_creates_
and_prunes` name, so `pytest -k` still resolves to exactly one test per
09-test-plan §7 gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup.sh"
CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
CADDYFILE_HERMES = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile.hermes"
HERMES_DASHBOARD_SH = Path(__file__).resolve().parents[2] / "deploy" / "hermes-dashboard.sh"
HERMES_DASHBOARD_UNIT = Path(__file__).resolve().parents[2] / "deploy" / "hermes-dashboard.service"
DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
DEPLOY_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
UPDATE_SCRIPT = DEPLOY_DIR / "update-from-main.sh"
UPDATE_TIMER = DEPLOY_DIR / "founder-radar-update.timer"
UPDATE_SERVICE = DEPLOY_DIR / "founder-radar-update.service"
INSTALL_SH = DEPLOY_DIR / "install.sh"


@pytest.fixture
def backup_env(tmp_path):
    """A temp RADAR_DB + BACKUP_DIR. Never the live database."""
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI not available")
    db_path = tmp_path / "radar.db"
    subprocess.run(["sqlite3", str(db_path), "CREATE TABLE t(x);"],
                   check=True, capture_output=True)
    env = dict(os.environ, RADAR_DB=str(db_path),
               BACKUP_DIR=str(tmp_path / "backups"), RETAIN_DAYS="14")
    return env, db_path, tmp_path / "backups"


def test_backup_script_creates_and_prunes(backup_env):
    """The timer's job: a snapshot lands, and last fortnight's goes away."""
    env, _, backup_dir = backup_env

    first = subprocess.run(["bash", str(SCRIPT)], env=env,
                           capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    snapshots = list(backup_dir.glob("radar-*.db"))
    assert len(snapshots) == 1

    aged = datetime.now() - timedelta(days=15)
    os.utime(snapshots[0], (aged.timestamp(), aged.timestamp()))

    second = subprocess.run(["bash", str(SCRIPT)], env=env,
                            capture_output=True, text=True)
    assert second.returncode == 0, second.stderr

    remaining = list(backup_dir.glob("radar-*.db"))
    assert len(remaining) == 1
    # A same-day snapshot reuses the filename, so "one file" alone proves
    # nothing — assert the survivor is the fresh one, not the aged one.
    cutoff = (datetime.now() - timedelta(minutes=5)).timestamp()
    assert os.path.getmtime(remaining[0]) > cutoff


def test_backup_script_fails_loudly_without_a_database(tmp_path):
    """A backup that silently succeeds against nothing is the worst outcome:
    the timer stays green while the retention window quietly empties."""
    env = dict(os.environ, RADAR_DB=str(tmp_path / "missing.db"),
               BACKUP_DIR=str(tmp_path / "backups"))
    out = subprocess.run(["bash", str(SCRIPT)], env=env,
                         capture_output=True, text=True)
    assert out.returncode != 0
    assert "no database" in out.stderr


def _assert_caddy_has_no_committed_password(text: str) -> None:
    assert "basic_auth" in text, "the web surface lost its password gate"
    assert "{$RADAR_WEB_USER} {$RADAR_WEB_PASS_HASH}" in text, \
        "the password must come from the environment, not the file"
    # Every bcrypt hash starts with $2a/$2b/$2y; a committed one here would
    # be a credential in the repo. A plaintext password would be worse.
    assert "$2" not in text, "a bcrypt hash leaked into the Caddyfile"


def test_web_surface_requires_a_password():
    """Client-issues plan §3.6 (F17) — the login surface.

    The web app is only ever reachable through Caddy basic auth, and the
    password must exist only as the environment hash — never as a committed
    bcrypt hash or plaintext (the client could not log in on 16 Aug, and the
    password-change procedure is the one thing he asked to control himself).
    """
    _assert_caddy_has_no_committed_password(CADDYFILE.read_text())


def test_hermes_dashboard_is_published_behind_caddy():
    """hermes.<host> must keep a TLS cert across radar deploys.

    install.sh used to write a radar-only Caddyfile. HTTP still 308'd the
    hermes hostname to HTTPS, then the handshake aborted with
    ERR_SSL_PROTOCOL_ERROR because Caddy had no cert for that SNI. The
    installer must rewrite Caddy from git every time, append the hermes
    site, and share the review-surface password. Host is rewritten to the
    loopback bind so Hermes does not 400 Invalid Host after TLS works.
    """
    text = CADDYFILE_HERMES.read_text()
    _assert_caddy_has_no_committed_password(text)
    assert "{$HERMES_WEB_DOMAIN}" in text
    assert "{$HERMES_DASHBOARD_UPSTREAM}" in text
    assert "flush_interval -1" in text, "Chat WebSockets would buffer"
    assert "header_up Host 127.0.0.1:9119" in text
    assert "header_up Host {host}" not in text
    assert "0.0.0.0" not in text

    unit = HERMES_DASHBOARD_UNIT.read_text()
    assert "hermes-dashboard.sh" in unit
    assert "MemoryMax=400M" in unit
    assert "0.0.0.0" not in unit

    assert HERMES_DASHBOARD_SH.is_file()
    assert os.access(HERMES_DASHBOARD_SH, os.X_OK), \
        "hermes-dashboard.sh is not executable"

    installer = INSTALL_SH.read_text()
    assert 'cat "$HERE/Caddyfile.hermes" >> /etc/caddy/Caddyfile' in installer
    assert "leaving existing /etc/caddy/Caddyfile in place" not in installer
    assert "systemctl reload caddy" in installer
    assert installer.index('cat "$HERE/Caddyfile.hermes"') < \
        installer.index("systemctl reload caddy")
    assert 'hermes_domain="hermes.$web_domain"' in installer
    assert "hermes-dashboard.service" in installer
    assert "HERMES_DASHBOARD_PUBLIC_URL" in installer
    assert "npm run build" in installer
    assert "Frontend not built" in installer
    assert "probe_hermes_dashboard" in installer
    assert "hermes.env" in installer
    # Still never source .env (bcrypt `$2y$` under `set -u`).
    assert '. "$ENV_FILE"' not in installer


def test_caddy_templates_compose_radar_and_hermes_sites():
    """The files install.sh concatenates must be two named sites, not one."""
    combined = CADDYFILE.read_text() + "\n" + CADDYFILE_HERMES.read_text()
    assert "{$RADAR_WEB_DOMAIN}" in combined
    assert "{$HERMES_WEB_DOMAIN}" in combined
    assert combined.count("basic_auth") == 2
    assert "127.0.0.1:8787" in combined
    assert "127.0.0.1:9119" in combined
    assert "tls internal" not in combined


def test_hermes_dashboard_wrapper_execs_loopback(tmp_path):
    """The unit must bind loopback with --no-open, never 0.0.0.0."""
    fake = tmp_path / "hermes"
    args_path = tmp_path / "args"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > '" + str(args_path) + "'\n"
    )
    fake.chmod(0o755)
    env_file = tmp_path / "hermes.env"
    env_file.write_text(
        "HERMES_BIN=%s\nHERMES_WEB_DOMAIN=hermes.example.test\n" % fake
    )
    path = str(tmp_path) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
    out = subprocess.run(
        ["bash", str(HERMES_DASHBOARD_SH)],
        env={**os.environ, "HERMES_ENV_FILE": str(env_file),
             "ROOT": str(tmp_path), "PATH": path},
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    args = args_path.read_text()
    assert "dashboard" in args
    assert "--host" in args and "127.0.0.1" in args
    assert "--port" in args and "9119" in args
    assert "--no-open" in args
    assert "0.0.0.0" not in args


def test_hermes_dashboard_wrapper_fails_without_a_binary(tmp_path):
    """A green unit that execs nothing is how the URL stays blank."""
    env_file = tmp_path / "hermes.env"
    env_file.write_text("HERMES_BIN=/no/such/hermes\nHERMES_HOME=%s\n" % tmp_path)
    path = str(tmp_path) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
    out = subprocess.run(
        ["bash", str(HERMES_DASHBOARD_SH)],
        env={**os.environ, "HERMES_ENV_FILE": str(env_file),
             "ROOT": str(tmp_path), "HOME": str(tmp_path),
             "PATH": path},
        capture_output=True, text=True,
    )
    assert out.returncode != 0
    assert "not found" in out.stderr


def test_deploy_ships_main_without_a_manual_click():
    """Client-issues plan I23/J26 — a green main must reach the VPS.

    GitHub Actions secrets were never filled, so the Actions Deploy job
    failed closed and the box kept old code. Auto-ship is on the VPS:
    a systemd timer runs update-from-main.sh, which fast-forwards main
    and reinstalls. GitHub remains optional and must not go red when
    those secrets are empty.
    """
    script = UPDATE_SCRIPT.read_text()
    assert UPDATE_SCRIPT.is_file()
    assert os.access(UPDATE_SCRIPT, os.X_OK), "update-from-main.sh is not executable"
    assert "merge --ff-only origin/main" in script
    assert "deploy/install.sh" in script
    assert "rescore" in script and "--all" in script
    assert "flock" in script
    assert "SSHPASS" not in script
    assert "BEGIN OPENSSH" not in script

    timer = UPDATE_TIMER.read_text()
    assert "OnBootSec=" in timer
    assert "OnUnitInactiveSec=" in timer
    assert "WantedBy=timers.target" in timer

    service = UPDATE_SERVICE.read_text()
    assert "User=root" in service
    assert "update-from-main.sh" in service
    assert "EnvironmentFile=" not in service, \
        "the update unit must not load .env (secrets would enter the journal)"

    installer = INSTALL_SH.read_text()
    assert "founder-radar-update.timer" in installer
    assert "founder-radar-update.service" in installer
    assert "enable --now founder-radar-update.timer" in installer
    assert "chmod 755" in installer and "update-from-main.sh" in installer
    assert "references/today-check.md" in installer
    assert "hermes-dashboard.sh" in installer
    assert 'cat "$HERE/Caddyfile.hermes" >> /etc/caddy/Caddyfile' in installer
    assert "leaving existing /etc/caddy/Caddyfile in place" not in installer
    # pip as radar from /root dies on an editable path hook. Pin the fix.
    assert 'cd "$APP_DIR"' in installer
    assert 'sudo -H -u "$APP_USER"' in installer
    # bcrypt `$2y$` in .env is `$2` under bash `set -u` and used to abort
    # install.sh after the timer was enabled. Presence-check the keys
    # without sourcing the file.
    assert '. "$ENV_FILE"' not in installer
    assert "web_hash_set" in installer

    script = UPDATE_SCRIPT.read_text()
    assert "hermes-dashboard.service" in script
    assert "Frontend not built" in script
    assert "not serving UI" in script

    workflow = DEPLOY_WORKFLOW.read_text()
    assert "configured=false" in workflow
    assert "update-from-main.sh" in workflow
    assert "::error::Required secret" not in workflow
    assert "exit 1" not in workflow.split("Skip when")[1].split("- name: Deploy")[0], \
        "missing GitHub secrets must skip, not fail the Actions tab"
    assert "workflow_dispatch:" in workflow
    assert "inputs.rescore_all" in workflow


def test_update_from_main_fast_forwards_and_skips_when_current(tmp_path):
    """The timer's job, against a local origin: no-op when HEAD matches,
    fast-forward when main moves. Dry-run so we do not invoke install.sh."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    origin = tmp_path / "origin.git"
    checkout = tmp_path / "app"
    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    env = {
        **os.environ,
        "ROOT": str(root),
        "APP_DIR": str(checkout),
        "RADAR_UPDATE_ALLOW_NONROOT": "1",
        "RADAR_UPDATE_DRY_RUN": "1",
        "RADAR_UPDATE_LOCK": str(tmp_path / "update.lock"),
        "RADAR_UPDATE_LOG": str(root / "logs" / "update.log"),
        "GIT_AUTHOR_NAME": "radar-test",
        "GIT_AUTHOR_EMAIL": "radar-test@example.test",
        "GIT_COMMITTER_NAME": "radar-test",
        "GIT_COMMITTER_EMAIL": "radar-test@example.test",
    }

    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=env,
                              capture_output=True, text=True, check=True)

    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "seed"
    git(tmp_path, "clone", str(origin), str(work))
    (work / "README").write_text("one\n")
    git(work, "add", "README")
    git(work, "commit", "-m", "one")
    git(work, "push", "origin", "main")
    git(tmp_path, "clone", str(origin), str(checkout))

    first = subprocess.run(["bash", str(UPDATE_SCRIPT)], env=env,
                           capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    log = (root / "logs" / "update.log").read_text()
    assert "nothing to do" in first.stdout + first.stderr + log

    head_before = git(checkout, "rev-parse", "HEAD").stdout.strip()
    (work / "README").write_text("two\n")
    git(work, "add", "README")
    git(work, "commit", "-m", "two")
    git(work, "push", "origin", "main")
    origin_head = git(work, "rev-parse", "HEAD").stdout.strip()
    assert origin_head != head_before

    second = subprocess.run(["bash", str(UPDATE_SCRIPT)], env=env,
                            capture_output=True, text=True)
    assert second.returncode == 0, second.stdout + second.stderr
    head_after = git(checkout, "rev-parse", "HEAD").stdout.strip()
    assert head_after == origin_head
    assert "dry-run" in second.stdout + second.stderr + (
        root / "logs" / "update.log").read_text()
