"""Merging: one row per real company, and every merge reversible.

The rules (05-pipeline §4.3):

1. `winner.first_seen = min(a, b)` — preserved, never lost
2. `winner.last_seen  = max(a, b)`
3. re-point every `observation`, `identifier`, `signal`, `founder`,
   `company_source` and `user_field` row to the winner
4. `loser.merged_into = winner.id` — a tombstone, never a delete
5. insert a `merge_event` with the rule, the score and the exact evidence
6. the loser's name survives as an `identifier(kind='alias')`

Reversibility is the reason step 5 records so much: the evidence carries the
primary key of every row that moved, every row that had to be dropped because
it collided with one the winner already had, and the winner's own column values
before the merge. `unmerge()` replays that backwards and the database ends up
byte-identical.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from radar.store.db import new_id, now_iso

from .match import MERGE, REVIEW, MatchResult, Record, find_match, record_from_row
from .normalise import is_placeholder_name, norm_key

# (table, primary-key columns, columns that must stay unique *within* a company)
MOVABLE: tuple[tuple[str, tuple[str, ...], tuple[str, ...] | None], ...] = (
    ("observation", ("id",), None),
    ("signal", ("id",), ("kind", "source_url")),
    ("founder", ("id",), ("norm_name",)),
    ("identifier", ("kind", "value", "company_id"), ("kind", "value")),
    ("company_source", ("company_id", "source_key", "external_id"),
     ("source_key", "external_id")),
    ("user_field", ("company_id", "field"), ("field",)),
)

# Filled on the winner only where the winner is NULL: a merge adds knowledge,
# it never overwrites it. `total_funding_gbp` is in here precisely because NULL
# and 0 are different facts — a NULL winner may learn 0 from the loser.
FILL_FORWARD = (
    "companies_house_no", "domain", "website_url", "incorporated_on", "age_source",
    "hq_postcode", "hq_region", "hq_city", "country_iso2", "sector", "stage",
    "founder_signal", "traction_signal", "one_liner", "sic_codes", "discovery_route",
    "officer_count", "is_university_spinout", "spinout_university", "last_round_gbp",
    "prior_total_gbp", "valuation_gbp", "uk_exec_pct", "seis_eis_qualifying",
    "extraction_method", "date_confidence", "total_funding_gbp", "enriched_at",
)

MAXED = ("has_share_issue", "on_vc_portfolio", "qualified", "age_unknown",
         "uk_unverified", "needs_review")
SUMMED = ("news_mention_count",)

# Columns restored verbatim by `unmerge`.
_SNAPSHOT_COLS = FILL_FORWARD + MAXED + SUMMED + (
    "canonical_name", "norm_key", "first_seen", "last_seen", "updated_at",
    "qualifiers", "synced",
)


@dataclass
class Resolution:
    """What `upsert_record` did with one incoming mention."""

    company_id: str
    action: str                       # created | matched | review
    match: MatchResult | None = None
    matched_id: str | None = None
    review_key: str | None = None


# ------------------------------------------------------------------- writing


def add_identifier(db, company_id: str, kind: str, value: str | None,
                   *, source_key: str, first_seen: str | None = None) -> None:
    """Record a key we have seen for this company. Idempotent."""
    if not value:
        return
    db.execute(
        "INSERT OR IGNORE INTO identifier(company_id, kind, value, source_key, first_seen)"
        " VALUES (?,?,?,?,?)",
        (company_id, kind, value, source_key, first_seen or now_iso()),
    )


def create_company(db, record: Record, *, source_key: str, source_type: str = "news",
                   source_url: str | None = None, external_id: str | None = None,
                   seen_at: str | None = None, fields: Mapping[str, Any] | None = None,
                   company_id: str | None = None) -> str:
    """Insert a new canonical company plus its identifiers and source link."""
    seen = seen_at or now_iso()
    cid = company_id or new_id()
    cols: dict[str, Any] = {
        "id": cid,
        "canonical_name": (record.name or "").strip() or "(unnamed)",
        "norm_key": record.norm_key,
        "companies_house_no": record.ch,
        "domain": record.dom,
        "country_iso2": record.country,
        "first_seen": record.first_seen or seen,
        "last_seen": record.first_seen or seen,
        "created_at": seen,
        "updated_at": seen,
    }
    for k, v in (fields or {}).items():
        cols[k] = v

    placeholders = ",".join("?" for _ in cols)
    db.execute(
        f"INSERT INTO company({','.join(cols)}) VALUES ({placeholders})",
        list(cols.values()),
    )

    # 03-data-model §3: placeholder_name is seeded with the static list "plus
    # anything matching ^(bluesky|company)\\d+$". Registering the key here is
    # what keeps duplicate-audit query 9 at zero rows: two unrelated companies
    # both called "Stealth" are not a de-duplication bug.
    if is_placeholder_name(record.name):
        db.execute("INSERT OR IGNORE INTO placeholder_name(norm_key) VALUES (?)",
                   (record.norm_key,))

    add_identifier(db, cid, "norm_key", record.norm_key, source_key=source_key,
                   first_seen=cols["first_seen"])
    add_identifier(db, cid, "ch", record.ch, source_key=source_key,
                   first_seen=cols["first_seen"])
    add_identifier(db, cid, "domain", record.dom, source_key=source_key,
                   first_seen=cols["first_seen"])
    link_source(db, cid, source_key=source_key, external_id=external_id,
                source_url=source_url, seen_at=cols["first_seen"])
    return cid


def link_source(db, company_id: str, *, source_key: str, external_id: str | None,
                source_url: str | None, seen_at: str | None = None) -> None:
    """"Found on three sources" is one row with three links (03-data-model §2)."""
    if not source_key:
        return
    seen = seen_at or now_iso()
    db.execute(
        """INSERT INTO company_source(company_id, source_key, external_id, source_url,
                                      first_seen, last_seen)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(company_id, source_key, external_id)
           DO UPDATE SET last_seen = excluded.last_seen,
                         source_url = COALESCE(company_source.source_url, excluded.source_url)""",
        (company_id, source_key, external_id or "", source_url or "", seen, seen),
    )


def attach(db, company_id: str, record: Record, *, source_key: str,
           source_type: str = "news", source_url: str | None = None,
           external_id: str | None = None, seen_at: str | None = None,
           fields: Mapping[str, Any] | None = None) -> None:
    """Fold a new mention into an existing company. Adds keys, never overwrites."""
    seen = seen_at or now_iso()
    row = db.one("SELECT * FROM company WHERE id = ?", (company_id,))
    if row is None:
        raise ValueError(f"no such company: {company_id}")

    updates: dict[str, Any] = {}
    first = record.first_seen or seen
    if first < row["first_seen"]:
        updates["first_seen"] = first
    if seen > row["last_seen"]:
        updates["last_seen"] = seen

    incoming = {"companies_house_no": record.ch, "domain": record.dom,
                "country_iso2": record.country}
    incoming.update(fields or {})
    for col, value in incoming.items():
        if value is not None and row[col] is None:
            updates[col] = value

    if updates:
        updates["updated_at"] = seen
        db.execute(
            f"UPDATE company SET {','.join(f'{c}=?' for c in updates)} WHERE id=?",
            [*updates.values(), company_id],
        )

    add_identifier(db, company_id, "ch", record.ch, source_key=source_key, first_seen=seen)
    add_identifier(db, company_id, "domain", record.dom, source_key=source_key,
                   first_seen=seen)
    kind = "norm_key" if record.norm_key == row["norm_key"] else "alias"
    add_identifier(db, company_id, kind, record.norm_key, source_key=source_key,
                   first_seen=seen)
    link_source(db, company_id, source_key=source_key, external_id=external_id,
                source_url=source_url, seen_at=seen)


def upsert_record(db, record: Record, *, source_key: str, source_type: str = "news",
                  source_url: str | None = None, external_id: str | None = None,
                  seen_at: str | None = None,
                  fields: Mapping[str, Any] | None = None) -> Resolution:
    """Resolve one mention against the database: match, review, or create.

    A `merge` verdict folds the mention into the company it matched — nothing is
    destroyed, so there is no `merge_event`. A `review` verdict creates the
    record *and* queues the pair: an ambiguous pair never blocks a run
    (05-pipeline, failure summary).
    """
    from .review import enqueue_review

    match_rec, result = find_match(db, record)

    if result.decision == MERGE and match_rec and match_rec.company_id:
        attach(db, match_rec.company_id, record, source_key=source_key,
               source_type=source_type, source_url=source_url,
               external_id=external_id, seen_at=seen_at, fields=fields)
        if result.rule.startswith("fuzzy"):
            add_identifier(db, match_rec.company_id, "alias", record.norm_key,
                           source_key="fuzzy", first_seen=seen_at or now_iso())
        return Resolution(match_rec.company_id, "matched", result, match_rec.company_id)

    cid = create_company(db, record, source_key=source_key, source_type=source_type,
                         source_url=source_url, external_id=external_id,
                         seen_at=seen_at, fields=fields)

    if result.decision == REVIEW and match_rec and match_rec.company_id:
        key = enqueue_review(db, match_rec.company_id, cid, result)
        return Resolution(cid, "review", result, match_rec.company_id, key)

    return Resolution(cid, "created", result)


# ------------------------------------------------------------------ merging


def _row_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def merge_companies(db, winner_id: str, loser_id: str, *, rule: str,
                    score: float | None = None,
                    evidence: Mapping[str, Any] | None = None,
                    merged_by: str = "auto", merged_at: str | None = None) -> int:
    """Merge `loser` into `winner`. Returns the `merge_event` id."""
    if winner_id == loser_id:
        raise ValueError("cannot merge a company into itself")
    winner = db.one("SELECT * FROM company WHERE id=?", (winner_id,))
    loser = db.one("SELECT * FROM company WHERE id=?", (loser_id,))
    if winner is None or loser is None:
        raise ValueError("both companies must exist")
    if loser["merged_into"] is not None:
        raise ValueError(f"{loser_id} is already merged into {loser['merged_into']}")

    stamp = merged_at or now_iso()
    ev: dict[str, Any] = {
        "rule": rule,
        "score": score,
        "winner": {"id": winner_id, "name": winner["canonical_name"],
                   "norm_key": winner["norm_key"]},
        "loser": {"id": loser_id, "name": loser["canonical_name"],
                  "norm_key": loser["norm_key"]},
        **dict(evidence or {}),
        "winner_before": {c: winner[c] for c in _SNAPSHOT_COLS},
        "loser_before": {"merged_into": loser["merged_into"],
                         "updated_at": loser["updated_at"],
                         "needs_review": loser["needs_review"]},
        "moved": [],
        "deleted": [],
        "inserted_identifiers": [],
    }

    with db.tx():
        # 4 first: the tombstone drops the loser out of the partial unique
        # indexes on companies_house_no and domain, so the winner can adopt them.
        db.execute("UPDATE company SET merged_into=?, updated_at=? WHERE id=?",
                   (winner_id, stamp, loser_id))

        _move_rows(db, winner_id, loser_id, ev)
        _fold_columns(db, winner, loser, stamp, ev)

        alias = loser["norm_key"]
        if alias and alias != winner["norm_key"] and not is_placeholder_name(alias):
            cur = db.one(
                "SELECT 1 FROM identifier WHERE kind='alias' AND value=? AND company_id=?",
                (alias, winner_id))
            if cur is None:
                add_identifier(db, winner_id, "alias", alias, source_key="merge",
                               first_seen=stamp)
                ev["inserted_identifiers"].append(["alias", alias, winner_id])

        cur = db.execute(
            """INSERT INTO merge_event(winner_id, loser_id, rule, score, evidence_json,
                                       merged_at, merged_by)
               VALUES (?,?,?,?,?,?,?)""",
            (winner_id, loser_id, rule, score, json.dumps(ev, default=str), stamp, merged_by),
        )
        event_id = int(cur.lastrowid)
    return event_id


def _move_rows(db, winner_id: str, loser_id: str, ev: dict[str, Any]) -> None:
    """Re-point the loser's child rows, dropping only exact collisions."""
    for table, pk_cols, unique_cols in MOVABLE:
        rows = db.query(f"SELECT * FROM {table} WHERE company_id = ?", (loser_id,))
        for row in rows:
            data = _row_dict(row)
            collides = False
            if unique_cols:
                where = " AND ".join(f"{c}=?" for c in unique_cols)
                hit = db.one(
                    f"SELECT 1 FROM {table} WHERE company_id=? AND {where}",
                    [winner_id, *[data[c] for c in unique_cols]],
                )
                collides = hit is not None
            pk_where = " AND ".join(f"{c}=?" for c in pk_cols)
            pk_values = [data[c] for c in pk_cols]
            if collides:
                db.execute(f"DELETE FROM {table} WHERE {pk_where}", pk_values)
                ev["deleted"].append({"table": table, "row": data})
            else:
                db.execute(
                    f"UPDATE {table} SET company_id=? WHERE {pk_where}",
                    [winner_id, *pk_values],
                )
                moved_pk = {c: (winner_id if c == "company_id" else data[c]) for c in pk_cols}
                ev["moved"].append({"table": table, "pk": moved_pk,
                                    "was": {"company_id": loser_id}})


