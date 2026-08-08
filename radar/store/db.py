"""Thin repository layer over SQLite. Hand-written SQL, no ORM.

Everything that touches the database goes through `Db`. The provenance model is
graph-shaped and reads better as SQL than as an object graph (02-architecture §9).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

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
                with self.tx():
                    self.conn.executescript(path.read_text())
                    self.execute(
                        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
                        (f"migration:{path.name}", path.name),
                    )

        self.executemany(
            "INSERT OR IGNORE INTO placeholder_name(norm_key) VALUES (?)",
            [(n,) for n in PLACEHOLDER_NAMES],
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
