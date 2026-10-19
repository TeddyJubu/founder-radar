"""Control room backups: refused restores, a real temp-db restore, listing."""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from radar.store.db import Db
from tests.fakes import seed_companies

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
GUARD = DEPLOY / "rollback-guard.sh"


@dataclass
class Admin:
    port: int
    path: str
    backup_dir: Path

    def read(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


@pytest.fixture
def admin(tmp_path, monkeypatch):
    from prototype.server import _conn, make_handler

    for name in ("SHEET_ID", "RADAR_SHEET_ID", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(name, raising=False)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setenv("RADAR_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("RADAR_APP_DIR", str(tmp_path / "no-such-app"))
    monkeypatch.setenv("RADAR_ROLLBACK_HOLD", str(tmp_path / "hold"))
    monkeypatch.setattr("radar.admin.backups.scan_is_running", lambda: False)

    path = tmp_path / "admin.db"
    setup = Db(str(path))
    setup.migrate()
    seed_companies(setup, count=2, shortlist=1)
    setup.execute(
        "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
        "VALUES ('2026-01-01T00:00:00Z', 'settings', 'shortlist_fit', '70', '75', NULL, 'ok')"
    )
    setup.close()

    conn = _conn(str(path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(conn))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Admin(server.server_port, str(path), backup_dir)
    finally:
        server.shutdown()
        server.server_close()
        conn.close()


def _send(admin: Admin, method: str, path: str, *, body: bytes | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", admin.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw
    finally:
        conn.close()


def get(admin: Admin, path: str) -> tuple[int, Any]:
    return _send(admin, "GET", path)


def post(admin: Admin, path: str, payload: dict) -> tuple[int, Any]:
    return _send(admin, "POST", path, body=json.dumps(payload).encode(),
                 headers={"Content-Type": "application/json"})


def _snapshot(backup_dir: Path, live: Path, date: str = "2026-10-18") -> Path:
    dest = backup_dir / f"radar-{date}.db"
    src = sqlite3.connect(str(live))
    out = sqlite3.connect(str(dest))
    try:
        src.backup(out)
        out.execute("CREATE TABLE IF NOT EXISTS restore_marker(x TEXT)")
        out.execute("INSERT INTO restore_marker(x) VALUES ('from-snapshot')")
        out.commit()
    finally:
        out.close()
        src.close()
    return dest


def test_backups_list_shows_nightly_snapshots(admin):
    _snapshot(admin.backup_dir, Path(admin.path), "2026-10-17")
    (admin.backup_dir / "notes.txt").write_text("ignore")
    (admin.backup_dir / "client-other.db").write_text("nope")

    status, body = get(admin, "/api/admin/backups")
    assert status == 200
    dates = [row["date"] for row in body["databases"]]
    assert dates == ["2026-10-17"]
    assert body["databases"][0]["filename"] == "radar-2026-10-17.db"
    assert "release_command" in body
    assert "rollback.sh release" in body["release_command"]


def test_restore_refuses_wrong_confirmation(admin):
    snap = _snapshot(admin.backup_dir, Path(admin.path))
    before = Path(admin.path).read_bytes()
    changes_before = admin.read("SELECT COUNT(*) FROM admin_change")[0][0]

    status, body = post(admin, "/api/admin/backups/restore", {
        "filename": snap.name, "confirm": "1999-01-01",
    })
    assert status == 400
    assert "confirmation" in body["error"]
    assert Path(admin.path).read_bytes() == before
    assert admin.read("SELECT COUNT(*) FROM admin_change")[0][0] == changes_before
    assert not list(admin.backup_dir.glob("radar-before-restore-*.db"))


def test_restore_refuses_path_outside_backup_dir(admin, tmp_path):
    outsider = tmp_path / "outside" / "radar-2026-10-18.db"
    outsider.parent.mkdir()
    outsider.write_bytes(Path(admin.path).read_bytes())
    before = Path(admin.path).read_bytes()

    status, body = post(admin, "/api/admin/backups/restore", {
        "filename": str(outsider), "confirm": "2026-10-18",
    })
    assert status == 400
    assert Path(admin.path).read_bytes() == before
    # Basename-only resolution: the file outside BACKUP_DIR is never opened.
    assert "not found" in body["error"]
    assert not list(admin.backup_dir.glob("radar-before-restore-*.db"))


def test_restore_refuses_while_morning_scan_runs(admin, monkeypatch):
    snap = _snapshot(admin.backup_dir, Path(admin.path))
    monkeypatch.setattr("radar.admin.backups.scan_is_running", lambda: True)
    before = Path(admin.path).read_bytes()

    status, body = post(admin, "/api/admin/backups/restore", {
        "filename": snap.name, "confirm": "2026-10-18",
    })
    assert status == 400
    assert "morning scan" in body["error"]
    assert Path(admin.path).read_bytes() == before


def test_matching_confirmation_restores_and_writes_safety_copy(admin):
    snap = _snapshot(admin.backup_dir, Path(admin.path), "2026-10-18")
    # Prove the live DB does not yet have the marker from the snapshot.
    assert admin.read(
        "SELECT name FROM sqlite_master WHERE name = 'restore_marker'") == []

    status, body = post(admin, "/api/admin/backups/restore", {
        "filename": snap.name, "confirm": "2026-10-18",
    })
    assert status == 200, body
    assert body["ok"] is True
    assert body["sheet_sync"] == "skipped"
    assert body["safety_copy"].startswith("radar-before-restore-")
    safety = admin.backup_dir / body["safety_copy"]
    assert safety.is_file()

    markers = admin.read("SELECT x FROM restore_marker")
    assert markers == [("from-snapshot",)]
    kinds = admin.read(
        "SELECT kind, key, sheet_sync FROM admin_change WHERE kind = 'backup'")
    assert kinds
    assert kinds[0][0] == "backup"
    assert kinds[0][1] == snap.name
    assert kinds[0][2] == "skipped"


def test_control_room_page_includes_backups_tab(admin):
    status, body = _send(admin, "GET", "/admin")
    # _send JSON-decodes; HTML stays as bytes when JSON fails — handle both.
    raw = body if isinstance(body, (bytes, bytearray)) else b""
    if not raw:
        conn = http.client.HTTPConnection("127.0.0.1", admin.port, timeout=10)
        try:
            conn.request("GET", "/admin")
            response = conn.getresponse()
            status = response.status
            raw = response.read()
        finally:
            conn.close()
    assert status == 200
    assert b'data-tab="backups"' in raw
    assert b"Google Sheet" in raw
    assert b"NOT" in raw or b"not" in raw


def test_rollback_refuses_commit_not_on_main(tmp_path, monkeypatch):
    from radar.admin import backups as backups_mod

    if subprocess.call(["git", "--version"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("git is not available")

    origin = tmp_path / "origin.git"
    app = tmp_path / "app"
    env = {
        **os.environ,
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
    git(tmp_path, "clone", str(origin), str(app))
    git(app, "checkout", "-b", "side")
    (app / "README").write_text("side\n")
    git(app, "add", "README")
    git(app, "commit", "-m", "side")
    side = git(app, "rev-parse", "HEAD").stdout.strip()
    git(app, "checkout", "main")
    git(app, "fetch", "origin", "main")

    monkeypatch.setenv("RADAR_APP_DIR", str(app))
    with pytest.raises(backups_mod.BackupError, match="not on origin/main"):
        backups_mod.rollback_code(side, side, app=app)


def test_guard_skips_updater_when_hold_matches_head(tmp_path):
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    hold = tmp_path / "hold"
    hold.write_text("abc123deadbeef\n")
    marker = tmp_path / "update-ran"
    git = tools / "git"
    git.write_text("#!/bin/sh\necho abc123deadbeef\n")
    git.chmod(0o755)
    update = tmp_path / "update-from-main.sh"
    update.write_text("#!/bin/sh\ntouch \"$TEST_UPDATE_MARKER\"\n")
    update.chmod(0o755)

    env = {
        **os.environ,
        "PATH": str(tools) + ":" + os.environ.get("PATH", ""),
        "APP_DIR": str(app),
        "RADAR_ROLLBACK_HOLD": str(hold),
        "RADAR_UPDATE_SCRIPT": str(update),
        "RADAR_UPDATE_LOG": str(tmp_path / "update.log"),
        "TEST_UPDATE_MARKER": str(marker),
    }
    result = subprocess.run(["bash", str(GUARD)], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "rollback hold active" in result.stdout + result.stderr
    assert not marker.exists()


def test_guard_runs_updater_without_hold(tmp_path):
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    marker = tmp_path / "update-ran"
    update = tmp_path / "update-from-main.sh"
    update.write_text("#!/bin/sh\ntouch \"$TEST_UPDATE_MARKER\"\n")
    update.chmod(0o755)
    env = {
        **os.environ,
        "APP_DIR": str(app),
        "RADAR_ROLLBACK_HOLD": str(tmp_path / "missing-hold"),
        "RADAR_UPDATE_SCRIPT": str(update),
        "RADAR_UPDATE_LOG": str(tmp_path / "update.log"),
        "TEST_UPDATE_MARKER": str(marker),
    }
    result = subprocess.run(["bash", str(GUARD)], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert marker.exists()
