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
    assert db.scalar(
        "SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
        (ids[0],),
    ) == "not for me"

    assert reset_daily_review(db.conn) == 1
    assert {row["company_id"] for row in build_today(db.conn)["companies"]} == set(ids)


def test_today_does_not_surface_age_unverified_companies(db, config):
    """Today is a surfaced-opportunity queue, not an age-verification queue."""
    from radar.pipeline import score_company
    from tests.factories import C, store_company

    company = C(age_months=None, canonical_name="Age Unverified Ltd",
                norm_key="ageunverified")
    cid = store_company(db, company)
    score_company(db, cid, config, today=date(2026, 8, 8))

    payload = build_today(db.conn)

    assert db.scalar(
        "SELECT COUNT(*) FROM score WHERE company_id = ? AND 'age_unknown' IN "
        "(SELECT value FROM json_each(COALESCE(flags, '[]'))) ",
        (cid,),
    ) > 0
    assert cid not in {row["company_id"] for row in payload["companies"]}


def test_today_exposes_a_direct_source_url_for_each_recommendation(db):
    ids = seed_companies(db, count=3, shortlist=3)

    payload = build_today(db.conn)

    assert {row["company_id"] for row in payload["companies"]} == set(ids)
    assert all(row["source_url"].startswith(("http://", "https://"))
               for row in payload["companies"])


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
    assert all("coverage" in score and "tier" in score for score in scores)


def test_today_does_not_surface_a_company_without_provenance(db, config):
    from radar.pipeline import score_company
    from tests.factories import C, store_company

    company = C(canonical_name="Unlinked Startup", norm_key="unlinkedstartup",
                age_months=6)
    cid = store_company(db, company)
    score_company(db, cid, config)

    assert cid not in {row["company_id"] for row in build_today(db.conn)["companies"]}


def test_empty_kept_renders_guidance(db):
    seed_companies(db, count=2, shortlist=2)
    html = render_kept_rows(db.conn)
    assert 'data-testid="kept-empty"' in html
    assert "Nothing kept yet" in html
    intro = render_kept_intro(db.conn)
    assert 'data-testid="kept-total">0</span>' in intro
    assert "/help" in intro


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
