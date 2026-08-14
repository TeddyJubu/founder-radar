"""Today screen prototype — a read layer over the real database.

Not part of the shipped system. `radar/` does not import it and it is not in
the package; it exists so the interface can be judged against real rows
instead of a mockup.

Two rules it inherits from 02-architecture §2, and they are the whole reason
this is thin:

* **It computes nothing.** Fit, edge, coverage, tier and the explanation
  sentence are read straight out of `score`. The moment a surface recomputes a
  number, the number has two sources of truth and the scoring stops being
  defensible.
* **One write path.** Verdicts go to `user_field`, the same table the Google
  Sheet writes and `radar/score/tune.py` reads — while a separate daily marker
  controls the review queue. Marking a company here improves the threshold
  sweep, and nothing else in the database is touched.

Stdlib only: no FastAPI, no npm, no build step.

    .venv/bin/python prototype/server.py --db /tmp/demo.db --port 8787
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent

# Tiers a human is asked to look at. `reject` never reaches Today: the gates
# already decided, and re-litigating them is what the Companies tab is for.
REVIEWABLE = ("shortlist", "watchlist")


def _is_http_url(value: str | None) -> bool:
    """Only real web URLs count as provenance links on Today."""
    if not value:
        return False
    parsed = urlsplit(str(value).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _conn(db_path: str) -> sqlite3.Connection:
    # The prototype is often pointed at an existing demo DB rather than
    # started through the CLI. Apply the normal schema/migrations first so a
    # newly added daily-review table exists before the first page load.
    if db_path != ":memory:":
        from radar.store.db import Db

        bootstrap = Db(db_path)
        try:
            bootstrap.migrate()
        finally:
            bootstrap.close()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # The daily run writes the same file (WAL). Busy-wait instead of failing
    # when a poll lands mid-write.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _config() -> Any:
    """The seeded fund configuration — the same object scoring gates on."""
    from radar.config.defaults import default_config

    return default_config()


def _vehicles() -> dict[str, dict]:
    """`vehicle_key -> display name and cheque range`, from the seeded config.

    The score table stores the key, not the label. Resolving it here rather
    than denormalising into the row keeps the config the single owner of what
    a vehicle is called.
    """
    out: dict[str, dict] = {}
    for fund in _config().funds:
        for v in fund.vehicles:
            out[v.vehicle_key] = {
                "name": v.vehicle_name,
                "fund": fund.name,
                "cheque_min": v.cheque_min,
                "cheque_max": v.cheque_max,
            }
    return out


# ------------------------------------------------- the onboarding fund rules
#
# The onboarding page used to state each fund's hard rules in hand-written
# prose, and twice it was wrong: it called Outward's *company* government-backed
# when it is the fund that takes British Business Bank money, dropped Outward's
# £5m round cap, and said Northstar "must be in North East England" when its
# EIS Growth Fund is only a SOFT preference across the north.
#
# Prose beside a rule table drifts from it, and nothing catches the drift. So
# the page no longer contains any fund rule: it carries a placeholder, and every
# sentence below is derived from the config those rules are actually scored
# from. A rule that changes in the sheet changes here in the same breath, and a
# claim the config cannot support is now unwriteable rather than merely wrong.

# Full phrases including their preposition: `outside_golden_triangle` is not a
# place you are "in", and "Must be in outside London" is how that reads if the
# preposition is bolted on by the caller.
GEO_LABEL = {
    "north_east": "in North East England",
    "north_england": "in the north of England",
    "sunderland": "in the Sunderland city region",
    "yorkshire": "in Yorkshire",
    "uk_wide": "in the UK",
    "uk_regions": "in the UK regions",
    "outside_golden_triangle": "outside London, Oxford and Cambridge",
}

NUMBER_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _money_short(value: float) -> str:
    """£5m / £750k / £250,000 — the form a person reads, not the stored float."""
    v = float(value)
    if v >= 1_000_000:
        m = v / 1_000_000
        return f"£{m:.0f}m" if m == int(m) else f"£{m:.1f}m"
    if v >= 1_000:
        k = v / 1_000
        return f"£{k:.0f}k" if k == int(k) else f"£{k:.1f}k"
    return f"£{v:,.0f}"


def _join(parts: list[str], word: str = "or") -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" {word} " + parts[-1]


def _geo_label(values: list[str], *, bare: bool = False) -> str:
    """"in North East England or the Sunderland city region".

    `bare` drops the leading preposition, for verbs that supply their own
    ("prefers the UK regions", not "prefers in the UK regions"). The
    preposition also only leads — repeating it on every branch of an `or`
    reads as a list of separate requirements rather than alternatives.
    """
    labels = [GEO_LABEL.get(v, "in " + str(v).replace("_", " ")) for v in values]
    if not labels:
        return "anywhere"
    trimmed = [labels[0]] + [re.sub(r"^in ", "", x) for x in labels[1:]]
    if bare:
        trimmed[0] = re.sub(r"^in ", "", trimmed[0])
    return _join(trimmed)


def _reject_phrase(key: str, value: Any) -> str | None:
    """One hard-reject key, in words. Unknown keys are skipped, never guessed.

    Mirrors `HARD_REJECT_KEYS` in radar/config/models.py. A key added there and
    not here simply does not appear, which is a silent omission — so
    `test_every_hard_reject_key_has_a_phrase` fails the build instead.
    """
    if key == "round_max":
        return f"the round must be under {_money_short(value)}"
    if key == "prior_total_max":
        return f"it must not have raised more than {_money_short(value)} before"
    if key == "valuation_max":
        return f"it must be valued under {_money_short(value)}"
    if key == "uk_exec_pct_min":
        return f"at least {int(value)}% of the executive team must be UK tax resident"
    if key == "university_spinout_required":
        return f"it must be a spinout from {_join([str(u).title() for u in (value or ())])}"
    if key == "beyond_research_stage":
        return "it must be past pure research" if value else None
    if key == "requires_seis_eis":
        return "it must qualify for SEIS/EIS tax relief" if value else None
    if key == "max_age_years":
        return f"it must be under {int(value)} years old"
    return None


def _scope_phrase(n: int, total: int) -> str:
    """"Every fund" vs "one of its four" — the distinction the prose lost.

    Northstar's page line was wrong precisely because it promoted a rule that
    holds for three of its vehicles into one that holds for all four. Returns
    "" for a single-vehicle fund, where there is no distinction to draw and a
    prefix would only add noise.
    """
    if total == 1:
        return ""
    if n == total:
        return "every fund: "
    return f"{NUMBER_WORD.get(n, n)} of its {NUMBER_WORD.get(total, total)}: "


def fund_rules(cfg: Any) -> list[dict]:
    """Per fund: the hard rules its ACTIVE vehicles actually impose.

    Inactive vehicles are excluded because they are never scored — listing
    `bbi_coinvest`'s terms would describe money that cannot be deployed.
    """
    out: list[dict] = []
    for fund in cfg.funds:
        vehicles = fund.active_vehicles
        if not vehicles:
            continue
        total = len(vehicles)
        rules: list[str] = []

        # --- geography, stated at the strength the config actually gives it
        hard = [v for v in vehicles if v.geo_rule == "HARD"]
        soft = [v for v in vehicles if v.geo_rule != "HARD"]
        if hard:
            places = _geo_label(sorted({g for v in hard for g in v.geo_values}))
            if soft:
                soft_places = _geo_label(
                    sorted({g for v in soft for g in v.geo_values}), bare=True)
                names = _join([v.vehicle_name for v in soft], "and")
                rules.append(
                    f"must be {places} — except {names}, which only prefers "
                    f"{soft_places}")
            else:
                rules.append(f"must be {places}")
        elif soft:
            bare = _geo_label(sorted({g for v in soft for g in v.geo_values}), bare=True)
            rules.append(f"prefers {bare}, but does not require it")

        # --- hard rejects and age caps, grouped by (key, VALUE)
        #
        # Grouping by key alone was itself a drift bug: DSW's two vehicles both
        # cap age, at 3 years and at 7, and reporting the first (or the lowest)
        # under "every fund" states a limit the EIS Investment Service does not
        # have. Two different values are two different rules.
        counts: dict[tuple[str, Any], int] = {}
        for v in vehicles:
            rejects = dict(v.hard_rejects or {})
            if v.max_age_years:
                rejects["max_age_years"] = v.max_age_years
            for key, value in rejects.items():
                frozen = tuple(value) if isinstance(value, list) else value
                counts[(key, frozen)] = counts.get((key, frozen), 0) + 1

        for (key, value), n in sorted(counts.items(), key=lambda kv: str(kv[0])):
            phrase = _reject_phrase(key, value)
            if phrase:
                rules.append(f"{_scope_phrase(n, total)}{phrase}")

        out.append({
            "key": fund.key,
            "name": fund.name,
            # Built lower-case so a scope prefix can lead ("one of its two:
            # …"); capitalised once, here, where they become sentences.
            "rules": [r[:1].upper() + r[1:] for r in rules],
            # The config's own editorial line. Written by whoever owns the rules,
            # so it cannot drift away from them the way the page's prose did.
            "flavour": next((v.one_liner for v in vehicles if v.one_liner), ""),
        })
    return out


def render_fund_rows(cfg: Any) -> str:
    """The `.fund-row` markup the onboarding placeholder is replaced with."""
    from html import escape

    rows = []
    for fund in fund_rules(cfg):
        # Escape the derived text FIRST, then add markup — the other order
        # escapes the <em> this function itself wrote.
        body = escape(". ".join(fund["rules"]))
        if body:
            body += "."
        if fund["flavour"]:
            body += f' <em>{escape(fund["flavour"])}</em>'
        rows.append(
            f'<div class="fund-row" data-testid="fund-row" '
            f'data-fund="{escape(fund["key"])}">'
            f'<span class="nm">{escape(fund["name"])}</span>'
            f'<span class="rule">{body}</span></div>'
        )
    return "\n      ".join(rows)


# ------------------------------------------------------------ the kept list
#
# "If I see a company I like and want to keep it, where does that go?" — until
# now, nowhere he could look at: the verdict landed in `user_field` and the
# only view of it was column Z of the spreadsheet he would rather not open.
#
# Kept means a verdict, not a tier. `tier = 'shortlist'` is the scoring engine's
# opinion and changes every time thresholds move; a verdict is his, and does
# not.

KEPT_VERDICTS = ("worth contacting", "unsure")


def _short_date(stamp: str | None) -> str:
    """`2026-08-11T09:12:00` → `11 Aug`. Blank rather than a guess."""
    if not stamp:
        return ""
    try:
        return datetime.fromisoformat(str(stamp)[:19]).strftime("%-d %b")
    except ValueError:
        return ""


def build_kept(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Every company carrying a verdict, newest decision first, by verdict.

    Reads `user_field` — the same table the sheet's column Z mirrors — so a
    decision made here and one typed into the sheet are the same decision.
    """
    rows = conn.execute(
        """SELECT u.company_id, u.value AS verdict, u.updated_at,
                  c.canonical_name, c.one_liner, c.domain, c.website_url,
                  c.hq_city, c.hq_region, c.sector, c.incorporated_on
             FROM user_field u
             JOIN company c ON c.id = u.company_id
            WHERE u.field = 'verdict' AND c.merged_into IS NULL
            ORDER BY u.updated_at DESC, c.canonical_name""").fetchall()

    vehicles = _vehicles()
    out: dict[str, list[dict]] = {v: [] for v in KEPT_VERDICTS}
    for r in rows:
        verdict = (r["verdict"] or "").strip().lower()
        if verdict not in out:
            continue                       # "not for me" is the point of saying it
        best = conn.execute(
            """SELECT fund_key, vehicle_key, fund_fit_pct, discovery_edge, priority
                 FROM score WHERE company_id = ? AND tier != 'reject'
                ORDER BY priority DESC LIMIT 1""", (r["company_id"],)).fetchone()
        vehicle = vehicles.get((best["vehicle_key"] or "") if best else "", {})
        source = conn.execute(
            "SELECT source_url FROM company_source WHERE company_id = ? "
            "ORDER BY first_seen DESC LIMIT 1", (r["company_id"],)).fetchone()
        out[verdict].append({
            "company_id": r["company_id"],
            "name": r["canonical_name"],
            "one_liner": r["one_liner"],
            "where": r["hq_city"] or (r["hq_region"] or "").replace("_", " "),
            "sector": (r["sector"] or "").replace("_", " "),
            "website": r["website_url"] or (f"https://{r['domain']}" if r["domain"] else ""),
            "source": source["source_url"] if source else "",
            "fund": (vehicle.get("fund") or (best["fund_key"] if best else "")) or "",
            "vehicle": vehicle.get("name") or ((best["vehicle_key"] or "") if best else ""),
            "fit": best["fund_fit_pct"] if best else None,
            "edge": best["discovery_edge"] if best else None,
            "decided": _short_date(r["updated_at"]),
        })
    return out


