"""09-test-plan §7 — operations (FR-9), plus the §8 CI guard script.

These are the cheap tests that catch a deployment which "looks fine" but
silently never runs.

What lives elsewhere, deliberately not duplicated here:

* the §8 greps themselves — `test_banned_fuzzy_scorers_appear_nowhere` and
  `test_sub_score_is_never_coerced_to_zero` in `test_schema_privacy.py`. What
  is added below is the shell form §8 specifies, and a test that it is real.

* `test_every_run_writes_a_run_log_row` (FR-9.2) — `test_pipeline.py`
* `test_every_telegram_command_maps_to_a_cli_command` (FR-9.6) — `test_pipeline.py`
* `test_telegram_allowlist_rejects_unknown_user` (FR-8.4) — `test_pipeline.py`
* `test_sh01_sets_has_share_issue` (FR-1.6) and `test_postcode_to_geography`
  (FR-1.3) — `test_enrich.py`
* `test_unknown_value_policies` (FR-4.6) — `test_scoring.py`
* `test_timer_is_enabled_and_scheduled` (FR-9.1) and
  `test_env_file_is_0600_and_never_logged` (FR-9.5) — `tests/integration/`,
  because both need the VPS.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from radar.store.db import Db

from tests.factories import C, registry_company, store_company

REPO = Path(__file__).resolve().parents[2]


def _cli(args, **kw):
    from radar.cli import cli

    return CliRunner().invoke(cli, args, obj={}, **kw)


# --------------------------------------------------------- FR-9.3 heartbeat


@pytest.fixture
def telegram_outbox(monkeypatch):
    """Capture what the heartbeat would send. The suite blocks sockets, so the
    alternative to injecting here is not "a real message" but a crash."""
    import radar.notify.telegram as telegram

    sent: list[str] = []

    def _send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send_message", _send)
    return sent


def _db_with_last_run(path: Path, *, hours_ago: float, status: str = "ok",
                      items_fetched: int = 0) -> Db:
    db = Db(path)
    db.migrate()
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        """INSERT INTO run(started_at, finished_at, mode, status, items_fetched,
                           items_extracted, companies_new, companies_merged,
                           gated_out, shortlisted, llm_calls)
           VALUES (?,?,'daily',?,?,0,0,0,0,0,0)""",
        (stamp, stamp, status, items_fetched),
    )
    db.close()
    return db


def test_heartbeat_alerts_when_stale(tmp_path, telegram_outbox):
    """FR-9.3 — a run that stopped happening produces no error of its own,
    because nothing runs to produce one. A second clock is the only defence.

    Driven through `founder-radar status --alert-if-stale 26h`, which is the
    command 08-deployment §4 puts in the systemd unit — not the `python -m`
    entry point, so the thing under test is the thing that ships.
    """
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=27)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert len(telegram_outbox) == 1, telegram_outbox
    assert "Stale" in telegram_outbox[0]
    assert "27h" in telegram_outbox[0]
    assert result.exit_code == 1, "an alert must be visible to systemd too"


def test_heartbeat_is_quiet_when_the_run_is_recent(tmp_path, telegram_outbox):
    """The other half: an alert that fires every day is an alert that is muted
    by the second week."""
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=3)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert telegram_outbox == []
    assert result.exit_code == 0


def test_heartbeat_accepts_a_partial_run_that_did_work(tmp_path, telegram_outbox):
    """`partial` is this pipeline's normal Tuesday, not an outage.

    Any one of 23 sources failing marks the whole run `partial`, and at least
    one always does — Companies House has no key, northern_accelerator serves
    403. On the live box `ok` had never once been written in four runs, so the
    heartbeat alerted every morning while the pipeline was collecting 1,338
    companies a day. An alarm that fires daily is an alarm nobody reads, which
    is the exact failure FR-9.3 exists to prevent.

    Proof of life is therefore: it finished, and it fetched something.
    """
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=2, status="partial", items_fetched=1338)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])
    assert telegram_outbox == [], telegram_outbox
    assert result.exit_code == 0


def test_heartbeat_still_alerts_when_a_partial_run_fetched_nothing(
        tmp_path, telegram_outbox):
    """The concern the old rule was protecting, kept.

    A run where every source failed still writes a `partial` row, and counting
    that as proof of life would mask a pipeline that is dead on its feet. The
    difference is measurable: it fetched nothing.
    """
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=2, status="partial", items_fetched=0)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])
    assert len(telegram_outbox) == 1, telegram_outbox
    assert result.exit_code == 1


def test_status_without_the_flag_sends_nothing(tmp_path, telegram_outbox):
    """`status` is also a human command. Reading it must never page anyone."""
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=99)

    result = _cli(["--db", str(path), "status"])
    assert telegram_outbox == []
    assert result.exit_code == 0


def _db_with_blocked_source(path: Path, *, blocked_checks: int,
                            source_key: str = "northern_accelerator") -> Db:
    """A healthy recent run plus `blocked_checks` consecutive `degraded` days
    for one source — the shape the blocked-source heartbeat reads."""
    db = Db(path)
    db.migrate()
    stamp = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        """INSERT INTO run(started_at, finished_at, mode, status, items_fetched,
                           items_extracted, companies_new, companies_merged,
                           gated_out, shortlisted, llm_calls)
           VALUES (?,?,'daily','ok',0,0,0,0,0,0,0)""",
        (stamp, stamp),
    )
    today = date.today()
    for offset in range(blocked_checks):
        day = (today - timedelta(days=offset)).isoformat()
        db.execute(
            """INSERT INTO source_health(source_key, observed_on, items, status, note)
               VALUES (?,?,0,'degraded',?)""",
            (source_key, day,
             f"HTTP 403 from https://{source_key}.example — the site is "
             "refusing us (possible anti-bot block)"),
        )
    db.close()
    return db


def test_heartbeat_alerts_when_a_tier1_source_is_blocked_twice(tmp_path, telegram_outbox):
    """A Tier 1 source refused on two consecutive checks is dying, not flaky
    — the WAF variant of the source-down alert. One blocked day can be a WAF
    sneeze; two in a row is worth paging."""
    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=2)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert len(telegram_outbox) == 1, telegram_outbox
    assert "Source blocked" in telegram_outbox[0]
    assert "northern_accelerator" in telegram_outbox[0]
    assert "2 consecutive checks" in telegram_outbox[0]
    assert result.exit_code == 1


def test_heartbeat_reports_the_full_block_streak(tmp_path, telegram_outbox):
    """The alert says how long the block has been going on, so a month-long
    WAF block escalates visibly (the weekly repeat carries the new count)
    instead of repeating the same "2 checks" line."""
    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=5)

    _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert len(telegram_outbox) == 1, telegram_outbox
    assert "northern_accelerator (5 consecutive checks)" in telegram_outbox[0]


def test_heartbeat_ignores_a_broken_block_streak(tmp_path, telegram_outbox):
    """Consecutive means consecutive — a day the source came back resets the
    count, so a WAF that lets us through every other day never pages."""
    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=0)     # healthy run only
    db = Db(path)
    today = date.today()
    for offset, status in ((0, "degraded"), (1, "ok"), (2, "degraded")):
        db.execute(
            """INSERT INTO source_health(source_key, observed_on, items, status, note)
               VALUES (?,?,0,?,?)""",
            ("northern_accelerator", (today - timedelta(days=offset)).isoformat(),
             status, "x"),
        )
    db.close()

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert telegram_outbox == []
    assert result.exit_code == 0


def test_heartbeat_is_quiet_after_a_single_blocked_check(tmp_path, telegram_outbox):
    """One refused check is not yet an alert — the WAF sneeze case."""
    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=1)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert telegram_outbox == []
    assert result.exit_code == 0


def test_heartbeat_ignores_blocked_tier2_sources(tmp_path, telegram_outbox):
    """Tier 2 sources are allowed to be flaky — a blocked Tier 2 source is a
    row on the Sources tab, not a pager."""
    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=2, source_key="startups_magazine")

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert telegram_outbox == []
    assert result.exit_code == 0


def _seed_health(db: Db, source_key: str, *, days_ago_last: int, n: int,
                 items: int, status: str = "ok") -> None:
    """`n` consecutive daily observations ending `days_ago_last` days ago."""
    today = date.today()
    for offset in range(n):
        day = (today - timedelta(days=days_ago_last + offset)).isoformat()
        db.execute(
            "INSERT INTO source_health(source_key, observed_on, items, status) "
            "VALUES (?,?,?,?)", (source_key, day, items, status))


def test_a_retired_source_is_not_reported_as_gone_quiet(tmp_path):
    """A source that was switched off stops writing `source_health`. Its history
    (a healthy average, then silence) used to read as "went quiet" and the
    heartbeat named it every morning for ever. Only sources still being
    observed can go quiet."""
    from radar.notify.heartbeat import stale_sources

    db = Db(tmp_path / "radar.db")
    db.migrate()
    _seed_health(db, "retired_source", days_ago_last=40, n=12, items=20)
    _seed_health(db, "quiet_source", days_ago_last=0, n=12, items=0)
    # Healthy, then silent for the whole recent window (the gap keeps the test
    # clear of the local-date vs UTC-date edge of SQLite's `date('now')`):
    _seed_health(db, "gone_quiet", days_ago_last=10, n=12, items=30)
    _seed_health(db, "gone_quiet", days_ago_last=0, n=6, items=0)
    db.close()

    db = Db(tmp_path / "radar.db")
    assert stale_sources(db) == ["gone_quiet"]


def test_a_source_alert_is_said_once_then_weekly(tmp_path):
    """One blocked source must be one alert, not a message every morning for a
    month. First crossing alerts; the days after are silent; a week later it
    is said again (with the longer streak); when the condition clears and
    returns it is treated as new."""
    from radar.notify.heartbeat import check

    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=3)
    db = Db(path)
    sent: list[str] = []

    def sender(body: str) -> bool:
        sent.append(body)
        return True

    t0 = datetime.now(timezone.utc)      # `stale_after` is 30d below: only sources matter here
    first = check(db, now=t0, sender=sender, check_disk=False, stale_after="30d")
    assert first.alerts and first.sent and len(sent) == 1

    day2 = check(db, now=t0 + timedelta(days=1), sender=sender, check_disk=False, stale_after="30d")
    assert day2.alerts == [] and day2.ok, "a known, recently announced block must stay quiet"
    assert day2.suppressed == ["blocked:northern_accelerator"]
    assert "Still open" in day2.lines()[-1]
    assert len(sent) == 1

    week = check(db, now=t0 + timedelta(days=7, minutes=1), sender=sender, check_disk=False, stale_after="30d")
    assert week.alerts and len(sent) == 2

    # The block clears (a healthy check today), then comes back: announced afresh.
    db.execute("UPDATE source_health SET status = 'ok', items = 5")
    cleared = check(db, now=t0 + timedelta(days=8), sender=sender, check_disk=False, stale_after="30d")
    assert cleared.alerts == [] and cleared.suppressed == []
    db.execute("UPDATE source_health SET status = 'degraded', items = 0")
    back = check(db, now=t0 + timedelta(days=9), sender=sender, check_disk=False, stale_after="30d")
    assert back.alerts and len(sent) == 3


def test_an_undelivered_alert_is_not_treated_as_announced(tmp_path):
    """If Telegram is down the alert must be tried again tomorrow, not silenced
    for a week by a message that never arrived."""
    from radar.notify.heartbeat import check

    path = tmp_path / "radar.db"
    _db_with_blocked_source(path, blocked_checks=2)
    db = Db(path)
    t0 = datetime.now(timezone.utc)

    down = check(db, now=t0, sender=lambda body: False, check_disk=False, stale_after="30d")
    assert down.alerts and not down.sent

    later = check(db, now=t0 + timedelta(days=1), sender=lambda body: True, check_disk=False, stale_after="30d")
    assert later.alerts and later.sent


def test_a_stale_run_is_never_suppressed(tmp_path):
    """Only source-level alerts are rate limited. The whole pipeline being dead
    is said every time the heartbeat runs."""
    from radar.notify.heartbeat import check

    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=40)
    db = Db(path)
    t0 = datetime.now(timezone.utc)
    sent: list[str] = []
    for day in range(3):
        result = check(db, now=t0 + timedelta(days=day), sender=lambda b: sent.append(b) or True,
                       check_disk=False)
        assert result.alerts
    assert len(sent) == 3


def test_doctor_reports_a_placeholder_user_agent_but_does_not_fail_on_it(tmp_path, monkeypatch):
    """A default crawler contact is a warning row, never a deploy blocker: the
    update script runs `doctor` on every deploy."""
    import json as _json

    path = tmp_path / "radar.db"
    Db(path).migrate()
    monkeypatch.delenv("RADAR_USER_AGENT", raising=False)
    rows = {r["check"]: r for r in _json.loads(
        _cli(["--db", str(path), "--json", "doctor"]).output)}
    assert rows["crawler User-Agent"]["ok"] is False
    assert "example.com" in rows["crawler User-Agent"]["detail"]

    monkeypatch.setenv("RADAR_USER_AGENT",
                       "founder-radar/2.0 (+https://radar.example.co.uk/c; ops@example.co.uk)")
    rows = {r["check"]: r for r in _json.loads(
        _cli(["--db", str(path), "--json", "doctor"]).output)}
    assert rows["crawler User-Agent"]["ok"] is True


# ---------------------------------------------------- failure alerts (OnFailure)


def test_failure_alert_says_which_unit_failed_and_what_to_do(tmp_path):
    from radar.notify.alert import notify

    sent: list[str] = []
    db = Db(tmp_path / "radar.db")
    db.migrate()

    assert notify("founder-radar-update.service", db=db,
                  sender=lambda body: sent.append(body) or True)
    assert "founder-radar-update.service" in sent[0]
    assert "auto-deploy" in sent[0]
    assert "journalctl -u founder-radar-update.service" in sent[0]


def test_failure_alert_is_rate_limited_per_unit(tmp_path):
    """The update timer retries every five minutes. A broken deploy must be one
    message, not twelve an hour — but a *different* unit failing is news, and
    so is the same one six hours later."""
    from radar.notify.alert import notify

    sent: list[str] = []
    db = Db(tmp_path / "radar.db")
    db.migrate()
    t0 = datetime.now(timezone.utc)

    def send(body: str) -> bool:
        sent.append(body)
        return True

    assert notify("founder-radar-update.service", db=db, sender=send, now=t0)
    assert not notify("founder-radar-update.service", db=db, sender=send,
                      now=t0 + timedelta(minutes=5))
    assert notify("founder-radar-backup.service", db=db, sender=send,
                  now=t0 + timedelta(minutes=5))
    assert notify("founder-radar-update.service", db=db, sender=send,
                  now=t0 + timedelta(hours=6, minutes=1))
    assert len(sent) == 3


def test_failure_alert_retries_when_delivery_fails(tmp_path):
    from radar.notify.alert import notify

    db = Db(tmp_path / "radar.db")
    db.migrate()
    assert not notify("founder-radar.service", db=db, sender=lambda body: False)
    assert notify("founder-radar.service", db=db, sender=lambda body: True)


def test_failure_alert_still_sends_without_a_database():
    from radar.notify.alert import notify

    sent: list[str] = []
    assert notify("founder-radar.service", db=None,
                  sender=lambda body: sent.append(body) or True)
    assert sent


def test_failure_alert_rejects_a_malformed_unit_name():
    """The name arrives from systemd's `%i`, but it lands in a message and a
    `_meta` key — refuse anything that is not a unit name."""
    from radar.notify.alert import notify

    with pytest.raises(ValueError):
        notify("x; rm -rf /", db=None, sender=lambda body: True)


# ------------------------------------------------------------ FR-9.4 backups


def _age(path: Path, days: int) -> None:
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))


def test_backup_creates_and_prunes(tmp_path):
    """FR-9.4 — "backed up daily, with 14 days retained" is two promises, and
    the second one is the one that silently stops being true.

    Pruning happens only after the new snapshot is on disk, so a failed backup
    can never be the thing that deletes the last good one.
    """
    db_path = tmp_path / "data" / "radar.db"
    backups = tmp_path / "data" / "backups"
    Db(db_path).migrate()

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0
    snapshots = list(backups.glob("radar-*.db"))
    assert len(snapshots) == 1
    assert snapshots[0].stat().st_size > 0

    # Age the existing snapshot past the window, under the name it would have
    # had when it was taken — a same-day re-run overwrites rather than accrues.
    old = backups / f"radar-{date.today() - timedelta(days=15)}.db"
    snapshots[0].rename(old)
    _age(old, days=15)

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0

    kept = sorted(backups.glob("radar-*.db"))
    assert not [f for f in kept if time.time() - f.stat().st_mtime > 14 * 86_400], \
        "a snapshot older than the 14-day window survived"
    assert len(kept) == 1
    assert kept[0].name == f"radar-{date.today()}.db"


def test_backup_keeps_everything_inside_the_window(tmp_path):
    """Retention prunes by age, not by count. A week of history must survive."""
    db_path = tmp_path / "radar.db"
    backups = tmp_path / "backups"
    Db(db_path).migrate()
    backups.mkdir()

    for days in (1, 5, 13):
        recent = backups / f"radar-{date.today() - timedelta(days=days)}.db"
        recent.write_bytes(b"")
        _age(recent, days=days)

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0
    assert len(list(backups.glob("radar-*.db"))) == 4


def test_backup_never_touches_files_that_are_not_ours(tmp_path):
    """`radar-*.db` only. A backup directory shared with anything else must
    come out the other side untouched."""
    db_path = tmp_path / "radar.db"
    backups = tmp_path / "backups"
    Db(db_path).migrate()
    backups.mkdir()
    stranger = backups / "important-notes.txt"
    stranger.write_text("do not delete")
    _age(stranger, days=400)

    _cli(["--db", str(db_path), "db", "backup"])
    assert stranger.exists()


# ------------------------------------------------- FR-4.7 / NFR-6 sheet edits


def _config_with(**settings):
    """A fresh `Config` with the Settings tab edited. Fresh, not mutated: two
    configs that share a `Settings` object would share a hash."""
    from radar.config.defaults import default_config

    cfg = default_config()
    return cfg.model_copy(
        update={"settings": cfg.settings.model_copy(update=settings)}, deep=True)


def test_sheet_edit_changes_scores_with_no_code_change():
    """FR-4.7 / NFR-6, committed to the client on 9 July: changing a fund's
    criteria is a sheet edit, not a deploy.

    `max_company_age_months` is the one the whole rebuild is about. A company
    two years old is a candidate at 36 months and a reject at 12, and the
    config hash moves with it so the two answers never collide in the score
    table.
    """
    from tests.factories import score_one

    company = C(age_months=24)

    generous = _config_with(max_company_age_months=36)
    strict = _config_with(max_company_age_months=12)

    before = score_one(company, "northstar", generous)
    after = score_one(company, "northstar", strict)

    assert before.tier != after.tier
    assert after.tier == "reject"
    assert after.reject_reason == "max_company_age_months"
    assert before.config_hash != after.config_hash

    # And nothing in `radar/` had to move for that to be true.
    assert generous.hash() == _config_with(max_company_age_months=36).hash()


def test_sheet_edit_to_a_weight_changes_the_ranking():
    """The other half of the 9 July promise: a *weight* edit re-ranks, with no
    code change either."""
    from tests.factories import score_one

    cfg = _config_with()
    climate = C(sector="climate_tech", geography="north_east", age_months=12)
    saas = C(sector="b2b_saas", geography="north_east", age_months=12)

    base_gap = score_one(climate, "northstar", cfg).fund_fit_pct \
        - score_one(saas, "northstar", cfg).fund_fit_pct

    tuned = cfg.model_copy(deep=True)
    tuned.weights.matrix["sector"]["b2b_saas"]["northstar"] = 4
    tuned.weights.matrix["sector"]["climate_tech"]["northstar"] = 0

    tuned_gap = score_one(climate, "northstar", tuned).fund_fit_pct \
        - score_one(saas, "northstar", tuned).fund_fit_pct

    assert base_gap != tuned_gap
    assert cfg.hash() != tuned.hash()


# --------------------------------------------------- NFR-5 adding a source


NEW_ADAPTER = '''
"""A whole new source. One file — this one — and one registry line."""

from datetime import date

from radar.sources.base import RawItem


class TynesideTechAdapter:
    key = "tyneside_tech"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    endpoint = "https://example.test/tyneside/feed.json"

    def fetch(self, ctx):
        yield RawItem(
            source_key=self.key,
            source_url="https://example.test/tyneside/quayside-robotics",
            external_id="quayside-robotics",
            published_at=date(2026, 8, 1),
            title="Quayside Robotics raises \\u00a3900k pre-seed",
            # A real article, not a stub. The prefilter floor is 400 characters
            # precisely so a stub never reaches the reader, and a news adapter
            # that emitted 40 characters was never exercising the path a news
            # adapter actually takes.
            body_text="<html><body><p>Quayside Robotics Ltd has raised "
                      "\\u00a3900k in pre-seed funding to expand its autonomous "
                      "warehouse fleet across the North East. The Newcastle "
                      "company was founded by two former marine engineers and "
                      "builds picking robots for cold-storage sites, where "
                      "existing automation struggles with condensation and "
                      "sub-zero temperatures. The round was led by a regional "
                      "angel syndicate with participation from two university "
                      "funds. Quayside Robotics says the money will take it "
                      "from three pilot sites to twelve by the end of next "
                      "year, and will fund a second engineering hire in "
                      "Gateshead.</p></body></html>",
        )


ADAPTER = TynesideTechAdapter()
'''

# Everything a new source must NOT have to touch. If adding a source means
# editing any of these, the 9 July promise is broken.
SHARED_MODULES = (
    "radar/pipeline.py",
    "radar/resolve/match.py",
    "radar/resolve/merge.py",
    "radar/render/sheet.py",
    "radar/render/digest.py",
    "radar/score/criteria.py",
    "radar/score/fund_fit.py",
    "radar/score/gates.py",
    "radar/score/tiering.py",
    "radar/store/schema.sql",
)


def test_adding_a_source_touches_no_shared_code(db, config, tmp_path, monkeypatch):
    """NFR-5, the client's 9 July promise: "if we add more sources later,
    straightforward to extend".

    Asserted statically rather than against a fixture commit hash — a test that
    depends on a particular commit existing in the history is a test that dies
    at the first rebase. The check is the same one a reviewer would make:

    1. the shared modules name no source, so none of them can need editing;
    2. the registry really is one line per source, and `register()` is public;
    3. a brand-new adapter file, written here and never imported by anything
       in `radar/`, fetches, resolves and scores end to end.
    """
    from radar.sources import REGISTRY, SOURCE_MODULES
    from radar.sources.base import FetchContext, SourceAdapter

    # ---- 1. the shared modules are source-agnostic -----------------------
    known = set(SOURCE_MODULES) - {"companies_house"}   # Track B is a pipeline stage
    for relative in SHARED_MODULES:
        text = (REPO / relative).read_text()
        named = sorted(key for key in known if key in text)
        assert not named, f"{relative} names {named} — adding a source would touch it"

    # ---- 2. the registry is one line per source --------------------------
    for key, module_path in SOURCE_MODULES.items():
        assert module_path == f"radar.sources.{key}", \
            f"{key} does not follow the one-line convention"
        assert (REPO / "radar" / "sources" / f"{key}.py").is_file()
    assert hasattr(REGISTRY, "register"), "registering a source is not a public API"

    # ---- 3. a new file plus that one line is genuinely enough ------------
    module = tmp_path / "tyneside_tech.py"
    module.write_text(NEW_ADAPTER)
    monkeypatch.syspath_prepend(str(tmp_path))
    # The whole diff to `radar/`: this one call, which in a real change is one
    # line in `SOURCE_MODULES`. Undone in `finally` so it cannot leak into the
    # rest of the session — the registry is process-wide.
    REGISTRY.register("tyneside_tech", "tyneside_tech")
    try:
        adapter = REGISTRY["tyneside_tech"]
        assert isinstance(adapter, SourceAdapter), "the protocol is the whole contract"

        from radar.pipeline import extract_stage, resolve_item, score_company
        from radar.sources import fetch_all

        ctx = FetchContext(http=None, config=config, db=db, now=date(2026, 8, 8))
        result = fetch_all(ctx, [adapter], db=db, observed_on=date(2026, 8, 8))
        assert result.source("tyneside_tech").status == "ok"
        assert len(result.items) == 1

        # Stage ③ before stage ④, as the run does. A prose source becomes a
        # company by being *read*, never by having its headline promoted: this
        # step used to be skipped here, and `resolve_item` covered for it by
        # falling back to `item.title` — which is how the client ended up with
        # a sheet full of headlines instead of companies.
        items = extract_stage(result.items, config, use_llm=False, db=db)
        company_id = resolve_item(db, items[0], config)
        assert company_id, "the new source produced no company"
        assert db.scalar("SELECT canonical_name FROM company WHERE id = ?",
                         (company_id,)) == "Quayside Robotics"
        score_company(db, company_id, config, today=date(2026, 8, 8))
        assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?",
                         (company_id,)) > 0
    finally:
        REGISTRY._modules.pop("tyneside_tech", None)
        REGISTRY._cache.pop("tyneside_tech", None)


# ------------------------------------------------- Phase 3 enrichment budget


class CountingCH:
    """A Companies House double that counts requests and answers everything."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, url, **kw):                                     # noqa: ARG002
        self.requests.append(url)
        return _Resp(_payload_for(url))


