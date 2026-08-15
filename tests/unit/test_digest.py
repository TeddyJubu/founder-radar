"""The digest, in its three shapes — plus status, show and fund.

The zero day is the important one. A quiet day and a broken pipeline produce
the same silence, so the message has to distinguish them itself: it prints the
funnel and says, in words, that the filter worked. Everything else here exists
to keep that message honest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta

import pytest

from radar.render.digest import (
    render_digest,
    render_fund,
    render_show,
    render_status,
)

DAY = "2026-08-07"          # a Friday
QUIET = "2026-08-08"        # the Saturday after


# ----------------------------------------------------------------- seeding


def seed_company(db, name, *, cid=None, incorporated_on="2026-06-14", city="Newcastle",
                 region="north_east", sector="life_sciences", stage="pre_seed",
                 domain=None, funding=None, ch_no=None, postcode="NE1 4ST",
                 one_liner=None):
    cid = cid or f"C{abs(hash(name)) % 10**10:010d}"
    stamp = f"{DAY}T06:30:00Z"
    db.execute(
        """INSERT INTO company (id, canonical_name, norm_key, companies_house_no, domain,
                                website_url, incorporated_on, hq_postcode, hq_region, hq_city,
                                sector, stage, total_funding_gbp, discovery_route, one_liner,
                                first_seen, last_seen, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, name, name.lower().replace(" ", ""), ch_no, domain,
         f"https://{domain}" if domain else None, incorporated_on, postcode, region, city,
         sector, stage, funding, "registry", one_liner, stamp, stamp, stamp, stamp),
    )
    return cid


