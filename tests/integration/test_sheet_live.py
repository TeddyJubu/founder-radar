"""09-test-plan §5 — the same four sheet tests, against a real spreadsheet.

`tests/unit/test_sheet.py` proves these offline against an in-memory double, and
that is what runs in CI. This file proves the double is not lying: same names,
same assertions, a live `SHEET_ID`.

Skipped unless `SHEET_ID` and `GOOGLE_SA_JSON` are set (see `conftest.py`), and
deselected by default by the `-m 'not integration …'` in `pyproject.toml`.

    pytest -m integration tests/integration/test_sheet_live.py

**`SHEET_ID` must be a scratch spreadsheet.** Every test starts by deleting all
of its tabs.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from radar.render.formatting import COMPANIES, col_index
from radar.render.sheet import sync_sheet
from tests.fakes import seed_companies
from tests.integration.conftest import cell, read_grid

pytestmark = pytest.mark.integration

# 09-test-plan §5, verbatim. Eleven visible + one hidden.
EXPECTED_TABS = {"📌 Today", "Companies", "Needs Review", "Fund Criteria",
                 "Scoring Weights", "Settings", "Outreach", "Sources", "Run Log",
                 "Tuning", "Lists", "_meta"}

TODAY_DATE = date(2026, 8, 8)

ID_COLUMN = col_index("A")
NAME_COLUMN = col_index("C")
VERDICT_COLUMN = col_index("Z")


def render(db, sheet):
    return sync_sheet(db, gateway=sheet, today=TODAY_DATE)


def companies_grid(sheet) -> list[list[str]]:
    return read_grid(sheet, COMPANIES)


def test_sheet_roundtrip(db, scratch_sheet):
    """A blank spreadsheet grows all twelve tabs; then 200 rows land in Companies."""
    render(db, scratch_sheet)

    assert set(scratch_sheet.inner.sheets()) == EXPECTED_TABS

    seed_companies(db, 200)
    render(db, scratch_sheet)

    grid = companies_grid(scratch_sheet)
    assert len(grid) - 1 == 200
    ids = [cell(grid, row, ID_COLUMN) for row in range(2, len(grid) + 1)]
    assert len(set(ids)) == 200

    # Google evaluated the =HYPERLINK, so D reads back as a label — which is
    # exactly why the plain URL is also kept in the hidden column AD.
    assert cell(grid, 2, col_index("AD")).startswith("https://")


def test_no_change_means_no_writes(db, scratch_sheet):
    """A steady-state re-render against the live sheet issues no write call."""
    seed_companies(db, 50)
    render(db, scratch_sheet)

    scratch_sheet.reset()
    result = render(db, scratch_sheet)

    assert scratch_sheet.write_calls == 0
    assert result["write_calls"] == 0
    assert result["ranges_written"] == 0


def test_render_call_budget(db, scratch_sheet):
    """FR-7.7: ≤ 10 API calls for 200 rows, building the sheet from nothing."""
    seed_companies(db, 200)
    result = render(db, scratch_sheet)

    assert scratch_sheet.call_count <= 10
    assert result["ranges_written"] < 60
    assert len(companies_grid(scratch_sheet)) - 1 == 200


def test_user_columns_survive_a_resort(db, scratch_sheet):
    """A verdict follows its company when Aryan sorts the tab by another column."""
    from radar.render.sheet import ValueRange

    ids = seed_companies(db, 25)
    render(db, scratch_sheet)
    top = ids[0]

    scratch_sheet.batch_set([ValueRange(f"'{COMPANIES}'!Z2:Z2",
                                        [["worth contacting"]])], "RAW")

    # Aryan selecting the data range and sorting it by Company — whole rows move.
    sheet_id = scratch_sheet.inner.sheets()[COMPANIES]
    scratch_sheet.batch_requests([{"sortRange": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": col_index("AE") + 1},
        "sortSpecs": [{"dimensionIndex": NAME_COLUMN, "sortOrder": "ASCENDING"}]}}])

    grid = companies_grid(scratch_sheet)
    assert cell(grid, 2, ID_COLUMN) != top              # the sort really moved it

    result = render(db, scratch_sheet)
    assert result["companies_resorted"] is True

    grid = companies_grid(scratch_sheet)
    rows = [cell(grid, row, ID_COLUMN) for row in range(2, len(grid) + 1)]
    row_of_top = rows.index(top) + 2
    assert cell(grid, row_of_top, VERDICT_COLUMN) == "worth contacting"

    # The grid is still a grid: no company duplicated, none silently dropped.
    assert Counter(rows).most_common(1)[0][1] == 1
    assert rows == ids
