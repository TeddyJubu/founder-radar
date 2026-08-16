"""The review queue — fuzzy matches a human must decide on (05-pipeline §4.2).

Tiers 3, 5 and the guard failures land here rather than auto-merging: an
ambiguous pair is never silently resolved, and it never blocks a run either
(the mention is created as its own record and the pair is queued).

Storage is `_meta` under the `review:` prefix, the same pattern the merge
undo and the filings-checked markers use — and the key is a deterministic
function of the pair, so the idempotency check is one `_meta` lookup, never a
scan of the queue. The schema has no review table by design — a queue is
transient state, not a fact about a company, and keeping it out of the
relational schema means it cannot be confused with provenance.

`review_queue()` returns the pairs with both names resolved so the CLI can
print something a human can act on, and `resolve_review()` merges the pair
once a decision is made.
"""

from __future__ import annotations

import json
from typing import Any

from radar.store.db import now_iso

_PREFIX = "review:"


def _pair_key(winner_id: str, loser_id: str) -> str:
    """Deterministic, order-independent key for the unordered pair.

    The key IS the pair: the two ids sorted and joined with `|` (ULIDs never
    contain it). One `_meta` lookup is therefore the whole idempotency check —
    no scan of the queue, so queueing N pairs costs O(N), not O(N²). Order
    must not matter, because a later run may present the pair the other way
    round; sorting makes winner/loser presentation irrelevant.
    """
    lo, hi = sorted((winner_id, loser_id))
    return f"{_PREFIX}{lo}|{hi}"


def enqueue_review(db: Any, winner_id: str, loser_id: str, result: Any) -> str:
    """Queue `(winner, loser)` for a human decision. Returns the stable key.

    Idempotent per pair: re-finding the same pair on a later run must not grow
    the queue, so the key is derived from the pair itself and the existing
    entry is found in O(1) — returning it untouched keeps the original
    `created_at`, which is what keeps the queue's newest-first order stable.
    `result` is the `MatchResult` the ladder produced, so the queue carries
    the rule, the score and the evidence a human needs to decide.
    """
    key = _pair_key(winner_id, loser_id)
    if db.get_meta(key) is not None:
        return key  # already queued — return the stable key untouched

    payload = {
        "winner_id": winner_id,
        "loser_id": loser_id,
        "rule": getattr(result, "rule", "review"),
        "score": getattr(result, "score", None),
        "confidence": getattr(result, "confidence", None),
        "evidence": dict(getattr(result, "evidence", {}) or {}),
        "created_at": now_iso(),
    }
    db.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        (key, json.dumps(payload, sort_keys=True, default=str)),
    )
    return key


def _pairs(db: Any) -> list[dict[str, Any]]:
    rows = db.query("SELECT key, value FROM _meta WHERE key LIKE ?", (f"{_PREFIX}%",))
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            continue
        payload["key"] = row["key"]
        out.append(payload)
    out.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return out


def _name_of(db: Any, company_id: str | None) -> str:
    if not company_id:
        return "?"
    row = db.one("SELECT canonical_name FROM company WHERE id = ?", (company_id,))
    return row["canonical_name"] if row else f"(merged/removed {company_id})"


def review_queue(db: Any) -> list[dict[str, Any]]:
    """Everything awaiting a human decision, with names resolved.

    Each entry is `{key, winner_id, loser_id, winner, loser, rule, score,
    evidence, created_at}`.
    """
    out = []
    for payload in _pairs(db):
        entry = dict(payload)
        entry["winner"] = _name_of(db, payload.get("winner_id"))
        entry["loser"] = _name_of(db, payload.get("loser_id"))
        out.append(entry)
    return out


def resolve_review(db: Any, key: str, *, merge: bool) -> dict[str, Any]:
    """Act on one queued pair: merge them, or dismiss the queue entry.

    Returns a receipt the CLI can render. Merging uses the same
    `merge_companies` machinery as auto-merges, so it is recorded, reversible
    and audited; the queue entry is removed either way.
    """
    payload = next((p for p in _pairs(db) if p["key"] == key), None)
    if payload is None:
        raise ValueError(f"no queued review with key {key!r}")

    from radar.resolve.merge import merge_companies

    result: dict[str, Any] = {"key": key, "merged": False}
    if merge:
        event_id = merge_companies(
            db,
            payload["winner_id"],
            payload["loser_id"],
            rule=payload.get("rule") or "manual",
            score=payload.get("score"),
            evidence=payload.get("evidence"),
            merged_by="user",
        )
        result["merged"] = True
        result["merge_event_id"] = event_id
    db.execute("DELETE FROM _meta WHERE key = ?", (key,))
    result["winner"] = _name_of(db, payload["winner_id"])
    result["loser"] = _name_of(db, payload["loser_id"])
    return result


__all__ = ["enqueue_review", "review_queue", "resolve_review"]
