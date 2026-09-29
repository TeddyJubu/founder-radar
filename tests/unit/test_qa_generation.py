"""QA must approve the current card even when writes share one second."""
from radar.config.defaults import default_config
from radar.qa.today import TodayCheckResult, load_today_cards, qa_state, record_check, run_today_qa
from tests.fakes import seed_companies


def test_same_second_company_change_invalidates_a_qa_pass(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    cfg = default_config()
    card = load_today_cards(db, cfg)[0]
    stamp = db.scalar("SELECT MAX(scored_at) FROM score WHERE company_id = ?", (cid,))
    record_check(db, card, TodayCheckResult(verdict="pass", checker="rules"), checked_at=stamp)
    assert qa_state(db, cid) == "pass"
    db.execute("UPDATE company SET one_liner = 'New facts: publicly listed on NASDAQ' WHERE id = ?", (cid,))
    assert db.scalar("SELECT MAX(scored_at) FROM score WHERE company_id = ?", (cid,)) == stamp
    assert qa_state(db, cid) == "incomplete"
    replacement = load_today_cards(db, cfg, company_id=cid)[0]
    record_check(db, replacement, TodayCheckResult(verdict="pass", checker="rules"), checked_at=stamp)
    assert qa_state(db, cid) == "pass"


def test_same_second_route_change_invalidates_a_qa_pass(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    cfg = default_config()
    card = load_today_cards(db, cfg)[0]
    stamp = db.scalar("SELECT MAX(scored_at) FROM score WHERE company_id = ?", (cid,))
    record_check(db, card, TodayCheckResult(verdict="pass", checker="hermes"), checked_at=stamp)
    assert qa_state(db, cid) == "pass"
    db.execute("UPDATE score SET vehicle_key = 'new_vehicle' WHERE company_id = ?", (cid,))
    assert qa_state(db, cid) == "incomplete"


def test_unchanged_rules_pass_is_reusable_and_changed_card_is_rechecked(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    cfg = default_config()
    first = run_today_qa(db, cfg, use_hermes=False)
    assert first.passed == 1 and qa_state(db, cid) == "pass"
    again = run_today_qa(db, cfg, use_hermes=False)
    assert again.cached == 1
    db.execute("UPDATE company SET one_liner = 'New company description' WHERE id = ?", (cid,))
    assert qa_state(db, cid) == "incomplete"
    checked = run_today_qa(db, cfg, use_hermes=False)
    assert checked.cached == 0 and checked.passed == 1
    assert qa_state(db, cid) == "pass"


def _snapshot_fixture(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    db.execute("UPDATE score SET scored_at='2026-08-03T06:00:00Z' WHERE company_id=?", (cid,))
    db.execute("""INSERT INTO score_snapshot
       (company_id,fund_key,snapshot_date,vehicle_key,config_hash,fund_fit_pct,
        coverage,discovery_edge,priority,tier,scored_at)
       SELECT company_id,fund_key,'2026-08-03',vehicle_key,config_hash,fund_fit_pct,
        coverage,discovery_edge,priority,tier,scored_at FROM score WHERE company_id=?""", (cid,))
    db.execute("DELETE FROM today_check WHERE company_id=?", (cid,))
    return cid


def test_approved_monday_snapshot_survives_a_later_missing_current_score(db):
    from datetime import date
    from radar.render.digest import _shortlist
    cid = _snapshot_fixture(db)
    card = load_today_cards(db, default_config())[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='rules'),
                 checked_at='2026-08-03T06:00:00Z')
    db.execute("DELETE FROM score WHERE company_id=?", (cid,))
    assert qa_state(db, cid) == 'incomplete'
    rows = _shortlist(db, date(2026,8,3), date(2026,8,3))
    assert [row['company_id'] for row in rows] == [cid]


def test_unapproved_historical_snapshot_stays_hidden(db):
    from datetime import date
    from radar.render.digest import _shortlist
    cid = _snapshot_fixture(db)
    # A later unrelated card approval is not proof this snapshot passed QA.
    db.execute("UPDATE company SET one_liner='Changed company facts' WHERE id=?", (cid,))
    db.execute("UPDATE score SET scored_at='2026-08-05T06:00:00Z' WHERE company_id=?", (cid,))
    card = load_today_cards(db, default_config())[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='rules'),
                 checked_at='2026-08-05T06:00:00Z')
    assert _shortlist(db, date(2026,8,3), date(2026,8,3)) == []


def test_latest_reject_vetoes_an_approved_historical_snapshot(db):
    from datetime import date
    from radar.render.digest import _shortlist
    cid = _snapshot_fixture(db)
    card = load_today_cards(db, default_config())[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='rules'),
                 checked_at='2026-08-03T06:00:00Z')
    db.execute("UPDATE company SET one_liner='IPO filing' WHERE id=?", (cid,))
    new = load_today_cards(db, default_config())[0]
    record_check(db, new, TodayCheckResult(verdict='reject', checker='rules'),
                 checked_at='2026-08-05T06:00:00Z')
    assert _shortlist(db, date(2026,8,3), date(2026,8,3)) == []


def test_unreleased_snapshot_table_gains_approval_column_without_trusting_old_rows(db):
    cid = _snapshot_fixture(db)
    db.execute('ALTER TABLE score_snapshot DROP COLUMN approved_snapshot_hash')
    db.execute("DELETE FROM _meta WHERE key='migration:006_snapshot_qa_approval.sql'")
    db.migrate()
    assert db.scalar('SELECT approved_snapshot_hash FROM score_snapshot WHERE company_id=?', (cid,)) is None
    from datetime import date
    from radar.render.digest import _shortlist
    assert _shortlist(db, date(2026,8,3), date(2026,8,3)) == []


def test_later_rescore_with_same_card_hash_requires_a_real_new_check(db):
    from prototype.server import build_today
    cid = seed_companies(db, count=1, shortlist=1)[0]
    cfg = default_config()
    db.execute("UPDATE score SET scored_at='2020-01-01T00:00:00Z' WHERE company_id=?", (cid,))
    card = load_today_cards(db, cfg)[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'),
                 checked_at='2020-01-01T00:00:00Z')
    db.execute("UPDATE score SET scored_at='2020-01-02T00:00:00Z' WHERE company_id=?", (cid,))
    assert load_today_cards(db, cfg)[0].snapshot_hash() == card.snapshot_hash()
    assert qa_state(db, cid) == 'incomplete'

    class Checker:
        name = 'hermes'
        calls = 0
        def review(self, checked):
            self.calls += 1
            return TodayCheckResult(verdict='pass', checker='hermes')

    checker = Checker()
    report = run_today_qa(db, cfg, checker=checker)
    assert checker.calls == 1
    assert report.cached == 0 and report.passed == 1
    assert qa_state(db, cid) == 'pass'
    assert [row['company_id'] for row in build_today(db.conn)['companies']] == [cid]


def test_rescore_does_not_remove_a_cached_rejection_veto(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    cfg = default_config()
    card = load_today_cards(db, cfg)[0]
    record_check(db, card, TodayCheckResult(verdict='reject', checker='hermes'),
                 checked_at='2020-01-01T00:00:00Z')
    class Checker:
        name = 'hermes'
        def review(self, checked):
            raise AssertionError('Cached rejection must stay a veto')
    report = run_today_qa(db, cfg, checker=Checker())
    assert report.cached == 1 and report.rejected == 1
    assert qa_state(db, cid) == 'reject'
