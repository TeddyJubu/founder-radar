"""M-04 — `db restore` must not copy a file over a live WAL-mode database.

The old command was `shutil.copy2(backup, live)`. Normal connections run in WAL
mode, so decisions made since the last checkpoint sit in `radar.db-wal`; a raw
copy replaces the main file underneath them, and the next connection replays the
old log on top of the backup's pages. These tests hold a live connection open
while restoring, which is the situation the copy got wrong.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from radar.store.db import Db, RestoreError, restore_database


def _cli(args):
    from radar.cli import cli

    return CliRunner().invoke(cli, args, obj={})


def _live_db(path: Path, names: list[str]) -> Db:
    db = Db(path)
    db.migrate()
    for name in names:
        db.execute(
            "INSERT INTO _meta(key, value) VALUES (?, ?)", (f"note:{name}", name))
    return db


def _notes(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT value FROM _meta WHERE key LIKE 'note:%'"))
    finally:
        conn.close()


def _snapshot(source: Db, dest: Path) -> Path:
    out = sqlite3.connect(dest)
    try:
        source.conn.backup(out)
    finally:
        out.close()
    return dest


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if ".restore-" in p.name)


def test_restore_replaces_a_live_database_that_has_unflushed_wal_pages(tmp_path):
    live_path = tmp_path / "radar.db"
    live = _live_db(live_path, ["from-the-backup"])
    backup = _snapshot(live, tmp_path / "backup.db")

    # Decisions made after the backup: they live in radar.db-wal, and this
    # connection stays open (as the web service's does).
    live.execute("INSERT INTO _meta(key, value) VALUES ('note:newer', 'newer')")
    assert Path(f"{live_path}-wal").stat().st_size > 0

    restore_database(backup, live_path)
    assert live.scalar("SELECT value FROM _meta WHERE key='note:newer'") is None
    live.execute("INSERT INTO _meta(key, value) VALUES ('note:after', 'after')")
    assert _notes(live_path) == ["after", "from-the-backup"]
    live.execute("DELETE FROM _meta WHERE key='note:after'")
    live.close()

    assert not Path(f"{live_path}-wal").exists(), "stale -wal left beside the new file"
    assert not Path(f"{live_path}-shm").exists()
    reopened = Db(live_path)
    assert reopened.scalar("PRAGMA integrity_check") == "ok"
    assert reopened.query("SELECT value FROM _meta WHERE key LIKE 'note:%'")[0][0] \
        == "from-the-backup"
    assert _notes(live_path) == ["from-the-backup"], \
        "the newer, unbacked-up decision must not leak into the restored file"
    assert reopened.scalar("PRAGMA journal_mode") == "wal"
    assert _leftovers(tmp_path) == []


def test_restore_refuses_while_the_live_database_has_an_active_writer(tmp_path):
    live_path = tmp_path / "radar.db"
    live = _live_db(live_path, ["original"])
    backup = _snapshot(_live_db(tmp_path / "other.db", ["stale"]), tmp_path / "backup.db")
    before = live_path.read_bytes()

    live.execute("BEGIN IMMEDIATE")                   # a run is mid-write
    live.execute("INSERT INTO _meta(key, value) VALUES ('note:inflight', 'inflight')")
    with pytest.raises(RestoreError, match="active writer"):
        restore_database(backup, live_path, lock_timeout=0.1)
    live.execute("COMMIT")

    assert live_path.read_bytes() == before or _notes(live_path) == ["inflight", "original"]
    assert "stale" not in _notes(live_path)
    assert _leftovers(tmp_path) == []
    live.close()


def test_restore_refuses_a_backup_that_fails_the_integrity_check(tmp_path):
    live_path = tmp_path / "radar.db"
    live = _live_db(live_path, ["original"])
    live.close()
    source = _live_db(tmp_path / "source.db", [f"row{i}" for i in range(400)])
    source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    source.close()
    backup = tmp_path / "backup.db"
    data = bytearray((tmp_path / "source.db").read_bytes())
    for offset in range(4096 * 3, 4096 * 6, 7):      # scribble over interior pages
        data[offset] = (data[offset] + 91) % 256
    backup.write_bytes(bytes(data))

    with pytest.raises(RestoreError):
        restore_database(backup, live_path)

    assert _notes(live_path) == ["original"]
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize("content", [b"", b"this is not a database at all" * 50])
def test_restore_refuses_a_file_that_is_not_a_database(tmp_path, content):
    live_path = tmp_path / "radar.db"
    _live_db(live_path, ["original"]).close()
    junk = tmp_path / "junk.db"
    junk.write_bytes(content)

    with pytest.raises(RestoreError):
        restore_database(junk, live_path)

    assert _notes(live_path) == ["original"]
    assert _leftovers(tmp_path) == []


def test_restore_refuses_a_valid_sqlite_file_that_is_not_ours(tmp_path):
    live_path = tmp_path / "radar.db"
    _live_db(live_path, ["original"]).close()
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()

    with pytest.raises(RestoreError, match="not a Founder Radar database"):
        restore_database(other, live_path)
    assert _notes(live_path) == ["original"]


def test_restore_refuses_a_missing_backup_and_never_creates_it(tmp_path):
    live_path = tmp_path / "radar.db"
    _live_db(live_path, ["original"]).close()
    missing = tmp_path / "nope.db"

    with pytest.raises(RestoreError, match="not found"):
        restore_database(missing, live_path)
    assert not missing.exists()
    assert _notes(live_path) == ["original"]


def test_restore_can_replace_a_corrupt_live_database(tmp_path):
    """A live file that is itself garbage is the reason to restore. It cannot
    be locked, and must not block the restore."""
    backup = _snapshot(_live_db(tmp_path / "good.db", ["good"]), tmp_path / "backup.db")
    live_path = tmp_path / "radar.db"
    live_path.write_bytes(b"corrupted beyond repair" * 100)
    Path(f"{live_path}-wal").write_bytes(b"stale wal bytes")

    restore_database(backup, live_path)

    assert _notes(live_path) == ["good"]
    assert not Path(f"{live_path}-wal").exists()


def test_restore_creates_the_database_when_none_exists_yet(tmp_path):
    backup = _snapshot(_live_db(tmp_path / "good.db", ["good"]), tmp_path / "backup.db")
    target = tmp_path / "fresh" / "radar.db"

    restore_database(backup, target)

    assert _notes(target) == ["good"]


def test_restore_refuses_to_restore_a_file_onto_itself(tmp_path):
    live_path = tmp_path / "radar.db"
    _live_db(live_path, ["original"]).close()
    with pytest.raises(RestoreError, match="same file"):
        restore_database(live_path, live_path)


def test_cli_restore_reports_success_and_refusal(tmp_path):
    live_path = tmp_path / "radar.db"
    live = _live_db(live_path, ["from-the-backup"])
    backup = _snapshot(live, tmp_path / "backup.db")
    live.execute("INSERT INTO _meta(key, value) VALUES ('note:newer', 'newer')")
    live.close()

    result = _cli(["--db", str(live_path), "db", "restore", str(backup)])
    assert result.exit_code == 0, result.output
    assert "restored" in result.output
    assert _notes(live_path) == ["from-the-backup"]

    bad = _cli(["--db", str(live_path), "db", "restore", str(tmp_path / "nope.db")])
    assert bad.exit_code != 0
    assert "not found" in bad.output
    assert _notes(live_path) == ["from-the-backup"]
