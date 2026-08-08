"""The layout-change detector — the answer to the dangerous failure.

A source that 404s is loud and gets caught by the per-source isolation in
stage ② (05-pipeline). A source that returns `200 OK` with an *empty list* is
silent: it looks exactly like a quiet week, and it is what a WordPress theme
change or a renamed CSS class actually produces. This module is the only thing
standing between "Northern Accelerator changed its markup in March" and "nobody
noticed until June".

Three complementary mechanisms, cheapest first:

1. **Structural guard** (`guard_nonempty`) — an adapter that parsed a
   non-trivial document into zero items raises `LayoutChanged` immediately.
   No history needed, fires on the very first bad run.
2. **Selector fingerprint** (`selector_fingerprint` / `check_fingerprint`) —
   a hash of the selector-path set an HTML adapter actually matched, stored in
   `_meta`. A theme change that still yields *some* items shifts the hash.
3. **Item-count history** (`check_health` over the `source_health` table) —
   a source that normally returns items and today returned none. This is the
   one that catches partial breakage and the 7-consecutive-zero-days alert
   condition from 04-sources §6.

Nothing here raises into the pipeline: `check_health` *returns* a verdict, and
stage ② decides what to do with it. Only the adapters raise `LayoutChanged`,
and stage ② already catches per-source exceptions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

#: A source averaging more than this many items is expected to keep producing.
MIN_AVERAGE_TO_ALERT = 2.0
#: Consecutive zero-item days before Telegram is told (04-sources §6).
ZERO_STREAK_DAYS = 7
#: Trailing window for the "normally returns items" average.
HEALTH_WINDOW_DAYS = 7
#: A document shorter than this is a genuine empty page, not a layout change.
MIN_DOCUMENT_BYTES = 200

_META_PREFIX = "layout:"


class LayoutChanged(Exception):
    """The source responded, but not in the shape the adapter understands.

    Deliberately not a `SourceError`: the message wants to say *layout*, loudly,
    because "0 items" and "the markup moved" are the same event from the outside
    and only one of them is actionable.
    """

    def __init__(self, source_key: str, detail: str) -> None:
        super().__init__(f"{source_key}: layout changed — {detail}")
        self.source_key = source_key
        self.detail = detail


# --------------------------------------------------------------- fingerprints


def selector_fingerprint(paths: Iterable[str]) -> str:
    """Stable hash over the *set* of selector paths an adapter matched.

    Order and repetition are dropped on purpose: a page with 12 cards and a
    page with 3 cards of the same shape must fingerprint identically, or every
    quiet week looks like a break.
    """
    unique = sorted({p.strip() for p in paths if p and p.strip()})
    return hashlib.sha256("\n".join(unique).encode()).hexdigest()[:16]


def stored_fingerprint(db: Any, source_key: str) -> str | None:
    if db is None:
        return None
    return db.get_meta(f"{_META_PREFIX}{source_key}")


def remember_fingerprint(db: Any, source_key: str, fingerprint: str) -> None:
    if db is not None:
        db.set_meta(f"{_META_PREFIX}{source_key}", fingerprint)


def check_fingerprint(db: Any, source_key: str, fingerprint: str, *, learn: bool = True) -> None:
    """Raise `LayoutChanged` when the structure hash moves.

    The first sighting is *learned*, not rejected — otherwise a brand-new
    adapter can never have a first successful run. `learn=False` makes it a
    pure assertion, which is what `sources --test` wants.
    """
    previous = stored_fingerprint(db, source_key)
    if previous is None:
        if learn:
            remember_fingerprint(db, source_key, fingerprint)
        return
    if previous != fingerprint:
        raise LayoutChanged(
            source_key, f"structure fingerprint {previous} -> {fingerprint}"
        )


def accept_fingerprint(db: Any, source_key: str, fingerprint: str) -> None:
    """Adopt a new structure after a human has looked at it."""
    remember_fingerprint(db, source_key, fingerprint)


# ------------------------------------------------------------------- guards


def guard_nonempty(
    source_key: str,
    items: Sequence[Any] | Iterable[Any],
    *,
    detail: str,
    document: str | bytes | None = None,
) -> list[Any]:
    """Zero items out of a non-trivial document is a layout change, not a lull.

    `document` is the raw response body. When it is genuinely tiny (an empty
    JSON array, a maintenance stub) we still raise — the caller decides what is
    legitimate by not calling this — but the length lands in the message, which
    is the first thing a human wants to know.
    """
    out = list(items)
    if out:
        return out
    size = len(document) if document is not None else -1
    suffix = f" (document {size} bytes)" if size >= 0 else ""
    raise LayoutChanged(source_key, f"{detail}{suffix}")


# ------------------------------------------------------- item-count history


@dataclass(frozen=True)
class HealthVerdict:
    """What the history says about today's item count."""

    source_key: str
    items: int
    average: float          # trailing average, EXCLUDING today
    zero_streak: int        # consecutive zero-item days, INCLUDING today
    status: str             # ok | empty | failed
    warning: str | None = None
    alert: bool = False     # escalate to Telegram

    @property
    def suspicious(self) -> bool:
        return self.warning is not None