def kept_count(conn: sqlite3.Connection) -> int:
    """How many companies sit on Kept — badge on Today, total on this page.

    Kept is a *verdict* (`worth contacting` / `unsure`), not the engine's
    `tier = 'shortlist'`. Count only those two so the badge never quietly
    includes a "not for me".
    """
    placeholders = ",".join("?" * len(KEPT_VERDICTS))
    row = conn.execute(
        f"""SELECT COUNT(*) AS n
              FROM user_field u
              JOIN company c ON c.id = u.company_id
             WHERE u.field = 'verdict' AND c.merged_into IS NULL
               AND lower(u.value) IN ({placeholders})""",
        KEPT_VERDICTS,
    ).fetchone()
    return int(row["n"] if row else 0)


def _today_review_date() -> str:
    """The calendar key for the local Today review session."""
    return date.today().isoformat()


def _eligible_today_company_ids(conn: sqlite3.Connection) -> set[str]:
    """Companies that can actually appear on Today, before daily decisions.

    Keep this count aligned with the queue's hard gates and provenance guard so
    the completed state can distinguish "nothing surfaced" from "everything
    surfaced has been reviewed".
    """
    rows = conn.execute(
        """SELECT DISTINCT c.id, cs.source_url
             FROM company c
             JOIN score s ON s.company_id = c.id
             JOIN company_source cs ON cs.company_id = c.id
            WHERE s.tier IN (?, ?)
              AND c.merged_into IS NULL
              AND c.incorporated_on IS NOT NULL
              AND (cs.source_url LIKE 'http://%' OR
                   cs.source_url LIKE 'https://%')""",
        REVIEWABLE,
    )
    return {row["id"] for row in rows if _is_http_url(row["source_url"])}


