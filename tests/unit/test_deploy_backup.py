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