def seed_score(db, cid, *, fund="northstar", vehicle="spinout_inspire", tier="shortlist",
               priority=89.0, fit=88.0, edge=90.0, coverage=1.0, scored_on=DAY,
               explanation="Matches on geography, sector and founder signal.",
               flags=None, config_hash="cfg1"):
    db.execute(
        """INSERT INTO score (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                              discovery_edge, priority, tier, explanation, flags,
                              config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, fund, vehicle, fit, coverage, edge, priority, tier, explanation,
         json.dumps(flags) if flags else None, config_hash, "1",
         f"{scored_on}T06:34:00Z"),
    )
    return db.scalar("SELECT last_insert_rowid()")


# The five fund-fit components plus age, in the shape `score_component` holds
# them. `sub_score=None` means nobody could establish the fact — which is not
# the same as establishing that it is zero, and the digest must not blur them.
FIT_COMPONENTS = [
    ("geography", 1.0, "Yorkshire", 4.0),
    ("sector", 0.25, "B2B SaaS", 4.0),
    ("stage", 0.5, "Pre-seed", 3.0),
    ("founder_signal", None, "unknown", 3.0),
    ("traction_signal", None, "unknown", 2.0),
    ("age", 0.98, "1 month old", 30.0),
]


def seed_components(db, score_id, components=None):
    for key, sub, evidence, weight in (components or FIT_COMPONENTS):
        db.execute(
            """INSERT INTO score_component
                 (score_id, key, label, sub_score, weight, contribution, evidence)
               VALUES (?,?,?,?,?,?,?)""",
            (score_id, key, key.replace("_", " ").title(), sub, weight, None, evidence),
        )


def seed_run(db, *, on=DAY, scanned=412, gated_out=374, shortlisted=6, status="ok",
             llm_calls=120, cost=0.42, mode="daily"):
    db.execute(
        """INSERT INTO run (started_at, finished_at, mode, items_fetched, gated_out,
                            shortlisted, llm_calls, llm_cost_usd, status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (f"{on}T06:30:00Z", f"{on}T06:44:00Z", mode, scanned, gated_out, shortlisted,
         llm_calls, cost, status),
    )
    return db.scalar("SELECT last_insert_rowid()")


def seed_signal(db, cid, headline, *, kind="spinout", occurred_on="2026-07-28",
                url=None):
    db.execute(
        """INSERT INTO signal (company_id, kind, occurred_on, headline, source_key,
                               source_url, first_seen)
           VALUES (?,?,?,?,?,?,?)""",
        (cid, kind, occurred_on, headline, "northern_accelerator",
         url or f"https://example.org/{abs(hash(headline)) % 10**6}", f"{DAY}T06:30:00Z"),
    )


@pytest.fixture
def full_day(db):
    """Twelve shortlisted companies on 7 Aug — more than the digest cap."""
    seed_run(db, scanned=412, gated_out=374, shortlisted=12)
    kelvin = seed_company(db, "Kelvin Bio", incorporated_on="2026-06-14",
                          domain="kelvinbio.com")
    seed_signal(db, kelvin, "Durham spinout announced")
    seed_signal(db, kelvin, "SH01 filed 22 Jul", kind="sh01", occurred_on="2026-07-22")
    seed_score(db, kelvin, priority=89.0)

    ledgerly = seed_company(db, "Ledgerly", incorporated_on="2025-11-01",
                            city="London", region="uk_wide", sector="fintech",
                            domain="ledgerly.io", postcode="EC2A 1AA")
    seed_score(db, ledgerly, fund="outward", vehicle="fund_ii", priority=81.0)

    for n in range(10):
        cid = seed_company(db, f"Filler {n}", incorporated_on="2025-09-01")
        seed_score(db, cid, priority=70.0 - n)
    return db


# ------------------------------------------------------------- the full day


def test_full_day_shows_the_funnel_and_caps_the_list(full_day):
    text = render_digest(full_day, period="today", on_date=DAY)

    assert text.startswith("📡 UK Founder Radar — Fri 7 Aug")
    assert "Scanned 412 → 38 passed gates → 12 shortlisted" in text
    assert "Median age today:" in text
    # Ten shown, two held back — the cap is `daily_digest_max`.
    assert "\n1. Kelvin Bio" in text
    assert "10. Filler 7" in text
    assert "11. " not in text
    assert "+2 more in the sheet · /today for the full list" in text


def test_full_day_entry_carries_route_facts_evidence_and_link(full_day):
    text = render_digest(full_day, period="today", on_date=DAY)
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("1. Kelvin Bio"))
    block = lines[start:start + 5]

    assert block[0].rstrip().endswith("89")
    assert block[1] == "   → Northstar · Spinout Inspire Fund (£200k–£750k)"
    assert block[2] == "   Newcastle · 1 month old · Life Sciences"
    assert "SH01 filed 22 Jul" in block[3]
    assert block[4] == "   🔗 kelvinbio.com"


def test_the_ledger_marks_each_fund_rule_instead_of_listing_bare_facts(db):
    """The line under each company used to read `Newcastle · 1 month old ·
    Life Sciences` — three criteria printed as neutral facts when the engine
    had already judged every one. Same values, now each says how it counted."""
    cid = seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    seed_components(db, seed_score(db, cid))
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)

    assert "🟢 Location: Yorkshire" in text
    assert "🔴 Sector: B2B SaaS" in text
    assert "🟠 Stage: Pre-seed" in text
    assert "🟢 Age: 1 month old" in text
    # The bare facts line it replaced is gone, not printed alongside.
    assert "Newcastle · 1 month old" not in text


def test_the_ledger_never_renders_an_unknown_as_a_failure(db):
    """`sub_score = None` is "nobody could find out", not "it scored zero".
    The full-model percentage keeps the two facts distinct, and a digest that
    marks them alike undoes that in the one place the client actually reads
    each morning."""
    cid = seed_company(db, "Kelvin Bio")
    seed_components(db, seed_score(db, cid))
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)

    assert "⚪ Not known: founders, traction" in text
    for absent in ("🔴 Founders", "🔴 Traction", "🟢 Founders", "🟢 Traction"):
        assert absent not in text