def reset_daily_review(conn: sqlite3.Connection, review_date: str | None = None) -> int:
    """Allow an explicit second pass without deleting lasting verdicts."""
    day = review_date or _today_review_date()
    cur = conn.execute("DELETE FROM daily_review WHERE review_date = ?", (day,))
    conn.commit()
    return cur.rowcount


def render_kept_intro(conn: sqlite3.Connection) -> str:
    """Where the list lives, and that the sheet is an optional mirror."""
    from html import escape

    n = kept_count(conn)
    return (
        f'<p class="kept-intro" data-testid="kept-intro">'
        f'<span class="count" data-testid="kept-total">{escape(str(n))}</span> '
        f'saved in this app. The Google Sheet&rsquo;s Verdict column is an '
        f'optional mirror after sync &mdash; not the primary list. '
        f'<a class="lnk" href="/help" data-testid="nav-help">How the parts connect</a>.'
        f'</p>'
    )


def render_kept_rows(conn: sqlite3.Connection) -> str:
    """The markup `<!--KEPT_ROWS-->` is replaced with."""
    from html import escape

    kept = build_kept(conn)
    if not any(kept.values()):
        return (
            '<p class="empty" data-testid="kept-empty">'
            'Nothing kept yet. On <a class="lnk" href="/">Today</a>, press '
            '<b>1</b> (Worth contacting) or <b>2</b> (Unsure) &mdash; the '
            'company lands here immediately and stays through the next daily run.'
            '</p>'
        )

    blocks: list[str] = []
    for verdict in KEPT_VERDICTS:
        items = kept[verdict]
        if not items:
            continue
        rows = []
        for c in items:
            facts = " · ".join(f for f in (c["where"], c["sector"]) if f)
            score = (f'{c["fit"]:.0f} / {c["edge"]:.0f}'
                     if c["fit"] is not None and c["edge"] is not None else "")
            links = "".join(
                f'<a class="lnk" href="{escape(url)}" target="_blank" '
                f'rel="noopener" data-testid="kept-{kind}">{label} ↗</a>'
                for kind, url, label in (("site", c["website"], "Site"),
                                         ("source", c["source"], "Source"))
                if url.startswith(("http://", "https://")))
            rows.append(
                f'<li class="kept-row" data-testid="kept-row" '
                f'data-company-id="{escape(c["company_id"])}" '
                f'data-verdict="{escape(verdict)}">'
                f'<div class="kept-main"><span class="kept-name">{escape(c["name"])}</span>'
                f'<span class="kept-desc">{escape(c["one_liner"] or facts)}</span></div>'
                f'<div class="kept-meta"><span>{escape(c["fund"])}'
                f'{" · " + escape(c["vehicle"]) if c["vehicle"] else ""}</span>'
                f'<span class="kept-score">{score}</span>'
                f'<span class="kept-when">{escape(c["decided"])}</span>{links}</div></li>')
        blocks.append(
            f'<section class="kept-group" data-testid="kept-group" '
            f'data-verdict="{escape(verdict)}">'
            f'<h2>{escape(verdict.capitalize())} '
            f'<span class="count" data-testid="kept-group-count">{len(items)}</span></h2>'
            f'<ul>{"".join(rows)}</ul></section>')
    return "\n".join(blocks)


