"""UK GDPR erasure, and the suppression that makes it stick.

Deleting a founder row is the easy half. The hard half is that tomorrow's run
re-reads the same article and puts the person straight back. So erasure writes
a `suppression` row, and every ingest path checks it — which is why erasure
lives next to the ingest helpers rather than in a one-off script.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Iterable

from radar.store.db import Db, now_iso

log = logging.getLogger(__name__)

#: What replaces a forgotten person's name inside text we keep (a headline, a
#: one-liner) because the surrounding evidence is still useful without them.
REDACTED = "[name removed]"


def norm_person(name: str) -> str:
    """Fold a person's name to a stable comparison key.

    Deliberately blunt: casefold, collapse whitespace, strip punctuation. A
    suppression that is too narrow fails open, and failing open on an erasure
    request is the one failure mode with legal consequences.
    """
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name)
    return " ".join(cleaned.casefold().split())


def norm_person_folded(name: str) -> str:
    """`norm_person` plus accent folding: the key Companies House ingest stores in
    `founder.norm_name` (`radar.enrich.ch_officers.norm_person`), so erasure can
    find a founder row written by either path."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_suppressed(db: Db, name: str) -> bool:
    """Check before every founder insert. Cheap: a single primary-key lookup."""
    return db.one(
        "SELECT 1 FROM suppression WHERE norm_name IN (?, ?)",
        (norm_person(name), norm_person_folded(name)),
    ) is not None


def suppress(db: Db, name: str, reason: str = "gdpr_erasure") -> None:
    for key in {norm_person(name), norm_person_folded(name)}:
        db.execute(
            "INSERT OR REPLACE INTO suppression(norm_name, reason, created_at) VALUES (?,?,?)",
            (key, reason, now_iso()),
        )


#: Founder-shaped observations are dropped for every company the person was
#: attached to, whether or not their JSON happens to spell the name.
_FOUNDER_OBSERVATION_FIELDS = ("founders", "founder", "officers")

#: Where else a person's name can be stored, and what erasure does about it.
#: (table, columns, action, column holding the company id)
#:
#: * `delete`: rows that are evidence or cache and mean nothing without the
#:   text (an observation, a cached extraction, a quarantined record, the text
#:   last written into a sheet cell). Deleting a cache entry only costs a
#:   re-read.
#: * `redact`: text a person reads (a Today card headline, a one-liner, QA
#:   commentary, a note) sits on a row that is still useful, so the name is
#:   replaced in place. Redacting rather than deleting a signal also keeps its
#:   `(company_id, kind, source_url)` key, so tomorrow's re-read of the same
#:   article is an INSERT OR IGNORE no-op instead of putting the headline back.
#:
#: Deliberately NOT rewritten: `suppression` (its whole job is to hold the
#: folded name), a company's registered name and its identifiers (register
#: identity, not our description of a person), and URLs (rewriting a source
#: link breaks the unique key that makes ingest idempotent).
_SCRUB_TARGETS: tuple[tuple[str, tuple[str, ...], str, str | None], ...] = (
    ("observation", ("value_json",), "delete", "company_id"),
    ("llm_cache", ("response_json",), "delete", None),
    ("quarantine", ("raw_json", "error"), "delete", None),
    ("sheet_row_state", ("last_value",), "delete", "company_id"),
    ("signal", ("headline", "detail"), "redact", "company_id"),
    ("company", ("one_liner",), "redact", "id"),
    ("user_field", ("value",), "redact", "company_id"),
    ("today_check", ("summary", "raw_text"), "redact", "company_id"),
    ("merge_event", ("evidence_json",), "redact", None),
)


def name_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    """A regex for every way the stored text might spell these names.

    Case, punctuation, spacing and accents do not matter, and both `Jane Smith`
    and `Smith, Jane` match. It refuses to match inside a longer word, so
    `Jane Smithson` survives an erasure of `Jane Smith`. Blunt on purpose: a
    pattern that is too narrow fails open on an erasure request.
    """
    alternatives: list[str] = []
    for name in names:
        folded = unicodedata.normalize("NFC", name or "")
        for key in {norm_person(folded), norm_person_folded(folded)}:
            tokens = key.split()
            if not tokens:
                continue
            orders = [tokens]
            if len(tokens) > 1:
                orders.append([tokens[-1], *tokens[:-1]])     # "SMITH, Jane"
            for order in orders:
                alt = r"[\W_]+".join(re.escape(t) for t in order)
                if alt not in alternatives:
                    alternatives.append(alt)
    if not alternatives:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)",
                      re.IGNORECASE)


def _redact_json(value: Any, pattern: re.Pattern[str]) -> tuple[Any, bool]:
    """Walk a decoded JSON document, redacting inside strings and keys."""
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value)
        return (pattern.sub(REDACTED, text), True) if pattern.search(text) else (value, False)
    if isinstance(value, list):
        out, hit = [], False
        for item in value:
            new, changed = _redact_json(item, pattern)
            out.append(new)
            hit = hit or changed
        return out, hit
    if isinstance(value, dict):
        out_d: dict[Any, Any] = {}
        hit = False
        for key, item in value.items():
            new_key, key_changed = _redact_json(key, pattern)
            new, changed = _redact_json(item, pattern)
            out_d[new_key] = new
            hit = hit or key_changed or changed
        return out_d, hit
    return value, False


