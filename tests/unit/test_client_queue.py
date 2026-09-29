"""Client backlog counts and soft daily diversity, using real scored cards."""
from datetime import date, timedelta

from prototype.server import build_today
from radar.config.defaults import default_config
from radar.qa.today import load_today_cards, record_check, TodayCheckResult
from radar.selection import diversify, rank_today_rows
from tests.factories import approve_cards
from tests.fakes import seed_companies


def test_soft_diversity_is_bounded_stable_and_keeps_every_company():
    rows = [{"company_id": str(i), "priority": p} for i, p in enumerate([100, 99, 98, 97, 20])]
    metadata = {str(i): {"track": 0, "fund": "dsw", "source": "grants", "sector": "software"} for i in range(5)}
    metadata['3'] = {"track": 0, "fund": "outward", "source": "press", "sector": "health"}
    result = diversify(rows, metadata=metadata)
    assert [r['company_id'] for r in result] == ['0', '3', '1', '2', '4']
    assert diversify(rows, metadata=metadata) == result
    assert sorted(r['company_id'] for r in result) == sorted(metadata)
    assert [r['priority'] for r in rows] == [100, 99, 98, 97, 20]


def test_today_and_qa_share_diverse_order_before_limit(db):
    ids = seed_companies(db, count=30, shortlist=30)
    db.execute("UPDATE score SET fund_key='dsw'")
    db.execute("UPDATE company SET sector='software', discovery_route='grant'")
    db.execute("UPDATE company_source SET source_key='innovate_uk'")
    for i, cid in enumerate(ids[20:24]):
        db.execute("UPDATE score SET fund_key=?,priority=98 WHERE company_id=?", (['northstar', 'outward', 'anticus', 'outward'][i], cid))
        db.execute("UPDATE company SET sector='health',discovery_route='news' WHERE id=?", (cid,))
        db.execute("UPDATE company_source SET source_key='uktn' WHERE company_id=?", (cid,))
    approve_cards(db)
    cards = load_today_cards(db, default_config(), limit=8)
    shown = build_today(db.conn, limit=8)
    assert [c.company_id for c in cards] == [c['company_id'] for c in shown['companies']]
    assert any(cid in {c.company_id for c in cards[:5]} for cid in ids[20:24])
    assert shown['totals']['remaining_total'] == 30
    assert db.scalar("SELECT COUNT(*) FROM score") == 30


def test_counter_counts_pending_unreviewed_before_page_cap(db):
    ids = seed_companies(db, count=35, shortlist=35)
    # Leave 25 genuine cards unchecked, one explicit rejection, one old decision,
    # one daily decision and one deterministic maturity failure.
    db.execute("DELETE FROM today_check WHERE company_id IN (%s)" % ','.join('?' for _ in ids[10:]), tuple(ids[10:]))
    card = load_today_cards(db, default_config(), company_id=ids[10], limit=1)[0]
    record_check(db, card, TodayCheckResult(verdict='reject', checker='rules'))
    old = (date.today() - timedelta(days=1)).isoformat()
    db.execute("INSERT INTO user_field(company_id,field,value,updated_at) VALUES (?,?,?,?)", (ids[11], 'verdict', 'not for me', old))
    db.execute("INSERT INTO daily_review(company_id,review_date,verdict,reviewed_at) VALUES (?,?,?,?)", (ids[12], date.today().isoformat(), 'unsure', date.today().isoformat()))
    db.execute("UPDATE company SET incorporated_on=NULL,stage=NULL WHERE id=?", (ids[13],))
    payload = build_today(db.conn, limit=3)
    assert len(payload['companies']) == 3
    assert payload['totals']['ready_to_review'] == 10
    assert payload['totals']['awaiting_final_check'] == 21
    assert payload['totals']['remaining_total'] == 31
    assert payload['totals']['remaining'] == 10


def test_blocked_high_priority_cards_do_not_consume_qa_limit(db):
    ids = seed_companies(db, count=26, shortlist=26)
    db.execute("DELETE FROM today_check")
    for cid in ids[:24]:
        db.execute("UPDATE company SET incorporated_on=NULL,stage=NULL WHERE id=?", (cid,))
    assert [c.company_id for c in load_today_cards(db, default_config(), limit=2)] == ids[24:]
    # Explicit evidence lookup remains available even for a blocked company.
    assert load_today_cards(db, default_config(), company_id=ids[0])[0].company_id == ids[0]


