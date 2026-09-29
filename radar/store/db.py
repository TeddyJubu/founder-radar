"""Thin repository layer over SQLite. Hand-written SQL, no ORM.

Everything that touches the database goes through `Db`. The provenance model is
graph-shaped and reads better as SQL than as an object graph (02-architecture §9).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
SCHEMA_VERSION = "1"

# Seeded by migrate(). A placeholder name is never a merge key and never a duplicate.
PLACEHOLDER_NAMES = (
    "na",
    "unknown",
    "stealth",
    "confidential",
    "tbc",
    "tbd",
    "newco",
    "none",
    "test",
)


def now_iso() -> str:
    """UTC timestamp, second resolution, ISO-8601. One format everywhere."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    """ULID for `company.id` — sortable by creation time and stable forever."""
    from ulid import ULID

    return str(ULID())


def default_db_path() -> Path:
    return Path(os.environ.get("RADAR_DB", "data/radar.db")).expanduser()


class Db:
    """A connection plus the few helpers every module needs.

    Deliberately not a repository-per-table: each phase owns its own SQL and
    calls `query`/`execute` directly. That keeps the shared surface small.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")

    # ---------------------------------------------------------------- basics

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, list(rows))

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None else None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. Every write path that spans tables uses this."""
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ migration

    def migrate(self) -> None:
        """Apply schema.sql then any numbered migrations, recording each."""
        self.conn.executescript(SCHEMA_PATH.read_text())
        self.set_meta("schema_version", SCHEMA_VERSION)

        if MIGRATIONS_DIR.is_dir():
            applied = {r["value"] for r in self.query(
                "SELECT value FROM _meta WHERE key LIKE 'migration:%'")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in applied:
                    continue
                self._apply_migration(path)

        self.executemany(
            "INSERT OR IGNORE INTO placeholder_name(norm_key) VALUES (?)",
            [(n,) for n in PLACEHOLDER_NAMES],
        )

    def _apply_migration(self, path: Path) -> None:
        """Run one migration and record it in a SINGLE transaction.

        The SQL and the `_meta` marker used to be two commits, so a stop between
        them left the change applied but unmarked, and the next start repeated
        `ALTER TABLE ... ADD COLUMN` and failed on the duplicate column. They now
        commit together or not at all.

        The file is run statement by statement rather than through
        `executescript`, which commits implicitly and so cannot share a
        transaction with the marker. Because of that a migration must not
        contain its own BEGIN/COMMIT, nor anything SQLite refuses to run inside
        a transaction (VACUUM, PRAGMA foreign_keys).

        Recovery: a database an older build left applied-but-unmarked fails its
        `ADD COLUMN` with "duplicate column name". That one statement is taken
        as already applied and the rest of the file still runs, so the marker
        finally gets written. Any other error rolls the whole migration back
        and propagates.
        """
        name = path.name
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            # Another `migrate()` may have finished while we waited for the lock.
            if self.get_meta(f"migration:{name}") is None:
                for statement in _sql_statements(path.read_text()):
                    try:
                        self.conn.execute(statement)
                    except sqlite3.OperationalError as exc:
                        if not _is_already_applied(statement, exc):
                            raise
                        log.warning("migration %s: %s — already applied, continuing",
                                    name, exc)
                self._record_migration(name)
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def _record_migration(self, name: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
            (f"migration:{name}", name),
        )

    def tables(self) -> set[str]:
        return {r["name"] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    # ----------------------------------------------------------------- meta

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM _meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)", (key, value))

    # ------------------------------------------------------------ provenance

    def add_observation(
        self,
        company_id: str,
        field: str,
        value: Any,
        *,
        source_key: str,
        source_type: str,
        source_url: str | None = None,
        confidence: float = 1.0,
        extractor_ver: str = "1",
        observed_at: str | None = None,
    ) -> None:
        """Append a fact. Facts are never overwritten (03-data-model §1)."""
        self.execute(
            """INSERT INTO observation
               (company_id, field, value_json, source_key, source_type, source_url,
                confidence, observed_at, extractor_ver)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (company_id, field, json.dumps(value), source_key, source_type,
             source_url, confidence, observed_at or now_iso(), extractor_ver),
        )

    def observations(self, company_id: str, field: str | None = None) -> list[dict]:
        sql = "SELECT * FROM observation WHERE company_id = ?"
        params: list[Any] = [company_id]
        if field is not None:
            sql += " AND field = ?"
            params.append(field)
        return [
            {**dict(r), "value": json.loads(r["value_json"])}
            for r in self.query(sql + " ORDER BY observed_at DESC", params)
        ]

    def resolve_company_id(self, company_id: str) -> str:
        """Follow the `merged_into` tombstone chain, with cycle detection."""
        seen: set[str] = set()
        current = company_id
        while current not in seen:
            seen.add(current)
            row = self.one("SELECT merged_into FROM company WHERE id = ?", (current,))
            if row is None or row["merged_into"] is None:
                return current
            current = row["merged_into"]
        return current  # cycle: stop rather than loop forever


# Trust ranking for `resolve()`. Keys MUST cover every SourceAdapter.kind plus
# 'derived' and 'llm' — an unguarded lookup here is a KeyError on every
# Companies House observation (03-data-model §2).
SOURCE_TRUST: dict[str, int] = {
    "registry": 100,
    "grant": 80,
    "company_site": 70,
    "spinout": 65,
    "accelerator": 60,
    "news": 40,
    "portfolio": 35,
    "derived": 30,
    "llm": 20,
}


