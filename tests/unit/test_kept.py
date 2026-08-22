"""Kept list — user shortlist in the Today prototype.

Kept is a verdict (`worth contacting` / `unsure`) in `user_field`, not
`score.tier = 'shortlist'`. These tests pin that distinction and the count
badge feed.
"""

from __future__ import annotations

from datetime import date

from prototype.server import (
    build_kept,
    build_kept_table,
    build_today,
    kept_count,
    render_calendar,
    reset_daily_review,
    render_kept_table,
    render_kept_intro,
    render_kept_rows,
    set_verdict,
)
from tests.fakes import seed_companies


def test_kept_count_ignores_not_for_me(db):
    ids = seed_companies(db, count=4, shortlist=4)
    conn = db.conn

    assert kept_count(conn) == 0
    set_verdict(conn, ids[0], "worth contacting")
    set_verdict(conn, ids[1], "unsure")
    set_verdict(conn, ids[2], "not for me")
    assert kept_count(conn) == 2

    kept = build_kept(conn)
    assert {c["company_id"] for c in kept["worth contacting"]} == {ids[0]}
    assert {c["company_id"] for c in kept["unsure"]} == {ids[1]}
    assert ids[2] not in {c["company_id"] for v in kept.values() for c in v}


def test_today_totals_include_kept(db):
    ids = seed_companies(db, count=3, shortlist=3)
    set_verdict(db.conn, ids[0], "worth contacting")
    payload = build_today(db.conn)
    assert payload["totals"]["kept"] == 1


def test_today_excludes_a_company_after_a_decision_until_review_again(db):
    ids = seed_companies(db, count=3, shortlist=3)

    assert {row["company_id"] for row in build_today(db.conn)["companies"]} == set(ids)

    set_verdict(db.conn, ids[0], "not for me")

    payload = build_today(db.conn)
    assert ids[0] not in {row["company_id"] for row in payload["companies"]}
    assert payload["totals"]["reviewed_today"] == 1
    assert payload["totals"]["remaining"] == 2
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons["reviewed_today"] == 1
    assert db.scalar(
        "SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
        (ids[0],),
    ) == "not for me"

    assert reset_daily_review(db.conn) == 1
    assert {row["company_id"] for row in build_today(db.conn)["companies"]} == set(ids)


def test_today_does_not_surface_age_unverified_companies(db, config):
    """Registry cards with unknown age stay in the research pool."""
    from radar.pipeline import score_company
    from tests.factories import registry_company, store_company

    company = registry_company(
        age_months=None, canonical_name="Age Unverified Ltd",
        norm_key="ageunverified", has_share_issue=True,
    )
    cid = store_company(db, company)
    score_company(db, cid, config, today=date(2026, 8, 8))

    payload = build_today(db.conn)

    assert db.scalar(
        "SELECT COUNT(*) FROM score WHERE company_id = ? AND 'age_unknown' IN "
        "(SELECT value FROM json_each(COALESCE(flags, '[]'))) ",
        (cid,),
    ) > 0
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons["age_unknown"] == 1


def test_today_exposes_a_direct_source_url_for_each_recommendation(db):
    ids = seed_companies(db, count=3, shortlist=3)

    payload = build_today(db.conn)

    assert {row["company_id"] for row in payload["companies"]} == set(ids)
    assert all(row["source_url"].startswith(("http://", "https://"))
               for row in payload["companies"])


def test_today_exposes_companies_house_verification_only_for_verified_signal(db):
    """The Today card badge is provenance-driven, not inferred from age alone."""
    from radar.store.db import now_iso

    ids = seed_companies(db, count=2, shortlist=2)
    stamp = now_iso()
    db.execute("UPDATE company SET incorporated_on = ? WHERE id = ?",
               ("2026-06-14", ids[0]))
    db.execute(
        """INSERT INTO signal
             (company_id, kind, occurred_on, headline, detail, source_key,
              source_url, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        (ids[0], "verification", "2026-06-14",
         "Company 0002 Ltd verified at Companies House (15021884)", None,
         "companies_house",
         "https://find-and-update.company-information.service.gov.uk/company/15021884",
         stamp),
    )

    companies = {row["company_id"]: row for row in build_today(db.conn)["companies"]}

    assert companies[ids[0]]["ch_verified"] == {
        "incorporated_on": "2026-06-14",
        "incorporated_on_display": "14 June 2026",
        "source_url": "https://find-and-update.company-information.service.gov.uk/company/15021884",
    }
    assert companies[ids[1]]["ch_verified"] is None


def test_today_exposes_match_scores_for_all_four_funds(db):
    """The primary route is not the only plausible pitch."""
    from radar.store.db import now_iso

    ids = seed_companies(db, count=1, shortlist=1)
    stamp = now_iso()
    for fund, fit in (("outward", 20.0), ("northstar", 50.0), ("anticus", 35.0)):
        db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ids[0], fund, None, fit, 0.4, 60.0, fit, "watchlist", None,
             "x", None, "testhash", "1", stamp),
        )

    scores = build_today(db.conn)["companies"][0]["fund_scores"]

    assert [score["fund_key"] for score in scores] == [
        "outward", "dsw", "northstar", "anticus"
    ]
    assert {score["fund_key"]: score["fit"] for score in scores} == {
        "outward": 20.0,
        "dsw": 90.0,
        "northstar": 50.0,
        "anticus": 35.0,
    }
    assert all("coverage" in score and "tier" in score and "why" in score for score in scores)
    assert all(isinstance(score["why"], str) and score["why"] for score in scores)


def test_fund_score_why_names_the_strongest_fit_and_miss(db):
    """A bar without a reason is how 82% vs 31% reads as a pie chart."""
    ids = seed_companies(db, count=1, shortlist=1)
    sid = db.scalar("SELECT id FROM score WHERE company_id = ?", (ids[0],))
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, "geography", "Geography", 1.0, 10, 10.0, "North East"),
    )
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, "stage", "Stage", 0.2, 8, 1.6, "growth"),
    )
    db.execute(
        """INSERT INTO score_component (score_id, key, label, sub_score, weight,
                                        contribution, evidence)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, "age", "Age", 0.9, 5, 4.5, "11 months"),
    )

    scores = {
        row["fund_key"]: row
        for row in build_today(db.conn)["companies"][0]["fund_scores"]
    }
    primary = db.scalar("SELECT fund_key FROM score WHERE id = ?", (sid,))
    why = scores[primary]["why"].lower()
    assert "geography" in why and "north east" in why
    assert "stage" in why and "growth" in why
    assert "11 months" not in why