def test_the_ledger_shows_what_matched_and_not_only_what_failed(db):
    """A company only reaches the digest by matching something. An earlier cut
    ranked failures first, which showed three red rows and hid the rule the
    company had qualified on — describing a different company than the one
    that was scored."""
    cid = seed_company(db, "Kelvin Bio")
    seed_components(db, seed_score(db, cid))
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)
    assert "🟢" in text, "a shortlisted company must show the rules it met"


def test_a_failed_rule_is_never_crowded_out_by_heavier_rules_that_passed(db):
    """`founder_signal` and `traction_signal` carry the lowest fit weights, so
    ranking purely by weight drops exactly those two. Here everything heavy
    passes and the light one fails: a top-three-by-weight would print four
    green rows and quietly lose the only reason to hesitate."""
    cid = seed_company(db, "Looks Perfect Ltd")
    seed_components(db, seed_score(db, cid), [
        ("geography", 1.0, "Newcastle", 4.0),
        ("sector", 1.0, "Deep Tech", 4.0),
        ("stage", 1.0, "Seed", 3.0),
        ("founder_signal", 1.0, "Durham spinout", 3.0),
        ("traction_signal", 0.0, "no customers found", 2.0),
        ("age", 1.0, "2 months old", 30.0),
    ])
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)
    assert "🔴 Traction: no customers found" in text
    assert "🟢" in text, "and the rules it passed still show"


def test_an_entry_without_components_keeps_its_facts_line(db):
    """Scores written before `score_component` was populated have nothing to
    mark. The entry falls back rather than losing its facts entirely."""
    cid = seed_company(db, "Old Score Ltd")
    seed_score(db, cid)                       # no components
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)
    assert "Newcastle · 1 month old · Life Sciences" in text


def test_the_explanation_is_not_repeated_underneath_its_own_ledger(db):
    """`score.explanation` is prose assembled from the same components the
    ledger just listed. A signal headline still shows — that is provenance,
    not a retelling."""
    cid = seed_company(db, "Kelvin Bio")
    seed_components(db, seed_score(db, cid))
    seed_run(db, on=DAY, shortlisted=1)

    text = render_digest(db, period="today", on_date=DAY)
    assert "Matches on geography, sector and founder signal." not in text

    seed_signal(db, cid, "SH01 filed 22 Jul")
    assert "SH01 filed 22 Jul" in render_digest(db, period="today", on_date=DAY)


def test_a_described_company_reads_as_a_company_not_as_an_article(db):
    """The client, 11 Aug: "I'm currently seeing articles rather than the
    actual companies themselves, so I still have to open and scan through
    them. Ideally I'd like the output to be the company itself, with the
    article just used as the source."

    So a company we read out of prose leads with `one_liner`. The article
    headline stays — "the article just used as the source" is a demotion, not
    a deletion — but it must come *below* the description, so the reader
    already knows what the company is before they meet the headline.
    """
    seed_run(db, shortlisted=1)
    cid = seed_company(db, "Loamweave", domain="loamweave.com",
                       one_liner="Turns brewery waste into packaging foam.")
    seed_score(db, cid, explanation="Sector and region match; no press yet.")
    seed_signal(db, cid, "Newcastle's Loamweave raises £900k pre-seed, UKTN reports",
                kind="funding_round")

    text = render_digest(db, period="today", on_date=DAY)
    lines = text.splitlines()

    assert "   Turns brewery waste into packaging foam." in text
    assert "UKTN reports" in text, "the article is the source — it is not removed"
    described = next(i for i, x in enumerate(lines) if "brewery waste" in x)
    article = next(i for i, x in enumerate(lines) if "UKTN reports" in x)
    assert described < article, "the company describes itself before the article speaks"


def test_a_registry_company_still_leads_with_its_filings(db):
    """The other half of the same rule: a Track B company is met at Companies
    House, where there is no prose to describe it. Signals are facts about the
    company there — "incorporated 11 May", "SH01 filed 22 Jul" — so they stay.
    """
    seed_run(db, shortlisted=1)
    cid = seed_company(db, "Quayside Robotics")
    seed_score(db, cid)
    seed_signal(db, cid, "SH01 filed 22 Jul", kind="share_issue")

    assert "SH01 filed 22 Jul" in render_digest(db, period="today", on_date=DAY)


