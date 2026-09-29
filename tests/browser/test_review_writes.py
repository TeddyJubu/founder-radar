"""The review page's writes, in a real browser (audit M-10, M-11, M-12).

* M-10 - Ctrl+Z used to rewind only the page while the verdict stayed in
  SQLite. It now asks the server, and only says "Undone" once the stored
  verdict and today's review marker are really back.
* M-11 - while a save was pending, arrow keys and a second verdict stayed live,
  so a late response advanced from (or rolled back) the wrong card.
* M-12 - the write endpoints accepted a form post from any origin.

Requests are held or failed with `page.route` so the pending window is
deterministic rather than a race against a fast loopback server.

    pytest -m browser tests/browser/test_review_writes.py
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest

from tests.browser.conftest import tid

pytestmark = pytest.mark.browser

UNDO_KEY = "Control+z"


def _card_id(page) -> str:
    return page.locator(tid("card")).get_attribute("data-company-id")


def _wait_card_other_than(page, previous: str) -> None:
    page.wait_for_function(
        """prev => {
          const card = document.querySelector('[data-testid="card"]');
          return !!(card && card.getAttribute('data-company-id') !== prev);
        }""",
        arg=previous, timeout=10_000)


def _wait_card_is(page, wanted: str) -> None:
    page.wait_for_function(
        """id => {
          const card = document.querySelector('[data-testid="card"]');
          return !!(card && card.getAttribute('data-company-id') === id);
        }""",
        arg=wanted, timeout=10_000)


def _toast(page) -> str:
    return page.locator(tid("toast")).text_content() or ""


def _wait_toast(page, text: str) -> None:
    page.wait_for_function(
        """t => (document.querySelector('[data-testid="toast"]').textContent || '').includes(t)""",
        arg=text, timeout=10_000)


def _rows(demo_db, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(demo_db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _verdict_row(demo_db, cid: str) -> list[tuple]:
    return _rows(demo_db, "SELECT value, updated_at FROM user_field "
                          "WHERE field = 'verdict' AND company_id = ?", (cid,))


def _marker_rows(demo_db, cid: str | None = None) -> list[tuple]:
    if cid is None:
        return _rows(demo_db, "SELECT company_id FROM daily_review")
    return _rows(demo_db, "SELECT verdict FROM daily_review WHERE company_id = ?", (cid,))


# ------------------------------------------------------------------- M-11


def test_a_pending_save_blocks_navigation_and_a_second_decision(today, demo_db):
    """Decide A, then try to move to B and decide B before A's save returns.

    Unfixed, the arrow key moved on and the second verdict went out as a second
    request, whose late completion advanced from B and skipped a card.
    """
    ids = today.evaluate("() => data.companies.map(c => c.company_id)")
    assert len(ids) >= 3
    held: list = []
    today.route("**/api/verdict", lambda route: held.append(route))

    today.keyboard.press("3")                    # A: the request is now pending
    today.wait_for_timeout(250)
    today.keyboard.press("ArrowRight")           # navigation must be ignored
    today.keyboard.press("2")                    # and so must a second decision
    today.locator(tid("verdict-worth-contacting")).click()
    today.wait_for_timeout(300)

    assert len(held) == 1, f"{len(held)} verdict requests were sent while one was pending"
    assert _card_id(today) == ids[0], "the page moved off the card being saved"

    held[0].continue_()
    _wait_card_other_than(today, ids[0])
    today.wait_for_timeout(300)

    assert _card_id(today) == ids[1], "the queue skipped a card"
    assert [row[0] for row in _marker_rows(demo_db)] == [ids[0]]


def test_a_failed_save_leaves_the_card_and_the_undo_history_alone(today, demo_db):
    ids = today.evaluate("() => data.companies.map(c => c.company_id)")
    today.route("**/api/verdict", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"error":"boom"}'))

    today.keyboard.press("3")
    _wait_toast(today, "Couldn't save")

    assert _card_id(today) == ids[0]
    assert _marker_rows(demo_db) == []
    today.keyboard.press(UNDO_KEY)               # nothing was saved, so nothing to undo
    _wait_toast(today, "Nothing to undo")

    today.unroute("**/api/verdict")
    today.keyboard.press("3")                    # the same card can be decided again
    _wait_card_other_than(today, ids[0])
    assert _card_id(today) == ids[1]


# ------------------------------------------------------------------- M-10


def test_undo_restores_the_saved_verdict_not_just_the_screen(today, server, demo_db):
    cid = _card_id(today)
    before = _verdict_row(demo_db, cid)

    today.keyboard.press("3")
    _wait_card_other_than(today, cid)
    assert _verdict_row(demo_db, cid)[0][0] == "not for me"
    assert _marker_rows(demo_db, cid) == [("not for me",)]

    today.keyboard.press(UNDO_KEY)
    _wait_toast(today, "Undone")

    assert _card_id(today) == cid
    assert _verdict_row(demo_db, cid) == before        # value and timestamp
    assert _marker_rows(demo_db, cid) == []
    with urllib.request.urlopen(server + "/api/today", timeout=5) as response:
        queue = [c["company_id"] for c in json.loads(response.read())["companies"]]
    assert cid in queue, "an undone company must be back in the server's queue"

    today.keyboard.press("1")                          # and it can be decided afresh
    _wait_card_other_than(today, cid)
    assert _verdict_row(demo_db, cid)[0][0] == "worth contacting"


def test_undone_is_only_said_after_the_server_confirms(today, demo_db):
    cid = _card_id(today)
    today.keyboard.press("1")
    _wait_card_other_than(today, cid)
    next_card = _card_id(today)

    held: list = []
    today.route("**/api/undo", lambda route: held.append(route))
    today.keyboard.press(UNDO_KEY)
    today.wait_for_timeout(400)

    assert len(held) == 1
    assert "Undone" not in _toast(today), "claimed an undo the server had not confirmed"
    assert _card_id(today) == next_card
    assert _verdict_row(demo_db, cid)[0][0] == "worth contacting"

    held[0].continue_()
    _wait_toast(today, "Undone")
    assert _card_id(today) == cid
    assert _marker_rows(demo_db, cid) == []


def test_a_refused_undo_says_so_changes_nothing_and_can_be_retried(today, demo_db):
    cid = _card_id(today)
    today.keyboard.press("3")
    _wait_card_other_than(today, cid)
    next_card = _card_id(today)
    saved = _verdict_row(demo_db, cid)

    today.route("**/api/undo", lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error":"sheet","reason":"sheet_unavailable"}'))
    today.keyboard.press(UNDO_KEY)
    _wait_toast(today, "nothing was changed")

    assert "Undone" not in _toast(today)
    assert _card_id(today) == next_card
    assert _verdict_row(demo_db, cid) == saved

    today.unroute("**/api/undo")                        # the Sheet is back
    today.keyboard.press(UNDO_KEY)
    _wait_toast(today, "Undone")
    assert _card_id(today) == cid


def test_an_undo_the_server_no_longer_holds_is_reported_not_faked(today, demo_db):
    """e.g. the service restarted between the decision and Ctrl+Z."""
    cid = _card_id(today)
    today.keyboard.press("3")
    _wait_card_other_than(today, cid)
    next_card = _card_id(today)

    today.route("**/api/undo", lambda route: route.fulfill(
        status=404, content_type="application/json",
        body='{"error":"nothing to undo","reason":"unknown_or_expired"}'))
    today.keyboard.press(UNDO_KEY)
    _wait_toast(today, "Can't undo")

    assert "Undone" not in _toast(today)
    assert _card_id(today) == next_card
    assert _verdict_row(demo_db, cid)[0][0] == "not for me"


# ------------------------------------------------------------------- M-12


def test_a_hostile_page_cannot_clear_todays_review_markers(page, server, demo_db):
    """The audit's scenario in a real browser: another origin (here `null`,
    from about:blank) submits a form to /api/review-again and fires a
    no-cors fetch at the endpoints. The browser labels both cross-site."""
    with urllib.request.urlopen(urllib.request.Request(
            server + "/api/today"), timeout=5) as response:
        cid = json.loads(response.read())["companies"][0]["company_id"]
    request = urllib.request.Request(
        server + "/api/verdict",
        data=json.dumps({"company_id": cid, "verdict": "unsure"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(request, timeout=5).read()
    markers = _marker_rows(demo_db)
    assert markers, "the setup decision should have left a review marker"

    page.goto("about:blank")
    page.set_content(
        f'<form id="f" method="post" action="{server}/api/review-again">'
        '<input name="x" value="1"></form>')
    with page.expect_navigation():
        page.evaluate("document.getElementById('f').submit()")
    assert "refused" in page.inner_text("body")

    for target, body in (("/api/review-again", "{}"),
                         ("/api/verdict",
                          json.dumps({"company_id": cid, "verdict": "not for me"}))):
        page.evaluate(
            """([url, body]) => fetch(url, {method: 'POST', mode: 'no-cors',
                                            headers: {'Content-Type': 'text/plain'},
                                            body}).catch(() => null)""",
            [server + target, body])
    page.wait_for_timeout(500)

    assert _marker_rows(demo_db) == markers, "a cross-origin request cleared review markers"
    assert _verdict_row(demo_db, cid)[0][0] == "unsure", "a cross-origin request changed a verdict"


def test_review_again_still_works_from_the_page(today, demo_db):
    """The same-origin path the check must leave open (the page now sends JSON)."""
    cid = _card_id(today)
    today.keyboard.press("2")
    _wait_card_other_than(today, cid)
    assert _marker_rows(demo_db, cid) == [("unsure",)]

    today.evaluate("() => reviewAgain()")
    _wait_toast(today, "Reviewing today's companies again")

    assert _marker_rows(demo_db, cid) == []
    assert _card_id(today) == cid