class _Resp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status = status
        self.text = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self):
        return self._payload


def _payload_for(url: str):
    if "filing-history" in url:
        return {"items": []}
    if "persons-with-significant-control" in url:
        return {"items": []}
    if "/officers" in url:
        return {"items": [{
            "name": "LOVELACE, Ada",
            "officer_role": "director",
            "appointed_on": "2026-01-05",
            "links": {"officer": {"appointments": "/officers/abc123/appointments"}},
        }]}
    if "/appointments" in url:
        return {"total_results": 1, "items": []}
    if "/company/" in url:
        return {
            "date_of_creation": "2026-01-15",
            "company_status": "active",
            "sic_codes": ["72110"],
            "registered_office_address": {
                "postal_code": "NE1 4ST",
                "locality": "Newcastle",
            },
        }
    return {}


def _queue(db, count: int) -> None:
    for index in range(count):
        store_company(db, C(
            canonical_name=f"Queued {index}",
            norm_key=f"queued{index}",
            companies_house_no=f"{10_000_000 + index}",
            discovery_route="registry",
            age_months=4,
        ))


def test_enrichment_respects_budget(db):
    """The Phase 3 done-criterion (10-build-plan): the budget counts requests,
    not companies, and running out is a clean stop rather than a crash.

    Companies House *bans* an application for repeated breaches rather than
    throttling it, so an over-eager first run is the most likely way to brick
    the key. Whatever the budget does not reach stays queued with
    `enriched_at IS NULL` for tomorrow — never dropped, never re-fetched twice.
    """
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 40)
    http = CountingCH()

    result = enrich_companies(db, http, api_key="test-key",
                              budget=RequestBudget(limit=9))

    assert len(http.requests) <= 9, f"{len(http.requests)} requests against a budget of 9"
    assert result.budget_spent <= result.budget_limit == 9

    # Full enrichment is 4-8 calls per company: nine requests must NOT have
    # been read as nine companies.
    assert result.enriched < 9

    still_queued = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NULL "
        "AND companies_house_no IS NOT NULL AND merged_into IS NULL")
    assert still_queued > 0
    assert result.queued == still_queued


