"""FR-9.7 — the VPS Hermes agent can diagnose and repair Founder Radar.

`founder-radar repair` is the complete interface (FR-9.6): anything the chat
layer does, a human can do here. Hermes on the box follows the skill; this
module is what the skill is allowed to run for operational remediations, and
what systemd runs at 09:05 / on a fatal scan.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from radar.store.db import Db

REPO = Path(__file__).resolve().parents[2]
REPAIR_SH = REPO / "deploy" / "hermes-repair.sh"


def _cli(args, **kw):
    from radar.cli import cli

    return CliRunner().invoke(cli, args, obj={}, **kw)


def _db(path: Path, *, hours_ago: float = 2, status: str = "ok",
        items_fetched: int = 10) -> Db:
    db = Db(path)
    db.migrate()
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        """INSERT INTO run(started_at, finished_at, mode, status, items_fetched,
                           items_extracted, companies_new, companies_merged,
                           gated_out, shortlisted, llm_calls, llm_cost_usd)
           VALUES (?,?,'daily',?,?,0,0,0,0,0,0,0)""",
        (stamp, stamp, status, items_fetched),
    )
    return db


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_ROOT", str(tmp_path))
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    return tmp_path


def test_repair_reports_healthy_when_the_box_is_fine(env):
    path = env / "data" / "radar.db"
    _db(path)
    result = _cli(["--db", str(path), "repair"])
    assert "Healthy." in result.output
    assert result.exit_code == 0


def test_repair_apply_migrates_an_empty_database(env):
    """A diagnostic that can see 'no tables' must be able to create them."""
    path = env / "data" / "radar.db"
    db = Db(path)
    assert db.tables() == set()
    db.close()

    result = _cli(["--db", str(path), "repair", "--apply"])
    db = Db(path)
    assert db.tables(), result.output
    assert any("migrated" in line.lower() for line in result.output.splitlines()), result.output


def test_repair_apply_prunes_old_backups_when_disk_is_low(env, monkeypatch):
    from radar.ops import repair as repair_mod

    path = env / "data" / "radar.db"
    _db(path)
    backups = env / "backups"
    backups.mkdir()
    old = backups / "radar-2000-01-01.db"
    old.write_bytes(b"old")
    os.utime(old, (time.time() - 20 * 86_400, time.time() - 20 * 86_400))
    fresh = backups / "radar-today.db"
    fresh.write_bytes(b"new")

    monkeypatch.setattr(repair_mod, "_disk_free_mb",
                        lambda p: (200, "200 MB free"))

    result = _cli(["--db", str(path), "repair", "--apply"])
    assert "pruned" in result.output, result.output
    assert not old.exists()
    assert fresh.exists(), "the newest snapshot must survive"


def test_repair_never_starts_a_pipeline_in_process(env, monkeypatch):
    """`--run` talks to systemd on the live box. It must not import the
    crawler — a 25-minute diagnostic is not a diagnostic."""
    import radar.ops.repair as repair_mod

    called = []

    def boom(*a, **k):
        called.append("pipeline")
        raise AssertionError("repair imported the pipeline")

    monkeypatch.setattr("radar.pipeline.run_pipeline", boom, raising=False)

    path = env / "data" / "radar.db"
    _db(path, hours_ago=40)
    result = _cli(["--db", str(path), "repair", "--apply", "--run"])
    assert called == []
    assert "not the live box" in result.output or "pass --run" in result.output


def test_repair_flags_quiet_sources_as_needing_hermes(env):
    path = env / "data" / "radar.db"
    db = _db(path)
    today = date.today()
    for offset in range(15):
        day = (today - timedelta(days=offset)).isoformat()
        items = 0 if offset < 8 else 10
        db.execute(
            """INSERT INTO source_health(source_key, observed_on, items, status)
               VALUES (?,?,?,'ok')""",
            ("uktn", day, items),
        )
    db.close()

    result = _cli(["--db", str(path), "--json", "repair"])
    payload = json.loads(result.output)
    assert payload["needs_agent"] is True, payload
    assert result.exit_code == 1


def test_repair_auto_writes_and_clears_the_hermes_request(env):
    path = env / "data" / "radar.db"
    db = _db(path)
    today = date.today()
    for offset in range(15):
        day = (today - timedelta(days=offset)).isoformat()
        items = 0 if offset < 8 else 10
        db.execute(
            """INSERT INTO source_health(source_key, observed_on, items, status)
               VALUES (?,?,?,'ok')""",
            ("uktn", day, items),
        )
    db.close()
    request = env / "logs" / "hermes-repair.requested"

    result = _cli(["--db", str(path), "repair", "--auto",
                   "--request-file", str(request)])
    assert request.is_file(), result.output
    payload = json.loads(request.read_text())
    assert payload["needs_agent"] is True

    # A later healthy pass must not leave yesterday's request sitting around
    # for the 09:05 job to treat as a live incident.
    db = _db(path)  # fresh healthy run, overwrites? new insert, health rows remain
    # Clear source_health by using a brand-new db path.
    healthy = env / "data" / "healthy.db"
    _db(healthy)
    request2 = env / "logs" / "second.requested"
    request2.write_text("stale-request\n")
    result = _cli(["--db", str(healthy), "repair", "--auto",
                   "--request-file", str(request2)])
    assert not request2.exists(), result.output
    assert "Healthy." in result.output


def test_repair_redacts_secrets_in_error_log(env):
    path = env / "data" / "radar.db"
    _db(path)
    log_dir = env / "logs"
    log_dir.mkdir()
    (log_dir / "error.log").write_text(
        "real traceback here\n"
        "COMPANIES_HOUSE_API_KEY=super-secret-value exploded\n"
    )
    result = _cli(["--db", str(path), "repair"])
    assert "super-secret-value" not in result.output
    assert "API_KEY=***" in result.output or "error.log" in result.output


def test_repair_does_not_touch_scoring(env):
    """The repair module is the ops door. Scoring stays arithmetic."""
    source = (REPO / "radar" / "ops" / "repair.py").read_text()
    assert "radar.score" not in source
    assert "from radar.pipeline" not in source


def test_fix_telegram_command_maps_to_repair(monkeypatch):
    from radar.notify.telegram import handle

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")
    reply = handle(111, "/fix")
    assert reply.status == "ok"
    assert reply.argv == ["repair", "--apply"]
    assert reply.ran_pipeline is False


def test_skill_teaches_repair_and_forbids_scoring_edits():
    refs = REPO / "hermes" / "skills" / "founder-radar" / "references"
    skill = (REPO / "hermes" / "skills" / "founder-radar" / "SKILL.md").read_text()
    playbook = (refs / "repair.md").read_text()
    workflow = (refs / "workflow.md").read_text()
    review = (refs / "review-prompt.md").read_text()
    test_prompt = (refs / "test-prompt.md").read_text()
    assert "founder-radar repair --apply" in skill
    assert "radar/score/" in skill
    assert "Never edit" in skill
    assert "I'm on it" in skill
    assert "hermes/fix-" in workflow
    assert "hermes-ship.sh" in workflow
    assert "VERDICT: APPROVE" in workflow
    assert "VERDICT: PASS" in workflow
    assert "One sub-agent at a time" in workflow
    assert "delegation.worktree_isolation" in workflow
    assert "radar/score/" in playbook
    assert "hermes-ship.sh" in playbook
    assert "VERDICT: APPROVE" in review
    assert "Do **not** edit files" in review
    assert "VERDICT: PASS" in test_prompt
    assert "pip install -e" in test_prompt
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in test_prompt


def test_daily_scan_pages_hermes_only_on_fatal():
    unit = (REPO / "deploy" / "founder-radar.service").read_text()
    assert "OnFailure=founder-radar-repair.service" in unit
    assert "SuccessExitStatus=0 1" in unit


def test_hermes_repair_script_is_safe_to_run_without_hermes(tmp_path):
    """Missing Hermes is not a failed timer. Same rule as digest fallback."""
    assert REPAIR_SH.is_file()
    assert os.access(REPAIR_SH, os.X_OK)

    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    env = {
        **os.environ,
        "ROOT": str(root),
        "RADAR_HERMES_DRY_RUN": "1",
        "RADAR_HERMES_SCAN_ACTIVE": "0",
        "RADAR_REPAIR_LOCK": str(tmp_path / "repair.lock"),
        "RADAR_REPAIR_LOG": str(root / "logs" / "repair.log"),
        "RADAR_UPDATE_ALLOW_NONROOT": "1",
    }
    done = subprocess.run(["bash", str(REPAIR_SH)], env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    log = (root / "logs" / "repair.log").read_text()
    assert "dry-run" in done.stdout + done.stderr + log


def test_hermes_repair_skips_agent_while_the_scan_is_running(tmp_path):
    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    request = root / "logs" / "hermes-repair.requested"
    request.write_text('{"needs_agent": true}\n')
    fake = tmp_path / "fake-cli"
    fake.write_text("#!/bin/bash\necho '{}'\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "ROOT": str(root),
        "RADAR_CLI": str(fake),
        "RADAR_HERMES_DRY_RUN": "0",
        "RADAR_HERMES_FORCE": "1",
        "RADAR_HERMES_SCAN_ACTIVE": "1",
        "RADAR_HERMES_REQUEST": str(request),
        "RADAR_REPAIR_LOCK": str(tmp_path / "repair.lock"),
        "RADAR_REPAIR_LOG": str(root / "logs" / "repair.log"),
        "RADAR_REPAIR_JSON": str(root / "logs" / "repair-last.json"),
    }
    done = subprocess.run(["bash", str(REPAIR_SH)], env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    combined = done.stdout + done.stderr + (root / "logs" / "repair.log").read_text()
    assert "daily scan is running" in combined
    assert "would invoke" not in combined


def test_hermes_repair_without_binary_still_applies_ops(tmp_path):
    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    fake = tmp_path / "fake-cli"
    fake.write_text(
        "#!/bin/bash\n"
        "req=\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--request-file\" ]; then req=$2; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "json='{\"healthy\": false, \"needs_agent\": true, \"stale\": false}'\n"
        "if [ -n \"$req\" ]; then printf '%s\\n' \"$json\" > \"$req\"; fi\n"
        "printf '%s\\n' \"$json\"\n"
        "exit 1\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "ROOT": str(root),
        "RADAR_CLI": str(fake),
        "RADAR_HERMES_DRY_RUN": "0",
        "RADAR_HERMES_SCAN_ACTIVE": "0",
        "RADAR_HERMES_COOLDOWN_SEC": "0",
        "HERMES_BIN": str(tmp_path / "no-such-hermes"),
        "RADAR_HERMES_REQUEST": str(root / "logs" / "hermes-repair.requested"),
        "RADAR_REPAIR_LOCK": str(tmp_path / "repair.lock"),
        "RADAR_REPAIR_LOG": str(root / "logs" / "repair.log"),
        "RADAR_REPAIR_JSON": str(root / "logs" / "repair-last.json"),
    }
    done = subprocess.run(["bash", str(REPAIR_SH)], env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    combined = done.stdout + done.stderr + (root / "logs" / "repair.log").read_text()
    assert "hermes not installed" in combined
    assert (root / "logs" / "hermes-repair.requested").is_file()


def test_hermes_repair_prompt_sends_the_nontech_workflow():
    text = REPAIR_SH.read_text()
    assert "workflow.md" in text
    assert "hermes-ship.sh" in text
    assert "one at a time" in text
    assert "Never ask them to run a command" in text