def test_fund_score_why_explains_a_reject_instead_of_a_zero(db):
    ids = seed_companies(db, count=1, shortlist=1)
    from radar.store.db import now_iso
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ids[0], "outward", None, 12.0, 0.4, 40.0, 12.0, "reject",
         "max_company_age_months", "x", None, "testhash", "1", now_iso()),
    )
    outward = next(
        row for row in build_today(db.conn)["companies"][0]["fund_scores"]
        if row["fund_key"] == "outward"
    )
    assert outward["fit"] is None
    assert "too old" in outward["why"].lower()


def test_today_uses_latest_score_history_before_picking_primary_route(db):
    """Historical config rows cannot outrank the current score on Today."""
    company_id = seed_companies(db, count=1, shortlist=1)[0]
    old_stamp = "2026-08-01T00:00:00Z"
    new_stamp = "2026-08-02T00:00:00Z"
    db.execute(
        "UPDATE score SET priority = ?, discovery_edge = ?, scored_at = ?, "
        "config_hash = ? WHERE company_id = ?",
        (100.0, 90.0, old_stamp, "old-config", company_id),
    )
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, "dsw", None, 40.0, 0.8, 30.0, 50.0, "watchlist", None,
         "Current config score.", None, "new-config", "1", new_stamp),
    )

    row = build_today(db.conn)["companies"][0]

    assert row["fund"] == "dsw"
    assert row["priority"] == 50.0
    assert row["edge"] == 30.0


def test_today_uses_latest_score_history_for_also_fits(db):
    """Secondary fund matches must not expose rows from an older config."""
    company_id = seed_companies(db, count=1, shortlist=1)[0]
    for fit, stamp, config_hash in (
        (90.0, "2026-08-01T00:00:00Z", "old-config"),
        (40.0, "2026-08-02T00:00:00Z", "new-config"),
    ):
        db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, "outward", None, fit, 0.8, 30.0, fit, "watchlist", None,
             "Secondary fund score.", None, config_hash, "1", stamp),
        )

    row = build_today(db.conn)["companies"][0]

    assert row["also_fits"] == [
        {"fund_key": "outward", "tier": "watchlist", "fund_fit_pct": 40.0},
    ]


def test_today_does_not_surface_a_company_without_provenance(db, config):
    from radar.pipeline import score_company
    from tests.factories import C, store_company

    company = C(canonical_name="Unlinked Startup", norm_key="unlinkedstartup",
                age_months=6)
    cid = store_company(db, company)
    score_company(db, cid, config)

    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons["missing_provenance"] == 1


def test_empty_kept_renders_guidance(db):
    seed_companies(db, count=2, shortlist=2)
    html = render_kept_rows(db.conn)
    assert 'data-testid="kept-empty"' in html
    assert "Nothing kept yet" in html
    intro = render_kept_intro(db.conn)
    assert 'data-testid="kept-total">0</span>' in intro
    assert "/help" in intro


def test_kept_page_renders_a_semantic_table(db):
    ids = seed_companies(db, count=2, shortlist=2)
    set_verdict(db.conn, ids[0], "worth contacting")
    set_verdict(db.conn, ids[1], "unsure")

    html = render_kept_rows(db.conn)

    assert '<table class="kept-table" data-testid="kept-table">' in html
    assert 'data-testid="kept-table-caption"' in html
    assert '<thead><tr>' in html
    assert '<tbody>' in html
    assert html.count('<th scope="col">') == 6
    assert html.count('<th scope="row" class="kept-company">') == 2
    assert 'data-kept-table-row="true"' in html
    assert 'data-verdict="worth contacting"' in html
    assert 'data-verdict="unsure"' in html


