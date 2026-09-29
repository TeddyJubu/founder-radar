"""A good model verdict is insufficient when its source is unusable."""

from datetime import datetime, timedelta, timezone

import pytest

from radar.config.defaults import default_config
from radar.qa.provenance import LinkOutcome
from radar.qa.today import (TodayCheckResult, check_one, load_today_cards,
                            qa_state, record_check, run_today_qa)
from tests.fakes import seed_companies


class Checker:
    name = "scripted"

    def __init__(self):
        self.calls = 0

    def review(self, card):
        self.calls += 1
        return TodayCheckResult(verdict="pass", checker=self.name)


def verifier(state):
    def verify(db, url):
        now = datetime.now(timezone.utc)
        return LinkOutcome(url, state, url, 404 if state == "dead" else 403,
                           f"fixture {state}", now.isoformat(),
                           (now + timedelta(hours=1)).isoformat())
    return verify


@pytest.mark.parametrize("state,verdict", [
    ("dead", "reject"), ("invalid", "reject"),
    ("blocked", "incomplete"), ("timeout", "incomplete"), ("error", "incomplete"),
])
def test_unusable_link_vetoes_even_a_cached_model_pass(db, state, verdict):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict="pass", checker="scripted"))
    checker = Checker()
    result = check_one(db, card, checker=checker, source_verifier=verifier(state))
    assert result.verdict == verdict
    assert result.checker == "source"
    assert checker.calls == 0
    assert qa_state(db, cid) != "pass"


def test_source_timeouts_do_not_abandon_the_healthy_company_checker(db):
    ids = seed_companies(db, count=5, shortlist=5)
    db.execute("DELETE FROM today_check")
    checker = Checker()
    seen = 0

    def verify(db, url):
        nonlocal seen
        seen += 1
        return verifier("timeout" if seen <= 3 else "reachable")(db, url)

    report = run_today_qa(db, default_config(), checker=checker, source_verifier=verify)
    assert report.incomplete == 3
    assert report.passed == 2
    assert checker.calls == 2
    assert report.hermes_failures == 0


def test_expired_link_withholds_a_current_model_approval_without_network(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict="pass", checker="scripted"))
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    url = card.source_url.split("#", 1)[0]
    db.execute("INSERT INTO source_link_check VALUES (?,?,?,?,?,?,?)",
               (url, "reachable", url, 200, "old GET", past, past))
    assert qa_state(db, cid) == "incomplete"


def test_missing_source_proof_withholds_a_real_hermes_approval(db):
    from prototype.server import build_today
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'))
    assert db.scalar('SELECT COUNT(*) FROM source_link_check') == 0
    assert qa_state(db, cid) == 'incomplete'
    assert build_today(db.conn)['companies'] == []


def test_default_run_verifies_a_cached_pass_when_hermes_is_unavailable(db, monkeypatch):
    from tests.fakes import source_link_proof
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'))
    monkeypatch.setattr('radar.qa.today.resolve_hermes_binary', lambda: None)
    monkeypatch.delenv('TODAY_QA', raising=False)
    monkeypatch.delenv('RADAR_ALLOW_RULES_ONLY_PUBLISH', raising=False)
    seen = []

    def offline_http_verifier(db, url):
        seen.append(url)
        return source_link_proof(db, url)

    # Replace the network boundary, not cache selection or the QA rules.
    monkeypatch.setattr('radar.qa.provenance.verify_source', offline_http_verifier)
    report = run_today_qa(db, default_config())
    assert seen == [card.source_url]
    assert report.passed == report.cached == 1
    assert report.hermes_used is False
    assert qa_state(db, cid) == 'pass'


def test_default_run_rejects_fresh_dead_source_even_with_cached_hermes_pass(db, monkeypatch):
    from tests.fakes import source_link_proof
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'))
    monkeypatch.setattr('radar.qa.today.resolve_hermes_binary', lambda: None)
    monkeypatch.delenv('TODAY_QA', raising=False)
    monkeypatch.delenv('RADAR_ALLOW_RULES_ONLY_PUBLISH', raising=False)
    seen = []

    def dead_http_verifier(db, url):
        seen.append(url)
        return source_link_proof(db, url, status=404)

    monkeypatch.setattr('radar.qa.provenance.verify_source', dead_http_verifier)
    report = run_today_qa(db, default_config())
    assert seen == [card.source_url]
    assert report.rejected == 1 and report.passed == 0
    assert report.hermes_failures == 0
    assert qa_state(db, cid) == 'reject'


def test_fresh_dead_link_proof_vetoes_approved_company_and_pending_count(db):
    from prototype.server import build_today
    from tests.fakes import source_link_proof
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'))
    source_link_proof(db, card.source_url, status=410)
    assert qa_state(db, cid) == 'reject'
    totals = build_today(db.conn)['totals']
    assert totals['ready_to_review'] == totals['awaiting_final_check'] == 0


def test_expired_reachable_proof_withholds_production_approval(db):
    from tests.fakes import source_link_proof
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    source_link_proof(db, card.source_url)
    record_check(db, card, TodayCheckResult(verdict='pass', checker='hermes'))
    assert qa_state(db, cid) == 'pass'
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute('UPDATE source_link_check SET expires_at=?', (past,))
    assert qa_state(db, cid) == 'incomplete'


def test_exact_cached_rejection_cannot_be_overwritten_by_link_timeout(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    card = load_today_cards(db, default_config(), company_id=cid)[0]
    veto = TodayCheckResult(verdict='reject', reason='already_backed', checker='hermes', summary='Verified portfolio company')
    record_check(db, card, veto)
    checker = Checker()

    def should_not_check_link(db, url):
        pytest.fail('An exact completed rejection must return before source verification')

    result = check_one(db, card, checker=checker, source_verifier=should_not_check_link)
    assert result.verdict == 'reject'
    assert result.reason == 'already_backed'
    assert checker.calls == 0
    assert qa_state(db, cid) == 'reject'
    assert db.scalar('SELECT verdict FROM today_check WHERE company_id=? AND snapshot_hash=?',
                     (cid, card.snapshot_hash())) == 'reject'
