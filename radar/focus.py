"""Fund focus: "focus on Outward VC this week", and a weekly rotation.

Focus narrows what Aryan is shown, never what is scored: Today, the morning
ping and the Sheet's Today tab show only companies with a live match for the
focus fund, and Today QA checks that fund's cards first. Scores, suggested
funds and the config hash are untouched, so setting a focus never rescores or
makes a checked card stale. Zero matches is a valid answer.

An explicit focus (`set_focus`) runs for N days. Without one, the weekly
rotation names a fund per weekday (Mon/Tue Outward VC, Wed DSW, ...), and a
day with no fund in the rotation shows every fund.

State lives in `_meta` (not the Sheet): it is a viewing preference, set from
Telegram or the Control room, and every change is logged in `admin_change`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

FUND_KEYS = ("dsw", "northstar", "outward", "anticus")
FUND_NAMES = {"dsw": "DSW", "northstar": "Northstar", "outward": "Outward VC",
              "anticus": "Anticus"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_DAYS = 60

_FUND = "focus:fund"
_UNTIL = "focus:until"
_ROTATION = "focus:rotation"


class FocusInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Focus:
    fund: str
    source: str            # "focus" (explicit, until a date) | "rotation" (today's weekday)
    until: date | None = None

    @property
    def name(self) -> str:
        return FUND_NAMES.get(self.fund, self.fund)

    def describe(self) -> str:
        if self.source == "focus" and self.until:
            return f"{self.name} until {self.until.strftime('%a %d %b')}"
        return f"{self.name} (today's rotation)"

    def as_dict(self) -> dict[str, Any]:
        return {"fund": self.fund, "name": self.name, "source": self.source,
                "until": self.until.isoformat() if self.until else None,
                "label": self.describe()}


# ------------------------------------------------------------------- storage


def _get(db: Any, key: str) -> str | None:
    if hasattr(db, "get_meta"):
        return db.get_meta(key)
    row = db.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(db: Any, key: str, value: str | None) -> None:
    if value is None:
        db.execute("DELETE FROM _meta WHERE key = ?", (key,))
    else:
        db.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)", (key, value))
    if not hasattr(db, "get_meta") and hasattr(db, "commit"):
        db.commit()


def _log(db: Any, key: str, old: str, new: str, note: str | None) -> None:
    from radar.store.db import now_iso

    try:
        db.execute(
            "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
            "VALUES (?, 'focus', ?, ?, ?, ?, 'n/a')",
            (now_iso(), key, old, new, (note or "").strip() or None))
    except Exception:  # noqa: BLE001 - a pre-008 database has no audit table
        pass


def fund_key(raw: str) -> str:
    """'Outward VC' / 'outward' / 'OUTWARD' → 'outward'; anything else raises."""
    text = str(raw or "").strip().lower()
    for key in FUND_KEYS:
        if text == key or text.startswith(key) or FUND_NAMES[key].lower() == text:
            return key
    raise FocusInvalid(f"unknown fund {raw!r} — use {', '.join(FUND_KEYS)}")


# --------------------------------------------------------------------- reads


def rotation(db: Any) -> dict[str, str | None]:
    try:
        stored = json.loads(_get(db, _ROTATION) or "{}")
    except ValueError:
        stored = {}
    return {day: (stored.get(day) or None) for day in WEEKDAYS}


def active(db: Any, today: date | None = None) -> Focus | None:
    """Today's focus: an explicit one still running, else the rotation's."""
    today = today or date.today()
    try:
        fund = _get(db, _FUND)
        until_raw = _get(db, _UNTIL)
    except Exception:  # noqa: BLE001 - no _meta table yet means no focus
        return None
    if fund and until_raw:
        until = date.fromisoformat(until_raw)
        if until >= today:
            return Focus(fund, "focus", until)
    try:
        planned = rotation(db).get(WEEKDAYS[today.weekday()])
    except Exception:  # noqa: BLE001
        planned = None
    return Focus(planned, "rotation") if planned else None


# -------------------------------------------------------------------- writes


def set_focus(db: Any, fund: str, days: int = 7, *, note: str | None = None,
              today: date | None = None) -> Focus:
    key = fund_key(fund)
    try:
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise FocusInvalid("days must be a whole number") from exc
    if not 1 <= days <= MAX_DAYS:
        raise FocusInvalid(f"days must be between 1 and {MAX_DAYS}")
    until = (today or date.today()) + timedelta(days=days - 1)
    before = active(db, today)
    _set(db, _FUND, key)
    _set(db, _UNTIL, until.isoformat())
    _log(db, "focus", before.describe() if before else "none",
         f"{FUND_NAMES[key]} until {until.isoformat()}", note)
    return Focus(key, "focus", until)


def clear_focus(db: Any, *, note: str | None = None) -> None:
    before = _get(db, _FUND)
    _set(db, _FUND, None)
    _set(db, _UNTIL, None)
    _log(db, "focus", before or "none", "cleared", note)


def parse_rotation(text: str) -> dict[str, str | None]:
    """'mon=outward tue=outward wed=dsw' (or 'mon outward, wed dsw') → mapping.

    Unnamed days have no fund. 'clear' / '' clears the whole week.
    """
    out: dict[str, str | None] = {day: None for day in WEEKDAYS}
    raw = str(text or "").strip().lower()
    if raw in {"", "clear", "none", "off"}:
        return out
    pairs = re.findall(r"([a-z]{3})[a-z]*\s*[=:\s]\s*([a-z]+(?:\s+vc)?)", raw)
    if not pairs:
        raise FocusInvalid("write it like: mon=outward tue=outward wed=dsw")
    for day, fund in pairs:
        if day not in WEEKDAYS:
            raise FocusInvalid(f"unknown day {day!r} — use {', '.join(WEEKDAYS)}")
        out[day] = None if fund in {"none", "all", "off"} else fund_key(fund)
    return out


def set_rotation(db: Any, plan: dict[str, Any], *, note: str | None = None) -> dict[str, str | None]:
    clean: dict[str, str | None] = {}
    for day in WEEKDAYS:
        value = plan.get(day)
        clean[day] = fund_key(value) if value else None
    before = rotation(db)
    _set(db, _ROTATION, json.dumps(clean))
    _log(db, "rotation", describe_rotation(before), describe_rotation(clean), note)
    return clean


def describe_rotation(plan: dict[str, str | None]) -> str:
    days = [f"{day.title()} {FUND_NAMES[f]}" for day, f in plan.items() if f]
    return ", ".join(days) if days else "no rotation (all funds every day)"
