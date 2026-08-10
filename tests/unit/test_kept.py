"""Kept list — user shortlist in the Today prototype.

Kept is a verdict (`worth contacting` / `unsure`) in `user_field`, not
`score.tier = 'shortlist'`. These tests pin that distinction and the count
badge feed.
"""

from __future__ import annotations

from prototype.server import (
    build_kept,
    build_today,
    kept_count,
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
