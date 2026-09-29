"""A failed scan, a failed QA check, or a dry run must never reach Aryan.

Audit findings H-01, H-02, H-03, M-09 and M-01. They share one shape: a step
that did NOT finish was treated as if it had, and yesterday's (or unchecked)
results went out anyway.

* H-01  a failed scan exits 0, the unit starts `publish --send`, and the gate
        never asks whether the scan behind the numbers actually finished.
* H-02  a Hermes check that timed out was stored as a *pass* and cached.
* H-03  cards whose QA did not complete stayed on the live Today page, the Sheet
        and the ping.
* M-09  `publish` recorded a new reject but never refreshed the Sheet.
* M-01  `run --dry-run` promised "write nothing" and wrote companies and scores.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from prototype.server import build_today
from radar.cli import EXIT_FATAL, EXIT_OK, EXIT_PARTIAL, cli
from radar.config.defaults import default_config
from radar.qa import today as qa_today
from radar.qa.publish import PublishReport, format_publish_report, pre_publish_check
from radar.qa.today import (
    HermesUnavailable,
    TodayCard,
    TodayCheckResult,
    TodayQaReport,
    latest_today_verdict,
    record_check,
    run_today_qa,
)
from tests.fakes import seed_companies as _seed_companies

def seed_companies(db, **kwargs):
    ids = _seed_companies(db, **kwargs)
    db.execute("DELETE FROM today_check")
    return ids

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


# ------------------------------------------------------------------ helpers


def _iso(hours_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_run(db, *, status="ok", hours_ago=1.0, items=120, error=None,
             finished=True) -> int:
    """One `run` row that ended `hours_ago` hours ago."""
    db.execute(
        "INSERT INTO run(started_at, finished_at, mode, items_fetched, status, error) "
        "VALUES (?,?,?,?,?,?)",
        (_iso(hours_ago + 0.2), _iso(hours_ago) if finished else None, "daily",
         items, status, error),
    )
    return db.scalar("SELECT MAX(id) FROM run")


def _card(company_id: str, name: str = "Some Co") -> TodayCard:
    return TodayCard(company_id=company_id, name=name, city="Newcastle",
                     region="north_east", stage="pre_seed")


class Boom:
    """A Hermes that exists but times out / errors on every card."""

    name = "hermes"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def review(self, card):
        self.calls.append(card.name)
        raise HermesUnavailable("simulated timeout")


class Ok:
    name = "hermes"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def review(self, card):
        self.calls.append(card.name)
        return TodayCheckResult(verdict="pass", checker="hermes", summary="fine")


class FlakyOnFirst(Ok):
    """Fails for exactly one company, passes the rest."""

    def review(self, card):
        if not self.calls:
            self.calls.append(card.name)
            raise HermesUnavailable("one bad card")
        return super().review(card)


def _failed_result(**extra):
    base = dict(status="failed", error="OperationalError: database is locked",
                summary=lambda: {"status": "failed"}, warnings=[])
    base.update(extra)
    return SimpleNamespace(**base)


def _invoke(tmp_path, *args):
    return CliRunner().invoke(cli, ["--db", str(tmp_path / "r.db"), *args], obj={})


# =========================================================================
# H-01 — a failed scan must not exit 0 and must not be publishable
# =========================================================================


def test_run_exits_fatal_when_the_pipeline_reports_failed(monkeypatch, tmp_path):
    monkeypatch.setattr("radar.pipeline.run_pipeline",
                        lambda *a, **k: _failed_result())
    result = _invoke(tmp_path, "run", "--no-llm")
    assert result.exit_code == EXIT_FATAL, result.output


def test_run_still_exits_1_for_a_partial_scan_and_0_for_ok(monkeypatch, tmp_path):
    for status, code in (("partial", EXIT_PARTIAL), ("ok", EXIT_OK)):
        monkeypatch.setattr(
            "radar.pipeline.run_pipeline",
            lambda *a, _s=status, **k: SimpleNamespace(
                status=_s, summary=lambda: {"status": _s}, warnings=[]),
        )
        assert _invoke(tmp_path, "run", "--no-llm").exit_code == code


def test_a_failed_run_is_outside_the_units_success_exit_list():
    """Exit 1 (partial) is success on purpose; a failed scan must not be, or
    systemd goes on to `publish --send` and never fires OnFailure."""
    text = (DEPLOY / "founder-radar.service").read_text()
    line = next(ln for ln in text.splitlines() if ln.startswith("SuccessExitStatus="))
    accepted = {int(code) for code in line.split("=", 1)[1].split()}
    assert EXIT_OK in accepted and EXIT_PARTIAL in accepted
    assert EXIT_FATAL not in accepted
    steps = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
    assert steps[0].endswith("founder-radar run")
    assert steps[1].endswith("founder-radar publish --send")
    assert "OnFailure=founder-radar-alert@%n.service" in text


def test_search_sends_no_ping_after_a_failed_scan(monkeypatch, tmp_path):
    sent: list[str] = []
    monkeypatch.setattr("radar.pipeline.run_pipeline",
                        lambda *a, **k: _failed_result())
    monkeypatch.setattr("radar.notify.telegram.send_message",
                        lambda text, **k: sent.append(text) or True)
    result = _invoke(tmp_path, "search", "--no-llm", "--send")
    assert result.exit_code == EXIT_FATAL, result.output
    assert sent == []
    assert "failed" in result.output.lower()


def _gate(db):
    return pre_publish_check(db, use_hermes=False, heal=False)


def _codes(report):
    return {issue.code for issue in report.issues}


def test_gate_blocks_when_the_latest_scan_failed_even_after_a_good_one(db):
    seed_run(db, status="ok", hours_ago=25)
    seed_run(db, status="failed", hours_ago=1, error="OperationalError: no such table")
    report = _gate(db)
    assert report.ok is False
    assert "no_fresh_scan" in _codes(report)
    text = format_publish_report(report)
    assert "failed" in text and "OperationalError: no such table" in text


def test_gate_blocks_when_no_scan_has_ever_been_recorded(db):
    report = _gate(db)
    assert report.ok is False and "no_fresh_scan" in _codes(report)
    assert "founder-radar run" in format_publish_report(report)


def test_gate_blocks_a_scan_that_never_finished(db):
    """A run killed mid-flight stays `running`; yesterday's numbers are not today's."""
    seed_run(db, status="ok", hours_ago=26)
    seed_run(db, status="running", hours_ago=2, finished=False)
    report = _gate(db)
    assert report.ok is False and "no_fresh_scan" in _codes(report)
    assert "not finished" in format_publish_report(report)


def test_gate_blocks_a_stale_successful_scan(db):
    seed_run(db, status="ok", hours_ago=40)
    report = _gate(db)
    assert report.ok is False and "no_fresh_scan" in _codes(report)
    assert "40h ago" in format_publish_report(report) or "limit" in format_publish_report(report)


def test_gate_blocks_a_partial_scan_that_fetched_nothing(db):
    """Every source failed: same rule the heartbeat uses for 'not alive'."""
    seed_run(db, status="partial", hours_ago=1, items=0)
    report = _gate(db)
    assert report.ok is False and "no_fresh_scan" in _codes(report)


@pytest.mark.parametrize("status", ["ok", "partial"])
def test_gate_passes_after_a_fresh_finished_scan(db, status):
    seed_run(db, status=status, hours_ago=2, items=300)
    report = _gate(db)
    assert report.ok is True, format_publish_report(report)
    assert "no_fresh_scan" not in _codes(report)


# =========================================================================
# H-02 — a failed check is never a pass, never cached as one
# =========================================================================


def _states(db):
    return [r["verdict"] for r in db.query("SELECT verdict FROM today_check")]


def test_a_failed_hermes_check_is_recorded_incomplete_not_pass(db):
    ids = seed_companies(db, count=2, shortlist=2)
    report = run_today_qa(db, default_config(), checker=Boom())
    assert report.cards == 2
    assert report.passed == 0 and report.incomplete == 2
    assert _states(db) == ["incomplete", "incomplete"]
    assert latest_today_verdict(db, ids[0]) == "incomplete"
    assert report.hermes_used is False, "a selected checker that failed is not 'used'"


def test_an_incomplete_check_is_retried_next_time_not_served_from_cache(db):
    seed_companies(db, count=2, shortlist=2)
    run_today_qa(db, default_config(), checker=Boom())
    ok = Ok()
    report = run_today_qa(db, default_config(), checker=ok)
    assert len(ok.calls) == 2, "incomplete rows must be asked again"
    assert report.passed == 2 and report.incomplete == 0
    assert report.hermes_used is True
    assert set(_states(db)) == {"pass"}


def test_hermes_used_is_false_if_even_one_card_was_not_checked(db):
    seed_companies(db, count=3, shortlist=3)
    report = run_today_qa(db, default_config(), checker=FlakyOnFirst())
    assert report.incomplete == 1 and report.passed == 2
    assert report.hermes_used is False


def test_a_missing_hermes_binary_is_incomplete_not_a_pass(db, monkeypatch):
    ids = seed_companies(db, count=2, shortlist=2)
    monkeypatch.setattr("radar.qa.today.resolve_hermes_binary", lambda: None)
    monkeypatch.delenv("RADAR_ALLOW_RULES_ONLY_PUBLISH", raising=False)
    monkeypatch.delenv("TODAY_QA", raising=False)
    report = run_today_qa(db, default_config(), use_hermes=True)
    assert report.incomplete == 2 and report.passed == 0
    assert report.hermes_used is False
    assert qa_today.qa_state(db, ids[0]) == "incomplete"
    assert any("Hermes" in w for w in report.warnings)


def test_legacy_skip_passes_no_longer_stand_in_for_a_check(db):
    """Boxes already in service hold `pass / skip` rows written when Hermes was
    down. They must read as incomplete and be re-checked, not trusted."""
    ids = seed_companies(db, count=1, shortlist=1)
    name = db.scalar("SELECT canonical_name FROM company WHERE id = ?", (ids[0],))
    record_check(db, _card(ids[0], name),
                 TodayCheckResult(verdict="pass", checker="skip",
                                  summary="Hermes unavailable; rules found no obvious veto."))
    assert qa_today.qa_state(db, ids[0]) == "incomplete"
    checker = Ok()
    run_today_qa(db, default_config(), checker=checker)
    assert checker.calls == [name] or len(checker.calls) == 1
    assert qa_today.qa_state(db, ids[0]) == "pass"


def test_an_explicit_rules_only_run_still_works_but_never_counts_as_a_model_check(db):
    """`--no-llm` / `--no-hermes` is a deliberate mode (documented since day
    one): rules-only passes stay visible. They must not satisfy a later run
    that DOES have Hermes."""
    ids = seed_companies(db, count=2, shortlist=2)
    report = run_today_qa(db, default_config(), use_hermes=False)
    assert report.passed == 2 and report.incomplete == 0
    assert qa_today.qa_state(db, ids[0]) == "pass"
    assert {r["checker"] for r in db.query("SELECT checker FROM today_check")} == {"rules"}

    ok = Ok()
    run_today_qa(db, default_config(), checker=ok)
    assert len(ok.calls) == 2, "Hermes must re-ask cards only rules had passed"
    assert {r["checker"] for r in db.query("SELECT checker FROM today_check")} == {"hermes"}


def test_a_dead_hermes_is_abandoned_after_a_few_consecutive_failures(db):
    seed_companies(db, count=8, shortlist=8)
    boom = Boom()
    report = run_today_qa(db, default_config(), checker=boom)
    assert len(boom.calls) == qa_today.MAX_CONSECUTIVE_FAILURES
    assert report.incomplete == 8
    assert any("abandon" in w.lower() for w in report.warnings)


def test_diagnosis_counts_the_latest_rejects_and_incompletes(db):
    from radar.render.today_diagnose import diagnose_today

    ids = seed_companies(db, count=3, shortlist=3)
    record_check(db, _card(ids[0]), TodayCheckResult(
        verdict="reject", reason="ipo", checker="hermes"))
    record_check(db, _card(ids[1]), TodayCheckResult(
        verdict="incomplete", checker="hermes", summary="timeout"))
    report = diagnose_today(db)
    assert report["hermes_rejects_latest"] == 1
    assert report["qa_incomplete"] == 2


# =========================================================================
# H-03 — one rule, three surfaces
# =========================================================================


def _withheld_fixture(db):
    ids = seed_companies(db, count=4, shortlist=3)
    record_check(db, _card(ids[0], "A"), TodayCheckResult(
        verdict="incomplete", checker="hermes", summary="timeout"))
    record_check(db, _card(ids[1], "B"), TodayCheckResult(
        verdict="reject", reason="ipo", checker="hermes"))
    record_check(db, _card(ids[2], "C"), TodayCheckResult(
        verdict="pass", checker="hermes"))
    record_check(db, _card(ids[3], "D"), TodayCheckResult(verdict="pass", checker="hermes"))
    return ids


def test_the_live_today_page_withholds_an_incomplete_card(db):
    ids = _withheld_fixture(db)
    payload = build_today(db.conn)
    shown = {row["company_id"] for row in payload["companies"]}
    assert ids[0] not in shown, "QA incomplete must not be shown"
    assert ids[1] not in shown, "QA reject must not be shown"
    assert ids[2] in shown
    reasons = {r["key"]: r["count"] for r in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons.get("qa_incomplete") == 1
    assert reasons.get("hermes_rejected") == 1


def test_the_sheet_and_the_digest_apply_the_same_rule(db):
    from radar.render.digest import _shortlist
    from radar.render.sheet import build_today as sheet_today

    ids = _withheld_fixture(db)
    rows = sheet_today(db, default_config(), {}, today=date.today())
    text = " ".join(str(v) for row in rows for v in row.cells.values())
    names = {cid: db.scalar("SELECT canonical_name FROM company WHERE id = ?", (cid,))
             for cid in ids}
    assert names[ids[0]] not in text and names[ids[1]] not in text
    assert names[ids[2]] in text

    day = date.today()
    digest_ids = {r["company_id"] for r in
                  _shortlist(db, day - timedelta(days=1), day + timedelta(days=1))}
    assert ids[0] not in digest_ids and ids[1] not in digest_ids
    assert ids[2] in digest_ids


def test_the_ping_counts_only_cards_that_passed_and_says_what_is_held_back(db, monkeypatch):
    from radar.render.digest import render_today_ping

    monkeypatch.delenv("RADAR_WEB_DOMAIN", raising=False)
    monkeypatch.delenv("RADAR_WEB_PUBLIC_URL", raising=False)
    _withheld_fixture(db)                    # 3 shortlist + 1 watchlist; two withheld
    text = render_today_ping(db)
    assert "1 shortlisted · 1 watchlist" in text
    assert "held back" in text


def test_when_qa_is_unavailable_nothing_unchecked_reaches_any_surface(db, monkeypatch):
    """The scenario from the audit: Hermes is down during the morning run."""
    from radar.render.digest import render_today_ping

    ids = seed_companies(db, count=3, shortlist=2)
    monkeypatch.setattr("radar.qa.today.resolve_hermes_binary", lambda: None)
    monkeypatch.delenv("RADAR_ALLOW_RULES_ONLY_PUBLISH", raising=False)
    monkeypatch.delenv("TODAY_QA", raising=False)
    monkeypatch.delenv("RADAR_WEB_DOMAIN", raising=False)
    monkeypatch.delenv("RADAR_WEB_PUBLIC_URL", raising=False)
    run_today_qa(db, default_config(), use_hermes=True)
    assert build_today(db.conn)["companies"] == []
    assert "0 shortlisted · 0 watchlist" in render_today_ping(db)
    assert all(qa_today.qa_state(db, cid) == "incomplete" for cid in ids)


def _quiet_fetch(monkeypatch):
    monkeypatch.setattr(
        "radar.pipeline.fetch_stage",
        lambda *a, **k: ([], SimpleNamespace(items=[], sources=[], status="ok")),
    )
    monkeypatch.setattr("radar.pipeline.enrich_stage", lambda *a, **k: {})


def test_the_run_says_loudly_that_cards_were_withheld(db, config, monkeypatch):
    from radar.pipeline import run_pipeline

    _quiet_fetch(monkeypatch)
    monkeypatch.setattr(
        "radar.qa.today.run_today_qa",
        lambda *a, **k: TodayQaReport(cards=5, checked=3, passed=3, incomplete=2,
                                      warnings=["today QA: 2 not checked"]),
    )
    result = run_pipeline(db, config=config, http=object(), use_llm=False,
                          gateway=None, now=date(2026, 8, 8))
    assert result.qa_incomplete == 2
    assert result.status == "partial", "an unchecked morning is not a clean 'ok'"
    assert any("withheld" in w for w in result.warnings)
    stored = db.one("SELECT status, warnings FROM run ORDER BY id DESC LIMIT 1")
    assert stored["status"] == "partial" and "withheld" in stored["warnings"]


@pytest.mark.parametrize("how", ["raises", "aborted"])
def test_a_qa_stage_that_could_not_run_holds_back_the_sheet(db, config, monkeypatch, how):
    """The old code swallowed the exception and synced unchecked cards anyway."""
    from radar.pipeline import run_pipeline

    _quiet_fetch(monkeypatch)
    synced: list[int] = []
    monkeypatch.setattr("radar.render.sheet.sync_sheet",
                        lambda *a, **k: synced.append(1))

    def fake_qa(*a, **k):
        if how == "raises":
            raise RuntimeError("qa exploded")
        return TodayQaReport(aborted="OperationalError: database is locked",
                             warnings=["today QA skipped: OperationalError"])

    monkeypatch.setattr("radar.qa.today.run_today_qa", fake_qa)
    result = run_pipeline(db, config=config, http=object(), use_llm=False,
                          gateway=object(), now=date(2026, 8, 8))
    assert synced == [], "unchecked cards must not be pushed to the Sheet"
    assert result.status == "partial"
    assert any("Today QA did not run" in w for w in result.warnings)
    assert any("sheet not written" in w for w in result.warnings)


# =========================================================================
# publish CLI: refuse on incomplete QA (H-02) and sync the Sheet (M-09)
# =========================================================================


def _publish_rig(monkeypatch, tmp_path, *, qa_writes, report):
    """A real DB with one company, everything slow or networked replaced."""
    from radar.store.db import Db

    db = Db(str(tmp_path / "r.db"))
    db.migrate()
    ids = seed_companies(db, count=1, shortlist=1)
    seed_run(db, status="ok", hours_ago=1)
    events: list[str] = []

    monkeypatch.setattr("radar.qa.publish.pre_publish_check",
                        lambda *a, **k: PublishReport(ok=True, hermes_verdict="pass"))
    gateway = object()
    monkeypatch.setattr("radar.config.loader.load_runtime_config",
                        lambda *a, **k: (default_config(), gateway, []))

    def fake_qa(db_, cfg, **k):
        events.append("qa")
        if qa_writes:
            record_check(db_, _card(ids[0]), TodayCheckResult(
                verdict=qa_writes, reason="ipo" if qa_writes == "reject" else None,
                checker="hermes"))
        return report

    monkeypatch.setattr("radar.qa.today.run_today_qa", fake_qa)
    monkeypatch.setattr(
        "radar.render.sheet.sync_sheet",
        lambda db_, *, gateway=None, today=None: events.append(
            "sync" if gateway is not None else "sync-no-gateway"))
    monkeypatch.setattr("radar.notify.telegram.send_message",
                        lambda text, **k: events.append("send") or True)
    monkeypatch.setattr("radar.render.digest.render_today_ping", lambda _db: "PING")
    monkeypatch.delenv("RADAR_ALLOW_RULES_ONLY_PUBLISH", raising=False)
    db.close()
    return events


def test_publish_syncs_the_sheet_after_a_new_reject_and_before_the_ping(monkeypatch, tmp_path):
    events = _publish_rig(
        monkeypatch, tmp_path, qa_writes="reject",
        report=TodayQaReport(cards=1, checked=1, rejected=1, hermes_used=True))
    result = _invoke(tmp_path, "publish", "--send")
    assert result.exit_code == EXIT_OK, result.output
    assert events == ["qa", "sync", "send"]


def test_publish_does_not_touch_the_sheet_when_qa_changed_nothing(monkeypatch, tmp_path):
    events = _publish_rig(
        monkeypatch, tmp_path, qa_writes=None,
        report=TodayQaReport(cards=1, checked=1, passed=1, cached=1, hermes_used=True))
    result = _invoke(tmp_path, "publish", "--send")
    assert result.exit_code == EXIT_OK, result.output
    assert events == ["qa", "send"]


def test_a_sheet_failure_at_publish_is_a_warning_not_a_lost_ping(monkeypatch, tmp_path):
    events = _publish_rig(
        monkeypatch, tmp_path, qa_writes="reject",
        report=TodayQaReport(cards=1, checked=1, rejected=1, hermes_used=True))

    def boom(*a, **k):
        raise RuntimeError("sheets 503")

    monkeypatch.setattr("radar.render.sheet.sync_sheet", boom)
    result = _invoke(tmp_path, "publish", "--send")
    assert result.exit_code == EXIT_OK, result.output
    assert events == ["qa", "send"]
    assert "sheet" in result.output.lower() and "sheets 503" in result.output


def test_publish_refuses_and_stays_silent_while_any_card_is_unchecked(monkeypatch, tmp_path):
    events = _publish_rig(
        monkeypatch, tmp_path, qa_writes="incomplete",
        report=TodayQaReport(cards=3, checked=2, passed=2, incomplete=1,
                             hermes_used=False))
    result = _invoke(tmp_path, "publish", "--send")
    assert result.exit_code == EXIT_FATAL, result.output
    assert "send" not in events
    assert "incomplete" in result.output.lower()
    # The Sheet still reflects what QA just decided, even though we refused.
    assert "sync" in events


def test_rules_only_override_still_lets_publish_through(monkeypatch, tmp_path):
    events = _publish_rig(
        monkeypatch, tmp_path, qa_writes=None,
        report=TodayQaReport(cards=3, checked=3, passed=3, hermes_used=False))
    monkeypatch.setenv("RADAR_ALLOW_RULES_ONLY_PUBLISH", "1")
    result = _invoke(tmp_path, "publish", "--send")
    assert result.exit_code == EXIT_OK, result.output
    assert "send" in events


# =========================================================================
# M-01 — a dry run writes nothing
# =========================================================================




def test_never_checked_cards_are_withheld_from_today_and_sheet(db):
    from radar.render.sheet import build_today as sheet_today
    ids = seed_companies(db, count=1, shortlist=1)
    assert build_today(db.conn)["companies"] == []
    assert all(row.key.startswith("__") for row in
               sheet_today(db, default_config(), {}, today=date.today()))
    assert qa_today.is_withheld(db, ids[0])


def test_a_pass_before_the_latest_score_is_incomplete(db):
    ids = seed_companies(db, count=1, shortlist=1)
    record_check(db, _card(ids[0]), TodayCheckResult(verdict="pass", checker="hermes"),
                 checked_at="2020-01-01T00:00:00Z")
    assert qa_today.qa_state(db, ids[0]) == "incomplete"
    assert build_today(db.conn)["companies"] == []
