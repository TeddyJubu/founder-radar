"""Record Aryan's lasting verdicts from any surface (web, CLI, Telegram).

SQLite `user_field` is the write-ahead record. The Google Sheet Z column is a
mirror. Today hides lasting verdicts from the *next* morning via
`already_decided`; same-calendar-day decisions use `daily_review` so
Review Again can restore the queue without erasing Kept / not for me.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

from radar.config.models import VERDICTS

KEPT_VERDICTS = ("worth contacting", "unsure")


@dataclass(frozen=True)
class VerdictReceipt:
    """What one decision wrote, and the state it replaced.

    `user_field` keeps only the latest verdict and `daily_review` one row per
    day, so once a decision lands the previous state is gone from SQLite. This
    receipt is the minimum needed to put it back (`undo_verdict`). It is
    returned to the caller rather than stored: no schema change, and a receipt
    that is lost (a restart) just means "cannot undo", never a wrong undo.
    """

    company_id: str
    day: str                  # the daily_review.review_date the decision wrote
    verdict: str              # the decision itself
    stamp: str                # the updated_at / reviewed_at it wrote
    kept_count: int           # Kept total after the decision
    prev_verdict: str | None  # user_field value before; None means no row
    prev_verdict_at: str | None
    prev_review: tuple[str, str] | None  # that day's (verdict, reviewed_at) before


class UndoConflict(RuntimeError):
    """The stored state is no longer the one the decision wrote.

    Something else changed it after the decision (a Sheet edit, Telegram, a
    second decision, Review Again). Undoing anyway would overwrite that newer
    choice with an older one, so the undo is refused and nothing changes.
    """


@contextmanager
def _atomic(db: Any) -> Iterator[None]:
    """One write transaction, unless the caller already opened one.

    `Db` runs in autocommit and the web server's raw connection opens
    transactions implicitly, so neither gives "read the old state, write the
    new one" a shared transaction on its own. BEGIN IMMEDIATE takes the write
    lock first, which is what makes the read-then-write below race-free.
    """
    conn = getattr(db, "conn", db)
    own = not getattr(conn, "in_transaction", False)
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise
    if own:
        conn.execute("COMMIT")


def _commit_if_supported(db: Any) -> None:
    # A raw connection handed in mid-transaction is committed here, as it was
    # before _atomic existed; after _atomic committed, this is a no-op.
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


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
    return record_verdict_receipt(
        db, company_id, verdict, review_date=review_date).kept_count


def record_verdict_receipt(
    db: Any,
    company_id: str,
    verdict: str,
    *,
    review_date: str | None = None,
) -> VerdictReceipt:
    """`record_verdict`, returning the receipt `undo_verdict` needs.

    The previous verdict and same-day marker are read and the new ones written
    in one transaction, so the receipt describes exactly what was overwritten.
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")

    now = datetime.now().isoformat(timespec="seconds")
    day = review_date or review_date_today()
    execute = db.execute
    with _atomic(db):
        prev = execute(
            "SELECT value, updated_at FROM user_field "
            "WHERE company_id = ? AND field = 'verdict'", (company_id,),
        ).fetchone()
        prev_review = execute(
            "SELECT verdict, reviewed_at FROM daily_review "
            "WHERE company_id = ? AND review_date = ?", (company_id, day),
        ).fetchone()
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
    _commit_if_supported(db)
    return VerdictReceipt(
        company_id=company_id,
        day=day,
        verdict=verdict,
        stamp=now,
        kept_count=kept_count(db),
        prev_verdict=None if prev is None else prev[0],
        prev_verdict_at=None if prev is None else prev[1],
        prev_review=None if prev_review is None else (prev_review[0], prev_review[1]),
    )


def verdict_undo_applies(db: Any, receipt: VerdictReceipt) -> bool:
    """True while the stored state is still exactly what the decision wrote."""
    row = db.execute(
        "SELECT value, updated_at FROM user_field "
        "WHERE company_id = ? AND field = 'verdict'", (receipt.company_id,),
    ).fetchone()
    if row is None or (row[0], row[1]) != (receipt.verdict, receipt.stamp):
        return False
    marker = db.execute(
        "SELECT verdict, reviewed_at FROM daily_review "
        "WHERE company_id = ? AND review_date = ?", (receipt.company_id, receipt.day),
    ).fetchone()
    return marker is not None and (marker[0], marker[1]) == (receipt.verdict, receipt.stamp)


def undo_verdict(db: Any, receipt: VerdictReceipt) -> int:
    """Put back the verdict and Today-review marker a decision replaced.

    Restores the previous lasting verdict *with its original `updated_at`*
    (Today's `already_decided` test reads that date) or removes it when there
    was none, and does the same for that day's `daily_review` row, so the
    company returns to the queue exactly as it was before the decision.

    Raises `UndoConflict`, changing nothing, when the stored state is no longer
    the one this decision wrote. Returns the Kept count afterwards.
    """
    execute = db.execute
    with _atomic(db):
        if not verdict_undo_applies(db, receipt):
            raise UndoConflict(
                f"{receipt.company_id}: stored verdict changed since the decision")
        if receipt.prev_verdict is None:
            execute("DELETE FROM user_field WHERE company_id = ? AND field = 'verdict'",
                    (receipt.company_id,))
        else:
            execute(
                """INSERT INTO user_field(company_id, field, value, updated_at)
                   VALUES (?, 'verdict', ?, ?)
                   ON CONFLICT(company_id, field)
                   DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (receipt.company_id, receipt.prev_verdict,
                 receipt.prev_verdict_at or receipt.stamp),
            )
        if receipt.prev_review is None:
            execute("DELETE FROM daily_review WHERE company_id = ? AND review_date = ?",
                    (receipt.company_id, receipt.day))
        else:
            execute(
                """INSERT INTO daily_review(company_id, review_date, verdict, reviewed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(company_id, review_date)
                   DO UPDATE SET verdict = excluded.verdict,
                                 reviewed_at = excluded.reviewed_at""",
                (receipt.company_id, receipt.day, *receipt.prev_review),
            )
    _commit_if_supported(db)
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
