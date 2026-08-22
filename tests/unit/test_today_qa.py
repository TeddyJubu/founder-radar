"""Today QA — Hermes subagent veto on the morning queue.

Scoring is not this module's job. These tests pin the only things it is
allowed to do: parse a subagent verdict, hide a reject from Today and the
digest, leave the score row alone, and fail open when Hermes is down unless
the deterministic pre-check already proved the card is wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from prototype.server import build_today
from radar.config.defaults import default_config
from radar.qa.today import (
    HermesSubagent,
    HermesUnavailable,
    InvalidVerdict,
    TodayCard,
    TodayCheckResult,
    is_rejected,
    latest_today_verdict,
    parse_verdict,
    record_check,
    rules_precheck,
    run_today_qa,
    subagent_prompt,
)
from tests.fakes import seed_companies


def _card(**kw) -> TodayCard:
    base = dict(company_id="c1", name="Kelvin Bio", city="Newcastle",
                region="north_east", stage="pre_seed")
    base.update(kw)
    return TodayCard(**base)


class ScriptedChecker:
    name = "scripted"

    def __init__(self, by_name: dict[str, TodayCheckResult] | None = None,
                 default: TodayCheckResult | None = None) -> None:
        self.by_name = by_name or {}
        self.default = default or TodayCheckResult(verdict="pass", checker="scripted")
        self.calls: list[str] = []

    def review(self, card: TodayCard) -> TodayCheckResult:
        self.calls.append(card.name)
        return self.by_name.get(card.name, self.default)


class BoomChecker:
    name = "hermes"

    def review(self, card: TodayCard) -> TodayCheckResult:
        raise HermesUnavailable("simulated outage")


# ------------------------------------------------------------------ parse


@pytest.mark.parametrize("text,verdict,reason", [
    ("VERDICT: PASS\nSUMMARY: Early-stage Newcastle spinout.", "pass", None),
    ("VERDICT: REJECT\nREASON: already_backed\nSUMMARY: Parkwalk.", "reject",
     "already_backed"),
    ('{"verdict":"reject","reason":"ipo","summary":"Filed for IPO."}',
     "reject", "ipo"),
])
def test_parse_verdict_shapes(text, verdict, reason):
    got = parse_verdict(text)
    assert got.verdict == verdict
    assert got.reason == reason


def test_parse_verdict_unknown_reason_falls_back():
    got = parse_verdict("VERDICT: REJECT\nREASON: vibes\nSUMMARY: No.")
    assert got.verdict == "reject"
    assert got.reason == "not_a_startup"


def test_parse_verdict_rejects_garbage():
    with pytest.raises(InvalidVerdict):
        parse_verdict("looks fine to me")
    with pytest.raises(InvalidVerdict):
        parse_verdict("")


# ------------------------------------------------------------------- rules


def test_rules_precheck_catches_ipo_copy():
    got = rules_precheck(_card(
        name="Spine",
        one_liner="Spine files for IPO on the London Stock Exchange",
    ))
    assert got is not None and got.verdict == "reject"
    assert got.reason == "ipo"
    assert got.checker == "rules"


def test_rules_precheck_catches_series_c():
    got = rules_precheck(_card(headlines=("Unibloom closes Series C",)))
    assert got is not None and got.reason == "late_stage"


def test_rules_precheck_catches_oxford_as_yorkshire():
    got = rules_precheck(_card(
        name="ionSIGHT", city="Oxford", region="uk_wide",
        geo_values=("yorkshire",), vehicle_key="fy_seedcorn",
    ))
    assert got is not None and got.reason == "geography_mismatch"


def test_rules_precheck_lets_a_newcastle_spinout_through():
    assert rules_precheck(_card()) is None


# ---------------------------------------------- hide / score untouched


def test_today_qa_hides_a_rejected_company(db):
    ids = seed_companies(db, count=2, shortlist=2)
    record_check(
        db, _card(company_id=ids[0], name="Dropped Co"),
        TodayCheckResult(
            verdict="reject", reason="already_backed",
            summary="Parkwalk portfolio.", checker="hermes",
        ),
    )
    payload = build_today(db.conn)
    shown = {row["company_id"] for row in payload["companies"]}
    assert ids[0] not in shown
    assert ids[1] in shown
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons["hermes_rejected"] == 1
    assert db.scalar(
        "SELECT tier FROM score WHERE company_id = ? LIMIT 1", (ids[0],)
    ) == "shortlist"


def test_today_qa_pass_still_shown(db):
    ids = seed_companies(db, count=1, shortlist=1)
    record_check(
        db, _card(company_id=ids[0]),
        TodayCheckResult(verdict="pass", checker="hermes", summary="Looks early."),
    )
    shown = {row["company_id"] for row in build_today(db.conn)["companies"]}
    assert ids[0] in shown


def test_digest_omits_a_rejected_company(db):
    from tests.unit.test_digest import DAY, seed_company, seed_score
    from radar.render.digest import render_digest

    keep = seed_company(db, "Kelvin Bio", domain="kelvinbio.com")
    drop = seed_company(db, "ionSIGHT", domain="ionsight.com")
    seed_score(db, keep, priority=90)
    seed_score(db, drop, priority=89)
    record_check(
        db, _card(company_id=drop, name="ionSIGHT"),
        TodayCheckResult(
            verdict="reject", reason="already_backed",
            summary="Oxford Innovation portfolio.", checker="hermes",
        ),
    )
    text = render_digest(db, period="today", on_date=DAY)
    assert "Kelvin Bio" in text
    assert "ionSIGHT" not in text


# ---------------------------------------------- runner / cache / fallback


def test_rules_reject_without_calling_hermes(db):
    ids = seed_companies(db, count=1, shortlist=1)
    db.execute(
        "UPDATE company SET one_liner = ? WHERE id = ?",
        ("Spine files for IPO on the London Stock Exchange", ids[0]),
    )
    checker = ScriptedChecker()
    report = run_today_qa(db, default_config(), checker=checker, use_hermes=True)
    assert report.rejected == 1
    assert checker.calls == []
    assert is_rejected(db, ids[0])
    assert latest_today_verdict(db, ids[0]) == "reject"


def test_hermes_reject_hides_a_clean_card(db):
    ids = seed_companies(db, count=2, shortlist=2)
    names = {
        db.scalar("SELECT canonical_name FROM company WHERE id = ?", (cid,)): cid
        for cid in ids
    }
    drop_name = next(iter(names))
    checker = ScriptedChecker({
        drop_name: TodayCheckResult(
            verdict="reject", reason="wrong_entity",
            summary="Matched the parent.", checker="scripted",
        ),
    })
    report = run_today_qa(db, default_config(), checker=checker)
    assert report.rejected == 1
    assert report.passed == 1
    assert is_rejected(db, names[drop_name])
    shown = {row["company_id"] for row in build_today(db.conn)["companies"]}
    assert names[drop_name] not in shown
    assert len(shown) == 1


def test_cached_snapshot_does_not_recall_hermes(db):
    ids = seed_companies(db, count=1, shortlist=1)
    checker = ScriptedChecker()
    run_today_qa(db, default_config(), checker=checker)
    run_today_qa(db, default_config(), checker=checker)
    assert checker.calls == [
        db.scalar("SELECT canonical_name FROM company WHERE id = ?", (ids[0],)),
    ]


def test_hermes_down_does_not_empty_today(db):
    ids = seed_companies(db, count=1, shortlist=1)
    report = run_today_qa(db, default_config(), checker=BoomChecker())
    assert report.rejected == 0
    assert not is_rejected(db, ids[0])
    shown = {row["company_id"] for row in build_today(db.conn)["companies"]}
    assert ids[0] in shown


def test_pipeline_invokes_today_qa(db, config, monkeypatch):
    from radar.pipeline import run_pipeline
    from radar.qa.today import TodayQaReport
    from tests.unit.test_pipeline import FakeHttp

    called: dict[str, int] = {"n": 0}

    def fake_qa(*args, **kwargs):
        called["n"] += 1
        return TodayQaReport(checked=0)

    monkeypatch.setattr("radar.qa.today.run_today_qa", fake_qa)
    run_pipeline(
        db, config=config, http=FakeHttp(), use_llm=False, gateway=None,
        now=date(2026, 8, 8),
    )
    assert called["n"] == 1


# ----------------------------------------------------------- hermes runner


def test_hermes_subagent_parses_a_reject():
    def runner(argv, **kw):
        assert "-q" in argv
        assert "today_card" in (kw.get("input") or "")

        class Completed:
            returncode = 0
            stdout = (
                "VERDICT: REJECT\nREASON: already_backed\n"
                "SUMMARY: Parkwalk portfolio company.\n"
            )
            stderr = ""

        return Completed()

    checker = HermesSubagent(binary="/usr/bin/hermes", runner=runner)
    got = checker.review(_card(name="ionSIGHT", city="Oxford"))
    assert got.verdict == "reject"
    assert got.reason == "already_backed"
    assert "Parkwalk" in got.summary


def test_hermes_subagent_missing_binary(monkeypatch):
    monkeypatch.setattr("radar.qa.today.shutil.which", lambda name: None)
    checker = HermesSubagent(binary=None, runner=lambda *a, **k: None)
    with pytest.raises(HermesUnavailable):
        checker.review(_card())


def test_subagent_prompt_is_the_skill_brief():
    text = subagent_prompt()
    assert "Today QA subagent" in text
    assert "VERDICT: REJECT" in text
    assert "already_backed" in text


def test_skill_maps_today_qa_to_the_cli():
    from pathlib import Path

    from radar.cli import cli
    from radar.notify.telegram import skill_commands

    argv = skill_commands("hermes/skills/founder-radar/SKILL.md")
    assert ["today-qa"] in argv
    assert "today-qa" in cli.commands
    prompt = Path("hermes/skills/founder-radar/references/today-check.md")
    assert prompt.is_file()
    assert "WRONG" in prompt.read_text()