def test_a_company_appears_once_even_when_several_funds_shortlist_it(db):
    seed_run(db, shortlisted=1)
    cid = seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    seed_score(db, cid, fund="northstar", priority=89.0)
    seed_score(db, cid, fund="dsw", vehicle="seis_fund", priority=71.0)

    text = render_digest(db, on_date=DAY)
    assert text.count("Kelvin Bio") == 1
    # The better fit wins the slot.
    assert "→ Northstar" in text and "→ DSW" not in text


def test_watchlist_and_reject_never_reach_the_digest(db):
    seed_run(db, shortlisted=0)
    seed_score(db, seed_company(db, "Nearly"), tier="watchlist", priority=68.0)
    seed_score(db, seed_company(db, "Nope"), tier="reject", priority=10.0)

    text = render_digest(db, on_date=DAY)
    assert "Nearly" not in text and "Nope" not in text
    assert "0 shortlisted today." in text


# ------------------------------------------------------------ the quiet day


def test_quiet_day_lists_what_there_is_and_promises_nothing_more(db):
    seed_run(db, scanned=180, gated_out=168, shortlisted=2)
    a = seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    seed_score(db, a, priority=89.0)
    b = seed_company(db, "Ledgerly", incorporated_on="2025-11-01", sector="fintech")
    seed_score(db, b, fund="outward", vehicle="fund_ii", priority=74.0)

    text = render_digest(db, on_date=DAY)

    assert "Scanned 180 → 12 passed gates → 2 shortlisted" in text
    assert "1. Kelvin Bio" in text and "2. Ledgerly" in text
    assert "more in the sheet" not in text


# ------------------------------------------------------------- the zero day


def test_zero_day_says_the_filter_worked_and_shows_its_working(db):
    """The message a client must be able to read without phoning anyone."""
    seed_run(db, on=QUIET, scanned=340, gated_out=318, shortlisted=0)

    text = render_digest(db, on_date=QUIET)

    assert text.startswith("📡 UK Founder Radar — Sat 8 Aug")
    assert "0 shortlisted today." in text
    assert "Scanned 340 → 22 passed gates → none cleared the bar." in text
    assert "That's the filter working, not a fault." in text
    assert "Loosen it in Settings if you want more volume." in text


def test_zero_day_without_a_run_does_not_claim_the_filter_worked(db):
    """No run row means we cannot honestly say anything was filtered."""
    text = render_digest(db, on_date=QUIET)

    assert "0 shortlisted today." in text
    assert "No run has been recorded for this date." in text
    assert "filter working" not in text
    assert "/status" in text


def test_a_failed_run_is_not_counted_as_a_scan(db):
    seed_run(db, on=QUIET, scanned=340, gated_out=318, shortlisted=0, status="failed")
    text = render_digest(db, on_date=QUIET)
    assert "No run has been recorded for this date." in text


# ------------------------------------------------------------------- period


def test_week_covers_seven_days_and_says_so(db):
    end = date.fromisoformat(DAY)
    seed_run(db, on=DAY, scanned=100, gated_out=90, shortlisted=1)
    seed_run(db, on=(end - timedelta(days=3)).isoformat(), scanned=100,
             gated_out=95, shortlisted=1)

    recent = seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    seed_score(db, recent, priority=89.0, scored_on=DAY)
    older = seed_company(db, "Ledgerly", incorporated_on="2025-11-01")
    seed_score(db, older, priority=80.0, scored_on=(end - timedelta(days=3)).isoformat())
    stale = seed_company(db, "Last Month")
    seed_score(db, stale, priority=95.0, scored_on=(end - timedelta(days=30)).isoformat())

    text = render_digest(db, period="week", on_date=DAY)

    assert text.startswith("📡 UK Founder Radar — week to Fri 7 Aug")
    assert "Scanned 200 → 15 passed gates → 2 shortlisted" in text
    assert "Kelvin Bio" in text and "Ledgerly" in text
    assert "Last Month" not in text
    assert "Median age this week:" in text


