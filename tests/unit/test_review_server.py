"""The review UI's write endpoints, over real HTTP (audit M-10 and M-12).

`prototype/server.py` sits behind Caddy Basic Auth. A browser re-sends cached
Basic credentials on a request another site triggers, so the write endpoints
have to tell "the page the person is looking at" from "a page they happen to
have open". These tests drive the real handler on a loopback socket (the
suite's socket guard allows loopback) with the headers a browser or a script
would send, including a forged cross-origin form post.

The same server carries `POST /api/undo`; its answers (`undone: true` only
once the stored state is really back) are pinned here too.
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from radar.render.formatting import COMPANIES
from radar.render.sheet import mirror_verdict, sync_sheet
from radar.store.db import Db
from radar.verdict import record_verdict
from tests.fakes import FakeSheetGateway, seed_companies

VERDICT = "/api/verdict"
REVIEW_AGAIN = "/api/review-again"
UNDO = "/api/undo"


@dataclass
class Web:
    port: int
    path: str
    ids: list[str]
    sheet: FakeSheetGateway
    sheet_down: list[bool]        # [True] makes every Sheet call raise

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.host}"

    def read(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def verdicts(self, cid: str) -> list[tuple]:
        return self.read(
            "SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
            (cid,))

    def markers(self) -> int:
        return self.read("SELECT COUNT(*) FROM daily_review")[0][0]

    def today_ids(self) -> set[str]:
        status, body = get(self, "/api/today")
        assert status == 200
        return {c["company_id"] for c in body["companies"]}


@pytest.fixture
def web(tmp_path, monkeypatch):
    from prototype.server import _conn, make_handler

    for name in ("SHEET_ID", "RADAR_SHEET_ID", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(name, raising=False)

    path = tmp_path / "web.db"
    setup = Db(str(path))
    setup.migrate()
    ids = seed_companies(setup, count=3, shortlist=3)
    sheet = FakeSheetGateway()
    sync_sheet(setup, gateway=sheet, today=date(2026, 8, 8))
    # The render snapshots the config, after which Today only reads scores from
    # that config's hash; the seeded scores predate it. Drop it (as the
    # tests without a snapshot do) so the seeded queue is what Today serves.
    setup.execute("DELETE FROM config_snapshot")
    setup.close()

    conn = _conn(str(path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(conn))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Web(server.server_port, str(path), ids, sheet, [False])
    finally:
        server.shutdown()
        server.server_close()
        conn.close()


@pytest.fixture
def sheet_on(web, monkeypatch):
    """Point the server's Sheet mirror at the fake spreadsheet."""
    monkeypatch.setenv("SHEET_ID", "scratch")
    monkeypatch.setenv("GOOGLE_SA_JSON", "/nonexistent.json")

    def mirror(company_id, verdict):
        if web.sheet_down[0]:
            raise ConnectionError("Sheets is unreachable")
        return mirror_verdict(company_id, verdict, gateway=web.sheet)

    monkeypatch.setattr("radar.render.sheet.mirror_verdict", mirror)
    return web


def request(web: Web, method: str, path: str, *, body: bytes | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
    finally:
        conn.close()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw


def get(web: Web, path: str) -> tuple[int, Any]:
    return request(web, "GET", path)


def post(web: Web, path: str, payload: Any = None, *, headers: dict[str, str] | None = None,
         content_type: str | None = "application/json",
         raw: bytes | None = None) -> tuple[int, Any]:
    head = {"Content-Type": content_type} if content_type else {}
    head.update(headers or {})
    body = raw if raw is not None else json.dumps(payload).encode()
    return request(web, "POST", path, body=body, headers=head)


def decide(web: Web, cid: str, verdict: str, **kwargs) -> tuple[int, Any]:
    return post(web, VERDICT, {"company_id": cid, "verdict": verdict}, **kwargs)


# ------------------------------------------------------ M-12: same-origin writes


CROSS_SITE = [
    pytest.param({"Sec-Fetch-Site": "cross-site"}, id="fetch-metadata-cross-site"),
    pytest.param({"Sec-Fetch-Site": "same-site"}, id="fetch-metadata-sibling-subdomain"),
    pytest.param({"Sec-Fetch-Site": "none"}, id="fetch-metadata-none"),
    pytest.param({"Sec-Fetch-Site": "cross-site", "Origin": "OWN"}, id="metadata-beats-origin"),
    pytest.param({"Origin": "https://evil.example"}, id="foreign-origin"),
    pytest.param({"Origin": "null"}, id="null-origin"),
    pytest.param({"Origin": "http://127.0.0.1:1"}, id="same-host-other-port"),
]

SAME_ORIGIN = [
    pytest.param({}, id="non-browser-client-sends-neither"),
    pytest.param({"Sec-Fetch-Site": "same-origin"}, id="fetch-metadata"),
    pytest.param({"Origin": "OWN"}, id="origin-matches-host"),
    pytest.param({"Origin": "OWN", "Sec-Fetch-Site": "same-origin"}, id="both"),
]


def _headers(web: Web, headers: dict[str, str]) -> dict[str, str]:
    return {k: (web.origin if v == "OWN" else v) for k, v in headers.items()}


@pytest.mark.parametrize("headers", CROSS_SITE)
def test_a_cross_site_verdict_post_is_refused_and_writes_nothing(web, headers):
    status, body = decide(web, web.ids[0], "not for me", headers=_headers(web, headers))

    assert status == 403 and "error" in body
    assert web.verdicts(web.ids[0]) == []
    assert web.markers() == 0


@pytest.mark.parametrize("headers", SAME_ORIGIN)
def test_a_same_origin_or_non_browser_verdict_post_is_accepted(web, headers):
    status, body = decide(web, web.ids[0], "not for me", headers=_headers(web, headers))

    assert status == 200 and body["ok"] is True
    assert web.verdicts(web.ids[0]) == [("not for me",)]


def test_a_forged_cross_origin_form_cannot_clear_todays_review_markers(web):
    """The audit's scenario: a hostile page auto-submits a form to
    /api/review-again. A form can only send urlencoded/multipart/text and no
    custom headers, and the browser labels it cross-site."""
    decide(web, web.ids[0], "unsure")
    decide(web, web.ids[1], "not for me")
    assert web.markers() == 2

    forged = post(
        web, REVIEW_AGAIN, content_type="application/x-www-form-urlencoded", raw=b"",
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})

    assert forged[0] == 403
    assert web.markers() == 2