def _iso(day: date | str | None) -> str:
    if day is None:
        return date.today().isoformat()
    return day if isinstance(day, str) else day.isoformat()


def record_health(
    db: Any,
    source_key: str,
    items: int,
    *,
    status: str = "ok",
    note: str | None = None,
    observed_on: date | str | None = None,
) -> None:
    """Upsert today's row. Re-running the pipeline twice a day must not double-count."""
    db.execute(
        """INSERT INTO source_health(source_key, observed_on, items, status, note)
           VALUES (?,?,?,?,?)
           ON CONFLICT(source_key, observed_on) DO UPDATE SET
             items=excluded.items, status=excluded.status, note=excluded.note""",
        (source_key, _iso(observed_on), int(items), status, note),
    )


def average_items(
    db: Any,
    source_key: str,
    *,
    days: int = HEALTH_WINDOW_DAYS,
    before: date | str | None = None,
) -> float:
    """Mean items per day over the `days` days strictly before `before`.

    Failed days are excluded: a source that was down is not evidence about how
    much it normally publishes, and counting its zeros would suppress the very
    warning we want.
    """
    end = _iso(before)
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    rows = db.query(
        """SELECT items FROM source_health
           WHERE source_key = ? AND observed_on >= ? AND observed_on < ?
             AND status != 'failed'""",
        (source_key, start, end),
    )
    if not rows:
        return 0.0
    return sum(r["items"] for r in rows) / len(rows)


def zero_streak(db: Any, source_key: str, *, upto: date | str | None = None) -> int:
    """Consecutive zero-item *recorded runs* ending at `upto`, counting backwards.

    Recorded runs, not calendar days: a weekly source has no row on six days out
    of seven, and treating those gaps as zeros would fire the alert on every
    weekly adapter in the registry.
    """
    end = _iso(upto)
    rows = db.query(
        """SELECT observed_on, items FROM source_health
           WHERE source_key = ? AND observed_on <= ?
           ORDER BY observed_on DESC""",
        (source_key, end),
    )
    streak = 0
    for row in rows:
        if row["items"] != 0:
            break
        streak += 1
    return streak


def check_health(
    db: Any,
    source_key: str,
    items: int,
    *,
    status: str = "ok",
    observed_on: date | str | None = None,
    note: str | None = None,
    min_average: float = MIN_AVERAGE_TO_ALERT,
    streak_days: int = ZERO_STREAK_DAYS,
) -> HealthVerdict:
    """Record today's count and say whether it looks like a silent break.

    The baseline average is measured over the window ending where the current
    zero-streak *began*, not the window ending today. That distinction is the
    whole test: by day seven of a silent break the trailing seven days are all
    zero, so an average taken up to today would be 0.0 and would suppress
    exactly the alert 04-sources §6 asks for.
    """
    day = date.fromisoformat(_iso(observed_on))
    prior_streak = zero_streak(db, source_key, upto=day - timedelta(days=1))
    average = average_items(db, source_key, before=day - timedelta(days=prior_streak))
    resolved_status = status if status != "ok" or items else "empty"

    warning: str | None = None
    if items == 0 and status != "failed" and average >= min_average:
        warning = (
            f"{source_key} returned 0 items but averages {average:.1f}/day "
            f"over the last {HEALTH_WINDOW_DAYS} days — suspected layout change"
        )

    record_health(
        db, source_key, items,
        status=resolved_status, note=note or warning, observed_on=observed_on,
    )

    streak = zero_streak(db, source_key, upto=observed_on)
    alert = bool(items == 0 and streak >= streak_days and average > min_average)
    if alert and warning is None:
        warning = (
            f"{source_key} has returned 0 items for {streak} consecutive days"
        )

    return HealthVerdict(
        source_key=source_key,
        items=items,
        average=average,
        zero_streak=streak,
        status=resolved_status,
        warning=warning,
        alert=alert,
    )


def health_report(db: Any, source_keys: Iterable[str] | None = None) -> list[dict]:
    """What the `Sources` sheet tab shows: last success, today, 7-day average."""
    keys = list(source_keys) if source_keys is not None else [
        r["source_key"] for r in db.query(
            "SELECT DISTINCT source_key FROM source_health ORDER BY source_key")
    ]
    out: list[dict] = []
    for key in keys:
        last = db.one(
            """SELECT observed_on, items, status, note FROM source_health
               WHERE source_key = ? ORDER BY observed_on DESC LIMIT 1""",
            (key,),
        )
        last_ok = db.scalar(
            """SELECT observed_on FROM source_health
               WHERE source_key = ? AND status = 'ok' AND items > 0
               ORDER BY observed_on DESC LIMIT 1""",
            (key,),
        )
        out.append({
            "source_key": key,
            "last_run": last["observed_on"] if last else None,
            "last_success": last_ok,
            "items": last["items"] if last else 0,
            "status": last["status"] if last else "unknown",
            "average_7d": round(average_items(db, key), 2),
            "zero_streak": zero_streak(db, key),
            "note": last["note"] if last else None,
        })
    return out
