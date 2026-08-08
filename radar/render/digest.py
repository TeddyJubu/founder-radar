"""Telegram-shaped text. The CLI prints these strings as-is (07-interfaces §2).

**Standard library only.** This module is on the digest path, which runs on a
1 vCPU / 4 GB box next to Hermes, and NFR-2 caps the whole thing at 700 MB.
Importing gspread or httpx here would cost tens of megabytes to render a
message that is fundamentally a few SQL queries and some `str.join`. Anything
heavier than `datetime` must be imported inside the function that needs it.

Four renderers, all pure reads:

* `render_digest` — the daily message. Three shapes: full day, quiet day, zero day.
* `render_status` — last run, source health, this month's AI spend.
* `render_show`   — one company, its signals and its score breakdown.
* `render_fund`   — top current matches for one fund.

The zero-day shape matters as much as the full one. A digest that says nothing
on a quiet day is indistinguishable from a digest that failed to run, so the
zero-day message prints the funnel and states plainly that the filter worked.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------- labels

# ponytail: these labels duplicate the `Fund Criteria` tab (07-interfaces §1,
# Tab 4). They are the fallback only — `_fund_config()` prefers the live config
# whenever `radar.config.defaults` is importable, so an added fifth fund shows
# up in the digest without a code change. The literals exist so the digest
# still renders on a database whose config snapshot predates a fund rename.
FUND_SHORT = {
    "northstar": "Northstar",
    "dsw": "DSW",
    "outward": "Outward VC",
    "anticus": "Anticus",
}

FUND_LONG = {
    "northstar": "Northstar Ventures",
    "dsw": "DSW Ventures",
    "outward": "Outward VC",
    "anticus": "Anticus Partners",
}

# vehicle_key -> (label, cheque_min_gbp, cheque_max_gbp)
VEHICLES: dict[str, tuple[str, int | None, int | None]] = {
    "spinout_inspire": ("Spinout Inspire Fund", 200_000, 750_000),
    "venture_sunderland": ("Venture Sunderland Fund", 200_000, 750_000),
    "ne_innovation_fund": ("NE Innovation Fund", 50_000, 500_000),
    "eis_growth": ("EIS Growth Fund", None, None),
    "ne_social": ("NE Social Investment Fund", 100_000, 1_000_000),
    "seis_fund": ("SEIS Fund", 50_000, 250_000),
    "eis_service": ("EIS Investment Service", 100_000, 1_000_000),
    "bbi_coinvest": ("BBI co-investment", None, None),
    "fund_ii": ("Fund II", 250_000, 2_500_000),
    "fy_seedcorn": ("FY Seedcorn Fund", 100_000, 1_500_000),
    "fy_growth": ("FY Growth Fund", 100_000, 1_500_000),
}

# Vocabulary that `str.title()` gets wrong.
PRETTY = {
    "ai_data": "AI / Data",
    "b2b_saas": "B2B SaaS",
    "vertical_saas": "Vertical SaaS",
    "deeptech": "Deep Tech",
    "life_sciences": "Life Sciences",
    "climate_tech": "Climate Tech",
    "healthy_ageing": "Healthy Ageing",
    "industrial_tech": "Industrial Tech",
    "fintech": "Fintech",
    "insurtech": "Insurtech",
    "regtech": "Regtech",
    "wealthtech": "Wealthtech",
    "north_east": "North East",
    "north_england": "North of England",
    "uk_wide": "UK",
    "uk_regions": "UK regions",
    "outside_golden_triangle": "outside the golden triangle",
    "pre_seed": "pre-seed",
    "series_a": "Series A",
    "seis_eis": "SEIS/EIS",
}

RULE = "━" * 23
DEFAULT_DIGEST_MAX = 10
SHORTLIST_TIER = "shortlist"

# Where the score line's number column starts. Wide enough for a 28-character
# name, narrow enough not to wrap on a phone.
_SCORE_COL = 34


# ----------------------------------------------------------------- primitives


def _tz():
    """Europe/London unless `TZ` says otherwise. Never raises."""
    name = os.environ.get("TZ") or "Europe/London"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a missing tzdb must not break the digest
        return None


def _now() -> datetime:
    tz = _tz()
    return datetime.now(tz) if tz else datetime.now()


def _today() -> date:
    return _now().date()


def _as_date(value) -> date | None:
    """Parse the handful of shapes the database actually holds. Never raises."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        d = _as_date(value)
        return datetime(d.year, d.month, d.day) if d else None