def test_week_with_nothing_says_this_week(db):
    seed_run(db, on=DAY, scanned=50, gated_out=50, shortlisted=0)
    assert "0 shortlisted this week." in render_digest(db, period="week", on_date=DAY)


def test_digest_cap_is_read_from_settings(db):
    seed_run(db, shortlisted=4)
    for n in range(4):
        seed_score(db, seed_company(db, f"Co {n}"), priority=90.0 - n)
    db.execute(
        "INSERT INTO config_snapshot (config_hash, config_json, is_last_good, created_at) "
        "VALUES (?,?,?,?)",
        ("cfg1", json.dumps({"settings": {"daily_digest_max": 2}}), 1, f"{DAY}T06:00:00Z"),
    )
    text = render_digest(db, on_date=DAY)
    assert "+2 more in the sheet" in text
    assert "3. " not in text


def test_unknown_incorporation_date_does_not_break_the_median(db):
    seed_run(db, shortlisted=1)
    cid = seed_company(db, "Ageless", incorporated_on=None)
    seed_score(db, cid, priority=88.0)
    text = render_digest(db, on_date=DAY)
    assert "age unknown" in text
    assert "Median age today" not in text


# ------------------------------------------------------------------- status


def test_status_reports_last_run_sources_and_spend(db):
    run_id = seed_run(db, scanned=412, gated_out=374, shortlisted=6, cost=0.42)
    for key, status, items, error in (
        ("companies_house", "ok", 412, None),
        ("conception_x", "skipped", 0, None),
        ("uktn", "failed", 0, "HTTP 503 from uktech.news"),
    ):
        db.execute(
            "INSERT INTO run_source (run_id, source_key, status, items, error) "
            "VALUES (?,?,?,?,?)", (run_id, key, status, items, error))

    from radar.render.digest import _now

    db.execute(
        "INSERT INTO llm_cache (key, response_json, cost_usd, created_at) VALUES (?,?,?,?)",
        ("k1", "{}", 2.91, _now().strftime("%Y-%m-%dT%H:%M:%SZ")),
    )

    text = render_status(db)

    assert "Last run" in text and "ok" in text
    assert "412 scanned · 374 gated out · 6 shortlisted" in text
    assert "AI 120 calls · $0.42" in text
    assert "✅ companies_house  412" in text
    assert "❌ uktn" in text and "HTTP 503" in text
    assert "$2.91" in text


def test_status_on_an_empty_database_says_so_rather_than_crashing(db):
    text = render_status(db)
    assert "Last run: none recorded." in text
    assert "(no source activity recorded)" in text


# --------------------------------------------------------------------- show


def test_show_prints_the_record_its_signals_and_the_breakdown(db):
    cid = seed_company(db, "METzero Technologies", incorporated_on="2024-03-11",
                       sector="climate_tech", stage="seed", funding=450000.0,
                       domain="metzero.com", ch_no="SC123456")
    seed_signal(db, cid, "Northern Accelerator spinout announcement")
    score_id = seed_score(db, cid, tier="watchlist", fit=92.2, edge=51.0, priority=75.7,
                          explanation="good fit but likely already on their radar")
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (score_id, "sector", "climate_tech", 1.0, 4, 4.0, "spinout page"),
    )
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (score_id, "traction", "unknown", None, 2, None, "no source"),
    )

    text = render_show(db, "METzero Technologies")

    assert text.startswith("METzero Technologies")
    assert "metzero.com" in text
    assert "11 Mar 2024" in text
    assert "£450,000" in text
    assert "SC123456" in text
    assert "Northern Accelerator spinout announcement" in text
    assert "NORTHSTAR" in text and "WATCHLIST" in text
    assert "fit 92.2 · edge 51.0 · coverage 1.00 · priority 75.7" in text
    assert "good fit but likely already on their radar" in text


