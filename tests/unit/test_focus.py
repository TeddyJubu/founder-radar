"""Fund focus and weekly rotation (client ask, Oct 2026)."""
from datetime import date

import pytest
from click.testing import CliRunner

from prototype.server import build_today
from radar import focus
from radar.notify.search_intent import classify, slash_rewrite
from radar.render.digest import render_today_ping
from tests.fakes import FUND_KEYS, seed_companies

MON = date(2026, 10, 19)


def test_an_explicit_focus_runs_for_its_days_then_ends(db):
    f = focus.set_focus(db, "Outward VC", 3, today=MON)
    assert (f.fund, f.until) == ("outward", date(2026, 10, 21))
    assert focus.active(db, date(2026, 10, 21)).fund == "outward"
    assert focus.active(db, date(2026, 10, 22)) is None
    focus.set_focus(db, "dsw", 7, today=MON)
    focus.clear_focus(db)
    assert focus.active(db, MON) is None


def test_the_rotation_applies_when_no_focus_runs_and_focus_wins(db):
    focus.set_rotation(db, focus.parse_rotation("mon=outward tue=outward wed=dsw"))
    assert focus.active(db, MON).describe() == "Outward VC (today's rotation)"
    assert focus.active(db, date(2026, 10, 21)).fund == "dsw"
    assert focus.active(db, date(2026, 10, 22)) is None          # Thursday: all funds
    focus.set_focus(db, "anticus", 1, today=MON)
    assert focus.active(db, MON).fund == "anticus"


@pytest.mark.parametrize("bad", ["moon=outward", "mon=nofund", "outward please"])
def test_bad_rotations_and_funds_are_refused(bad):
    with pytest.raises(focus.FocusInvalid):
        focus.parse_rotation(bad)


def test_focus_narrows_today_and_all_funds_overrides_it(db):
    ids = seed_companies(db, count=8, shortlist=2)
    fund = FUND_KEYS[2]
    focus.set_focus(db, fund, 7)
    payload = build_today(db.conn)
    expected = {cid for i, cid in enumerate(ids) if FUND_KEYS[i % 4] == fund}
    assert {c["company_id"] for c in payload["companies"]} == expected
    assert payload["fund_filter"] == fund and payload["focus"]["fund"] == fund
    assert len(build_today(db.conn, fund="all")["companies"]) == 8


def test_zero_matches_is_a_valid_focused_today(db):
    seed_companies(db, count=3, shortlist=0)          # dsw, northstar, outward
    focus.set_focus(db, "anticus", 7)
    assert build_today(db.conn)["companies"] == []


def test_the_morning_message_leads_with_the_focus(db, monkeypatch):
    monkeypatch.delenv("RADAR_WEB_DOMAIN", raising=False)
    monkeypatch.delenv("RADAR_WEB_PUBLIC_URL", raising=False)
    seed_companies(db, count=8, shortlist=2)
    focus.set_focus(db, "outward", 7)
    text = render_today_ping(db)
    assert "🎯 Focus: Outward VC until" in text
    assert "2 Outward VC matches on Today" in text
    assert "All funds:" in text


def test_focus_fund_cards_are_checked_first(db):
    from radar.config.defaults import default_config
    from radar.qa.today import load_today_cards
    ids = seed_companies(db, count=8, shortlist=2)
    focus.set_focus(db, "anticus", 7)
    order = [c.company_id for c in load_today_cards(db, default_config(), limit=100)]
    anticus = [cid for i, cid in enumerate(ids) if FUND_KEYS[i % 4] == "anticus"]
    assert order[:len(anticus)] == anticus


@pytest.mark.parametrize("text,command", [
    ("Focus on companies for Outward VC", "/focus outward 7"),
    ("focus on DSW for 3 days", "/focus dsw 3"),
    ("focus on anticus for 2 weeks", "/focus anticus 14"),
    ("clear focus", "/clearfocus"),
    ("schedule mon=outward tue=outward wed=dsw", "/schedule mon=outward tue=outward wed=dsw"),
])
def test_telegram_plain_language_becomes_a_command(text, command):
    assert slash_rewrite(text) == command


def test_focus_words_without_a_fund_are_left_to_hermes():
    assert classify("Focus on the dashboard") is None
    assert classify("scan outward") == "search"


def test_cli_focus_and_rotation(tmp_path):
    from radar.cli import cli
    path = str(tmp_path / "r.db")
    run = lambda *args: CliRunner().invoke(cli, ["--db", path, *args], obj={})
    out = run("focus", "outward", "--days", "5")
    assert out.exit_code == 0, out.output and "Focus: Outward VC" in out.output
    assert "Zero is a fine answer" in out.output
    assert run("focus", "nofund").exit_code != 0
    assert "No focus" in run("focus", "--clear").output
    out = run("rotation", "mon=outward", "wed=dsw")
    assert out.exit_code == 0 and "Mon Outward VC, Wed DSW" in out.output
    assert "no rotation" in run("rotation", "--clear").output