def test_review_again_needs_a_json_content_type_even_without_a_body(web):
    """The old endpoint cleared the markers for any body-less POST."""
    decide(web, web.ids[0], "unsure")

    for content_type in (None, "text/plain", "application/x-www-form-urlencoded"):
        status, body = post(web, REVIEW_AGAIN, content_type=content_type, raw=b"")
        assert status == 415, content_type
        assert "application/json" in body["error"]
    assert web.markers() == 1

    status, body = post(web, REVIEW_AGAIN, {})
    assert status == 200 and body == {"ok": True, "reset_count": 1}
    assert web.markers() == 0


def test_review_again_from_another_origin_is_refused_even_as_json(web):
    decide(web, web.ids[0], "unsure")

    status, _ = post(web, REVIEW_AGAIN, {}, headers={"Origin": "https://evil.example"})

    assert status == 403
    assert web.markers() == 1


@pytest.mark.parametrize("content_type", [
    None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data",
])
def test_verdict_post_must_be_json(web, content_type):
    body = json.dumps({"company_id": web.ids[0], "verdict": "unsure"}).encode()

    status, _ = post(web, VERDICT, content_type=content_type, raw=body)

    assert status == 415
    assert web.verdicts(web.ids[0]) == []


def test_json_content_type_with_a_charset_is_accepted(web):
    status, _ = decide(web, web.ids[0], "unsure",
                       headers={"Content-Type": "application/json; charset=utf-8"})
    assert status == 200


@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_a_malformed_body_is_a_400_not_a_crash(web, raw):
    status, body = post(web, VERDICT, raw=raw)
    assert status == 400 and "error" in body
    assert web.markers() == 0


def test_a_non_string_company_id_is_a_400(web):
    status, _ = post(web, VERDICT, {"company_id": ["x"], "verdict": "unsure"})
    assert status == 400


def test_unknown_post_routes_still_404(web):
    assert post(web, "/api/nonsense", {})[0] == 404


def test_reads_are_not_gated(web):
    """Only writes are same-origin-checked: the page and its API stay GETtable
    with any headers (the guide, curl, Caddy health checks)."""
    assert request(web, "GET", "/api/today",
                   headers={"Sec-Fetch-Site": "cross-site"})[0] == 200


# ------------------------------------------------------------- M-10: POST /api/undo


def test_undo_restores_the_stored_verdict_and_returns_the_card_to_the_queue(web):
    cid = web.ids[0]
    status, saved = decide(web, cid, "not for me")
    assert status == 200 and isinstance(saved["undo_id"], str) and saved["undo_id"]
    assert web.verdicts(cid) == [("not for me",)]
    assert cid not in web.today_ids()

    status, body = post(web, UNDO, {"undo_id": saved["undo_id"], "company_id": cid})

    assert status == 200
    assert body["undone"] is True and body["kept_count"] == 0
    assert web.verdicts(cid) == []
    assert web.markers() == 0
    assert cid in web.today_ids()


def test_undo_gives_back_the_kept_count(web):
    _, first = decide(web, web.ids[0], "worth contacting")
    _, second = decide(web, web.ids[1], "unsure")
    assert second["kept_count"] == 2

    status, body = post(web, UNDO, {"undo_id": second["undo_id"], "company_id": web.ids[1]})

    assert status == 200 and body["kept_count"] == 1
    assert web.verdicts(web.ids[0]) == [("worth contacting",)]
    assert first["undo_id"] != second["undo_id"]