def _fold_columns(db, winner, loser, stamp: str, ev: dict[str, Any]) -> None:
    """first_seen = min, last_seen = max, and knowledge added where it was NULL."""
    updates: dict[str, Any] = {}
    if loser["first_seen"] < winner["first_seen"]:
        updates["first_seen"] = loser["first_seen"]
    if loser["last_seen"] > winner["last_seen"]:
        updates["last_seen"] = loser["last_seen"]
    for col in FILL_FORWARD:
        if winner[col] is None and loser[col] is not None:
            updates[col] = loser[col]
    for col in MAXED:
        a, b = winner[col], loser[col]
        if b is not None and (a is None or b > a):
            updates[col] = b
    for col in SUMMED:
        a, b = winner[col] or 0, loser[col] or 0
        if b:
            updates[col] = a + b
    updates["updated_at"] = stamp
    db.execute(
        f"UPDATE company SET {','.join(f'{c}=?' for c in updates)} WHERE id=?",
        [*updates.values(), winner["id"]],
    )


# ---------------------------------------------------------------- reversing

UNDONE_META = "merge_undone:"


def merge_events(db, *, include_undone: bool = False) -> list[dict[str, Any]]:
    """Every merge, newest first, with its evidence parsed."""
    out = []
    for row in db.query("SELECT * FROM merge_event ORDER BY id DESC"):
        undone = db.get_meta(f"{UNDONE_META}{row['id']}") is not None
        if undone and not include_undone:
            continue
        item = _row_dict(row)
        item["evidence"] = json.loads(row["evidence_json"])
        item["undone_at"] = db.get_meta(f"{UNDONE_META}{row['id']}")
        out.append(item)
    return out