def _pretty(value) -> str:
    if value in (None, ""):
        return ""
    key = str(value).strip()
    if key in PRETTY:
        return PRETTY[key]
    return key.replace("_", " ").strip().title() if "_" in key else key


def _money(amount) -> str:
    """£450,000 — the full number, for the record view."""
    if amount is None:
        return ""
    return f"£{int(round(float(amount))):,}"


def _money_short(amount) -> str:
    """£200k / £2.5m — the compact form the digest quotes cheque ranges in."""
    if amount is None:
        return ""
    value = float(amount)
    if value >= 1_000_000:
        millions = value / 1_000_000
        return f"£{millions:.0f}m" if millions == int(millions) else f"£{millions:.1f}m"
    if value >= 1_000:
        thousands = value / 1_000
        return f"£{thousands:.0f}k" if thousands == int(thousands) else f"£{thousands:.1f}k"
    return f"£{value:.0f}"


def _months_old(incorporated_on, ref: date) -> int | None:
    start = _as_date(incorporated_on)
    if start is None:
        return None
    months = (ref.year - start.year) * 12 + (ref.month - start.month)
    if ref.day < start.day:
        months -= 1
    return max(months, 0)


def _age_phrase(months: int | None) -> str:
    if months is None:
        return "age unknown"
    if months < 1:
        return "under 1 month old"
    if months == 1:
        return "1 month old"
    if months < 24:
        return f"{months} months old"
    years = months // 12
    return f"{years} years old" if months % 12 else f"{years} years old"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _long_date(value) -> str:
    d = _as_date(value)
    return d.strftime("%-d %b %Y") if d else "—"


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;.") + "…"


def _host(company) -> str:
    """The clickable bit. Domain if we have one, else the URL without scheme."""
    domain = company["domain"] if "domain" in company.keys() else None
    if domain:
        return str(domain)
    url = company["website_url"] if "website_url" in company.keys() else None
    if not url:
        return ""
    return str(url).split("://", 1)[-1].rstrip("/")


# ------------------------------------------------------------------- settings


def _settings(db) -> dict:
    """Settings from the newest config snapshot, best-effort.

    The Settings tab is the source of truth (07-interfaces §1, Tab 6) and the
    pipeline stamps every validated config into `config_snapshot`. Reading it
    here rather than importing `radar.config` keeps the digest path free of
    Pydantic — and free of a hard dependency on a phase that may not be built.
    """
    try:
        raw = db.scalar(
            "SELECT config_json FROM config_snapshot "
            "ORDER BY is_last_good DESC, created_at DESC LIMIT 1"
        )
        if not raw:
            return {}
        cfg = json.loads(raw)
    except Exception:  # noqa: BLE001 - a malformed snapshot must not kill the digest
        return {}
    if not isinstance(cfg, dict):
        return {}
    settings = cfg.get("settings")
    return {**cfg, **settings} if isinstance(settings, dict) else cfg


