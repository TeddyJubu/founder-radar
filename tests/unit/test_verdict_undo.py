"""Undo of a saved verdict (audit M-10).

Today's Ctrl+Z used to rewind only the browser: the verdict and the day's
review marker were already in SQLite (and the Sheet), so the page said
"Undone" while the decision stayed. `radar.verdict` now returns a receipt of
what a decision replaced and `undo_verdict` puts exactly that back — or
refuses, changing nothing, when something else has touched the verdict since.
"""

from __future__ import annotations

import sqlite3

import pytest

from radar.render.formatting import COMPANIES
from radar.render.sheet import mirror_verdict, sync_sheet
from radar.store.db import Db
from radar.verdict import (
    UndoConflict,
    kept_count,
    record_verdict,
    record_verdict_receipt,
    undo_verdict,
    verdict_undo_applies,
)
from tests.fakes import FakeSheetGateway, seed_companies

DAY = "2026-09-30"
EARLIER = "2026-09-30T08:00:00"


def _state(db, company_id, day=DAY):
    """(lasting verdict row, that day's review marker) exactly as stored."""
    verdict = db.one(
        "SELECT value, updated_at FROM user_field "
        "WHERE company_id = ? AND field = 'verdict'", (company_id,))
    marker = db.one(
        "SELECT verdict, reviewed_at FROM daily_review "
        "WHERE company_id = ? AND review_date = ?", (company_id, day))
    return (tuple(verdict) if verdict else None,
            tuple(marker) if marker else None)


def _store_earlier(db, company_id, verdict, *, marker=True):
    db.execute(
        "INSERT INTO user_field(company_id, field, value, updated_at) "
        "VALUES (?, 'verdict', ?, ?)", (company_id, verdict, EARLIER))
    if marker:
        db.execute(
            "INSERT INTO daily_review(company_id, review_date, verdict, reviewed_at) "
            "VALUES (?, ?, ?, ?)", (company_id, DAY, verdict, EARLIER))


def test_undo_of_a_first_decision_leaves_no_verdict_and_no_marker(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    receipt = record_verdict_receipt(db, cid, "not for me", review_date=DAY)
    assert _state(db, cid)[0][0] == "not for me"
    assert _state(db, cid)[1] is not None

    assert undo_verdict(db, receipt) == 0

    assert _state(db, cid) == (None, None)


def test_undo_restores_the_earlier_verdict_with_its_original_stamps(db):
    """A same-day re-decision: the earlier verdict AND marker come back as they
    were, timestamps included (Today's `already_decided` reads `updated_at`)."""
    cid = seed_companies(db, count=1, shortlist=1)[0]
    _store_earlier(db, cid, "unsure")
    before = _state(db, cid)
    assert kept_count(db) == 1

    receipt = record_verdict_receipt(db, cid, "not for me", review_date=DAY)
    assert kept_count(db) == 0

    assert undo_verdict(db, receipt) == 1
    assert _state(db, cid) == before
    assert before == (("unsure", EARLIER), ("unsure", EARLIER))


def test_undo_restores_a_verdict_whose_marker_was_cleared_by_review_again(db):
    """Review Again removes today's markers but keeps lasting verdicts; the
    receipt must not invent a marker that was not there."""
    cid = seed_companies(db, count=1, shortlist=1)[0]
    _store_earlier(db, cid, "worth contacting", marker=False)

    receipt = record_verdict_receipt(db, cid, "not for me", review_date=DAY)
    undo_verdict(db, receipt)

    assert _state(db, cid) == (("worth contacting", EARLIER), None)


def test_undo_is_refused_when_another_surface_changed_the_verdict(db):
    """Telegram / the CLI / the Sheet decided in between: an undo must not
    overwrite that newer choice with an older one."""
    cid = seed_companies(db, count=1, shortlist=1)[0]
    receipt = record_verdict_receipt(db, cid, "not for me", review_date=DAY)
    record_verdict(db, cid, "worth contacting", review_date=DAY)
    after = _state(db, cid)

    assert not verdict_undo_applies(db, receipt)
    with pytest.raises(UndoConflict):
        undo_verdict(db, receipt)

    assert _state(db, cid) == after
    assert after[0][0] == "worth contacting"


def test_undo_is_refused_after_review_again_cleared_the_marker(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    receipt = record_verdict_receipt(db, cid, "unsure", review_date=DAY)
    db.execute("DELETE FROM daily_review WHERE review_date = ?", (DAY,))

    with pytest.raises(UndoConflict):
        undo_verdict(db, receipt)

    assert _state(db, cid)[0][0] == "unsure"


def test_undo_works_on_the_web_servers_raw_connection(tmp_path):
    """The server holds a plain `sqlite3` connection (implicit transactions),
    not `Db` (autocommit). Both must give the same receipt and the same undo,
    and a second connection must see the restored state committed."""
    path = tmp_path / "raw.db"
    setup = Db(str(path))
    setup.migrate()
    cid = seed_companies(setup, count=1, shortlist=1)[0]
    _store_earlier(setup, cid, "unsure")
    before = _state(setup, cid)

    raw = sqlite3.connect(str(path), check_same_thread=False)
    raw.row_factory = sqlite3.Row
    try:
        receipt = record_verdict_receipt(raw, cid, "not for me", review_date=DAY)
        assert _state(setup, cid)[0][0] == "not for me"      # committed, not pending
        assert not raw.in_transaction

        undo_verdict(raw, receipt)
        assert not raw.in_transaction
    finally:
        raw.close()

    assert _state(setup, cid) == before
    setup.close()


def test_undo_returns_the_company_to_todays_queue(db):
    """The point of the feature, through the real Today query."""
    from prototype.server import build_today, set_verdict_receipt

    ids = seed_companies(db, count=3, shortlist=3)
    queue = lambda: {c["company_id"] for c in build_today(db.conn)["companies"]}  # noqa: E731
    assert queue() == set(ids)

    receipt = set_verdict_receipt(db.conn, ids[0], "not for me")
    assert ids[0] not in queue()
    assert build_today(db.conn)["totals"]["reviewed_today"] == 1

    undo_verdict(db.conn, receipt)

    assert queue() == set(ids)
    totals = build_today(db.conn)["totals"]
    assert totals["reviewed_today"] == 0 and totals["kept"] == 0


# ---------------------------------------------------------------- the Sheet


def test_mirror_verdict_can_clear_the_cell_and_still_rejects_nonsense(db):
    """Undoing a first-ever decision has no verdict to put back, so the mirror
    must be able to blank column Z — without loosening what it accepts."""
    ids = seed_companies(db, count=2, shortlist=2)
    sheet = FakeSheetGateway()
    from datetime import date
    sync_sheet(db, gateway=sheet, today=date(2026, 8, 8))
    row = sheet.row_of(COMPANIES, ids[0])
    sheet.set_cell(COMPANIES, f"AA{row}", "keep this note")

    assert mirror_verdict(ids[0], "not for me", gateway=sheet)["status"] == "synced"
    assert sheet.at(COMPANIES, row, "Z") == "not for me"

    assert mirror_verdict(ids[0], "", gateway=sheet) == {"status": "synced", "row": row}
    assert sheet.at(COMPANIES, row, "Z") == ""
    assert sheet.at(COMPANIES, row, "AA") == "keep this note"
    assert mirror_verdict(ids[0], "", gateway=sheet)["status"] == "already_synced"

    with pytest.raises(ValueError):
        mirror_verdict(ids[0], "maybe", gateway=sheet)
