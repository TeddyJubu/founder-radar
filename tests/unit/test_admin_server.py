"""The Control room (`/admin`) over real HTTP.

The routes live in `prototype/server.py` and the logic in `radar/admin/`. These
tests drive the real handler on a loopback socket against a migrated file
database, the way the browser page and the Caddy-fronted server would. No
Google Sheet is touched: the Sheet env vars are removed, and where a Sheet
write is wanted it is a fake gateway patched in at `radar.admin.settings`.
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from radar.admin import settings as admin_settings
from radar.config.defaults import default_config
from radar.config.loader import save_snapshot
from radar.store.db import Db
from tests.fakes import FakeSheetGateway, seed_companies

STAGE_IDS = ["config", "fetch", "extract", "resolve", "enrich", "score", "today_qa", "render"]


@dataclass
class Admin:
    port: int
    path: str

    def read(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def shortlist_fit(self) -> str:
        status, body = get(self, "/api/admin/settings")
        assert status == 200
        return next(s["value"] for s in body["settings"] if s["key"] == "shortlist_fit")

    def change_count(self) -> int:
        return self.read("SELECT COUNT(*) FROM admin_change")[0][0]

    def last_good_hash(self) -> str | None:
        rows = self.read(
            "SELECT config_hash FROM config_snapshot WHERE is_last_good = 1 "
            "ORDER BY created_at DESC LIMIT 1")
        return rows[0][0] if rows else None


@pytest.fixture
def admin(tmp_path, monkeypatch):
    from prototype.server import _conn, make_handler

    for name in ("SHEET_ID", "RADAR_SHEET_ID", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(name, raising=False)

    path = tmp_path / "admin.db"
    setup = Db(str(path))
    setup.migrate()
    seed_companies(setup, count=3, shortlist=3)
    setup.close()

    conn = _conn(str(path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(conn))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Admin(server.server_port, str(path))
    finally:
        server.shutdown()
        server.server_close()
        conn.close()


def _send(admin: Admin, method: str, path: str, *, body: bytes | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", admin.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, raw
    finally:
        conn.close()


def request(admin: Admin, method: str, path: str, *, body: bytes | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, Any]:
    status, _, raw = _send(admin, method, path, body=body, headers=headers)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def get(admin: Admin, path: str) -> tuple[int, Any]:
    return request(admin, "GET", path)


def post(admin: Admin, path: str, payload: Any = None, *,
         content_type: str | None = "application/json",
         headers: dict[str, str] | None = None,
         raw: bytes | None = None) -> tuple[int, Any]:
    head = {"Content-Type": content_type} if content_type else {}
    head.update(headers or {})
    body = raw if raw is not None else json.dumps(payload).encode()
    return request(admin, "POST", path, body=body, headers=head)


class _SheetDown:
    """A configured Sheet whose reads fail, as when Google is unreachable."""

    def batch_get(self, ranges):
        raise RuntimeError("Sheets is unreachable")

    def batch_set(self, data, value_input_option):
        raise RuntimeError("Sheets is unreachable")


# ------------------------------------------------------------------- the page


def test_the_control_room_page_is_served(admin):
    status, headers, raw = _send(admin, "GET", "/admin")

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b"Control room" in raw


# ------------------------------------------------------------------ the flow


def test_flow_lists_the_eight_stages_in_run_order(admin):
    status, body = get(admin, "/api/admin/flow")

    assert status == 200
    assert [s["id"] for s in body["stages"]] == STAGE_IDS


# ------------------------------------------------------------------ settings


def test_settings_view_has_settings_and_sources(admin):
    status, body = get(admin, "/api/admin/settings")

    assert status == 200
    keys = {s["key"] for s in body["settings"]}
    assert "shortlist_fit" in keys
    assert body["sources"], "the Control room lists the sources"
    assert any(s["key"] == "uktn" for s in body["sources"])


def test_a_valid_setting_change_is_applied_logged_and_read_back(admin):
    status, body = post(admin, "/api/admin/settings",
                        {"changes": {"shortlist_fit": "65"}, "note": "t"})

    assert status == 200
    assert body["ok"] is True
    assert body["applied"] == {"shortlist_fit": "65"}
    assert body["sheet_sync"] == "skipped"          # no Sheet configured in this test
    assert "today_qa_needed" in body
    assert admin.shortlist_fit() == "65"

    status, changes = get(admin, "/api/admin/changes")
    assert status == 200
    row = next(c for c in changes["changes"] if c["kind"] == "setting")
    assert row["key"] == "shortlist_fit"
    assert row["old_value"] == "70"
    assert row["new_value"] == "65"
    assert row["note"] == "t"


def test_an_out_of_range_setting_is_a_400_and_changes_nothing(admin):
    status, body = post(admin, "/api/admin/settings",
                        {"changes": {"shortlist_fit": "999"}})

    assert status == 400
    assert body["error"] == "invalid settings"
    assert "shortlist_fit" in body["errors"]
    assert admin.shortlist_fit() == "70"
    assert admin.change_count() == 0


def test_a_cross_site_settings_post_is_refused_and_writes_nothing(admin):
    status, body = post(admin, "/api/admin/settings",
                        {"changes": {"shortlist_fit": "65"}},
                        headers={"Sec-Fetch-Site": "cross-site"})

    assert status == 403 and "error" in body
    assert admin.shortlist_fit() == "70"
    assert admin.change_count() == 0


def test_a_settings_post_without_json_content_type_is_a_415(admin):
    status, body = post(admin, "/api/admin/settings", content_type="text/plain",
                        raw=json.dumps({"changes": {"shortlist_fit": "65"}}).encode())

    assert status == 415
    assert admin.shortlist_fit() == "70"
    assert admin.change_count() == 0


def test_a_failed_sheet_write_is_a_502_and_nothing_changes(admin, monkeypatch):
    setup = Db(admin.path)
    setup.migrate()
    save_snapshot(setup, default_config(), is_last_good=True)
    setup.close()
    before_hash = admin.last_good_hash()
    assert before_hash is not None

    monkeypatch.setattr(admin_settings, "_gateway", lambda gateway: _SheetDown())

    status, body = post(admin, "/api/admin/settings",
                        {"changes": {"shortlist_fit": "65"}})

    assert status == 502
    assert "nothing was changed" in body["error"]
    assert admin.shortlist_fit() == "70"
    assert admin.change_count() == 0
    assert admin.last_good_hash() == before_hash


def test_a_successful_sheet_write_lands_in_the_settings_tab(admin, monkeypatch):
    sheet = FakeSheetGateway(["Settings"])
    sheet.grids["Settings"] = [
        ["Key", "Value", "Type", "Status", "Description"],
        ["shortlist_fit", "70", "int 0–100", "", ""],
    ]
    monkeypatch.setattr(admin_settings, "_gateway", lambda gateway: sheet)

    status, body = post(admin, "/api/admin/settings",
                        {"changes": {"shortlist_fit": "65"}})

    assert status == 200
    assert body["sheet_sync"] == "synced"
    assert sheet.grids["Settings"][1][1] == "65"
    assert admin.shortlist_fit() == "65"


# ------------------------------------------------------------------- sources


def test_a_source_can_be_switched_off(admin):
    status, body = post(admin, "/api/admin/sources", {"key": "uktn", "enabled": False})

    assert status == 200
    assert body["applied"] == {"uktn": "off"}
    _, view = get(admin, "/api/admin/settings")
    uktn = next(s for s in view["sources"] if s["key"] == "uktn")
    assert uktn["enabled"] is False


def test_an_unknown_source_is_a_404(admin):
    status, body = post(admin, "/api/admin/sources", {"key": "no-such-source", "enabled": False})

    assert status == 404
    assert "error" in body


def test_a_source_post_without_enabled_is_a_400(admin):
    status, body = post(admin, "/api/admin/sources", {"key": "uktn"})

    assert status == 400
    assert "error" in body


# ------------------------------------------------------------------- prompts


def test_the_three_prompts_are_listed(admin):
    status, body = get(admin, "/api/admin/prompts")

    assert status == 200
    keys = [p["key"] for p in body["prompts"]]
    assert sorted(keys) == sorted(["extract.system", "today_qa.brief", "publish.brief"])
    assert all(p["source"] == "default" for p in body["prompts"])


def test_a_prompt_override_round_trips_through_detail_preview_and_reset(admin):
    status, body = post(admin, "/api/admin/prompts",
                        {"key": "today_qa.brief", "text": "Be strict."})

    assert status == 200
    assert body["ok"] is True
    assert body["source"] == "override"
    assert "+" in body["version"]

    status, detail = get(admin, "/api/admin/prompts/today_qa.brief")
    assert status == 200
    assert detail["text"] == "Be strict."
    assert detail["source"] == "override"
    assert any(h["active"] for h in detail["history"])

    status, preview = get(admin, "/api/admin/prompts/today_qa.brief/preview")
    assert status == 200
    assert "Be strict." in preview["preview"]

    status, body = post(admin, "/api/admin/prompts/reset", {"key": "today_qa.brief"})
    assert status == 200
    assert body["source"] == "default"

    status, detail = get(admin, "/api/admin/prompts/today_qa.brief")
    assert detail["source"] == "default"
    assert detail["text"] == detail["default_text"]


def test_an_unknown_prompt_key_is_a_404(admin):
    status, body = get(admin, "/api/admin/prompts/no.such.prompt")

    assert status == 404
    assert "error" in body


def test_an_empty_prompt_text_is_a_400(admin):
    status, body = post(admin, "/api/admin/prompts",
                        {"key": "today_qa.brief", "text": "   "})

    assert status == 400
    assert "error" in body


# ------------------------------------------------------------------- rescore


def test_rescore_reports_the_same_config_hash_as_the_settings_view(admin):
    _, view = get(admin, "/api/admin/settings")

    status, body = post(admin, "/api/admin/rescore", {})

    assert status == 200
    assert body["ok"] is True
    assert body["rescore"]["config_hash"] == view["config_hash"]
