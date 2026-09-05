"""Record Aryan's lasting verdicts from any surface (web, CLI, Telegram).

SQLite `user_field` is the write-ahead record. The Google Sheet Z column is a
mirror. Today hides lasting verdicts from the *next* morning via
`already_decided`; same-calendar-day decisions use `daily_review` so
Review Again can restore the queue without erasing Kept / not for me.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from radar.config.models import VERDICTS

KEPT_VERDICTS = ("worth contacting", "unsure")


def review_date_today() -> str:
    """Calendar day for the daily_review marker (Europe/London when available)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    except Exception:  # noqa: BLE001 — zoneinfo missing or bad tzdata
        return date.today().isoformat()


def record_verdict(
    db: Any,
    company_id: str,
    verdict: str,
    *,
    review_date: str | None = None,
) -> int:
    """Write lasting verdict + dated Today-review marker.

    `db` is either `radar.store.db.Db` or a raw sqlite3 connection that
    exposes `.execute` / `.commit`. Returns the current Kept count.
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")

    now = datetime.now().isoformat(timespec="seconds")
    day = review_date or review_date_today()
    execute = db.execute
    execute(
        """INSERT INTO user_field(company_id, field, value, updated_at)
           VALUES (?, 'verdict', ?, ?)
           ON CONFLICT(company_id, field)
           DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (company_id, verdict, now),
    )
    execute(
        """INSERT INTO daily_review(company_id, review_date, verdict, reviewed_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_id, review_date)
           DO UPDATE SET verdict = excluded.verdict,
                         reviewed_at = excluded.reviewed_at""",
        (company_id, day, verdict, now),
    )
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()
    return kept_count(db)


def kept_count(db: Any) -> int:
    row = db.execute(
        """SELECT COUNT(*) AS n FROM user_field u
             JOIN company c ON c.id = u.company_id
            WHERE u.field = 'verdict'
              AND c.merged_into IS NULL
              AND lower(trim(u.value)) IN ('worth contacting', 'unsure')"""
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict) or hasattr(row, "keys"):
        return int(row["n"] if "n" in row.keys() else row[0])
    return int(row[0])


def resolve_company_id(db: Any, name: str) -> tuple[str | None, list[str]]:
    """Resolve a display name to a company id.

    Returns `(company_id, [])` on a unique match, `(None, [candidates…])`
    when ambiguous or missing.
    """
    needle = (name or "").strip()
    if not needle:
        return None, []

    exact = db.query(
        "SELECT id, canonical_name FROM company "
        "WHERE merged_into IS NULL AND lower(canonical_name) = lower(?) "
        "ORDER BY canonical_name LIMIT 5",
        (needle,),
    )
    if len(exact) == 1:
        return exact[0]["id"], []
    if len(exact) > 1:
        return None, [r["canonical_name"] for r in exact]

    fuzzy = db.query(
        "SELECT id, canonical_name FROM company "
        "WHERE merged_into IS NULL AND canonical_name LIKE ? "
        "ORDER BY canonical_name LIMIT 8",
        (f"%{needle}%",),
    )
    if len(fuzzy) == 1:
        return fuzzy[0]["id"], []
    return None, [r["canonical_name"] for r in fuzzy]