def _setting(db, key: str, default):
    value = _settings(db).get(key)
    if value is None:
        value = db.get_meta(key)
    if value is None:
        return default
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def _fund_config(db) -> tuple[dict[str, str], dict[str, tuple[str, int | None, int | None]]]:
    """Live fund/vehicle labels when the config layer exists, literals otherwise."""
    funds = dict(FUND_SHORT)
    vehicles = dict(VEHICLES)
    cfg = _settings(db)
    for fund in (cfg.get("funds") or []):
        if not isinstance(fund, dict):
            continue
        key = fund.get("key") or fund.get("fund_key")
        if not key:
            continue
        funds[key] = fund.get("short_name") or fund.get("name") or funds.get(key, key)
        for vehicle in (fund.get("vehicles") or []):
            if not isinstance(vehicle, dict):
                continue
            vkey = vehicle.get("key") or vehicle.get("vehicle_key")
            if not vkey:
                continue
            vehicles[vkey] = (
                vehicle.get("name") or vehicles.get(vkey, (vkey, None, None))[0],
                vehicle.get("cheque_min"),
                vehicle.get("cheque_max"),
            )
    return funds, vehicles


def _fund_label(funds: dict[str, str], key) -> str:
    if not key:
        return "unrouted"
    return funds.get(str(key), _pretty(key))


def _vehicle_label(vehicles: dict, key) -> str:
    if not key:
        return ""
    label, lo, hi = vehicles.get(str(key), (_pretty(key), None, None))
    if lo is None and hi is None:
        return label
    if lo is not None and hi is not None:
        return f"{label} ({_money_short(lo)}–{_money_short(hi)})"
    return f"{label} ({_money_short(lo or hi)}+)"


# --------------------------------------------------------------------- reads

_ENTRY_SQL = """
    SELECT s.company_id      AS company_id,
           s.fund_key        AS fund_key,
           s.vehicle_key     AS vehicle_key,
           s.fund_fit_pct    AS fund_fit_pct,
           s.discovery_edge  AS discovery_edge,
           s.coverage        AS coverage,
           s.priority        AS priority,
           s.tier            AS tier,
           s.explanation     AS explanation,
           s.flags           AS flags,
           s.scored_at       AS scored_at,
           c.canonical_name  AS canonical_name,
           c.domain          AS domain,
           c.website_url     AS website_url,
           c.incorporated_on AS incorporated_on,
           c.hq_city         AS hq_city,
           c.hq_region       AS hq_region,
           c.sector          AS sector,
           c.stage           AS stage
      FROM score s
      JOIN company c ON c.id = s.company_id
     WHERE s.tier = ?
       AND c.merged_into IS NULL
       AND date(s.scored_at) BETWEEN ? AND ?
     ORDER BY s.priority DESC, c.canonical_name ASC
"""


def _shortlist(db, start: date, end: date) -> list[dict]:
    """One row per company — its best-scoring fund wins the digest slot."""
    seen: set[str] = set()
    out: list[dict] = []
    for row in db.query(_ENTRY_SQL, (SHORTLIST_TIER, start.isoformat(), end.isoformat())):
        if row["company_id"] in seen:
            continue
        seen.add(row["company_id"])
        out.append(dict(row))
    return out


def _funnel(db, start: date, end: date) -> dict | None:
    """Scanned → passed gates → shortlisted, summed over the window's runs."""
    row = db.one(
        """SELECT COUNT(*)                  AS runs,
                  SUM(items_fetched)        AS scanned,
                  SUM(gated_out)            AS gated_out,
                  SUM(shortlisted)          AS shortlisted
             FROM run
            WHERE date(started_at) BETWEEN ? AND ?
              AND status IN ('ok', 'partial')""",
        (start.isoformat(), end.isoformat()),
    )
    if row is None or not row["runs"]:
        return None
    scanned = int(row["scanned"] or 0)
    gated_out = int(row["gated_out"] or 0)
    return {
        "scanned": scanned,
        "gated_out": gated_out,
        # `gated_out` counts rejections, so what survived is the remainder.
        "passed": max(scanned - gated_out, 0),
        "shortlisted": int(row["shortlisted"] or 0),
    }