def test_enrichment_budget_of_zero_makes_no_requests(db):
    """The boundary that matters on the very first run after a key rotation."""
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 5)
    http = CountingCH()
    result = enrich_companies(db, http, api_key="test-key", budget=RequestBudget(limit=0))

    assert http.requests == []
    assert result.enriched == 0
    assert result.queued == 5


def test_enrichment_resumes_where_the_budget_stopped(db):
    """A deferred company is deferred, not lost: the next run picks it up and
    does not re-spend pass-1 requests on the ones already checked."""
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 12)
    first = CountingCH()
    enrich_companies(db, first, api_key="k", budget=RequestBudget(limit=6))
    after_first = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NOT NULL")

    second = CountingCH()
    enrich_companies(db, second, api_key="k", budget=RequestBudget(limit=200))
    after_second = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NOT NULL")

    assert after_second > after_first
    assert db.scalar("SELECT COUNT(*) FROM company WHERE enriched_at IS NULL "
                     "AND companies_house_no IS NOT NULL") == 0
    filings_calls = [u for u in second.requests if "filing-history" in u]
    assert len(filings_calls) < 12, "pass 1 was re-paid for companies already checked"


def test_enrichment_does_not_starve_hydration_behind_new_filing_checks(db):
    """A large first-run queue must make progress past pass 1.

    In production the first run checked 500 companies' filing histories. On
    the next run those checked rows were followed by enough unchecked rows to
    consume the whole budget again, so officers/PSC never ran and no registry
    company could earn a qualifier. Keep budget for the already-checked cohort
    so the Today queue can make progress while the backlog drains.
    """
    from radar.enrich import RequestBudget, enrich_companies
    from radar.store.db import now_iso

    _queue(db, 10)
    first = db.query(
        "SELECT id FROM company WHERE companies_house_no IS NOT NULL "
        "ORDER BY incorporated_on DESC, id LIMIT 1"
    )[0]
    db.set_meta("ch_filings_checked:" + first["id"], now_iso())

    http = CountingCH()
    result = enrich_companies(db, http, api_key="k", budget=RequestBudget(limit=6))

    assert result.enriched >= 1
    assert any("/officers" in url for url in http.requests)


