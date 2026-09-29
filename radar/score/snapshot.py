"""Score snapshots — what a scoring pass concluded, kept apart from `score`.

`score` is the *current* answer. A rescore upserts over it and moves
`scored_at`, so a digest that filters `score` by date loses every day that was
later rescored: a company shortlisted on Monday and rescored on Tuesday vanishes
from Monday's digest and from the weekly one (audit M-06). `score_snapshot`
keeps the answer as it stood at the end of each UTC day for those readers.

What is kept, and why — the table is written on every scoring pass, so its
growth is the cost of the feature:

* **Shortlist rows only.** The dated and weekly digests list shortlisted
  companies and nothing else. A row nobody reads is not worth a row per fund per
  company per day; the shortlist is tens of rows, the score table is tens of
  thousands.
* **One row per company × fund × day.** The last pass of the day wins, so a
  retune at 10:00 that drops a company from the shortlist also drops it from
  today's digest — exactly as the live table already behaves — while no later
  day can touch an earlier day's row. Digests are per day, so a finer grain
  would only store rows they would have to collapse again.
* **The ledger's inputs, not the sentence.** The digest draws its three-row
  ledger from `score_component`; those are stored as one JSON list per row
  (`key, label, sub_score, weight, evidence`). `explanation` and `flags` are not
  read by any historical digest, and an explanation can quote a signal
  headline, so neither is copied.

`score_history` (run-keyed, never populated) is superseded and left in place:
dropping a table needs a migration, and a rescore has no `run` row to key on.

Bookkeeping must never stop scoring, so a database that has not yet been
migrated (`db migrate` creates this table) skips the snapshot with a warning
instead of failing the run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

SNAPSHOT_TIER = "shortlist"

COLUMNS = ("company_id", "fund_key", "snapshot_date", "vehicle_key", "config_hash",
           "fund_fit_pct", "coverage", "discovery_edge", "priority", "tier",
           "components", "scored_at")

_INSERT = (
    f"INSERT OR REPLACE INTO score_snapshot ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in COLUMNS)})"
)

# (key, label, sub_score, weight, evidence) — the order `load_components` reads.
COMPONENT_FIELDS = ("key", "label", "sub_score", "weight", "evidence")


def _dump_components(components: Iterable[Sequence[Any]]) -> str:
    return json.dumps([list(c) for c in components], separators=(",", ":"))


def load_components(text: str | None) -> list[dict]:
    """A snapshot's components as the dicts the digest ledger reads."""
    try:
        parsed = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return [dict(zip(COMPONENT_FIELDS, item)) for item in parsed
            if isinstance(item, list) and len(item) == len(COMPONENT_FIELDS)]


def from_score(score: Any, scored_at: str) -> tuple | None:
    """The snapshot row for a `Score`, or None when the digests do not read it."""
    if score.tier != SNAPSHOT_TIER:
        return None
    parts = [(c.key, c.label, c.sub_score, c.weight, c.evidence)
             for c in [*score.components, *score.edge_components]]
    return (score.company_id, score.fund_key, scored_at[:10], score.vehicle_key,
            score.config_hash, score.fund_fit_pct, score.coverage,
            score.discovery_edge, score.priority, score.tier,
            _dump_components(parts), scored_at)


def from_bulk(score_row: Sequence[Any], components: Iterable[Sequence[Any]]) -> tuple:
    """The snapshot row for one `_score_row` tuple of the bulk rescore.

    `components` are the bulk path's `(fund, vehicle, key, label, sub,
    weight, contribution, evidence)` tuples; the caller has already checked the
    tier.
    """
    (company_id, fund_key, vehicle_key, fit, coverage, edge, priority, tier,
     _reason, _explanation, _flags, config_hash, _version, scored_at) = score_row
    parts = [(key, label, sub, weight, evidence)
             for _f, _v, key, label, sub, weight, _contribution, evidence in components]
    return (company_id, fund_key, scored_at[:10], vehicle_key, config_hash, fit,
            coverage, edge, priority, tier, _dump_components(parts), scored_at)


def _guarded(db: Any, work) -> None:
    try:
        work()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        log.warning("score_snapshot is missing — run `founder-radar db migrate`; "
                    "this pass leaves no snapshot")


def record_company(db: Any, company_id: str, scores: Sequence[Any],
                   scored_at: str) -> None:
    """Replace one company's snapshot for today in the funds `scores` covers.

    A fund scored again today but no longer shortlisted loses its row; a fund
    this pass did not score (a `--fund` run) keeps whatever it had.
    """
    day = scored_at[:10]

    def work() -> None:
        db.executemany(
            "DELETE FROM score_snapshot "
            "WHERE company_id = ? AND fund_key = ? AND snapshot_date = ?",
            [(company_id, s.fund_key, day) for s in scores])
        rows = [row for row in (from_score(s, scored_at) for s in scores) if row]
        if rows:
            db.executemany(_INSERT, rows)

    _guarded(db, work)


def clear_company(db: Any, company_id: str, day: str) -> None:
    """Drop today's snapshot of a company that is no longer scored at all."""
    _guarded(db, lambda: db.execute(
        "DELETE FROM score_snapshot WHERE company_id = ? AND snapshot_date = ?",
        (company_id, day)))


def replace_day(db: Any, day: str, rows: Sequence[tuple]) -> None:
    """A whole-database rescore: today's snapshot becomes exactly `rows`."""
    def work() -> None:
        db.execute("DELETE FROM score_snapshot WHERE snapshot_date = ?", (day,))
        if rows:
            db.executemany(_INSERT, rows)

    _guarded(db, work)


def approve_current(db: Any, card: Any, checked_at: str) -> None:
    """Approve only frozen rows matching the exact currently scored route.

    Called after a real completed check of the current card, never when scoring
    merely creates a snapshot. Rescoring replaces the snapshot and clears proof.
    """
    try:
        db.execute(
            """UPDATE score_snapshot SET approved_snapshot_hash = ?
               WHERE company_id = ? AND fund_key = ? AND vehicle_key IS ?
                 AND EXISTS (
                   SELECT 1 FROM score s
                    WHERE s.company_id = score_snapshot.company_id
                      AND s.fund_key = score_snapshot.fund_key
                      AND s.vehicle_key IS score_snapshot.vehicle_key
                      AND s.config_hash = score_snapshot.config_hash
                      AND s.scored_at = score_snapshot.scored_at
                      AND s.scored_at <= ? AND s.tier = 'shortlist')""",
            (card.snapshot_hash(), card.company_id, card.fund_key,
             card.vehicle_key, checked_at))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc) and "no such column" not in str(exc):
            raise
        log.warning("snapshot QA approval unavailable; historical rows withheld")
