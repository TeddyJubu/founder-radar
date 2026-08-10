"""09-test-plan §5 — the Google Sheet, offline.

The test plan marks these `integration`, needing a real scratch spreadsheet.
That would leave the client's *primary interface* with no CI coverage at all, so
each one is written twice: here against `tests.fakes.FakeSheetGateway`, and again
in `tests/integration/test_sheet_live.py` against a live sheet. The names are the
canonical ones from 09-test-plan §5 — `10-build-plan.md` gates Phase 6 on
`pytest -k <name>` — and the assertions are deliberately the same in both files.

`SheetGateway` is a `Protocol` for exactly this: the double goes in, the calls
come out countable, and no test ever needs a credential.

`EXPECTED_TABS` below is transcribed from 09-test-plan §5 rather than imported
from `radar.render.formatting`, so a tab quietly renamed in the code fails here
instead of agreeing with itself.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from radar.render.formatting import (
    COMPANIES,
    META,
    OUTREACH,
    RUN_LOG,
    SETTINGS,
    SOURCES,
    TODAY,
    USER_COLUMN_FIELD,
    col_index,
)
from radar.render.sheet import plan_tab, read_user_columns, sync_sheet
from tests.fakes import FakeSheetGateway, seed_companies, seed_failed_source

# 09-test-plan §5, verbatim. Eleven visible + one hidden.
EXPECTED_TABS = {"📌 Today", "Companies", "Needs Review", "Fund Criteria",
                 "Scoring Weights", "Settings", "Outreach", "Sources", "Run Log",
                 "Tuning", "Lists", "_meta"}

TODAY_DATE = date(2026, 8, 8)


@pytest.fixture
def sheet() -> FakeSheetGateway:
    """An empty spreadsheet — no tabs at all, as on day one."""
    return FakeSheetGateway()


def render(db, sheet: FakeSheetGateway) -> dict:
    return sync_sheet(db, gateway=sheet, today=TODAY_DATE)


# ------------------------------------------------------------------ roundtrip


def test_sheet_roundtrip(db, sheet):
    """Empty spreadsheet → twelve formatted tabs; then 200 rows land in Companies."""
    render(db, sheet)

    assert set(sheet.tab_names()) == EXPECTED_TABS
    assert len(sheet.tab_names()) == 12

    properties = [r["properties"] for r in sheet.requests_of("updateSheetProperties")]

    # Formatted: a frozen, coloured header band on every tab the pipeline owns.
    frozen = [p for p in properties
              if "frozenRowCount" in p.get("gridProperties", {})]
    assert len(frozen) == 11                                  # everything but Outreach
    assert any(p.get("sheetId") == sheet.ids[META] and p.get("hidden") for p in properties)

    # Dropdowns: Aryan's Verdict column is one-of-list, sourced from `Lists`.
    verdict = [v for v in sheet.requests_of("setDataValidation")
               if v["range"]["sheetId"] == sheet.ids[COMPANIES]
               and v["range"]["startColumnIndex"] == col_index("Z")]
    assert len(verdict) == 1
    assert verdict[0]["rule"]["condition"]["type"] == "ONE_OF_LIST"
    assert verdict[0]["rule"]["condition"]["values"]

    # Protected ranges: warning-only, and stopping short of Aryan's Z–AC.
    protections = [p["protectedRange"] for p in sheet.requests_of("addProtectedRange")]
    assert protections and all(p["warningOnly"] for p in protections)
    generated = next(p for p in protections
                     if p["range"]["sheetId"] == sheet.ids[COMPANIES])
    assert generated["range"]["endColumnIndex"] == col_index("Y") + 1

    # "The Outreach tab is never touched. At all." Created once, then left alone.
    assert OUTREACH in sheet.tab_names()
    assert OUTREACH not in sheet.touched_tabs()
    assert sheet.ids[OUTREACH] not in sheet.formatted_sheet_ids()

    seed_companies(db, 200)
    result = render(db, sheet)

    assert len(sheet.rows(COMPANIES)) == 200
    assert len(set(sheet.column(COMPANIES, "A"))) == 200      # no duplicate rows
    assert result["rows"]["companies"] == 200
    assert OUTREACH not in sheet.touched_tabs()

    # The hidden read-back columns carry the plain URL, because D and Y hold
    # =HYPERLINK() and read back as their label (client request, 24 July).
    assert sheet.cell(COMPANIES, "D2") and "http" not in sheet.cell(COMPANIES, "D2")
    assert sheet.cell(COMPANIES, "AD2").startswith("https://")


def test_today_says_what_each_company_does_before_it_cites_an_article(db, sheet):
    """The Today tab is the one Aryan opens, and on 11 Aug he said of it: "I'm
    currently seeing articles rather than the actual companies themselves, so I
    still have to open and scan through them."

    Every block therefore carries a `What they do` row directly under the name,
    fed by `company.one_liner` — a column that has existed since the first
    commit and that nothing wrote until the extractor's `one_line_description`
    was plumbed into it. `Evidence` keeps the article, one row further down,
    which is the "just used as the source" half of the same sentence.
    """
    from radar.store.db import now_iso
    from tests.factories import C, store_company

    stamp = now_iso()
    company = C(canonical_name="Loamweave Ltd", norm_key="loamweave",
                domain="loamweave.example", website_url="https://loamweave.example/",
                hq_city="Newcastle", age_months=4,
                one_liner="Turns brewery waste into packaging foam.")
    store_company(db, company)
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, explanation, config_hash,
              scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company.id, "northstar", None, 88.0, 0.9, 90.0, 89.0, "shortlist",
         "Matches on geography and sector.", "cfg1", "1", stamp))
    db.execute(
        """INSERT INTO signal (company_id, kind, occurred_on, headline,
             source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?,?)""",
        (company.id, "funding_round", "2026-08-01",
         "Newcastle's Loamweave raises £900k pre-seed, UKTN reports",
         "uktn", "https://uktn.test/loamweave", stamp))

    render(db, sheet)
    labels = sheet.column(TODAY, "A")
    values = sheet.column(TODAY, "B")
    block = dict(zip(labels, values))

    assert block["What they do"] == "Turns brewery waste into packaging foam."
    assert labels.index("What they do") == labels.index("Company") + 1
    # The article is still there — as the source, below the company.
    assert "UKTN reports" in block["Evidence"]
    assert labels.index("Evidence") > labels.index("What they do")


# -------------------------------------------------------------- steady state


def test_no_change_means_no_writes(db, sheet):
    """An unchanged database renders to reads only — no value or format write."""
    seed_companies(db, 50)
    render(db, sheet)

    sheet.reset()
    sheet.writes.clear()
    result = render(db, sheet)

    assert sheet.write_calls == 0
    assert sheet.writes == []
    assert result["write_calls"] == 0
    assert result["ranges_written"] == 0
    assert sheet.methods() == ["sheets", "batch_get"]


# --------------------------------------------------------------- call budget


def test_render_call_budget(db, sheet):
    """FR-7.7: ≤ 10 API calls for 200 rows, on a spreadsheet built from nothing.

    Rule 6 of 10-build-plan: never `update_cell()`/`append_row()` in a loop. The
    gateway has no such verb, so the only way to fail this is to batch badly.
    """
    seed_companies(db, 200)
    result = render(db, sheet)

    assert sheet.call_count <= 10
    assert len(sheet.rows(COMPANIES)) == 200
    # Two value writes at most — RAW and USER_ENTERED — however many rows moved.
    assert sheet.methods().count("batch_set") <= 2
    assert sheet.methods().count("batch_requests") <= 1
    # Adjacent rows coalesce, so 200 rows are a handful of ranges, not 200.
    assert result["ranges_written"] < 60


# ------------------------------------------------------------ Aryan's columns


def test_user_columns_survive_a_resort(db, sheet):
    """A verdict follows its company through a manual sort by another column."""
    ids = seed_companies(db, 25)
    render(db, sheet)
    assert sheet.column(COMPANIES, "A") == ids                # rendered by priority

    top = sheet.cell(COMPANIES, "A2")
    sheet.set_cell(COMPANIES, "Z2", "worth contacting")
    sheet.sort_by(COMPANIES, "C")                             # Aryan sorts by Company
    assert sheet.cell(COMPANIES, "A2") != top                 # the sort really moved it

    result = render(db, sheet)

    row = sheet.row_of(COMPANIES, top)
    assert sheet.at(COMPANIES, row, "Z") == "worth contacting"
    assert result["companies_resorted"] is True

    # And the grid is still a grid. A positional diff against a hand-sorted tab
    # duplicates one company and deletes another unless the re-sort is noticed.
    column_a = sheet.column(COMPANIES, "A")
    assert Counter(column_a).most_common(1)[0][1] == 1
    assert column_a == ids

    # The verdict is mirrored into `user_field`, which is what `tune` reads.
    assert db.scalar(
        "SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
        (top,)) == "worth contacting"


def test_a_verdict_is_never_originated_by_the_pipeline(db, sheet):
    """Z–AC are Aryan's. The renderer relocates a value; it never invents one."""
    seed_companies(db, 10)
    render(db, sheet)

    assert set(sheet.column(COMPANIES, "Z")) == {""}
    assert set(sheet.column(COMPANIES, "AA")) == {""}
    assert db.query("SELECT * FROM user_field") == []


