"""Client-issues regression plan §3 — browser counterparts.

The offline guards prove the engine; these prove the same complaints on the
surface Aryan actually reviews. Where the demo dataset cannot show the
condition (it is uniformly fresh and well-covered), the test injects the
counter-example into the session demo database, asserts on the rendered page,
then removes it — the session-scoped server must never carry one test's
company into the next.

    pytest -m browser
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.browser.conftest import tid

pytestmark = pytest.mark.browser

#: G19-G21 / F18 — the sections the /help page must keep explaining.
HELP_SECTIONS = (
    "how the parts connect",
    "data flow",
    "shortlist vs kept",
    "where kept is stored",
    "update funds later",
    "edit fund criteria",
    "add or remove sourcing channels",
    "change the login password",
)


def _inject(demo_db, companies) -> list[str]:
    """Store, provenance and score `companies`; returns their ids.

    Mirrors the demo build in `tests.demo_db.build` so an injected company
    is indistinguishable from a demo one.
    """
    from radar.config.defaults import default_config
    from radar.pipeline import score_company
    from radar.store.db import Db, now_iso
    from tests.demo_db import TODAY
    from tests.factories import store_company

    db = Db(str(demo_db))
    cfg = default_config()
    stamp = now_iso()
    ids: list[str] = []
    try:
        for company in companies:
            cid = store_company(db, company)
            db.execute(
                "INSERT INTO company_source(company_id, source_key, external_id, "
                "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
                (cid, "uktn", f"ext-{company.norm_key}",
                 f"https://uktn.co.uk/{company.norm_key}", stamp, stamp),
            )
            score_company(db, cid, cfg, today=TODAY)
            ids.append(cid)
    finally:
        db.close()
    return ids


def _remove(demo_db, company_ids) -> None:
    """Delete an injected company and every row that references it."""
    conn = sqlite3.connect(str(demo_db))
    try:
        for cid in company_ids:
            conn.execute("DELETE FROM daily_review WHERE company_id = ?", (cid,))
            conn.execute("DELETE FROM user_field WHERE company_id = ?", (cid,))
            conn.execute("DELETE FROM signal WHERE company_id = ?", (cid,))
            conn.execute("DELETE FROM founder WHERE company_id = ?", (cid,))
            conn.execute("DELETE FROM company_source WHERE company_id = ?", (cid,))
            conn.execute(
                "DELETE FROM score_component WHERE score_id IN "
                "(SELECT id FROM score WHERE company_id = ?)", (cid,))
            conn.execute("DELETE FROM score WHERE company_id = ?", (cid,))
            conn.execute("DELETE FROM company WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- C11 (3.3)

def test_edge_varies_across_the_today_queue(page, server, demo_db):
    """C11 — "Edge seems to be the same across the results", on the real page.

    Two companies with the same fit but opposite visibility must carry
    different Fresh values in the queue the page renders, and the visible tile
    must be the same number the queue carries.
    """
    from tests.factories import C

    ids = _inject(demo_db, [
        C(canonical_name="Obscure Browser Co", norm_key="obscurebrowserco",
          hq_postcode="NE1 4ST", is_university_spinout=False,
          discovery_route="registry", age_months=3, funding=0,
          news_mention_count=0, sector="climate_tech",
          founder_signal="research_spinout"),
        C(canonical_name="Famous Browser Co", norm_key="famousbrowserco",
          hq_postcode="NE1 4ST", is_university_spinout=False,
          discovery_route="news", age_months=30, funding=2_000_000,
          news_mention_count=8, sector="climate_tech",
          founder_signal="research_spinout"),
    ])
    try:
        page.goto(server + "/", wait_until="networkidle")
        page.wait_for_selector(tid("card"))

        edges = page.evaluate("() => data.companies.map(c => c.edge)")
        assert len(set(edges)) >= 2, f"Fresh identical everywhere: {edges}"

        shown = float(page.locator(tid("score-edge")).get_attribute("data-value"))
        assert shown == edges[0], f"tile {shown} != queue value {edges[0]}"
    finally:
        _remove(demo_db, ids)


# ---------------------------------------------------------------- C13 (3.4)

def test_sparse_company_is_never_offered_as_a_review_card(page, server, demo_db):
    """C13 — "100 Match even when only one or two criteria are confirmed".

    A company with only two confirmed criteria must never reach the review
    surface with an inflated score. On the default config the full-model fit
    is honest (34, not 100) and sits under the watchlist bar, so the company
    is excluded from Today entirely; and page-wide, no company under the
    coverage floor may be a shortlist pick.
    """
    import sqlite3

    from tests.factories import C

    # Exactly two confirmed criteria: stage + geography. `has_share_issue=True`
    # blocks the derivation that would otherwise infer a traction signal for a
    # young company, keeping coverage at 0.4 instead of 0.6.
    company = C(canonical_name="Sparse Browser Co", norm_key="sparsebrowserco",
                sector=None, founder_signal=None, traction_signal=None,
                has_share_issue=True, age_months=6, discovery_route="registry",
                hq_postcode="NE1 4ST", is_university_spinout=False)
    ids = _inject(demo_db, [company])
    try:
        # The engine's score for the sparse company is honest: nowhere near 100.
        conn = sqlite3.connect(str(demo_db))
        fit = conn.execute(
            "SELECT fund_fit_pct, tier FROM score WHERE company_id = ? "
            "AND fund_key = 'northstar'", (ids[0],)).fetchone()
        conn.close()
        assert fit is not None
        assert fit[0] < 100 and fit[1] != "shortlist", \
            f"sparse company scored fit={fit[0]} tier={fit[1]}"

        page.goto(server + "/", wait_until="networkidle")
        page.wait_for_selector(tid("card"))

        companies = page.evaluate("() => data.companies")
        assert ids[0] not in {c["company_id"] for c in companies}, \
            "a two-criteria company was offered as a review card"

        # Page-wide invariant: nothing under the coverage floor is shortlisted.
        for c in companies:
            if c["coverage"] < 0.5:
                assert c["tier"] != "shortlist", f"{c['name']} is thin but shortlisted"
    finally:
        _remove(demo_db, ids)


# ---------------------------------------------------------------- D14 (3.5)

def test_onboarding_fund_rules_never_call_outward_government_backed(page, server):
    """D14 — the fund rules on the served onboarding page.

    The page derives its rules from the config; the literal prose that once
    called Outward "government-backed" must stay gone, and the rules that prose
    once lost must stay present. Inactive vehicles must not read as deployable.
    """
    page.goto(server + "/onboarding", wait_until="networkidle")
    page.wait_for_selector(tid("onboarding"))

    outward = page.locator(f'{tid("fund-row")}[data-fund="outward"]')
    assert outward.count() == 1
    text = outward.inner_text().lower()
    assert "government" not in text
    assert "£5m" in text          # the round cap the prose lost once
    assert "£20m" in text
    assert "66%" in text

    dsw = page.locator(f'{tid("fund-row")}[data-fund="dsw"]').inner_text().lower()
    assert "co-investment" not in dsw, "an inactive vehicle reads as deployable"


# -------------------------------------------------------------- G19-G21 (3.7)

def test_help_covers_the_handover_sections(page, server):
    """G19-G21 / F18 — the served /help page still explains the handover."""
    page.goto(server + "/help", wait_until="networkidle")
    page.wait_for_selector(tid("help"))

    text = page.locator(tid("help")).inner_text().lower()
    missing = [s for s in HELP_SECTIONS if s not in text]
    assert not missing, f"/help no longer explains: {missing}"