def _signals(db, company_id: str, limit: int = 3) -> list[dict]:
    rows = db.query(
        """SELECT kind, headline, detail, occurred_on, source_url
             FROM signal WHERE company_id = ?
            ORDER BY COALESCE(occurred_on, first_seen) DESC LIMIT ?""",
        (company_id, limit),
    )
    return [dict(r) for r in rows]


def _why_line(db, entry: dict) -> str:
    """The one evidence line under each company.

    ponytail: 07-interfaces §2 shows a signal-shaped line ("Durham spinout,
    SH01 filed 22 Jul, no press yet") while `score.explanation` is the canonical
    prose that the Sheet's Why column carries. Signals win when we have them —
    they are the specific, checkable thing — and the explanation is the fallback.
    """
    signals = _signals(db, entry["company_id"])
    if signals:
        return _truncate(", ".join(s["headline"] for s in signals if s["headline"]), 96)
    return _truncate(entry.get("explanation") or "", 96)


# -------------------------------------------------------------------- digest


def render_digest(db, period: str = "today", on_date: str | None = None) -> str:
    """The daily Telegram message.

    `period` is `today` or `week`; `on_date` (YYYY-MM-DD) anchors both, so a
    digest can be re-rendered for any past day without a clock trick.
    """
    end = _as_date(on_date) or _today()
    weekly = str(period).lower() == "week"
    start = end - timedelta(days=6) if weekly else end

    entries = _shortlist(db, start, end)
    funnel = _funnel(db, start, end)
    limit = max(int(_setting(db, "daily_digest_max", DEFAULT_DIGEST_MAX)), 1)

    title = (
        f"📡 UK Founder Radar — week to {end.strftime('%a %-d %b')}"
        if weekly
        else f"📡 UK Founder Radar — {end.strftime('%a %-d %b')}"
    )
    when = "this week" if weekly else "today"

    if not entries:
        return _zero_day(title, when, funnel)

    funds, vehicles = _fund_config(db)
    lines = [title, ""]

    if funnel:
        lines.append(
            f"Scanned {funnel['scanned']} → {funnel['passed']} passed gates "
            f"→ {len(entries)} shortlisted"
        )
    else:
        lines.append(f"{len(entries)} shortlisted {when}")

    ages = [m for m in (_months_old(e["incorporated_on"], end) for e in entries) if m is not None]
    median_age = _median(ages)
    if median_age is not None:
        label = "Median age this week" if weekly else "Median age today"
        shown = int(median_age) if float(median_age).is_integer() else round(median_age, 1)
        lines.append(f"{label}: {shown} months")

    lines.append("")
    lines.append(RULE)
    for index, entry in enumerate(entries[:limit], start=1):
        lines.extend(_entry_block(db, index, entry, end, funds, vehicles))
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append(RULE)

    remaining = len(entries) - min(limit, len(entries))
    if remaining > 0:
        lines.append(f"+{remaining} more in the sheet · /today for the full list")

    return "\n".join(lines)


def _zero_day(title: str, when: str, funnel: dict | None) -> str:
    """The message that stops a quiet day looking like a broken one."""
    lines = [title, "", f"0 shortlisted {when}."]
    if funnel is None:
        # Honest rather than reassuring: we cannot claim the filter worked if
        # we cannot show that anything ran. The heartbeat covers the rest.
        lines.append("No run has been recorded for this date.")
        lines.append("")
        lines.append("Check /status — if the last run is old, the scan did not fire.")
        return "\n".join(lines)

    lines.append(
        f"Scanned {funnel['scanned']} → {funnel['passed']} passed gates "
        f"→ none cleared the bar."
    )
    lines.append("")
    lines.append("That's the filter working, not a fault.")
    lines.append("Loosen it in Settings if you want more volume.")
    return "\n".join(lines)


