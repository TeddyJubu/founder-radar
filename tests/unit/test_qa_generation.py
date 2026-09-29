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
