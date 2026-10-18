"""Foreign-currency funding → GBP, so a "$4m seed" meets the same £ thresholds.

Rates are the European Central Bank's daily euro reference rates — public,
published every working day around 16:00 CET, one small XML file. They are
fetched at most once per run and kept in `_meta`, so a day the ECB is
unreachable reuses the last good set. `FALLBACK_GBP_PER` is only for a box
that has never fetched: approximate October 2026 rates, and a converted
amount always keeps its original beside it for a human to check.

An amount in a currency with no rate stays unconverted (unknown, not guessed).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
META_KEY = "fx:gbp_per"

#: GBP for one unit of each currency. Used only before the first ECB fetch.
FALLBACK_GBP_PER: dict[str, float] = {
    "GBP": 1.0, "USD": 0.75, "EUR": 0.86, "CHF": 0.93, "CAD": 0.54,
    "AUD": 0.49, "SEK": 0.071, "NOK": 0.069, "DKK": 0.115, "JPY": 0.0050,
}

_CUBE = re.compile(r"currency=['\"](?P<code>[A-Z]{3})['\"]\s+rate=['\"](?P<rate>[\d.]+)['\"]")
_TIME = re.compile(r"time=['\"](?P<day>\d{4}-\d{2}-\d{2})['\"]")

_current: dict[str, float] | None = None


def parse_ecb(xml: str) -> tuple[str | None, dict[str, float]]:
    """ECB XML (rates per EUR) → `(as_of, {code: GBP per unit})`."""
    per_eur = {m.group("code"): float(m.group("rate")) for m in _CUBE.finditer(xml)}
    gbp = per_eur.get("GBP")
    if not gbp:
        return None, {}
    out = {code: gbp / rate for code, rate in per_eur.items() if rate > 0}
    out["EUR"] = gbp
    out["GBP"] = 1.0
    day = _TIME.search(xml)
    return (day.group("day") if day else None), out


def load_rates(db: Any = None, http: Any = None, *, today: date | None = None) -> dict[str, float]:
    """Today's rates: cached if fetched today, else the ECB, else the last good set."""
    global _current
    today = today or date.today()
    cached: dict[str, Any] = {}
    if db is not None:
        try:
            cached = json.loads(db.get_meta(META_KEY) or "{}")
        except Exception:  # noqa: BLE001 - a bad cache is just no cache
            cached = {}
    rates = cached.get("rates") or {}
    if cached.get("fetched_on") != today.isoformat() and http is not None:
        try:
            resp = http.get(ECB_DAILY, timeout=10, max_retries=0)
            if resp.ok:
                as_of, fresh = parse_ecb(resp.text)
                if fresh:
                    rates = fresh
                    if db is not None:
                        db.set_meta(META_KEY, json.dumps({
                            "fetched_on": today.isoformat(), "as_of": as_of, "rates": rates,
                        }))
        except Exception as exc:  # noqa: BLE001 - FX is a nicety, never the run
            log.warning("ECB FX fetch failed (%s); using %s rates",
                        type(exc).__name__, "cached" if rates else "fallback")
    _current = {**FALLBACK_GBP_PER, **rates}
    return _current


def to_gbp(amount: float | None, currency: str | None,
           rates: dict[str, float] | None = None) -> float | None:
    """Amount in GBP, or None when there is no amount or no rate for it."""
    if amount is None:
        return None
    code = (currency or "GBP").strip().upper()
    table = rates or _current or FALLBACK_GBP_PER
    rate = table.get(code)
    if rate is None:
        return None
    return round(float(amount) * rate, 2)


SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥"}


def short_money(amount: float | None, currency: str | None = "GBP") -> str:
    """`4000000, 'USD'` → `$4.0M`; `250000` → `£250k`; unknown code → `CHF 4.0M`."""
    if amount is None:
        return ""
    code = (currency or "GBP").upper()
    sign = SYMBOL.get(code)
    value = float(amount)
    if value >= 1_000_000:
        body = f"{value / 1_000_000:.1f}M"
    elif value >= 1_000:
        body = f"{value / 1_000:.0f}k"
    else:
        body = f"{value:,.0f}"
    return f"{sign}{body}" if sign else f"{code} {body}"