def _entry_block(db, index: int, entry: dict, ref: date, funds, vehicles) -> list[str]:
    name = _truncate(entry["canonical_name"], 28)
    head = f"{index}. {name}"
    block = [f"{head.ljust(_SCORE_COL)}{round(float(entry['priority'] or 0)):>3}"]

    route = f"   → {_fund_label(funds, entry['fund_key'])}"
    vehicle = _vehicle_label(vehicles, entry["vehicle_key"])
    if vehicle:
        route += f" · {vehicle}"
    block.append(route)

    facts = [
        _pretty(entry["hq_city"] or entry["hq_region"]),
        _age_phrase(_months_old(entry["incorporated_on"], ref)),
        _pretty(entry["sector"]),
    ]
    block.append("   " + " · ".join(f for f in facts if f))

    why = _why_line(db, entry)
    if why:
        block.append(f"   {why}")

    host = _host(entry)
    if host:
        block.append(f"   🔗 {host}")
    return block


# -------------------------------------------------------------------- status

_STATUS_ICON = {"ok": "✅", "partial": "⚠️", "running": "⏳", "failed": "❌",
                "skipped": "⏭", "disabled": "⏸"}


def render_status(db) -> str:
    """Last run, source health, this month's AI cost (07-interfaces §2)."""
    lines = ["📡 Founder Radar — status", ""]

    last = db.one(
        "SELECT * FROM run WHERE status IN ('ok','partial') "
        "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1"
    )
    if last is None:
        lines.append("Last run: none recorded.")
    else:
        stamp = _as_datetime(last["finished_at"] or last["started_at"])
        icon = _STATUS_ICON.get(last["status"], "•")
        when = stamp.strftime("%a %-d %b %H:%M") if stamp else "—"
        lines.append(f"Last run  {icon} {last['status']} · {when} · {_ago(stamp)}")
        lines.append(
            f"          {int(last['items_fetched'] or 0)} scanned · "
            f"{int(last['gated_out'] or 0)} gated out · "
            f"{int(last['shortlisted'] or 0)} shortlisted"
        )
        lines.append(
            f"          AI {int(last['llm_calls'] or 0)} calls · "
            f"${float(last['llm_cost_usd'] or 0):.2f}"
        )
        if last["error"]:
            lines.append(f"          ⚠️ {_truncate(last['error'], 90)}")

    lines.append("")
    lines.append("Sources")
    sources = db.query(
        """SELECT rs.source_key AS source_key, rs.status AS status,
                  rs.items AS items, rs.error AS error
             FROM run_source rs
            WHERE rs.run_id = (SELECT MAX(id) FROM run)
            ORDER BY rs.source_key"""
    )
    if not sources:
        lines.append("  (no source activity recorded)")
    for row in sources:
        icon = _STATUS_ICON.get(row["status"], "•")
        line = f"  {icon} {row['source_key']}  {int(row['items'] or 0)}"
        if row["error"]:
            line += f" — {_truncate(row['error'], 60)}"
        lines.append(line)

    month = _now().strftime("%Y-%m")
    spend = db.scalar(
        "SELECT ROUND(SUM(cost_usd), 2) FROM llm_cache "
        "WHERE strftime('%Y-%m', created_at) = ?",
        (month,),
    )
    lines.append("")
    lines.append(f"AI cost {month}  ${float(spend or 0):.2f}")

    companies = db.scalar("SELECT COUNT(*) FROM company WHERE merged_into IS NULL") or 0
    shortlisted = db.scalar(
        "SELECT COUNT(DISTINCT company_id) FROM score WHERE tier = ?", (SHORTLIST_TIER,)
    ) or 0
    lines.append(f"Database  {companies:,} companies · {shortlisted:,} ever shortlisted")
    return "\n".join(lines)