def test_incomplete_appointment_hydration_remains_queued(db):
    """Rows marked hydrated before pass 3 completes must be resumable."""
    from radar.enrich import enrichment_queue
    from radar.store.db import now_iso

    _queue(db, 1)
    company = db.one("SELECT id FROM company LIMIT 1")
    stamp = now_iso()
    db.execute(
        "UPDATE company SET enriched_at = ?, officer_count = 1 WHERE id = ?",
        (stamp, company["id"]),
    )

    assert any(row["id"] == company["id"] for row in enrichment_queue(db))

    db.set_meta("ch_appointments_complete:" + company["id"], stamp)
    assert not any(row["id"] == company["id"] for row in enrichment_queue(db))


# --------------------------------------------------------- §8 the CI greps


# The two patterns 09-test-plan §8 asks CI to grep for:
#
#   ! grep -rn "token_set_ratio\|partial_ratio\|WRatio" radar/
#   ! grep -rn "or 0\b.*sub_score\|sub_score or 0" radar/score/
#
# Both are already asserted directly, by `test_banned_fuzzy_scorers_appear_nowhere`
# and `test_sub_score_is_never_coerced_to_zero` in test_schema_privacy.py. What
# is missing is the shell form §8 actually specifies, for a CI job that wants
# the guards before anything is installed — and a test that the shell form is
# real, executable, and still agrees with the Python one.
GUARD_PATTERNS = ("token_set_ratio", "partial_ratio", "WRatio",
                  "sub_score or 0", "or 0\\b.*sub_score")