def _age_phrase(incorporated_on: str | None, today: date) -> tuple[str, str | None]:
    """Prose first, exact date on hover. The complaint was about age, so it
    reads as a sentence rather than as an ISO string in a table cell."""
    if not incorporated_on:
        return "incorporation date not confirmed", None
    try:
        d = datetime.fromisoformat(str(incorporated_on)[:10]).date()
    except ValueError:
        return "incorporation date not confirmed", None
    months = (today.year - d.year) * 12 + (today.month - d.month)
    exact = d.strftime("%-d %B %Y")
    if months < 1:
        return "incorporated this month", exact
    if months == 1:
        return "incorporated 1 month ago", exact
    if months < 24:
        return f"incorporated {months} months ago", exact
    return f"incorporated {months // 12} years ago", exact


def _fund_score_payload(
    conn: sqlite3.Connection,
    company_id: str,
    config: Any,
    vehicles: dict[str, dict],
) -> list[dict]:
    """Return the latest score for every configured fund, in config order.

    Today still chooses one primary route per company, but the reader needs
    the other three fund fits to spot overlap. Missing rows stay explicit as
    ``None`` rather than being mistaken for a zero match.
    """
    latest: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        """SELECT id, fund_key, vehicle_key, fund_fit_pct, coverage, tier,
                         reject_reason, scored_at
                  FROM score
                 WHERE company_id = ?
                 ORDER BY scored_at DESC, id DESC""",
        (company_id,),
    ):
        latest.setdefault(row["fund_key"], row)

    scores: list[dict] = []
    for fund in config.funds:
        row = latest.get(fund.key)
        vehicle = vehicles.get(row["vehicle_key"] or "", {}) if row else {}
        scores.append({
            "fund_key": fund.key,
            "fund_name": fund.name,
            "fit": row["fund_fit_pct"] if row else None,
            "coverage": row["coverage"] if row else None,
            "tier": row["tier"] if row else "unscored",
            "reject_reason": row["reject_reason"] if row else None,
            "vehicle_key": row["vehicle_key"] if row else None,
            "vehicle_name": vehicle.get("name") if row else None,
        })
    return scores


