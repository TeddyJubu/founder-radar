"""UK GDPR erasure, and the suppression that makes it stick.

Deleting a founder row is the easy half. The hard half is that tomorrow's run
re-reads the same article and puts the person straight back. So erasure writes
a `suppression` row, and every ingest path checks it — which is why erasure
lives next to the ingest helpers rather than in a one-off script.
"""

from __future__ import annotations

from typing import Any

from radar.store.db import Db, now_iso


def norm_person(name: str) -> str:
    """Fold a person's name to a stable comparison key.

    Deliberately blunt: casefold, collapse whitespace, strip punctuation. A
    suppression that is too narrow fails open, and failing open on an erasure
    request is the one failure mode with legal consequences.
    """
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name)
    return " ".join(cleaned.casefold().split())


def is_suppressed(db: Db, name: str) -> bool:
    """Check before every founder insert. Cheap: a single primary-key lookup."""
    return db.one(
        "SELECT 1 FROM suppression WHERE norm_name = ?", (norm_person(name),)
    ) is not None


def suppress(db: Db, name: str, reason: str = "gdpr_erasure") -> None:
    db.execute(
        "INSERT OR REPLACE INTO suppression(norm_name, reason, created_at) VALUES (?,?,?)",
        (norm_person(name), reason, now_iso()),
    )


def forget_person(db: Db, name: str) -> dict[str, Any]:
    """Erase a person and stop them coming back.

    Returns a receipt rather than printing, so the CLI can render it and a
    caller can assert on it.
    """
    key = norm_person(name)
    with db.tx():
        rows = db.query(
            "SELECT id, company_id, name FROM founder WHERE norm_name = ?", (key,)
        )
        db.execute("DELETE FROM founder WHERE norm_name = ?", (key,))
        # Observations can carry the name in their JSON payload; drop the
        # founder-shaped ones rather than trying to rewrite JSON in place.
        db.execute(
            """DELETE FROM observation
               WHERE field IN ('founders', 'founder', 'officers')
                 AND company_id IN (SELECT company_id FROM founder WHERE norm_name = ?)""",
            (key,),
        )
        suppress(db, name)

    return {
        "name": name,
        "norm_name": key,
        "founders_deleted": len(rows),
        "companies_affected": sorted({r["company_id"] for r in rows}),
        "suppressed": True,
    }


def insert_founder(
    db: Db,
    company_id: str,
    *,
    name: str,
    source_url: str,
    role: str | None = None,
    profile_url: str | None = None,
    is_psc: bool = False,
    appointed_on: str | None = None,
    prior_appointments: int | None = None,
) -> bool:
    """The only sanctioned way to add a founder. Returns False if suppressed.

    Personal data that Companies House hands over — date of birth, correspondence
    address — has no parameter here at all. The schema has no column for it and
    this function has no argument for it, so dropping it is not something an
    adapter author has to remember.
    """
    if is_suppressed(db, name):
        return False

    # ponytail: founder.norm_name uses the same fold as suppression, not the
    # company matcher. It only has to make (company_id, norm_name) unique and
    # make erasure findable — people are never merged on it.
    db.execute(
        """INSERT OR IGNORE INTO founder
           (company_id, name, norm_name, role, profile_url, is_psc,
            appointed_on, prior_appointments, source_url, first_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (company_id, name, norm_person(name), role, profile_url, int(is_psc),
         appointed_on, prior_appointments, source_url, now_iso()),
    )
    return True


def purge_stale_founders(db: Db, months: int = 12) -> int:
    """Retention rule: drop founders of companies rejected over a year ago."""
    cur = db.execute(
        f"""DELETE FROM founder WHERE company_id IN (
              SELECT s.company_id FROM score s
              WHERE s.tier = 'reject'
              GROUP BY s.company_id
              HAVING MAX(s.scored_at) < date('now', '-{int(months)} month')
            )"""
    )
    return cur.rowcount or 0
