"""Control room settings and source edits (`radar/admin/settings.py`).

All-or-nothing is the property under test: a value stage ① would reject, or
a Sheet that cannot be written, leaves the snapshot, the scores and the
change log exactly as they were. A successful edit leaves Today reading the
score generation it just wrote, never an empty hash.
"""

from __future__ import annotations

import pytest

from radar.admin import settings
from radar.render.sheet import ValueRange
from radar.store.db import Db
from tests.fakes import FakeSheetGateway, seed_companies


@pytest.fixture
def db(tmp_path, monkeypatch):
    for var in ("SHEET_ID", "RADAR_SHEET_ID", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(var, raising=False)
    handle = Db(tmp_path / "r.db")
    handle.migrate()
    seed_companies(handle, count=5, shortlist=2)
    yield handle
    handle.close()


def _active_hash(db):
    """The generation Today reads (prototype/server.py `_active_config_hash`)."""
    from radar.config.loader import parse_snapshot

    row = db.one("SELECT config_json FROM config_snapshot WHERE is_last_good = 1")
    return parse_snapshot(row["config_json"]).hash() if row else None


def _state(db):
    return (
        db.query("SELECT config_hash, is_last_good FROM config_snapshot ORDER BY config_hash"),
        db.scalar("SELECT COUNT(*) FROM admin_change"),
        db.query("SELECT DISTINCT config_hash FROM score"),
    )


def _sheet(value="70"):
    gw = FakeSheetGateway(["Settings", "Sources"])
    gw.batch_set([
        ValueRange("'Settings'!A1:E2", [["Key", "Value", "Type", "Status", "Description"],
                                         ["shortlist_fit", value, "int 0–100", "", ""]]),
        ValueRange("'Sources'!A1:C2", [["Source", "Track", "Enabled"], ["uktn", "A", "TRUE"]]),
    ], "RAW")
    return gw


def _cell(gw, tab, a1):
    rng = f"'{tab}'!{a1}:{a1}"
    grid = gw.batch_get([rng])[rng]
    return grid[0][0] if grid and grid[0] else ""


def test_apply_snapshots_rescores_and_logs(db):
    result = settings.apply_settings(db, {"shortlist_fit": "65"}, note="tighter", gateway=None)
    assert result.applied == {"shortlist_fit": "65"}
    assert result.sheet_sync == "skipped"
    assert settings.settings_view(db)["config_source"] == "snapshot"
    view = {s["key"]: s["value"] for s in settings.settings_view(db)["settings"]}
    assert view["shortlist_fit"] == "65"
    # Today's active hash is exactly the generation the rescore wrote.
    assert _active_hash(db) == result.config_hash
    assert [r[0] for r in db.query("SELECT DISTINCT config_hash FROM score")] == [result.config_hash]
    change = settings.changes_view(db)[0]
    assert (change["kind"], change["key"], change["old_value"], change["new_value"],
            change["note"]) == ("setting", "shortlist_fit", "70", "65", "tighter")


@pytest.mark.parametrize("changes", [
    {"shortlist_fit": "abc"},
    {"shortlist_fit": "999"},
    {"max_stage": "unicorn"},
    {"not_a_setting": "1"},
    {"shortlist_fit": ""},
])
def test_invalid_values_change_nothing(db, changes):
    before = _state(db)
    with pytest.raises(settings.SettingsInvalid) as err:
        settings.apply_settings(db, changes, gateway=None)
    assert set(err.value.errors) == set(changes)
    assert "using last good" not in " ".join(err.value.errors.values())
    assert _state(db) == before


def test_one_bad_value_blocks_the_whole_edit(db):
    before = _state(db)
    with pytest.raises(settings.SettingsInvalid):
        settings.apply_settings(db, {"shortlist_fit": "60", "min_coverage": "5"}, gateway=None)
    assert _state(db) == before


def test_sheet_is_written_first(db):
    gw = _sheet()
    result = settings.apply_settings(db, {"shortlist_fit": "60", "daily_digest_max": "8"},
                                     gateway=gw)
    assert result.sheet_sync == "synced"
    assert _cell(gw, "Settings", "B2") == "60"
    # A key missing from the tab is appended as a full row.
    assert _cell(gw, "Settings", "A3") == "daily_digest_max"
    assert _cell(gw, "Settings", "B3") == "8"


def test_sheet_failure_changes_nothing(db):
    class Down:
        def batch_get(self, ranges):
            raise RuntimeError("quota")

    before = _state(db)
    with pytest.raises(settings.SheetUnavailable):
        settings.apply_settings(db, {"shortlist_fit": "60"}, gateway=Down())
    assert _state(db) == before


def test_unchanged_value_is_a_no_op(db):
    result = settings.apply_settings(db, {"shortlist_fit": "70"}, gateway=None)
    assert result.applied == {}
    assert db.scalar("SELECT COUNT(*) FROM admin_change") == 0


def test_source_toggle_writes_sheet_and_snapshot(db):
    gw = _sheet()
    result = settings.apply_source(db, "uktn", False, gateway=gw)
    assert result.applied == {"uktn": "off"}
    assert _cell(gw, "Sources", "C2") == "FALSE"
    src = {s["key"]: s["enabled"] for s in settings.settings_view(db)["sources"]}
    assert src["uktn"] is False
    assert _active_hash(db) == result.config_hash


def test_unknown_source(db):
    with pytest.raises(settings.UnknownSource):
        settings.apply_source(db, "nope", False, gateway=None)


def test_rescore_now_matches_the_snapshot(db):
    settings.apply_settings(db, {"shortlist_fit": "65"}, gateway=None)
    out = settings.rescore_now(db, note="manual")
    assert out["config_hash"] == _active_hash(db)
    assert settings.changes_view(db)[0]["kind"] == "rescore"
