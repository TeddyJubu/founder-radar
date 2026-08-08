"""Fixtures for the tests that need a real Google Sheet.

Everything here **skips** unless `SHEET_ID` and `GOOGLE_SA_JSON` are both set and
the credential file exists, so `pytest -m integration` on a laptop with no keys
reports skips rather than errors, and `--collect-only` always works.

Point `SHEET_ID` at a **scratch** spreadsheet. `scratch_sheet` wipes it back to a
single empty tab before every test, which is not something to do to Aryan's.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import CountingGateway

# A spreadsheet must always keep at least one sheet, so the survivor of a wipe
# is named `Outreach` — one of the twelve, and the one the system never touches.
PLACEHOLDER = "Outreach"


@pytest.fixture(autouse=True)
def _network_for_integration():
    """Lift the session-wide socket guard for this package only.

    `tests/conftest.py` replaces `socket.socket` for the whole session, which is
    what keeps the default suite honest. These tests are the one deliberate
    exception, and they restore the guard on the way out.
    """
    guard = socket.socket
    real = guard.__bases__[0] if guard.__name__ == "GuardedSocket" else guard
    socket.socket = real                              # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = guard                         # type: ignore[misc]


@pytest.fixture(scope="session")
def sheet_credentials() -> tuple[str, str]:
    sheet_id = os.environ.get("SHEET_ID")
    sa_json = os.environ.get("GOOGLE_SA_JSON")
    if not sheet_id or not sa_json:
        pytest.skip("SHEET_ID and GOOGLE_SA_JSON are not set — no scratch spreadsheet")
    if not Path(sa_json).expanduser().is_file():
        pytest.skip(f"GOOGLE_SA_JSON does not exist: {sa_json}")
    return sheet_id, str(Path(sa_json).expanduser())


def blank(gateway: Any) -> None:
    """Wipe the spreadsheet back to one empty `Outreach` tab.

    Deleting every other sheet also disposes of their protected ranges,
    conditional formats and validations, so each test really does start from the
    day-one state `test_sheet_roundtrip` claims to exercise.
    """
    existing = dict(gateway.sheets())
    if PLACEHOLDER not in existing:
        existing.update(gateway.add_tabs([PLACEHOLDER]))
    keep = existing[PLACEHOLDER]

    requests: list[dict] = [{"deleteSheet": {"sheetId": sid}}
                            for sid in existing.values() if sid != keep]
    requests.append({"updateCells": {"range": {"sheetId": keep}, "fields": "*"}})
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": keep, "title": PLACEHOLDER, "index": 0,
                       "gridProperties": {"frozenRowCount": 0}},
        "fields": "title,index,gridProperties.frozenRowCount"}})
    gateway.batch_requests(requests)


@pytest.fixture
def scratch_sheet(sheet_credentials) -> CountingGateway:
    """A live, freshly emptied spreadsheet behind the call-counting gateway.

    The wipe runs on the bare gateway, so the counter a test reads starts at
    zero and its budget assertions mean the same thing as the offline ones.
    """
    from radar.render.sheet import open_gateway

    sheet_id, sa_json = sheet_credentials
    inner = open_gateway(sheet_id, sa_json)
    blank(inner)
    return CountingGateway(inner)


def read_grid(gateway: Any, tab: str, last_col: str = "AE",
              last_row: int = 5000) -> list[list[str]]:
    """Read a whole tab back, the way Aryan sees it. Costs one API call."""
    rng = f"'{tab}'!A1:{last_col}{last_row}"
    return list(gateway.batch_get([rng]).get(rng, []) or [])


def cell(grid: list[list[str]], row: int, column: int) -> str:
    """1-based row, 0-based column, blank past the end — `batchGet` trims."""
    if row - 1 >= len(grid) or column >= len(grid[row - 1]):
        return ""
    return grid[row - 1][column]