def resolve(field: str, observations: Sequence[Mapping[str, Any]]) -> tuple[Any, list]:
    """Pure resolution: highest trust wins, full audit trail returned alongside.

    Recomputed on demand rather than stored, so "why does it say this?" is
    always answerable and a bad merge stays reversible.
    """
    obs = [o for o in observations if o["field"] == field and o.get("value") is not None]
    if not obs:
        return None, []
    obs.sort(
        key=lambda o: (
            SOURCE_TRUST.get(o["source_type"], 10),
            o["confidence"],
            o["observed_at"],
        ),
        reverse=True,
    )
    return obs[0]["value"], obs


def open_db(path: str | Path | None = None, *, migrate: bool = False) -> Db:
    db = Db(path if path is not None else default_db_path())
    if migrate:
        db.migrate()
    return db


# ---------------------------------------------------------- migration helpers

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_ADD_COLUMN = re.compile(r"^\s*ALTER\s+TABLE\s+\S+\s+ADD\b", re.IGNORECASE)


def _strip_sql_comments(sql: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def _sql_statements(script: str) -> list[str]:
    """Split a migration file into statements exactly where SQLite would.

    `sqlite3.complete_statement` does the judging, so a `;` inside a comment, a
    string or a trigger body never ends a statement early.
    """
    statements: list[str] = []
    start = 0
    for index, char in enumerate(script):
        if char == ";" and sqlite3.complete_statement(script[start:index + 1]):
            statements.append(script[start:index + 1].strip())
            start = index + 1
    tail = script[start:]
    if _strip_sql_comments(tail).strip():
        statements.append(tail.strip())       # a final statement with no `;`
    return statements


def _is_already_applied(statement: str, exc: sqlite3.Error) -> bool:
    """True for `ALTER TABLE ... ADD COLUMN` failing because the column exists.

    Deliberately narrow: a duplicate column anywhere else (CREATE TABLE, RENAME
    COLUMN) is a genuine error in the migration, not evidence it already ran.
    """
    return ("duplicate column name" in str(exc).lower()
            and _ADD_COLUMN.match(_strip_sql_comments(statement)) is not None)


# ------------------------------------------------------------------- restore


class RestoreError(RuntimeError):
    """A restore was refused. The live database has not been touched."""


def restore_database(src: str | Path, dest: str | Path, *,
                     lock_timeout: float = 2.0) -> None:
    """Verify a backup, then restore through SQLite's transactional backup API.

    Existing connections keep using the same database and see the restored
    pages on their next transaction. Never rename a valid live database or
    remove its WAL while another connection can still use it. Busy writers
    cause a bounded refusal. A corrupt or absent destination is installed
    atomically from the verified temporary copy.
    """
    src_path, dest_path = Path(src), Path(dest)
    if not src_path.is_file():
        raise RestoreError(f"backup not found: {src_path}")
    if dest_path.exists() and src_path.resolve() == dest_path.resolve():
        raise RestoreError("the backup and the live database are the same file")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = dest_path.with_name(f"{dest_path.name}.restore-{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    probe: sqlite3.Connection | None = None
    try:
        _copy_and_verify(src_path, tmp)
        probe = _lock_live_database(dest_path, lock_timeout)
        if probe is None:
            _match_live_ownership(tmp, dest_path)
            os.replace(tmp, dest_path)
            for suffix in ("-wal", "-shm"):
                Path(f"{dest_path}{suffix}").unlink(missing_ok=True)
            _fsync_directory(dest_path.parent)
        else:
            # The backup API owns its destination transaction. Release the
            # preliminary writer check first; a competing writer is handled
            # by its bounded busy callback, never by replacing the inode.
            probe.execute("ROLLBACK")
            source = sqlite3.connect(tmp)
            deadline = time.monotonic() + lock_timeout
            def progress(status, remaining, total):
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    if time.monotonic() >= deadline:
                        raise RestoreError("the live database has an active writer — "
                                           "stop the daily run and web service, then retry")
            try:
                source.backup(probe, pages=256, progress=progress, sleep=0.01)
            finally:
                source.close()
    finally:
        tmp.unlink(missing_ok=True)
        if probe is not None:
            probe.close()


def _copy_and_verify(src: Path, tmp: Path) -> None:
    try:
        source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
            verdict = [row[0] for row in target.execute("PRAGMA integrity_check")]
            if verdict != ["ok"]:
                raise RestoreError(
                    f"backup {src} failed PRAGMA integrity_check: "
                    + "; ".join(str(v) for v in verdict[:3]))
            tables = {row[0] for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"_meta", "company"} <= tables:
                raise RestoreError(
                    f"{src} is a valid SQLite file but not a Founder Radar database")
        finally:
            target.close()
            source.close()
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"cannot read backup {src}: {exc}") from exc


def _lock_live_database(dest: Path, timeout: float) -> sqlite3.Connection | None:
    """Hold the live database's write lock, or refuse. None if there is none."""
    if not dest.exists():
        return None
    conn = sqlite3.connect(dest, timeout=timeout, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        conn.close()
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            raise RestoreError(
                f"the live database {dest} has an active writer — stop the daily "
                "run and the web service, then retry") from exc
        raise RestoreError(f"cannot open the live database {dest}: {exc}") from exc
    except sqlite3.DatabaseError:
        conn.close()          # not a database / malformed: nothing to protect
        return None
    return conn


def _match_live_ownership(tmp: Path, dest: Path) -> None:
    """Keep the live file's mode and owner, so a restore run as root does not
    leave the service account unable to write its own database."""
    try:
        live = dest.stat()
    except OSError:
        return
    try:
        os.chmod(tmp, stat.S_IMODE(live.st_mode))
        os.chown(tmp, live.st_uid, live.st_gid)
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