def test_show_never_turns_an_unknown_component_into_a_zero(db):
    """`sub_score` NULL means unknown. Printing 0.00 would be a lie (06-scoring §4)."""
    cid = seed_company(db, "Sparse Ltd")
    score_id = seed_score(db, cid)
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (score_id, "traction", "no data", None, 2, None, ""),
    )
    text = render_show(db, "Sparse Ltd")
    assert "unknown" in text
    assert "0.00 × 2" not in text


def test_show_finds_a_company_by_partial_name(db):
    seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    assert "Kelvin Bio" in render_show(db, "kelvin")


def test_show_on_a_miss_says_so(db):
    assert render_show(db, "nothing here") == 'No company matching "nothing here".'


def test_show_disambiguates_rather_than_guessing(db):
    seed_company(db, "Kelvin Bio")
    seed_company(db, "Kelvin Robotics")
    text = render_show(db, "kelvin")
    assert "2 companies match" in text
    assert "Kelvin Bio" in text and "Kelvin Robotics" in text


def test_show_reports_an_unscored_company_plainly(db):
    seed_company(db, "Brand New Ltd")
    assert "has not been through scoring" in render_show(db, "Brand New Ltd")


# --------------------------------------------------------------------- fund


def test_fund_lists_top_matches_in_priority_order(db):
    for n, name in enumerate(["Kelvin Bio", "Second Co", "Third Co"]):
        seed_score(db, seed_company(db, name, domain=f"co{n}.com"), priority=90.0 - n * 5)
    seed_score(db, seed_company(db, "Other Fund Co"), fund="dsw", vehicle="seis_fund",
               priority=99.0)

    text = render_fund(db, "northstar", top=10)

    assert text.startswith("📡 Northstar Ventures — top 3")
    assert "1. Kelvin Bio" in text and "3. Third Co" in text
    assert "Other Fund Co" not in text
    assert "Spinout Inspire Fund (£200k–£750k)" in text


def test_fund_respects_top(db):
    for n in range(5):
        seed_score(db, seed_company(db, f"Co {n}"), priority=90.0 - n)
    text = render_fund(db, "northstar", top=2)
    assert "top 2" in text and "3. " not in text


def test_fund_includes_watchlist_because_it_is_a_research_prompt(db):
    seed_score(db, seed_company(db, "Watch Me"), tier="watchlist", priority=60.0)
    text = render_fund(db, "northstar")
    assert "Watch Me" in text and "watchlist" in text


def test_fund_with_no_matches_is_explicit(db):
    assert "No current matches" in render_fund(db, "anticus")


def test_unknown_fund_key_lists_the_real_ones(db):
    text = render_fund(db, "sequoia")
    assert 'No fund called "sequoia"' in text
    assert "northstar" in text


# ---------------------------------------------------------- NFR-2: 700 MB

HEAVY = ("gspread", "google", "httpx", "anthropic", "trafilatura", "pydantic",
         "rapidfuzz", "tldextract")


def test_the_digest_path_imports_nothing_heavy():
    """NFR-2. Rendering a message must not drag in the whole pipeline.

    Checked in a clean interpreter, because by this point in a pytest session
    another test has usually imported half of them already.
    """
    probe = (
        "import sys;"
        "import radar.render.digest, radar.notify.telegram, radar.notify.heartbeat;"
        f"heavy=[m for m in {HEAVY!r} if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"digest path imported: {out.stdout.strip()}"


def test_memory_under_700mb(full_day):
    """NFR-2: the process must coexist with Hermes on a 4 GB box."""
    import resource

    render_digest(full_day, on_date=DAY)
    render_status(full_day)
    render_fund(full_day, "northstar")

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    assert peak_mb < 700, f"peak RSS {peak_mb:.0f} MB"
