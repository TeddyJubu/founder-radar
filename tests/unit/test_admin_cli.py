"""`founder-radar admin ...` — the Control room's CLI twin of /admin.

Every test runs against a fresh temp database with no Google Sheet configured,
so the writes go to the snapshot and the audit log only.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from radar.cli import cli


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    for name in ("SHEET_ID", "RADAR_SHEET_ID", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(name, raising=False)
    return str(tmp_path / "r.db")


def _run(path, *args, as_json=False):
    argv = ["--db", path] + (["--json"] if as_json else []) + list(args)
    return CliRunner().invoke(cli, argv, obj={})


def _json(path, *args):
    result = _run(path, *args, as_json=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _setting(path, key):
    view = _json(path, "admin", "settings", "show")
    return next(s for s in view["settings"] if s["key"] == key)


def test_admin_help_lists_the_control_room_commands():
    result = CliRunner().invoke(cli, ["admin", "--help"], obj={})
    assert result.exit_code == 0
    for word in ("flow", "settings", "sources", "prompts", "changes", "rescore"):
        assert word in result.output


def test_prompts_set_show_reset_round_trip(db_path):
    default = _json(db_path, "admin", "prompts", "show", "extract.system")
    assert default["source"] == "default"
    assert "+" not in default["version"]

    result = _run(db_path, "admin", "prompts", "set", "extract.system",
                  "--text", "Custom test prompt body.")
    assert result.exit_code == 0, result.output

    edited = _json(db_path, "admin", "prompts", "show", "extract.system")
    assert edited["source"] == "override"
    assert edited["text"] == "Custom test prompt body."
    assert edited["version"].startswith(default["version"] + "+")

    listed = {p["key"]: p for p in _json(db_path, "admin", "prompts", "list")}
    assert listed["extract.system"]["source"] == "override"

    result = _run(db_path, "admin", "prompts", "reset", "extract.system")
    assert result.exit_code == 0, result.output

    back = _json(db_path, "admin", "prompts", "show", "extract.system")
    assert back["source"] == "default"
    assert back["version"] == default["version"]
    assert back["text"] == default["text"]


def test_prompts_set_needs_exactly_one_source_of_text(db_path):
    neither = _run(db_path, "admin", "prompts", "set", "extract.system")
    both = _run(db_path, "admin", "prompts", "set", "extract.system",
                "--text", "x", "--file", "nope.txt")
    assert neither.exit_code != 0
    assert both.exit_code != 0


def test_prompts_unknown_key_is_a_clean_error(db_path):
    result = _run(db_path, "admin", "prompts", "show", "no.such.prompt")
    assert result.exit_code != 0
    assert "unknown prompt" in result.output


def test_settings_bad_value_is_refused_and_nothing_changes(db_path):
    before = _setting(db_path, "shortlist_fit")["value"]

    result = _run(db_path, "admin", "settings", "set", "shortlist_fit", "abc")

    assert result.exit_code != 0
    assert "shortlist_fit" in result.output
    assert _setting(db_path, "shortlist_fit")["value"] == before
    assert _json(db_path, "admin", "changes") == []


def test_settings_set_is_shown_and_logged(db_path):
    result = _run(db_path, "admin", "settings", "set", "shortlist_fit", "65",
                  "--note", "test tweak")
    assert result.exit_code == 0, result.output
    assert "shortlist_fit" in result.output

    assert _setting(db_path, "shortlist_fit")["value"] == "65"

    changes = _json(db_path, "admin", "changes")
    setting_rows = [c for c in changes if c["kind"] == "setting"]
    assert setting_rows and setting_rows[0]["key"] == "shortlist_fit"
    assert setting_rows[0]["new_value"] == "65"
    assert setting_rows[0]["note"] == "test tweak"

    text = _run(db_path, "admin", "changes")
    assert text.exit_code == 0
    assert "shortlist_fit" in text.output


def test_prompt_edits_appear_in_changes(db_path):
    _run(db_path, "admin", "prompts", "set", "publish.brief", "--text", "Brief v2.")
    rows = _json(db_path, "admin", "changes")
    assert any(c["kind"] == "prompt" and c["key"] == "publish.brief" for c in rows)


def test_sources_set_turns_a_source_off(db_path):
    sources = _json(db_path, "admin", "settings", "show")["sources"]
    key = sources[0]["key"]

    result = _run(db_path, "admin", "sources", "set", key, "off")
    assert result.exit_code == 0, result.output

    after = {s["key"]: s for s in _json(db_path, "admin", "settings", "show")["sources"]}
    assert after[key]["enabled"] is False


def test_sources_unknown_key_is_a_clean_error(db_path):
    result = _run(db_path, "admin", "sources", "set", "no_such_source", "on")
    assert result.exit_code != 0
    assert "unknown source" in result.output


def test_flow_json_has_eight_stages(db_path):
    flow = _json(db_path, "admin", "flow")
    ids = [s["id"] for s in flow["stages"]]
    assert len(ids) == 8
    assert ids[0] == "config" and ids[-1] == "render"


def test_flow_text_has_one_line_per_stage(db_path):
    result = _run(db_path, "admin", "flow")
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    stage_lines = [line for line in lines if " — " in line]
    assert len(stage_lines) == 8
    assert stage_lines[0].startswith("① Config")
    assert any(line.startswith("latest run") for line in lines)


def test_rescore_is_logged(db_path):
    result = _run(db_path, "admin", "rescore", "--note", "manual")
    assert result.exit_code == 0, result.output
    rows = _json(db_path, "admin", "changes")
    assert any(c["kind"] == "rescore" and c["note"] == "manual" for c in rows)