def test_read_user_columns_keys_by_company_id_not_row():
    """Z:AC is read back keyed by the ULID in column A — which is what makes
    it survive a re-sort (05-pipeline ⑦)."""
    grid = [
        ["id", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
         "", "", "", "", "", "", "", "", "", "Z", "AA", "AB", "AC"],
        ["c-1", "2026-01-01", "Acme", "", "", "", "", "", "", "", "", "",
         "", "", "", "", "", "", "", "", "", "", "", "", "",
         "worth contacting", "note", "TRUE", "northstar"],
        ["c-2", "2026-01-02", "Beta", "", "", "", "", "", "", "", "", "",
         "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ]
    user = read_user_columns(grid)
    assert user["c-1"]["verdict"] == "worth contacting"
    assert user["c-1"]["notes"] == "note"
    assert user["c-1"]["fund_sent"] == "northstar"
    assert user["c-2"] == {field: "" for field in USER_COLUMN_FIELD.values()}


# ----------------------------------------------------------------- the diff


def test_plan_tab_diffs_only_what_moved(db):
    """`plan_tab` writes only the changed rows — the core of the diff engine."""
    from radar.render.sheet import Row, save_state

    columns = ["A", "B"]
    rows = [Row(key="c1", number=2, cells={"A": "c1", "B": "x"})]
    plan = plan_tab(db, COMPANIES, rows, columns)
    assert len(plan.raw) >= 1
    save_state(db, plan)                                      # what sync does

    plan2 = plan_tab(db, COMPANIES, rows, columns)            # identical data
    assert plan2.empty


# ---------------------------------------------------------------- the config


def test_typo_in_settings_is_reported_not_crashing(db, sheet):
    """A bad Settings value stops nothing; the status column reports it.

    The fallback half of this — that the *last good* value is used — is
    `test_typo_uses_last_good_and_reports_in_sheet` in `test_config.py`. This is
    the sheet-side half: the message reaches Aryan, in the cell next to his typo.
    """
    seed_companies(db, 10)
    render(db, sheet)

    grid = sheet.grid(SETTINGS)
    row = next(i for i, r in enumerate(grid, start=1)
               if r and r[0] == "max_company_age_months")
    sheet.set_cell(SETTINGS, f"B{row}", "not a number")

    result = render(db, sheet)

    assert "❌" in sheet.at(SETTINGS, row, "D")
    assert result["errors"]                                   # surfaced, not swallowed
    assert result["rows"]["companies"] == 10                  # and the run finished


# ---------------------------------------------- source failure containment


def test_source_failures_appear_only_on_the_sources_tab(db, sheet):
    """Client, 24 July: "I don't think we need the Source Failed section."

    It lives on `Sources`, and nowhere Aryan reads every morning.
    """
    seed_companies(db, 20)
    error = seed_failed_source(db, "uktn")
    render(db, sheet)

    assert error in sheet.text_of(SOURCES)
    assert "❌" in sheet.column(SOURCES, "G")

    for tab in (TODAY, COMPANIES):
        assert error not in sheet.text_of(tab)
        assert "❌" not in sheet.text_of(tab)
        assert "failed" not in sheet.text_of(tab).lower()

    # Run Log still names the source that failed — that tab is the audit trail.
    assert "uktn" in sheet.column(RUN_LOG, "O")


# --------------------------------------------------------------------- tuning


def _seed_verdicts(db, ids, *, positive: int = 12) -> None:
    """Aryan's own labels — the only training data the thresholds ever get."""
    from radar.store.db import now_iso

    stamp = now_iso()
    for index, company_id in enumerate(ids):
        db.execute(
            "INSERT INTO user_field(company_id, field, value, updated_at) "
            "VALUES (?,?,?,?)",
            (company_id, "verdict",
             "worth contacting" if index < positive else "not for me", stamp),
        )


def _padded(row, width: int = 6) -> list[str]:
    """The fake trims trailing blanks, exactly as Sheets does on read-back."""
    return list(row) + [""] * (width - len(row))


def test_tuning_tab_carries_the_sweep_not_just_headers(db, sheet):
    """The regression: the tab was created with headers and never filled.

    `founder-radar tune` computed the sweep and printed it to stdout, so the
    one tab that exists to settle where the threshold sits stayed empty on
    every render — and the decision it supports could never be made in the
    place Aryan actually works (06-scoring §8, 07-interfaces tab 10).
    """
    ids = seed_companies(db, count=40, shortlist=8)
    _seed_verdicts(db, ids)
    render(db, sheet)

    grid = sheet.grid("Tuning")
    assert grid, "Tuning tab was never written at all"
    header, *rows = grid
    assert header[:6] == ["Metric", "Threshold", "Would shortlist",
                          "Precision", "Recall", "F1"]
    assert rows, "Tuning tab is headers-only — the sweep never landed"

    # Thresholds come from the data, not a fixed grid — so assert the property
    # that matters rather than the numbers a particular fixture happens to
    # produce: every row is a threshold that gives a *different* shortlist.
    counts = [int(_padded(r)[2]) for r in rows]
    assert len(set(counts)) == len(counts), (
        f"rows with identical shortlists — the grid is back: {counts}")
    assert [r[1] for r in rows] == sorted(r[1] for r in rows), "thresholds unsorted"
    # Every row must carry a real count; a blank here is a sweep that ran on
    # nothing, which looks identical to a sweep that never ran.
    assert all(_padded(r)[2] != "" for r in rows)


def test_tuning_leaves_precision_blank_when_there_are_no_verdicts(db, sheet):
    """Unmeasurable is not zero.

    With no labels there is nothing to be precise *against*. Rendering 0.00
    would read as "every one of these thresholds is terrible" rather than
    "fill in the Verdict column", which is the opposite instruction.
    """
    seed_companies(db, count=20, shortlist=4)
    render(db, sheet)

    _, *rows = sheet.grid("Tuning")
    assert rows
    for row in rows:
        cells = _padded(row)
        assert cells[2] != "", "would-shortlist is computable without labels"
        assert cells[3] == "" and cells[4] == "" and cells[5] == "", (
            f"unmeasurable precision/recall/F1 rendered as {cells[3:6]!r}")


def test_tuning_marks_exactly_one_best_threshold(db, sheet):
    """The recommendation has to be visible in the grid, not just in stdout."""
    ids = seed_companies(db, count=40, shortlist=8)
    _seed_verdicts(db, ids)
    render(db, sheet)

    _, *rows = sheet.grid("Tuning")
    starred = [r for r in rows if "★" in r[0]]
    assert len(starred) == 1, f"expected one starred row, got {len(starred)}"

    # The star must sit on the row with the highest F1, not merely somewhere.
    scored = [(float(_padded(r)[5]), r) for r in rows if _padded(r)[5]]
    assert starred[0] is max(scored, key=lambda pair: pair[0])[1]