def test_rejected_latest_fund_never_supplies_diversity(db):
    ids = seed_companies(db, count=3, shortlist=3)
    db.execute("UPDATE score SET fund_key='dsw'")
    db.execute("UPDATE score SET tier='reject',reject_reason='geography',fund_key='outward' WHERE company_id=?", (ids[2],))
    rows = [dict(company_id=cid, priority=p, fund_key=f, sector='software', discovery_route='news')
            for cid, p, f in zip(ids, [100, 99, 99.5], ['dsw', 'dsw', 'outward'])]
    # An apparent alternative rejected in the actual latest scores earns no
    # novelty credit over another viable regional match.
    ranked = rank_today_rows(db, rows)
    assert [r['company_id'] for r in ranked[:2]] == ids[:2]


def _london_pair(db, *, alternative=True, alternative_coverage=1.0, alternative_tier='watchlist'):
    ids = seed_companies(db, count=1, shortlist=1)
    cid = ids[0]
    db.execute("UPDATE company SET canonical_name='Blooming Surveys Ltd',hq_city='Fitzrovia',hq_region=NULL,hq_postcode='W1T6EB',sector='b2b_saas' WHERE id=?", (cid,))
    db.execute("UPDATE score SET fund_key='dsw',vehicle_key='eis_service',fund_fit_pct=45.3,priority=45.3,tier='watchlist' WHERE company_id=?", (cid,))
    if alternative:
        db.execute("""INSERT INTO score(company_id,fund_key,vehicle_key,fund_fit_pct,coverage,
          discovery_edge,priority,tier,reject_reason,explanation,config_hash,scorer_version,scored_at)
          SELECT company_id,'outward','fund_ii',35,?,discovery_edge,35,?, ?,
            'Existing Outward match',config_hash,scorer_version,scored_at FROM score WHERE company_id=?""",
          (alternative_coverage, alternative_tier, 'sector' if alternative_tier == 'reject' else None, cid))
    return cid


def test_london_uses_real_viable_alternative_in_qa_and_today(db):
    cid = _london_pair(db)
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    assert card.fund_key == 'outward'
    assert 'London' in card.recommendation_reason
    # An old approval for the former recommendation cannot approve this card.
    assert build_today(db.conn)['totals']['awaiting_final_check'] == 1
    approve_cards(db)
    shown = build_today(db.conn)['companies'][0]
    assert shown['fund'] == card.fund_key
    assert shown['fit'] == 35
    assert shown['recommendation_reason'] == card.recommendation_reason
    assert len(shown['fund_scores']) == 4
    assert next(s for s in shown['fund_scores'] if s['fund_key'] == 'dsw')['fit'] == 45.3
    assert db.scalar("SELECT fund_fit_pct FROM score WHERE company_id=? AND fund_key='dsw'", (cid,)) == 45.3
    from dataclasses import replace
    assert replace(card, recommendation_reason='Different policy').snapshot_hash() != card.snapshot_hash()


import pytest

@pytest.mark.parametrize('alternative,coverage,tier', [(False, 1, 'watchlist'), (True, .2, 'watchlist'), (True, 1, 'reject')])
def test_london_without_adequate_alternative_is_honest_research(db, alternative, coverage, tier):
    cid = _london_pair(db, alternative=alternative, alternative_coverage=coverage, alternative_tier=tier)
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    assert card.fund_key == 'dsw'
    assert card.recommendation_reason is None
    assert 'Research only' in card.recommendation_warning
    approve_cards(db)
    shown = build_today(db.conn)['companies'][0]
    assert shown['fund'] == 'dsw'
    assert shown['recommendation_warning'] == card.recommendation_warning


def test_genuine_regional_dsw_recommendation_is_unchanged(db):
    cid = _london_pair(db)
    db.execute("UPDATE company SET hq_region='north_east',hq_city='Newcastle',hq_postcode='NE14ST' WHERE id=?", (cid,))
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    assert card.fund_key == 'dsw'
    assert card.recommendation_reason is card.recommendation_warning is None


def test_second_qa_batch_advances_past_approved_first_24(db):
    from radar.qa.today import run_today_qa
    from tests.unit.test_today_qa import ScriptedChecker
    ids = seed_companies(db, count=50, shortlist=50)
    db.execute('DELETE FROM today_check')
    first = ScriptedChecker()
    report = run_today_qa(db, default_config(), checker=first, limit=24)
    assert len(first.calls) == report.checked == 24
    second = ScriptedChecker()
    report = run_today_qa(db, default_config(), checker=second, limit=24)
    assert len(second.calls) == 24
    assert set(first.calls).isdisjoint(second.calls)
    assert report.cached == 24
    assert report.passed == 48
    assert report.uncovered == 2
    assert build_today(db.conn)['totals']['ready_to_review'] == 48
    assert build_today(db.conn)['totals']['awaiting_final_check'] == 2