def unmerge(db, event_id: int) -> dict[str, Any]:
    """Undo a merge exactly. The database returns to its pre-merge state.

    ponytail: the schema has no `undone` column on `merge_event` and this phase
    owns no migration, so the reversal is recorded in `_meta` under
    `merge_undone:<id>`. The event row itself is kept — deleting it would erase
    the provenance that made the merge explainable in the first place.
    """
    row = db.one("SELECT * FROM merge_event WHERE id = ?", (event_id,))
    if row is None:
        raise ValueError(f"no such merge_event: {event_id}")
    if db.get_meta(f"{UNDONE_META}{event_id}") is not None:
        raise ValueError(f"merge_event {event_id} has already been undone")

    ev = json.loads(row["evidence_json"])
    winner_id, loser_id = row["winner_id"], row["loser_id"]
    loser = db.one("SELECT * FROM company WHERE id=?", (loser_id,))
    if loser is None or loser["merged_into"] != winner_id:
        raise ValueError(f"{loser_id} is no longer merged into {winner_id}")

    with db.tx():
        for kind, value, cid in ev.get("inserted_identifiers", []):
            db.execute(
                "DELETE FROM identifier WHERE kind=? AND value=? AND company_id=?",
                (kind, value, cid))

        before = ev.get("winner_before", {})
        if before:
            db.execute(
                f"UPDATE company SET {','.join(f'{c}=?' for c in before)} WHERE id=?",
                [*before.values(), winner_id],
            )

        for item in ev.get("moved", []):
            table, pk = item["table"], item["pk"]
            where = " AND ".join(f"{c}=?" for c in pk)
            db.execute(f"UPDATE {table} SET company_id=? WHERE {where}",
                       [loser_id, *pk.values()])

        for item in ev.get("deleted", []):
            table, data = item["table"], item["row"]
            cols = ",".join(data)
            marks = ",".join("?" for _ in data)
            db.execute(f"INSERT OR REPLACE INTO {table}({cols}) VALUES ({marks})",
                       list(data.values()))

        lb = ev.get("loser_before", {})
        db.execute(
            "UPDATE company SET merged_into=?, updated_at=?, needs_review=? WHERE id=?",
            (lb.get("merged_into"), lb.get("updated_at", loser["updated_at"]),
             lb.get("needs_review", loser["needs_review"]), loser_id),
        )
        db.set_meta(f"{UNDONE_META}{event_id}", now_iso())

    return {"event_id": event_id, "winner_id": winner_id, "loser_id": loser_id,
            "undone": True}


# ------------------------------------------------------------------- audits


DUPLICATE_AUDIT_SQL = """
SELECT norm_key, country_iso2, COUNT(*) c
FROM company
WHERE merged_into IS NULL
  AND norm_key NOT IN (SELECT norm_key FROM placeholder_name)
GROUP BY norm_key, country_iso2
HAVING c > 1
"""


def duplicate_audit(db) -> list[dict[str, Any]]:
    """03-data-model §6, query 9. Must always return an empty list."""
    return [_row_dict(r) for r in db.query(DUPLICATE_AUDIT_SQL)]