def test_an_undo_can_only_be_used_once(web):
    cid = web.ids[0]
    _, saved = decide(web, cid, "unsure")
    ask = {"undo_id": saved["undo_id"], "company_id": cid}
    assert post(web, UNDO, ask)[0] == 200

    decide(web, cid, "not for me")            # decided again afterwards
    status, body = post(web, UNDO, ask)       # the old token must not reach it

    assert status == 404 and body["reason"] == "unknown_or_expired"
    assert web.verdicts(cid) == [("not for me",)]


@pytest.mark.parametrize("ask", [
    {"undo_id": "no-such-token", "company_id": "OWN"},
    {"undo_id": "TOKEN", "company_id": "someone-else"},
])
def test_undo_with_an_unknown_or_foreign_token_changes_nothing(web, ask):
    cid = web.ids[0]
    _, saved = decide(web, cid, "not for me")
    ask = {k: (saved["undo_id"] if v == "TOKEN" else cid if v == "OWN" else v)
           for k, v in ask.items()}

    status, _ = post(web, UNDO, ask)

    assert status == 404
    assert web.verdicts(cid) == [("not for me",)]


def test_undo_is_refused_when_the_verdict_changed_since(web):
    """Telegram (`founder-radar decide`) or a Sheet edit got there after the
    page: undoing must not put the older choice over the newer one."""
    cid = web.ids[0]
    _, saved = decide(web, cid, "not for me")
    other = sqlite3.connect(web.path)
    try:
        record_verdict(other, cid, "worth contacting")
    finally:
        other.close()

    status, body = post(web, UNDO, {"undo_id": saved["undo_id"], "company_id": cid})

    assert status == 409 and body["reason"] == "changed"
    assert web.verdicts(cid) == [("worth contacting",)]


def test_undo_is_refused_from_another_origin(web):
    cid = web.ids[0]
    _, saved = decide(web, cid, "not for me")

    status, _ = post(web, UNDO, {"undo_id": saved["undo_id"], "company_id": cid},
                     headers={"Sec-Fetch-Site": "cross-site"})

    assert status == 403
    assert web.verdicts(cid) == [("not for me",)]


def test_undo_requires_both_fields(web):
    assert post(web, UNDO, {"company_id": web.ids[0]})[0] == 400
    assert post(web, UNDO, {"undo_id": "x"})[0] == 400


# ----------------------------------------------------- undo and the Google Sheet


def _z(web: Web, cid: str) -> str:
    return web.sheet.at(COMPANIES, web.sheet.row_of(COMPANIES, cid), "Z")


def test_undo_clears_the_sheet_cell_a_first_decision_wrote(web, sheet_on):
    cid = web.ids[0]
    _, saved = decide(web, cid, "not for me")
    assert saved["sheet_sync"] == "synced" and _z(web, cid) == "not for me"

    status, body = post(web, UNDO, {"undo_id": saved["undo_id"], "company_id": cid})

    assert status == 200 and body["sheet_sync"] == "synced"
    assert _z(web, cid) == ""
    assert web.verdicts(cid) == []


def test_undo_puts_the_earlier_verdict_back_in_the_sheet_too(web, sheet_on):
    cid = web.ids[0]
    _, first = decide(web, cid, "unsure")
    assert _z(web, cid) == "unsure"
    _, second = decide(web, cid, "not for me")
    assert _z(web, cid) == "not for me"

    status, _ = post(web, UNDO, {"undo_id": second["undo_id"], "company_id": cid})

    assert status == 200
    assert _z(web, cid) == "unsure"
    assert web.verdicts(cid) == [("unsure",)]


def test_undo_changes_nothing_if_the_sheet_cannot_be_restored(web, sheet_on):
    """The Sheet wins the next sync, so restoring SQLite while the Sheet still
    holds the undone verdict would bring it straight back. Refuse instead, and
    let a retry succeed once the Sheet is reachable."""
    cid = web.ids[0]
    _, saved = decide(web, cid, "not for me")
    ask = {"undo_id": saved["undo_id"], "company_id": cid}

    web.sheet_down[0] = True
    status, body = post(web, UNDO, ask)

    assert status == 502 and body["reason"] == "sheet_unavailable"
    assert web.verdicts(cid) == [("not for me",)]
    assert web.markers() == 1
    assert _z(web, cid) == "not for me"

    web.sheet_down[0] = False
    assert post(web, UNDO, ask)[0] == 200
    assert web.verdicts(cid) == [] and _z(web, cid) == ""


def test_undo_skips_the_sheet_when_the_decision_never_reached_it(web, sheet_on):
    """A decision made during a Sheets outage left Z untouched, so its undo has
    nothing to restore there and must not fail just because the outage lasts."""
    cid = web.ids[0]
    web.sheet_down[0] = True
    _, saved = decide(web, cid, "not for me")
    assert saved["sheet_sync"] == "failed"

    status, body = post(web, UNDO, {"undo_id": saved["undo_id"], "company_id": cid})

    assert status == 200 and body["sheet_sync"] == "skipped"
    assert web.verdicts(cid) == []