def build_today(conn: sqlite3.Connection, limit: int = 20) -> dict:
    config = _config()
    vehicles = _vehicles()
    today = date.today()
    review_date = today.isoformat()
    eligible_ids = _eligible_today_company_ids(conn)
    reviewed_ids = {
        row["company_id"] for row in conn.execute(
            "SELECT company_id FROM daily_review WHERE review_date = ?",
            (review_date,),
        ) if row["company_id"] in eligible_ids
    }

    # One row per company: the fund it fits best. The others become "also
    # fits", because the decision Aryan is making is "who do I send this to",
    # and four rows for one company is the version-1 complaint about scanning.
    rows = conn.execute(
        """SELECT s.id sid, s.company_id, s.fund_key, s.vehicle_key, s.tier,
                  s.fund_fit_pct, s.discovery_edge, s.coverage, s.priority,
                  s.explanation, s.flags,
                  c.canonical_name, c.domain, c.website_url, c.hq_city,
                  c.hq_region, c.incorporated_on, c.sector, c.stage,
                  c.one_liner, c.discovery_route, c.companies_house_no
             FROM score s
             JOIN company c ON c.id = s.company_id
             JOIN (SELECT company_id, MAX(priority) best
                     FROM score s2 JOIN company c2 ON c2.id = s2.company_id
                    WHERE s2.tier IN (?, ?) AND c2.incorporated_on IS NOT NULL
                    GROUP BY s2.company_id) t
               ON t.company_id = s.company_id AND t.best = s.priority
            WHERE s.tier IN (?, ?) AND c.merged_into IS NULL
              AND c.incorporated_on IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1 FROM daily_review dr
                     WHERE dr.company_id = c.id
                       AND dr.review_date = ?
              )
              AND EXISTS (
                    SELECT 1 FROM company_source cs
                     WHERE cs.company_id = c.id
                       AND (cs.source_url LIKE 'http://%' OR
                            cs.source_url LIKE 'https://%')
              )
            GROUP BY s.company_id
            -- Ties break on coverage: among companies the scoring cannot
            -- separate, review the one we actually know something about
            -- first. Age is a prerequisite for this surface; an unknown-age
            -- row stays in the research pool until enrichment verifies it.
            ORDER BY s.priority DESC, s.coverage DESC, c.canonical_name
            LIMIT ?""",
        (*REVIEWABLE, *REVIEWABLE, review_date, limit),
    ).fetchall()

    verdicts = {
        r["company_id"]: r["value"]
        for r in conn.execute(
            "SELECT company_id, value FROM user_field WHERE field = 'verdict'")
    }

    out = []
    for r in rows:
        vehicle = vehicles.get(r["vehicle_key"] or "", {})
        phrase, exact = _age_phrase(r["incorporated_on"], today)

        signals = [dict(s) for s in conn.execute(
            """SELECT kind, headline, source_url, occurred_on, source_key
                 FROM signal WHERE company_id = ?
                ORDER BY COALESCE(occurred_on, first_seen) DESC LIMIT 5""",
            (r["company_id"],))]

        # Where we met the company. `signal` only gets a row when the adapter
        # emitted a `kind_hint` we recognise, but `resolve_item` writes a
        # `company_source` row for *every* mention — so this is the reliable
        # home of the article URL, and the reason the card had no link to
        # anything: 20 of 20 companies on the queue have zero signal rows.
        sources = [dict(s) for s in conn.execute(
            """SELECT source_key, source_url, first_seen
                 FROM company_source WHERE company_id = ?
                ORDER BY first_seen DESC""",
            (r["company_id"],)) if _is_http_url(s["source_url"])]
        source = sources[0] if sources else None
        if source is None:
            # The SQL guard is intentionally cheap; this second check handles
            # malformed values such as `https://` without leaking a card that
            # cannot be verified by a human.
            continue

        components = [dict(c) for c in conn.execute(
            """SELECT key, label, sub_score, weight, contribution, evidence
                 FROM score_component WHERE score_id = ? ORDER BY contribution DESC""",
            (r["sid"],))]

        also = [dict(a) for a in conn.execute(
            """SELECT fund_key, tier, fund_fit_pct FROM score
                WHERE company_id = ? AND fund_key != ? AND tier != 'reject'
                ORDER BY fund_fit_pct DESC""",
            (r["company_id"], r["fund_key"]))]
        fund_scores = _fund_score_payload(conn, r["company_id"], config, vehicles)

        out.append({
            "company_id": r["company_id"],
            "name": r["canonical_name"],
            "domain": r["domain"],
            "website": r["website_url"],
            "city": r["hq_city"],
            "region": r["hq_region"],
            "sector": r["sector"],
            "stage": r["stage"],
            "one_liner": r["one_liner"],
            "route": r["discovery_route"],
            "ch_number": r["companies_house_no"],
            "source_url": source["source_url"],
            "source_key": source["source_key"],
            "age_phrase": phrase,
            "age_exact": exact,
            "fund": r["fund_key"],
            "fund_name": vehicle.get("fund"),
            "vehicle": vehicle.get("name") or r["vehicle_key"],
            "cheque_min": vehicle.get("cheque_min"),
            "cheque_max": vehicle.get("cheque_max"),
            "tier": r["tier"],
            "fit": r["fund_fit_pct"],
            "edge": r["discovery_edge"],
            "coverage": r["coverage"],
            "priority": r["priority"],
            "explanation": r["explanation"],
            "flags": json.loads(r["flags"]) if r["flags"] else [],
            "signals": signals,
            "sources": sources,
            "components": components,
            "also_fits": also,
            "fund_scores": fund_scores,
            "verdict": verdicts.get(r["company_id"]),
        })

    counts = dict(conn.execute(
        "SELECT tier, COUNT(*) FROM score GROUP BY tier").fetchall())
    run = conn.execute(
        "SELECT started_at, items_fetched, companies_new, shortlisted, status "
        "FROM run ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "date": today.isoformat(),
        "companies": out,
        "totals": {
            "reviewable": len(out),
            "total_reviewable": len(eligible_ids),
            "reviewed_today": len(reviewed_ids),
            "remaining": max(0, len(eligible_ids) - len(reviewed_ids)),
            "shortlist": counts.get("shortlist", 0),
            "watchlist": counts.get("watchlist", 0),
            "rejected": counts.get("reject", 0),
            "kept": kept_count(conn),
        },
        "run": dict(run) if run else None,
    }


