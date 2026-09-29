"""M-05 — a migration and its `_meta` completion marker commit together.

They used to be two commits. A stop between them left the SQL applied and
unmarked, so the next start repeated `ALTER TABLE ... ADD COLUMN` and failed on
the duplicate column, blocking the next installation.
"""

from __future__ import annotations

from radar.store.db import Db, SCHEMA_PATH


def _pre_migration_db() -> Db:
    """Schema present, no migration applied — how an old database is found."""
    db = Db(":memory:")
    db.conn.executescript(SCHEMA_PATH.read_text())
    return db
def _table_columns(db: Db, table: str) -> set[str]:
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}


def _deny_second_meta_insert(hard_exit: bool = False):
    """An authorizer that fails the SECOND INSERT into `_meta`.

    The first is `schema_version` in `migrate()`; the second is the first
    migration's completion marker. That is exactly the window the bug lived in,
    and it does not depend on how the marker statement is spelled.
    """
    import os
    import sqlite3

    seen = {"n": 0}

    def authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001
        if action == sqlite3.SQLITE_INSERT and arg1 == "_meta":
            seen["n"] += 1
            if seen["n"] == 2:
                if hard_exit:
                    os._exit(17)
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def test_migration_and_its_marker_commit_together(tmp_path):
    import sqlite3

    import pytest

    db = Db(tmp_path / "radar.db")
    db.conn.set_authorizer(_deny_second_meta_insert())
    with pytest.raises(sqlite3.DatabaseError):
        db.migrate()                       # the marker write "crashes"

    assert not db.conn.in_transaction, "a failed migration left a transaction open"
    assert "warnings" not in _table_columns(db, "run"), \
        "001 was committed without its marker"
    assert db.get_meta("migration:001_run_warnings.sql") is None

    db.conn.set_authorizer(None)
    db.migrate()                           # and the retry is clean
    assert "warnings" in _table_columns(db, "run")
    assert db.get_meta("migration:001_run_warnings.sql") == "001_run_warnings.sql"


def test_a_process_killed_before_the_marker_leaves_a_clean_database(tmp_path):
    """The crash itself: the process dies (no exception handling, no cleanup)
    after the ALTER ran and before the marker was written."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    path = tmp_path / "radar.db"
    script = textwrap.dedent(f"""
        import os, sqlite3
        from radar.store.db import Db

        seen = {{"n": 0}}
        def authorizer(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_INSERT and arg1 == "_meta":
                seen["n"] += 1
                if seen["n"] == 2:
                    os._exit(17)
            return sqlite3.SQLITE_OK

        db = Db({str(path)!r})
        db.conn.set_authorizer(authorizer)
        db.migrate()
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=repo, capture_output=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo)},
    )
    assert proc.returncode == 17, proc.stderr.decode()

    db = Db(path)
    assert "warnings" not in _table_columns(db, "run")
    db.migrate()
    assert "warnings" in _table_columns(db, "run")
    assert db.get_meta("migration:001_run_warnings.sql") == "001_run_warnings.sql"


def test_migration_applied_but_not_marked_is_recovered():
    """A database an older build left mid-way: 001's column exists, its marker
    does not. `migrate()` must adopt it, not fail on the duplicate column."""
    db = _pre_migration_db()
    db.execute("ALTER TABLE run ADD COLUMN warnings TEXT")   # applied, unmarked

    db.migrate()

    assert db.get_meta("migration:001_run_warnings.sql") == "001_run_warnings.sql"
    assert db.get_meta("migration:004_today_check.sql") == "004_today_check.sql"
    db.migrate()                                             # and stays quiet


def test_recovery_still_runs_the_rest_of_a_partly_applied_migration(tmp_path, monkeypatch):
    import radar.store.db as store

    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_two_steps.sql").write_text(
        "ALTER TABLE run ADD COLUMN extra_a TEXT;\n"
        "-- a comment; with a semicolon\n"
        "CREATE TABLE IF NOT EXISTS extra_b (id INTEGER);\n"
        "ALTER TABLE run ADD COLUMN extra_c TEXT;\n"
    )
    monkeypatch.setattr(store, "MIGRATIONS_DIR", folder)

    db = _pre_migration_db()
    db.execute("ALTER TABLE run ADD COLUMN extra_a TEXT")    # only step 1 survived

    db.migrate()

    assert {"extra_a", "extra_c"} <= _table_columns(db, "run")
    assert "extra_b" in db.tables()
    assert db.get_meta("migration:001_two_steps.sql") == "001_two_steps.sql"


def test_a_real_migration_error_is_not_mistaken_for_recovery(tmp_path, monkeypatch):
    """Only a duplicate column on an ADD COLUMN is 'already applied'. Any other
    failure — including a duplicate column inside a CREATE TABLE — must raise,
    and must leave nothing of the migration behind."""
    import sqlite3

    import pytest

    import radar.store.db as store

    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_broken.sql").write_text(
        "CREATE TABLE half_done (id INTEGER);\n"
        "CREATE TABLE bad_columns (a TEXT, a TEXT);\n"
    )
    monkeypatch.setattr(store, "MIGRATIONS_DIR", folder)

    db = _pre_migration_db()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate()

    assert not db.conn.in_transaction
    assert "half_done" not in db.tables(), "a failed migration must roll back whole"
    assert db.get_meta("migration:001_broken.sql") is None


def test_every_shipped_migration_splits_into_its_statements():
    """The runner executes statement by statement; make sure the splitter
    agrees with SQLite about where each shipped file's statements end."""
    from radar.store.db import MIGRATIONS_DIR, _sql_statements

    counts = {
        p.name: len(_sql_statements(p.read_text()))
        for p in sorted(MIGRATIONS_DIR.glob("*.sql"))
    }
    assert counts["001_run_warnings.sql"] == 1
    assert counts["002_daily_review.sql"] == 2
    assert counts["003_rekey_review_queue.sql"] == 2
    assert all(n >= 1 for n in counts.values())