def test_set_verdict_returns_kept_count(db):
    ids = seed_companies(db, count=2, shortlist=2)
    assert set_verdict(db.conn, ids[0], "worth contacting") == 1
    assert set_verdict(db.conn, ids[0], "not for me") == 0
    assert set_verdict(db.conn, ids[1], "unsure") == 1


def test_dashboard_kept_table_renders_dates_and_source_labels(db):
    ids = seed_companies(db, count=1, shortlist=1)
    set_verdict(db.conn, ids[0], "worth contacting")

    rows = build_kept_table(db.conn)
    html = render_kept_table(db.conn)

    assert rows[0]["company_id"] == ids[0]
    assert rows[0]["sources"][0]["source_key"] == "uktn"
    assert 'data-testid="kept-table"' in html
    assert "Company 0001 Ltd" in html
    assert "Uktn" in html


def test_dashboard_calendar_marks_a_kept_company(db):
    ids = seed_companies(db, count=1, shortlist=1)
    set_verdict(db.conn, ids[0], "worth contacting")
    first_seen = date.fromisoformat(
        db.scalar("SELECT substr(first_seen, 1, 10) FROM company WHERE id = ?", (ids[0],)))

    html = render_calendar(db.conn, first_seen.year, first_seen.month, first_seen)

    assert f"data-date='{first_seen.isoformat()}'" in html
    assert 'data-kept="true"' in html


def _watchlist_row(db, company, *, source_key, source_url, priority, fund="dsw"):
    from radar.store.db import now_iso
    from tests.factories import store_company

    cid = store_company(db, company)
    stamp = now_iso()
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, source_key, f"ext-{cid}", source_url, stamp, stamp),
    )
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, fund, None, 80.0, 0.8, 70.0, priority, "watchlist", None,
         "Queued for review.", None, "testhash", "1", stamp),
    )
    return cid


def test_today_hides_companies_house_shells(db):
    """Incorporation plus a CH URL is not enough to occupy Today."""
    from tests.factories import registry_company

    cid = _watchlist_row(
        db,
        registry_company(
            canonical_name="4DCONSTRUCTIONPLANNING LTD",
            norm_key="4dconstructionplanning",
            age_months=6,
        ),
        source_key="companies_house",
        source_url="https://find-and-update.company-information.service.gov.uk/company/15021884",
        priority=99,
    )
    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons["registry_without_venture_signal"] == 1


def test_today_shows_registry_with_a_share_issue(db):
    from tests.factories import registry_company

    cid = _watchlist_row(
        db,
        registry_company(
            canonical_name="SH01 Startup Ltd",
            norm_key="sh01startup",
            age_months=6,
            has_share_issue=True,
        ),
        source_key="companies_house",
        source_url="https://find-and-update.company-information.service.gov.uk/company/15021885",
        priority=80,
    )
    payload = build_today(db.conn)
    assert cid in {row["company_id"] for row in payload["companies"]}


def test_today_shows_track_a_with_unknown_age(db):
    from tests.factories import C

    cid = _watchlist_row(
        db,
        C(canonical_name="Innovate Winner Ltd", norm_key="innovatewinner",
          age_months=None, discovery_route="grant", country="GB"),
        source_key="innovate_uk",
        source_url="https://www.gov.uk/innovate-uk/award-1",
        priority=70,
    )
    payload = build_today(db.conn)
    assert cid in {row["company_id"] for row in payload["companies"]}
    assert payload["companies"][0]["source_key"] == "innovate_uk"


def test_today_ranks_track_a_ahead_of_registry(db):
    from tests.factories import C, registry_company

    registry_id = _watchlist_row(
        db,
        registry_company(
            canonical_name="Registry SH01 Ltd",
            norm_key="registrysh01today",
            age_months=6,
            has_share_issue=True,
        ),
        source_key="companies_house",
        source_url="https://find-and-update.company-information.service.gov.uk/company/15021999",
        priority=99,
    )
    news_id = _watchlist_row(
        db,
        C(canonical_name="News Startup Ltd", norm_key="newsstartuptoday",
          age_months=8, discovery_route="news"),
        source_key="uktn",
        source_url="https://uktn.co.uk/news-startup",
        priority=40,
    )
    order = [row["company_id"] for row in build_today(db.conn)["companies"]]
    assert order == [news_id, registry_id]


def test_today_ignores_stale_config_hash_watchlist(db, config):
    from radar.config.loader import save_snapshot

    ids = seed_companies(db, count=1, shortlist=1)
    save_snapshot(db, config, is_last_good=True)
    db.execute("UPDATE score SET config_hash = 'oldhash' WHERE company_id = ?",
               (ids[0],))

    payload = build_today(db.conn)
    assert ids[0] not in {row["company_id"] for row in payload["companies"]}
    assert payload["totals"]["watchlist"] == 0
    assert payload["totals"]["shortlist"] == 0

    db.execute(
        "UPDATE score SET config_hash = ? WHERE company_id = ?",
        (config.hash(), ids[0]),
    )
    payload = build_today(db.conn)
    assert ids[0] in {row["company_id"] for row in payload["companies"]}
