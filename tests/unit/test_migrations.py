"""Schema migrations — each numbered SQL file in `radar/store/migrations/`.

`Db.migrate()` applies schema.sql and then every un-recorded migration in
order, each inside its own transaction and marked in `_meta` under
`migration:<name>` only on success (radar/store/db.py). The tests here build
the database the way a migration *finds* it — schema present, migrations not
yet applied — and then run `migrate()` as the CLI's `db migrate` would.
"""

from __future__ import annotations

from radar.resolve.review import _pair_key
from radar.store.db import Db, SCHEMA_PATH

# ULID-shaped ids: 26 chars of 0-9A-Z, as `new_id()` produces. The exact
# characters do not matter to the migration — what matters is that they are
# ASCII, so SQLite's BINARY collation sorts them exactly like Python's
# `sorted()` in `_pair_key`.
A = "01HX0000000000000000000001"
B = "01HX0000000000000000000002"
C = "01HX0000000000000000000003"
D = "01HX0000000000000000000004"

_LEGACY_SEQ = iter(range(100_000))


def _pre_migration_db() -> Db:
    """A database with the schema but no migrations applied — the shape a
    database that predates 003 has when `db migrate` first sees it."""
    db = Db(":memory:")
    db.conn.executescript(SCHEMA_PATH.read_text())
    return db


def _queue_entry(winner: str, loser: str, *, created_at: str = "2026-08-15T00:00:00Z") -> str:
    import json

    return json.dumps({
        "winner_id": winner,
        "loser_id": loser,
        "rule": "fuzzy",
        "score": 87.0,
        "confidence": None,
        "evidence": {},
        "created_at": created_at,
    }, sort_keys=True)


def _legacy_key() -> str:
    """A pre-003 random key: the `review:` prefix plus a bare ULID, no `|`.

    The old code's scan-based dedup kept at most one entry per unordered pair,
    so every legacy key is unique in the same way these are."""
    return f"review:01HXLEGACY{next(_LEGACY_SEQ):07d}"


def test_migration_003_rekeys_legacy_review_entries():
    """Every random-keyed queue entry lands on the exact deterministic key
    `_pair_key` computes — otherwise the O(1) idempotency check would still
    miss it and a re-found pair would be queued twice."""
    db = _pre_migration_db()
    legacy = [
        (_legacy_key(), _queue_entry(A, B)),
        (_legacy_key(), _queue_entry(C, D)),
        (_legacy_key(), _queue_entry(A, C)),     # distinct pairs only — the old
    ]                                            # scan-dedup made duplicates unrepresentable
    untouched = ("merge_undone:7", "2026-08-16T00:00:00Z")
    for key, value in legacy + [untouched]:
        db.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?,?)", (key, value))

    db.migrate()

    keys = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM _meta")}
    assert _pair_key(A, B) in keys, "A/B pair was not rekeyed"
    assert _pair_key(C, D) in keys, "C/D pair was not rekeyed"
    assert keys[untouched[0]] == untouched[1], "a non-review _meta key was touched"
    # Every review key must now be a deterministic pair key — no legacy shape left.
    for key in keys:
        if key.startswith("review:"):
            import json

            payload = json.loads(keys[key])
            assert key == _pair_key(payload["winner_id"], payload["loser_id"]), key
    assert db.get_meta("migration:003_rekey_review_queue.sql") == "003_rekey_review_queue.sql"


def test_migration_004_creates_today_check(db):
    """The Hermes Today QA table is present on a fresh migrate and recorded."""
    assert "today_check" in db.tables()
    cols = {r["name"] for r in db.execute("PRAGMA table_info(today_check)")}
    assert {"company_id", "snapshot_hash", "verdict", "reason", "checker"} <= cols
    assert db.get_meta("migration:004_today_check.sql") == "004_today_check.sql"


def test_migration_003_collapses_a_pair_queued_twice():
    """A pair re-queued after the fix has two entries — one under the legacy
    random key, one under the deterministic key. The legacy one is redundant:
    the deterministic entry carries the newer decision, so exactly it must
    survive."""
    db = _pre_migration_db()
    db.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?,?)",
        (_legacy_key(), _queue_entry(A, B, created_at="2026-08-15T00:00:00Z")),
    )
    db.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?,?)",
        (_pair_key(A, B), _queue_entry(A, B, created_at="2026-08-16T00:00:00Z")),
    )

    db.migrate()

    rows = db.query("SELECT key, value FROM _meta WHERE key LIKE 'review:%'")
    assert len(rows) == 1, f"expected one survivor, got {len(rows)}"
    assert rows[0]["key"] == _pair_key(A, B)
    import json

    assert json.loads(rows[0]["value"])["created_at"] == "2026-08-16T00:00:00Z"


def test_migration_003_is_idempotent():
    """Re-running `db migrate` on an already-rekeyed database changes nothing:
    the migration is recorded, and no legacy-shaped key remains to match."""
    db = _pre_migration_db()
    db.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?,?)",
        (_legacy_key(), _queue_entry(A, B)),
    )

    db.migrate()
    after_first = dict(db.query("SELECT key, value FROM _meta"))
    db.migrate()
    after_second = dict(db.query("SELECT key, value FROM _meta"))

    assert after_first == after_second
    assert _pair_key(A, B) in after_first


def test_migration_003_leaves_malformed_entries_alone():
    """A review-prefixed entry whose payload is not a JSON object with both
    ids cannot be rekeyed safely — it must be left untouched, not corrupted
    into a key that collides with a real pair."""
    db = _pre_migration_db()
    malformed = "review:01HXNOTJSON"
    db.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?,?)",
               (malformed, "not json at all"))

    db.migrate()

    assert db.get_meta(malformed) == "not json at all"


def test_migration_004_creates_today_check(db):
    """The Hermes Today QA table is present on a fresh migrate and recorded."""
    assert "today_check" in db.tables()
    cols = {r["name"] for r in db.execute("PRAGMA table_info(today_check)")}
    assert {"company_id", "snapshot_hash", "verdict", "reason", "checker"} <= cols
    assert db.get_meta("migration:004_today_check.sql") == "004_today_check.sql"