def set_verdict(conn: sqlite3.Connection, company_id: str, verdict: str) -> int:
    """Record one lasting verdict and one dated Today-review decision.

    The lasting verdict uses the same table and field name the Sheet uses, so
    the two surfaces cannot disagree and `tune.py` picks it up unchanged. The
    dated marker is deliberately separate so Review Again never erases it.

    Returns the new Kept count so Today can refresh its badge without a second
    round-trip.
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO user_field(company_id, field, value, updated_at)
           VALUES (?, 'verdict', ?, ?)
           ON CONFLICT(company_id, field)
           DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (company_id, verdict, now),
    )
    conn.execute(
        """INSERT INTO daily_review(company_id, review_date, verdict, reviewed_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_id, review_date)
           DO UPDATE SET verdict = excluded.verdict,
                         reviewed_at = excluded.reviewed_at""",
        (company_id, _today_review_date(), verdict, now),
    )
    conn.commit()
    return kept_count(conn)


def make_handler(conn: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Browsers must never serve a stale shortlist: the run rewrites
            # the score table in place, so the same URL means different data
            # later in the day.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:          # noqa: N802 - stdlib naming
            if self.path in ("/", "/index.html"):
                self._send(200, (HERE / "index.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif self.path in ("/onboarding", "/onboarding.html"):
                # The fund rules are substituted here rather than fetched by
                # the page: onboarding.html has no JavaScript at all, and the
                # one thing on it that must never be wrong is a poor candidate
                # for the first thing that needs a script to appear.
                page = (HERE / "onboarding.html").read_text(encoding="utf-8")
                page = page.replace("<!--FUND_RULES-->",
                                    render_fund_rows(_config()))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path in ("/kept", "/kept.html"):
                page = (HERE / "kept.html").read_text(encoding="utf-8")
                page = page.replace("<!--KEPT_INTRO-->", render_kept_intro(conn))
                page = page.replace("<!--KEPT_ROWS-->", render_kept_rows(conn))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path in ("/help", "/help.html", "/ops"):
                self._send(200, (HERE / "help.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif self.path == "/tokens.css":
                self._send(200, (HERE / "tokens.css").read_bytes(),
                           "text/css; charset=utf-8")
            elif self.path.startswith("/api/today"):
                payload = json.dumps(build_today(conn), default=str).encode()
                self._send(200, payload, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:         # noqa: N802
            if self.path == "/api/review-again":
                n = reset_daily_review(conn)
                payload = json.dumps({"ok": True, "reset_count": n}).encode()
                self._send(200, payload, "application/json")
                return
            if not self.path.startswith("/api/verdict"):
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            company_id, verdict = body.get("company_id"), body.get("verdict")
            if not company_id or verdict not in (
                    "worth contacting", "not for me", "unsure"):
                self._send(400, b'{"error":"bad verdict"}', "application/json")
                return
            n = set_verdict(conn, company_id, verdict)
            payload = json.dumps({"ok": True, "kept_count": n}).encode()
            self._send(200, payload, "application/json")

        def log_message(self, *args) -> None:      # quiet
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/demo.db")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    conn = _conn(args.db)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(conn))
    print(f"Today prototype on http://127.0.0.1:{args.port}  (db: {args.db})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