def redact_text(text: str, pattern: re.Pattern[str]) -> str | None:
    """`text` with the person redacted, or None when it does not mention them.

    A JSON document is decoded first (so an escape like `\\u00eb` cannot hide a
    name) and written back as JSON.
    """
    if text[:1] in ("{", "[", '"'):
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = None
        else:
            new, hit = _redact_json(decoded, pattern)
            return json.dumps(new) if hit else None
    normalised = unicodedata.normalize("NFC", text)
    if not pattern.search(normalised):
        return None
    return pattern.sub(REDACTED, normalised)


def _drop_founder_rows_from_merge_evidence(text: str, pattern: re.Pattern[str]) -> str:
    """A merge that dropped a duplicate founder row keeps it in `evidence_json`
    so `unmerge` can put it back. Restoring an erased person is exactly what
    must not happen, so those entries are removed rather than redacted."""
    try:
        evidence = json.loads(text)
    except ValueError:
        return text
    if not isinstance(evidence, dict) or not isinstance(evidence.get("deleted"), list):
        return text
    kept = []
    for item in evidence["deleted"]:
        row = item.get("row") if isinstance(item, dict) else None
        if (isinstance(item, dict) and item.get("table") == "founder"
                and isinstance(row, dict)
                and any(pattern.search(str(row.get(c) or "")) for c in ("name", "norm_name"))):
            continue
        kept.append(item)
    if len(kept) == len(evidence["deleted"]):
        return text
    evidence["deleted"] = kept
    return json.dumps(evidence, default=str)


def _scrub_table(db: Db, table: str, columns: tuple[str, ...], action: str,
                 company_col: str | None, pattern: re.Pattern[str]) -> set[str]:
    """Apply one `_SCRUB_TARGETS` entry. Returns the company ids it touched."""
    touched: set[str] = set()
    company_sql = company_col or "NULL"
    for row in db.query(
        f"SELECT rowid AS _rid, {company_sql} AS _cid, {', '.join(columns)} FROM {table}"
    ):
        updates: dict[str, str] = {}
        for col in columns:
            value = row[col]
            if not isinstance(value, str) or not value:
                continue
            if table == "merge_event":
                value = _drop_founder_rows_from_merge_evidence(value, pattern)
            new = redact_text(value, pattern)
            if new is not None:
                updates[col] = new
            elif value != row[col]:
                updates[col] = value            # only the founder entries went
        if not updates:
            continue
        if row["_cid"]:
            touched.add(row["_cid"])
        if action == "delete":
            db.execute(f"DELETE FROM {table} WHERE rowid = ?", (row["_rid"],))
        else:
            assignments = ", ".join(f"{c} = ?" for c in updates)
            db.execute(f"UPDATE {table} SET {assignments} WHERE rowid = ?",
                       [*updates.values(), row["_rid"]])
    return touched


def forget_person(db: Db, name: str) -> dict[str, Any]:
    """Erase a person and stop them coming back.

    Everything that identifies them is read *before* anything is deleted: the
    founder rows say which companies they were attached to and how their name
    is spelled in our data. Then the founder rows go, founder-shaped
    observations go, and every other place the name was stored as text is
    deleted or redacted (`_SCRUB_TARGETS`). One transaction: an erasure is
    complete or it did not happen.

    Returns a receipt rather than printing, so the CLI can render it and a
    caller can assert on it. `companies_affected` lists every company whose
    stored data changed.
    """
    key = norm_person(name)
    keys = {k for k in (key, norm_person_folded(name)) if k}
    with db.tx():
        rows = []
        if keys:
            marks = ",".join("?" for _ in keys)
            rows = db.query(
                f"SELECT id, company_id, name FROM founder WHERE norm_name IN ({marks})",
                tuple(keys),
            )
        affected = {r["company_id"] for r in rows}
        variants = [name, *(r["name"] for r in rows)]
        pattern = name_pattern(variants)

        if rows:
            db.execute(
                f"DELETE FROM founder WHERE id IN ({','.join('?' for _ in rows)})",
                [r["id"] for r in rows],
            )
        # Observations can carry the name in their JSON payload; the
        # founder-shaped ones for the companies the person belonged to are
        # dropped rather than rewritten. The company ids came from the founder
        # rows above, captured before those rows were deleted.
        if affected:
            db.execute(
                f"""DELETE FROM observation
                    WHERE field IN ({','.join('?' for _ in _FOUNDER_OBSERVATION_FIELDS)})
                      AND company_id IN ({','.join('?' for _ in affected)})""",
                (*_FOUNDER_OBSERVATION_FIELDS, *sorted(affected)),
            )
        if pattern is not None:
            present = db.tables()
            for table, columns, action, company_col in _SCRUB_TARGETS:
                if table in present:
                    affected |= _scrub_table(db, table, columns, action, company_col,
                                             pattern)
        suppress(db, name)

    log.info("forget: %d founder row(s) deleted, %d company(ies) affected",
             len(rows), len(affected))
    return {
        "name": name,
        "norm_name": key,
        "founders_deleted": len(rows),
        "companies_affected": sorted(affected),
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
