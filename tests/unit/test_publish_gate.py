"""Publish gate: refuse send when Hermes is required but unavailable."""

from __future__ import annotations

from radar.qa.publish import PublishIssue, pre_publish_check
from radar.qa.today import HermesUnavailable


class _FakeDiag:
    def __init__(self, reviewable: int = 0, poisoned: bool = False, scored: int = 5, other: int = 0):
        self.payload = {
            "poisoned_fund_criteria": poisoned,
            "scored_for_active_hash": scored,
            "scores_on_other_hashes": other,
            "tiers": {"shortlist": min(reviewable, 1), "watchlist": max(reviewable - 1, 0), "reject": 0},
            "last_run": {"shortlisted": reviewable},
            "config_hash": "abc",
            "likely_causes": [],
            "reviewable": reviewable,
        }


def test_hermes_skip_blocks_when_reviewable_scores_exist(db, monkeypatch):
    diag = _FakeDiag(reviewable=3, scored=10)

    def fake_diagnose(_db):
        return diag.payload

    monkeypatch.setattr("radar.render.today_diagnose.diagnose_today", fake_diagnose)

    def boom(_payload):
        raise HermesUnavailable("no hermes")

    monkeypatch.setattr("radar.qa.publish._run_hermes_publish_check", boom)

    report = pre_publish_check(db, use_hermes=True, heal=False)
    assert report.hermes_verdict == "skip"
    assert report.ok is False
    assert any("Hermes unavailable while reviewable" in w for w in report.warnings)


def test_hermes_skip_allows_quiet_zero_reviewable_day(db, monkeypatch):
    diag = _FakeDiag(reviewable=0, scored=5)

    monkeypatch.setattr(
        "radar.render.today_diagnose.diagnose_today",
        lambda _db: diag.payload,
    )
    monkeypatch.setattr(
        "radar.qa.publish._run_hermes_publish_check",
        lambda _p: (_ for _ in ()).throw(HermesUnavailable("no hermes")),
    )

    report = pre_publish_check(db, use_hermes=True, heal=False)
    assert report.hermes_verdict == "skip"
    assert report.ok is True


def test_hermes_skip_override_env(db, monkeypatch):
    diag = _FakeDiag(reviewable=4, scored=10)
    monkeypatch.setattr(
        "radar.render.today_diagnose.diagnose_today",
        lambda _db: diag.payload,
    )
    monkeypatch.setattr(
        "radar.qa.publish._run_hermes_publish_check",
        lambda _p: (_ for _ in ()).throw(HermesUnavailable("no hermes")),
    )
    monkeypatch.setenv("RADAR_ALLOW_RULES_ONLY_PUBLISH", "1")

    report = pre_publish_check(db, use_hermes=True, heal=False)
    assert report.ok is True


def test_resolve_hermes_binary_prefers_env(tmp_path, monkeypatch):
    from radar.qa.today import resolve_hermes_binary

    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(fake))
    monkeypatch.setattr("radar.qa.today.shutil.which", lambda _n: "/usr/bin/other-hermes")
    assert resolve_hermes_binary() == str(fake)


def test_skill_requires_decide_and_publish():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2]
            / "hermes/skills/founder-radar/SKILL.md").read_text()
    assert "founder-radar decide" in text
    assert "founder-radar publish --send" in text
    assert "publish-check" in text