def _ago(stamp: datetime | None) -> str:
    if stamp is None:
        return "—"
    now = _now()
    if stamp.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif stamp.tzinfo is not None and now.tzinfo is None:
        stamp = stamp.replace(tzinfo=None)
    delta = now - stamp
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{max(int(delta.total_seconds() // 60), 0)}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


# ---------------------------------------------------------------------- show


def _find_company(db, name: str) -> list[dict]:
    needle = " ".join(str(name or "").split()).lower()
    if not needle:
        return []
    rows = db.query(
        """SELECT * FROM company
            WHERE merged_into IS NULL
              AND (LOWER(canonical_name) = ?
                   OR norm_key = ?
                   OR LOWER(canonical_name) LIKE ?)
            ORDER BY CASE WHEN LOWER(canonical_name) = ? THEN 0 ELSE 1 END,
                     LENGTH(canonical_name) ASC
            LIMIT 10""",
        (needle, needle.replace(" ", ""), f"%{needle}%", needle),
    )
    return [dict(r) for r in rows]


def render_show(db, name: str) -> str:
    """Full record, signals and score breakdown for one company."""
    matches = _find_company(db, name)
    if not matches:
        return f'No company matching "{name}".'
    if len(matches) > 1 and str(matches[0]["canonical_name"]).lower() != str(name).strip().lower():
        listed = "\n".join(f"  · {m['canonical_name']}" for m in matches)
        return f'{len(matches)} companies match "{name}":\n{listed}\n\nAsk again with a full name.'

    company = matches[0]
    funds, vehicles = _fund_config(db)
    ref = _today()
    months = _months_old(company["incorporated_on"], ref)

    lines = [str(company["canonical_name"]), ""]

    def field(label: str, value: str) -> None:
        if value:
            lines.append(f"  {label.ljust(13)}{value}")

    field("Website", _host(company))
    field(
        "Incorporated",
        f"{_long_date(company['incorporated_on'])} ({_age_phrase(months)})"
        if company["incorporated_on"]
        else "unknown — cannot shortlist",
    )
    field("Location", " · ".join(
        p for p in (_pretty(company["hq_city"]), _pretty(company["hq_region"]),
                    company["hq_postcode"] or "") if p))
    field("Sector", _pretty(company["sector"]))
    field("Stage", _pretty(company["stage"]))
    field("Funding", _money(company["total_funding_gbp"])
          if company["total_funding_gbp"] is not None else "unknown")
    field("Signals", _pretty(company["founder_signal"]))
    field("Traction", _pretty(company["traction_signal"]))
    field("Discovery", _pretty(company["discovery_route"]))
    field("Companies Ho", str(company["companies_house_no"] or ""))

    founders = db.query(
        "SELECT name, role FROM founder WHERE company_id = ? ORDER BY name", (company["id"],)
    )
    if founders:
        field("Founders", ", ".join(
            f["name"] + (f" ({f['role']})" if f["role"] else "") for f in founders))

    signals = _signals(db, company["id"], limit=12)
    if signals:
        lines.append("")
        lines.append("SIGNALS")
        for signal in signals:
            when = _long_date(signal["occurred_on"]) if signal["occurred_on"] else "—"
            lines.append(f"  {when.rjust(11)}  {signal['kind']}  {signal['headline']}")

    scores = db.query(
        "SELECT * FROM score WHERE company_id = ? ORDER BY priority DESC", (company["id"],)
    )
    if not scores:
        lines.append("")
        lines.append("No scores yet — this company has not been through scoring.")
        return "\n".join(lines)

    lines.append("")
    lines.append("SCORES")
    for score in scores:
        header = f"  {_fund_label(funds, score['fund_key']).upper()}"
        vehicle = _vehicle_label(vehicles, score["vehicle_key"])
        if vehicle:
            header += f" · {vehicle}"
        lines.append("")
        lines.append(f"{header}   {str(score['tier']).upper()}")
        lines.append(
            f"    fit {float(score['fund_fit_pct']):.1f} · "
            f"edge {float(score['discovery_edge']):.1f} · "
            f"coverage {float(score['coverage']):.2f} · "
            f"priority {float(score['priority']):.1f}"
        )
        for component in db.query(
            "SELECT key, label, sub_score, weight, contribution, evidence "
            "FROM score_component WHERE score_id = ? ORDER BY key", (score["id"],)
        ):
            sub = "unknown" if component["sub_score"] is None else f"{float(component['sub_score']):.2f}"
            earned = "—" if component["contribution"] is None else f"{float(component['contribution']):.2f}"
            lines.append(
                f"    {str(component['key']).ljust(10)} "
                f"{_truncate(component['label'], 24).ljust(25)} "
                f"{sub.rjust(7)} × {float(component['weight']):.0f} = {earned.rjust(5)}"
            )
        if score["reject_reason"]:
            lines.append(f"    rejected: {score['reject_reason']}")
        if score["flags"]:
            lines.append(f"    flags: {_flags(score['flags'])}")
        if score["explanation"]:
            lines.append(f"    {_truncate(score['explanation'], 220)}")
    return "\n".join(lines)


def _flags(raw) -> str:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    if isinstance(parsed, list):
        return ", ".join(str(f) for f in parsed)
    return str(parsed)


# ---------------------------------------------------------------------- fund


def render_fund(db, fund_key: str, top: int = 10) -> str:
    """Top current matches for one fund — `/fund northstar`."""
    funds, vehicles = _fund_config(db)
    key = str(fund_key or "").strip().lower()
    label = FUND_LONG.get(key, _fund_label(funds, key))

    known = {r["fund_key"] for r in db.query("SELECT DISTINCT fund_key FROM score")}
    if key not in known and key not in funds:
        available = ", ".join(sorted(funds)) or "none"
        return f'No fund called "{fund_key}". Try: {available}.'

    rows = db.query(
        """SELECT s.company_id AS company_id, s.vehicle_key AS vehicle_key,
                  s.fund_fit_pct AS fund_fit_pct, s.discovery_edge AS discovery_edge,
                  s.priority AS priority, s.tier AS tier, s.explanation AS explanation,
                  c.canonical_name AS canonical_name, c.domain AS domain,
                  c.website_url AS website_url, c.incorporated_on AS incorporated_on,
                  c.hq_city AS hq_city, c.hq_region AS hq_region, c.sector AS sector
             FROM score s
             JOIN company c ON c.id = s.company_id
            WHERE s.fund_key = ?
              AND s.tier IN ('shortlist', 'watchlist')
              AND c.merged_into IS NULL
            ORDER BY s.priority DESC, c.canonical_name ASC""",
        (key,),
    )

    seen: set[str] = set()
    entries: list[dict] = []
    for row in rows:
        if row["company_id"] in seen:
            continue
        seen.add(row["company_id"])
        entries.append(dict(row))
        if len(entries) >= max(int(top), 1):
            break

    if not entries:
        return f"📡 {label}\n\nNo current matches. Nothing has cleared the bar for this fund yet."

    ref = _today()
    lines = [f"📡 {label} — top {len(entries)}", "", RULE]
    for index, entry in enumerate(entries, start=1):
        name = _truncate(entry["canonical_name"], 28)
        head = f"{index}. {name}"
        lines.append(f"{head.ljust(_SCORE_COL)}{round(float(entry['priority'] or 0)):>3}")
        detail = f"   {str(entry['tier'])}"
        vehicle = _vehicle_label(vehicles, entry["vehicle_key"])
        if vehicle:
            detail += f" · {vehicle}"
        lines.append(detail)
        facts = [
            _pretty(entry["hq_city"] or entry["hq_region"]),
            _age_phrase(_months_old(entry["incorporated_on"], ref)),
            _pretty(entry["sector"]),
        ]
        lines.append("   " + " · ".join(f for f in facts if f))
        host = _host(entry)
        if host:
            lines.append(f"   🔗 {host}")
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append(RULE)
    return "\n".join(lines)


__all__ = ["render_digest", "render_status", "render_show", "render_fund"]
