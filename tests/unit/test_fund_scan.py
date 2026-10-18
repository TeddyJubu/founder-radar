"""A Telegram fund scan must land on Today, and say what it found for that fund.

Oct 2026: "Scan Outward VC" replied "found 4 companies" — the global shortlist
count, four companies decided weeks earlier — and none of them was on Today.
"""
from prototype.server import build_today
from radar.qa import today as qa_today
from radar.render.digest import render_fund_scan_ping
from tests.fakes import FUND_KEYS, seed_companies, source_link_proof


def _by_fund(ids):
    return {cid: FUND_KEYS[i % len(FUND_KEYS)] for i, cid in enumerate(ids)}


def test_the_fund_scan_reply_names_that_funds_cards_and_links_the_filter(db, monkeypatch):
    monkeypatch.setenv("RADAR_WEB_DOMAIN", "radar.example.test")
    ids = seed_companies(db, count=8, shortlist=2)
    fund = FUND_KEYS[1]
    names = {cid: db.scalar("SELECT canonical_name FROM company WHERE id = ?", (cid,))
             for cid in ids}
    text = render_fund_scan_ping(db, fund)
    mine = [cid for cid, f in _by_fund(ids).items() if f == fund]
    others = [cid for cid, f in _by_fund(ids).items() if f != fund]
    assert all(names[cid] in text for cid in mine)
    assert not any(names[cid] in text for cid in others)
    assert f"https://radar.example.test/?fund={fund}" in text


def test_a_fund_scan_with_nothing_new_says_so(db, monkeypatch):
    monkeypatch.delenv("RADAR_WEB_DOMAIN", raising=False)
    monkeypatch.delenv("RADAR_WEB_PUBLIC_URL", raising=False)
    seed_companies(db, count=3, shortlist=0)
    text = render_fund_scan_ping(db, "nofund")
    assert "No new" in text and "•" not in text


def test_today_can_be_filtered_to_one_fund(db):
    ids = seed_companies(db, count=8, shortlist=2)
    fund = FUND_KEYS[0]
    expected = {cid for cid, f in _by_fund(ids).items() if f == fund}
    payload = build_today(db.conn, fund=fund)
    shown = {c["company_id"] for c in payload["companies"]}
    assert shown == expected and payload["fund_filter"] == fund
    assert len(build_today(db.conn)["companies"]) == 8


def test_fund_scoped_qa_only_checks_that_funds_cards(db):
    ids = seed_companies(db, count=8, shortlist=2)
    db.execute("DELETE FROM today_check")
    fund = FUND_KEYS[2]
    seen = []

    class Checker:
        name = "fake"

        def review(self, card):
            seen.append(card.company_id)
            return qa_today.TodayCheckResult(verdict="pass", checker="fake")

    qa_today.run_today_qa(db, None, checker=Checker(), fund_key=fund,
                          source_verifier=source_link_proof)
    assert set(seen) == {cid for cid, f in _by_fund(ids).items() if f == fund}