def test_ci_guard_script_runs_both_greps_and_passes():
    """`scripts/ci-guards.sh` must exist, be executable, carry both §8 greps,
    and pass on the tree as it stands."""
    import subprocess

    script = REPO / "scripts" / "ci-guards.sh"
    assert script.is_file(), "scripts/ci-guards.sh is missing"
    assert os.access(script, os.X_OK), "scripts/ci-guards.sh is not executable"

    body = script.read_text()
    for pattern in GUARD_PATTERNS:
        assert pattern in body, f"{pattern!r} is not guarded by ci-guards.sh"

    done = subprocess.run(["bash", str(script)], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_ci_guard_script_actually_fails_on_a_breach(tmp_path):
    """A guard that never goes red is decoration. Plant one breach of each
    pattern in a throwaway copy of the tree and watch the script catch it."""
    import shutil
    import subprocess

    script = REPO / "scripts" / "ci-guards.sh"
    for relative, line in (("radar/resolve/_probe.py", "s = fuzz.WRatio(a, b)\n"),
                           ("radar/score/_probe.py", "x = component.sub_score or 0\n")):
        sandbox = tmp_path / relative.replace("/", "_")
        sandbox.mkdir()
        shutil.copytree(REPO / "radar", sandbox / "radar",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (sandbox / "scripts").mkdir()
        shutil.copy2(script, sandbox / "scripts" / "ci-guards.sh")
        (sandbox / relative).write_text(line)

        done = subprocess.run(["bash", str(sandbox / "scripts" / "ci-guards.sh")],
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 1, f"{relative} slipped past the guard"


# ------------------------------------------------------------------ .env


def test_env_file_is_loaded_by_the_cli(tmp_path, monkeypatch):
    """The README's quick start is `cp .env.example .env`, then `doctor`.

    Nothing in the process read that file. The systemd unit loads it with
    `EnvironmentFile=`, so the server was always fine — but anyone following
    the documented local steps filled in a Companies House key and was then
    told the key was missing, which is the exact wall this sits behind.
    """
    from radar.cli import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "COMPANIES_HOUSE_API_KEY=abc-123\n"
        'export TELEGRAM_CHAT_ID="98765"\n'
        "MALFORMED_LINE_NO_EQUALS\n"
        "EMPTY_VALUE=\n"
    )
    for name in ("COMPANIES_HOUSE_API_KEY", "TELEGRAM_CHAT_ID", "EMPTY_VALUE"):
        monkeypatch.delenv(name, raising=False)

    assert load_env_file(env) == 2
    assert os.environ["COMPANIES_HOUSE_API_KEY"] == "abc-123"
    assert os.environ["TELEGRAM_CHAT_ID"] == "98765"       # export + quotes
    assert "EMPTY_VALUE" not in os.environ                 # blank is not a value


def test_a_real_environment_variable_beats_the_env_file(tmp_path, monkeypatch):
    """`CH_API_KEY=... founder-radar run` has to keep working, and systemd's
    own EnvironmentFile must not be second-guessed by a stray file on disk."""
    from radar.cli import load_env_file

    env = tmp_path / ".env"
    env.write_text("COMPANIES_HOUSE_API_KEY=from-the-file\n")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "from-the-shell")

    assert load_env_file(env) == 0
    assert os.environ["COMPANIES_HOUSE_API_KEY"] == "from-the-shell"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    """Most runs have no .env at all — systemd injects the variables."""
    from radar.cli import load_env_file

    assert load_env_file(tmp_path / "nope.env") == 0


def test_today_requires_verified_age_and_uk_presence(db):
    """Today is an opportunity queue, not the unknown-data research pool."""
    from prototype.server import build_today
    from radar.store.db import now_iso

    stamp = now_iso()

    def add(company, *, priority, source_key="uktn"):
        cid = store_company(db, company)
        db.execute(
            "INSERT INTO company_source(company_id, source_key, external_id, "
            "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            (cid, source_key, f"source-{cid}", "https://uktn.co.uk/story", stamp, stamp),
        )
        db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, "outward", "fund_ii", 80, 0.8, 70, priority, "watchlist", None,
             "Matches on stage.", None, "testhash", "1", stamp),
        )
        return cid

    valid = add(C(canonical_name="Verified UK Co", age_months=6, country="GB"), priority=90)
    news_unknown = add(
        C(canonical_name="Unknown Age Co", age_months=None, country="GB",
          discovery_route="news"),
        priority=100,
    )
    registry_unknown = add(
        registry_company(
            canonical_name="Registry Unknown Age Ltd",
            norm_key="registryunknownage",
            age_months=None,
            has_share_issue=True,
        ),
        priority=101,
        source_key="companies_house",
    )
    add(C(canonical_name="Old UK Co", age_months=60, country="GB"), priority=99)
    add(C(canonical_name="Funded UK Co", age_months=6, country="GB", funding=4_000_000), priority=98)
    add(C(canonical_name="Dubai Co", age_months=6, country="AE"), priority=95)
    add(C(canonical_name="Unverified Location Co", age_months=6, country=None,
          hq_region=None, hq_postcode=None, companies_house_no=None,
          hq_city="Dubai"), priority=94)

    payload = build_today(db.conn)
    shown = [c["company_id"] for c in payload["companies"]]
    assert news_unknown in shown
    assert valid in shown
    assert registry_unknown not in shown
    assert set(shown) == {news_unknown, valid}
    assert len(payload["companies"][0]["fund_scores"]) == 4

    diagnostics = payload["eligibility_diagnostics"]
    assert diagnostics["scored_companies"] == 7
    assert diagnostics["eligible_before_review"] == 2
    assert diagnostics["shown"] == 2
    assert diagnostics["excluded"] == 5
    assert {row["key"]: row["count"] for row in diagnostics["reasons"]} == {
        "age_unknown": 1,
        "max_company_age_months": 1,
        "max_total_funding_gbp": 1,
        "min_uk_presence": 1,
        "uk_unverified": 1,
    }
