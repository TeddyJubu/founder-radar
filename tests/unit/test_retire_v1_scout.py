"""v1 sheet scout must stay dead across deploys."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "retire-v1-scout.sh"


def test_retire_script_is_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def _v1_job(workdir: str) -> dict:
    return {
        "id": "89682ea1d13c",
        "name": "founder-radar-daily",
        "enabled": True,
        "skill": "uk-founder-radar",
        "prompt": "Run the UK Founder Radar daily scan following the uk-founder-radar skill EXACTLY.",
        "workdir": workdir,
        "state": "scheduled",
    }


def test_retire_v1_scout_archives_skill_and_disables_cron(tmp_path):
    hermes = tmp_path / ".hermes"
    skill = hermes / "skills" / "uk-founder-radar"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# v1\n")
    cron = hermes / "cron"
    cron.mkdir()
    jobs = [
        _v1_job(str(tmp_path / "radar")),
        {
            "id": "keep-me",
            "name": "unrelated",
            "enabled": True,
            "skill": "other",
            "prompt": "hello",
            "state": "scheduled",
        },
    ]
    (cron / "jobs.json").write_text(json.dumps(jobs, indent=2) + "\n")

    out = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr + out.stdout
    assert not skill.exists()
    retired = hermes / "_retired" / "uk-founder-radar" / "SKILL.md"
    assert retired.is_file()
    saved = json.loads((cron / "jobs.json").read_text())
    by_id = {j["id"]: j for j in saved}
    assert by_id["89682ea1d13c"]["enabled"] is False
    assert by_id["89682ea1d13c"]["state"] == "disabled"
    assert by_id["keep-me"]["enabled"] is True
    memory = hermes / "memories" / "FOUNDER-RADAR-V2.md"
    assert memory.is_file()
    assert "uk-founder-radar" in memory.read_text()
    stub = tmp_path / "radar" / "sheets.py"
    assert stub.is_file()
    dead = subprocess.run([sys.executable, str(stub)], capture_output=True, text=True)
    assert dead.returncode == 2
    assert "retired" in dead.stderr


def test_retire_v1_scout_disables_dict_shaped_jobs_json(tmp_path):
    hermes = tmp_path / ".hermes"
    (hermes / "skills").mkdir(parents=True)
    cron = hermes / "cron"
    cron.mkdir()
    payload = {"updated_at": "now", "jobs": [_v1_job(str(tmp_path / "radar"))]}
    (cron / "jobs.json").write_text(json.dumps(payload, indent=2) + "\n")

    out = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr + out.stdout
    saved = json.loads((cron / "jobs.json").read_text())
    assert saved["jobs"][0]["enabled"] is False
    assert saved["jobs"][0]["state"] == "disabled"


def test_retire_v1_scout_is_a_noop_without_hermes(tmp_path):
    out = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr
    assert "nothing to retire" in out.stdout


def test_telegram_today_is_the_dashboard_ping():
    from radar.notify.telegram import COMMANDS, route

    assert COMMANDS["/today"] == ["today"]
    _, argv, _ = route("/today")
    assert argv == ["today"]
