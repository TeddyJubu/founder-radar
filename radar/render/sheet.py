"""Stage ⑦ — write the sheet with the minimum possible number of API calls.

The whole module talks to Google through five verbs (`SheetGateway`). That is
what makes it testable: the suite injects an in-memory double and counts method
calls, and no test has ever needed a live spreadsheet or a credential.

The rules that keep a 200-row render inside the FR-7.7 budget of ten calls:

* Never `update_cell()` or `append_row()` in a loop. 500 single-row writes is
  eight minutes of `429`s; 500 rows in one `batch_update` is one request.
* Read every tab in one `values.batchGet`, diff in Python, write back only the
  ranges that changed.
* All formatting, conditional rules, validation and protected ranges go into
  **one** `batchUpdate` — and only when the layout version has moved, so a
  steady-state run issues no write calls at all.

Two invariants the tests enforce:

* **The Outreach tab is never touched.** It is created once, because it is one
  of the twelve, and then never read, written or formatted.
* **Columns Z–AC belong to Aryan.** They are read before every render and
  written back beside the right ULID after any re-sort. The pipeline never
  originates a value there — it only relocates one.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence

from radar.render.formatting import (
    COMPANIES,
    COMPANIES_HEADERS,
    EXPECTED_TABS,
    FUND_CRITERIA,
    HEADERS,
    HYPERLINK_COLUMNS,
    IMPORTANCE_BANNER,
    IMPORTANCE_HEADERS,
    LAYOUT_VERSION,
    LISTS,
    META,
    NEEDS_REVIEW,
    RUN_LOG,
    SCORING_WEIGHTS,
    SETTINGS,
    SOURCES,
    TODAY,
    TUNING,
    UNTOUCHABLE_TABS,
    USER_COLUMNS,
    USER_COLUMN_FIELD,
    a1,
    col_index,
    col_letter,
    columns_for,
    layout_requests,
    status_format_requests,
)

RAW = "RAW"
USER_ENTERED = "USER_ENTERED"

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

# The canonical column order for the four fit columns M–P (07-interfaces tab 2).
FUND_ORDER: tuple[str, ...] = ("dsw", "northstar", "outward", "anticus")

# One batchGet for everything stage ① and stage ⑦ need.
READ_RANGES: dict[str, str] = {
    SETTINGS: f"'{SETTINGS}'!A1:E200",
    FUND_CRITERIA: f"'{FUND_CRITERIA}'!A1:Q200",
    SCORING_WEIGHTS: f"'{SCORING_WEIGHTS}'!A1:I300",
    LISTS: f"'{LISTS}'!A1:L500",
    SOURCES: f"'{SOURCES}'!A1:H200",
    COMPANIES: f"'{COMPANIES}'!A1:AC5000",
}

TODAY_BLOCK_LABELS: tuple[str, ...] = (
    "Company", "Send to", "Fit / Edge", "Why", "Evidence", "Also fits", "Your call",
)
TODAY_BLOCK_HEIGHT = len(TODAY_BLOCK_LABELS) + 1     # + one blank separator row

# The Settings tab, in the order of 07-interfaces tab 6. Seeded once; after that
# it is Aryan's, and the pipeline only ever writes column D.
SETTING_SPECS: tuple[tuple[str, str, str], ...] = (
    ("max_company_age_months", "int 1–120", "Reject anything older"),
    ("max_total_funding_gbp", "money", "Reject anything better funded"),
    ("max_stage", "enum", "Reject anything later"),
    ("shortlist_fit", "int 0–100", "Fit needed to shortlist"),
    ("shortlist_edge", "int 0–100", "Discovery Edge needed to shortlist"),
    ("min_coverage", "0–1", "Minimum data completeness"),
    ("watchlist_fit", "int 0–100", "Fit needed to watchlist"),
    ("min_qualifiers", "int 0–5", "Signals a registry company needs before it's scored"),
    ("weight_fit", "0–1", "Fit's share of the ranking"),
    ("weight_edge", "0–1", "Edge's share of the ranking"),
    ("regions_enabled", "csv", "Which UK regions to sweep"),
    ("ch_backfill_days", "int", "Companies House first-run window"),
    ("ch_daily_window_days", "int", "Daily trailing window"),
    ("max_enrichment_requests_per_run", "int", "Companies House request budget (not companies)"),
    ("daily_digest_max", "int", "Cap on digest length"),
    ("llm_model", "string", "Swap provider without a redeploy"),
    ("llm_enabled", "bool", "Off = heuristic extraction only, zero AI cost"),
)

FUND_DISPLAY: dict[str, str] = {
    "dsw": "DSW", "northstar": "Northstar", "outward": "Outward", "anticus": "Anticus",
}


# ---------------------------------------------------------------- gateway


@dataclass(frozen=True)
class ValueRange:
    """One A1 range and the block of values that goes into it."""

    range: str
    values: list[list[Any]]


class SheetGateway(Protocol):
    """Five verbs. Everything above this line is pure Python.

    `batch_set` and `batch_requests` are deliberately different names because
    in gspread 6.x they are different methods on different objects:
    `Worksheet.batch_update(list)` writes values, `Spreadsheet.batch_update(dict)`
    sends raw requests, and passing one to the other raises (05-pipeline ⑦).
    """

    def sheets(self) -> dict[str, int]: ...
    def add_tabs(self, titles: Sequence[str]) -> dict[str, int]: ...
    def batch_get(self, ranges: Sequence[str]) -> dict[str, list[list[str]]]: ...
    def batch_set(self, data: Sequence[ValueRange], value_input_option: str) -> None: ...
    def batch_requests(self, requests: Sequence[dict]) -> None: ...


class GspreadGateway:
    """The real thing. One method per API call, so the budget is countable."""

    def __init__(self, spreadsheet: Any) -> None:
        self._sh = spreadsheet

    def sheets(self) -> dict[str, int]:
        return {ws.title: ws.id for ws in self._sh.worksheets()}

    def add_tabs(self, titles: Sequence[str]) -> dict[str, int]:
        requests = [
            {"addSheet": {"properties": {"title": title,
                                         "gridProperties": {"rowCount": 2000,
                                                            "columnCount": 40}}}}
            for title in titles
        ]
        reply = self._sh.batch_update({"requests": requests})
        out: dict[str, int] = {}
        for item in reply.get("replies", []):
            props = item.get("addSheet", {}).get("properties", {})
            if props:
                out[props["title"]] = props["sheetId"]
        return out

    def batch_get(self, ranges: Sequence[str]) -> dict[str, list[list[str]]]:
        reply = self._sh.values_batch_get(list(ranges))
        got = reply.get("valueRanges", [])
        # Keyed by the *requested* range: the API normalises what it echoes back.
        return {rng: (got[i].get("values", []) if i < len(got) else [])
                for i, rng in enumerate(ranges)}

    def batch_set(self, data: Sequence[ValueRange], value_input_option: str) -> None:
        if not data:
            return
        self._sh.values_batch_update({
            "valueInputOption": value_input_option,
            "data": [{"range": vr.range, "values": vr.values} for vr in data],
        })

    def batch_requests(self, requests: Sequence[dict]) -> None:
        if not requests:
            return
        self._sh.batch_update({"requests": list(requests)})


def open_gateway(sheet_id: str | None = None,
                 credentials_path: str | None = None) -> GspreadGateway:
    """Authorise and open. Imported lazily so `founder-radar --help` stays fast."""
    import gspread
    from google.oauth2.service_account import Credentials

    key = sheet_id or os.environ.get("SHEET_ID")
    if not key:
        raise RuntimeError("SHEET_ID is not set — run `founder-radar doctor`")
    sa_path = credentials_path or os.environ.get("GOOGLE_SA_JSON")
    if not sa_path:
        raise RuntimeError("GOOGLE_SA_JSON is not set — run `founder-radar doctor`")
    creds = Credentials.from_service_account_file(sa_path, scopes=list(SCOPES))
    return GspreadGateway(gspread.authorize(creds).open_by_key(key))


# ------------------------------------------------------------- formatting


def _txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def _num(value: Any, digits: int = 1) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def hyperlink(url: str | None, label: str | None = None) -> str:
    """A clickable cell. The plain URL also goes into a hidden column so
    read-back and CSV export both work (07-interfaces tab 1)."""
    if not url:
        return _txt(label)
    safe_url = str(url).replace('"', '""')
    safe_label = str(label or url).replace('"', '""')
    return f'=HYPERLINK("{safe_url}","{safe_label}")'


def _months_between(start: str | None, today: date) -> float | None:
    if not start:
        return None
    try:
        began = date.fromisoformat(str(start)[:10])
    except ValueError:
        return None
    return round((today - began).days / 30.44, 1)


def _relative_age(months: float | None) -> str:
    if months is None:
        return "age unknown"
    if months < 1:
        return "incorporated this month"
    if months < 24:
        return f"{int(round(months))} months ago"
    return f"{months / 12:.1f} years ago"


# ------------------------------------------------------------ the diff core


@dataclass
class Row:
    """One rendered line: where it goes, what identifies it, what's in it."""

    key: str
    number: int
    cells: dict[str, str]


@dataclass
class WritePlan:
    raw: list[ValueRange] = field(default_factory=list)
    user_entered: list[ValueRange] = field(default_factory=list)
    state: list[tuple[str, str, str, str]] = field(default_factory=list)
    touched_tabs: set[str] = field(default_factory=set)

    def extend(self, other: "WritePlan") -> None:
        self.raw += other.raw
        self.user_entered += other.user_entered
        self.state += other.state
        self.touched_tabs |= other.touched_tabs

    @property
    def empty(self) -> bool:
        return not self.raw and not self.user_entered


def _load_state(db: Any, state_tab: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for row in db.query(
        "SELECT company_id, col, last_value FROM sheet_row_state WHERE tab = ?",
        (state_tab,),
    ):
        out[row["company_id"]][row["col"]] = row["last_value"] or ""
    return dict(out)


def _coalesce(numbers: Iterable[int]) -> list[tuple[int, int]]:
    """Adjacent changed rows become one range. 200 scattered rows would be 200
    ranges; 200 consecutive rows are one."""
    blocks: list[tuple[int, int]] = []
    for number in sorted(numbers):
        if blocks and number == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], number)
        else:
            blocks.append((number, number))
    return blocks


def plan_tab(
    db: Any,
    tab: str,
    rows: Sequence[Row],
    columns: Sequence[str],
    *,
    state_tab: str | None = None,
    hyperlink_cols: frozenset[str] = frozenset(),
    force: bool = False,
    existing_rows: int = 0,
) -> WritePlan:
    """Diff the rendered grid against `sheet_row_state` and emit only what moved.

    The state is keyed `(company_id, tab, col)`, but the diff has to be
    *positional*: when a company moves from row 5 to row 3 because Aryan
    re-sorted or its priority changed, the cells at row 3 change even though the
    company's values did not. The reserved column `_row` carries the previous
    row number so the previous grid can be reconstructed exactly.
    """
    if tab in UNTOUCHABLE_TABS:
        return WritePlan()

    state_tab = state_tab or tab
    previous = _load_state(db, state_tab)

    prev_grid: dict[int, dict[str, str]] = {}
    for key, cells in previous.items():
        try:
            number = int(cells.get("_row", ""))
        except ValueError:
            continue
        prev_grid[number] = {c: v for c, v in cells.items() if c != "_row"}

    new_grid: dict[int, dict[str, str]] = {}
    for row in rows:
        new_grid[row.number] = {c: _txt(row.cells.get(c, "")) for c in columns}

    blank = {c: "" for c in columns}
    stale = set(prev_grid) - set(new_grid)
    if not prev_grid and existing_rows:
        # First render onto a sheet we have no state for: clear whatever is
        # sitting below our last row so a shrinking list cannot leave ghosts.
        top = max(new_grid) if new_grid else 1
        stale |= {n for n in range(top + 1, existing_rows + 1)}
    for number in stale:
        new_grid[number] = dict(blank)

    changed = [
        number for number in sorted(new_grid)
        if force or {c: new_grid[number].get(c, "") for c in columns}
        != {c: prev_grid.get(number, blank).get(c, "") for c in columns}
    ]

    plan = WritePlan()
    if changed:
        plan.touched_tabs.add(tab)
    runs = _column_runs(columns, hyperlink_cols)
    for first, last in _coalesce(changed):
        for option, run in runs:
            values = [[new_grid[n].get(c, "") for c in run] for n in range(first, last + 1)]
            vr = ValueRange(a1(tab, run[0], first, run[-1], last), values)
            (plan.user_entered if option == USER_ENTERED else plan.raw).append(vr)

    for row in rows:
        plan.state.append((row.key, state_tab, "_row", str(row.number)))
        for column in columns:
            plan.state.append((row.key, state_tab, column, _txt(row.cells.get(column, ""))))
    return plan


def resorted(db: Any, tab: str, grid: Sequence[Sequence[Any]], *,
             state_tab: str | None = None, key_col: str = "A") -> bool:
    """True when the sheet's own row order no longer matches what we last wrote.

    Aryan sorting the Companies tab by a different column is a supported thing
    to do — it is "sortable, filterable" in 07-interfaces tab 2. But it silently
    invalidates the positional diff in `plan_tab`: that diff reconstructs the
    previous grid from `sheet_row_state`, which records where the *pipeline* put
    each row, not where a human dragged it. After a re-sort the rows the diff
    decides not to rewrite are no longer the rows it believes they are, so the
    next render leaves them where the sort left them — duplicating one company
    and deleting another from the grid, with no error anywhere.

    Comparing the ULIDs in column A against the recorded `_row` costs nothing
    (the tab is already in the one `batchGet`) and turns the next render into a
    full rewrite, which is the only correct response.
    """
    previous = _load_state(db, state_tab or tab)
    if not previous:
        return False
    index = col_index(key_col)
    rows = list(grid)
    for company_id, cells in previous.items():
        try:
            number = int(cells.get("_row", ""))
        except ValueError:
            continue
        row = rows[number - 1] if 0 < number <= len(rows) else ()
        actual = (str(row[index]).strip()
                  if index < len(row) and row[index] is not None else "")
        if actual != company_id:
            return True
    return False


def _column_runs(columns: Sequence[str],
                 hyperlink_cols: frozenset[str]) -> list[tuple[str, list[str]]]:
    """Split a row into contiguous runs sharing one `valueInputOption`.

    `USER_ENTERED` only where `=HYPERLINK(...)` or a real date is needed; `RAW`
    everywhere else, because `USER_ENTERED` turns "0114" into 114 and "1-2" into
    a date (05-pipeline ⑦).
    """
    runs: list[tuple[str, list[str]]] = []
    for column in columns:
        option = USER_ENTERED if column in hyperlink_cols else RAW
        if runs and runs[-1][0] == option:
            runs[-1][1].append(column)
        else:
            runs.append((option, [column]))
    return runs


def save_state(db: Any, plan: WritePlan) -> None:
    tabs = {state_tab for _, state_tab, _, _ in plan.state}
    with db.tx():
        for state_tab in tabs:
            db.execute("DELETE FROM sheet_row_state WHERE tab = ?", (state_tab,))
        db.executemany(
            "INSERT OR REPLACE INTO sheet_row_state(company_id, tab, col, last_value) "
            "VALUES (?,?,?,?)",
            [(key, state_tab, column, value) for key, state_tab, column, value in plan.state],
        )


# --------------------------------------------------------- Aryan's columns


def read_user_columns(grid: Sequence[Sequence[Any]]) -> dict[str, dict[str, str]]:
    """`Companies!Z:AC`, keyed by the ULID in column A of the *same* read.

    Keying off column A rather than the row number is what makes this correct
    whatever order Aryan has left the sheet in (05-pipeline ⑦).
    """
    out: dict[str, dict[str, str]] = {}
    for row in list(grid)[1:]:
        company_id = str(row[0]).strip() if row and row[0] is not None else ""
        if not company_id:
            continue
        out[company_id] = {
            field_name: (str(row[col_index(column)]).strip()
                         if col_index(column) < len(row) and row[col_index(column)] is not None
                         else "")
            for column, field_name in USER_COLUMN_FIELD.items()
        }
    return out


def save_user_fields(db: Any, user: Mapping[str, Mapping[str, str]]) -> int:
    """The sheet always wins for Aryan's columns; the pipeline never overwrites
    them (07-interfaces §4). It only mirrors them into `user_field`, which is
    also the labelled data `founder-radar tune` needs."""
    from radar.store.db import now_iso

    known = {r["id"] for r in db.query("SELECT id FROM company")}
    stamp = now_iso()
    written = 0
    with db.tx():
        for company_id, fields in user.items():
            if company_id not in known:
                continue
            for name, value in fields.items():
                if value:
                    db.execute(
                        "INSERT INTO user_field(company_id, field, value, updated_at) "
                        "VALUES (?,?,?,?) ON CONFLICT(company_id, field) DO UPDATE SET "
                        "value = excluded.value, updated_at = excluded.updated_at",
                        (company_id, name, value, stamp))
                    written += 1
                else:
                    db.execute("DELETE FROM user_field WHERE company_id = ? AND field = ?",
                               (company_id, name))
    return written


def user_fields_from_db(db: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for row in db.query("SELECT company_id, field, value FROM user_field"):
        out[row["company_id"]][row["field"]] = row["value"] or ""
    return dict(out)


# --------------------------------------------------------------- row builders


@dataclass
class CompanyView:
    """Everything one Companies row needs, gathered once per render."""

    row: Any
    best: Any | None
    per_fund: dict[str, float]
    also: list[tuple[str, float, str]]
    founders: list[str]
    signals: list[str]
    sources: list[str]
    evidence: list[tuple[str, str]]


def _gather(db: Any) -> dict[str, CompanyView]:
    companies = db.query(
        "SELECT * FROM company WHERE merged_into IS NULL ORDER BY first_seen, id")
    views: dict[str, CompanyView] = {}
    for row in companies:
        views[row["id"]] = CompanyView(row, None, {}, [], [], [], [], [])

    for row in db.query("SELECT * FROM score ORDER BY priority DESC, fund_key"):
        view = views.get(row["company_id"])
        if view is None:
            continue
        if view.best is None:
            view.best = row
        current = view.per_fund.get(row["fund_key"])
        if current is None or row["fund_fit_pct"] > current:
            view.per_fund[row["fund_key"]] = row["fund_fit_pct"]
        if view.best is not None and row["fund_key"] != view.best["fund_key"]:
            view.also.append((row["fund_key"], row["fund_fit_pct"], row["tier"]))

    for row in db.query("SELECT company_id, name FROM founder ORDER BY id"):
        if row["company_id"] in views:
            views[row["company_id"]].founders.append(row["name"])

    for row in db.query(
        "SELECT company_id, kind, headline, source_url FROM signal "
        "ORDER BY occurred_on DESC, id DESC"
    ):
        view = views.get(row["company_id"])
        if view is None:
            continue
        if row["kind"] not in view.signals:
            view.signals.append(row["kind"])
        view.evidence.append((row["headline"], row["source_url"]))

    for row in db.query("SELECT company_id, source_url FROM company_source ORDER BY source_key"):
        if row["company_id"] in views:
            views[row["company_id"]].sources.append(row["source_url"])

    return views


def _fund_order(cfg: Any) -> list[str]:
    keys = [f.key for f in getattr(cfg, "funds", [])]
    ordered = [k for k in FUND_ORDER if k in keys or not keys]
    for key in keys:
        if key not in ordered:
            ordered.append(key)
    return ordered[:4] if len(ordered) >= 4 else ordered


def build_companies(db: Any, cfg: Any, user: Mapping[str, Mapping[str, str]],
                    *, today: date) -> list[Row]:
    """One row per company, sorted by Priority — the sort column (07-interfaces tab 2).

    Columns A–Y and the hidden AD–AE are the pipeline's. Z–AC carry whatever the
    sheet said a moment ago, which is how a verdict follows its company through
    a re-sort.
    """
    views = _gather(db)
    order = _fund_order(cfg)

    def sort_key(view: CompanyView) -> tuple:
        priority = view.best["priority"] if view.best is not None else -1.0
        return (-priority, str(view.row["first_seen"]), view.row["id"])

    rows: list[Row] = []
    for index, view in enumerate(sorted(views.values(), key=sort_key), start=2):
        company, best = view.row, view.best
        months = _months_between(company["incorporated_on"], today)
        owner = user.get(company["id"], {})
        cells: dict[str, str] = {
            "A": company["id"],
            "B": str(company["first_seen"] or "")[:10],
            "C": company["canonical_name"],
            "D": hyperlink(company["website_url"], company["domain"] or company["website_url"]),
            "E": str(company["incorporated_on"] or "")[:10],
            "F": "" if months is None else str(int(round(months))),
            "G": company["hq_region"] or "",
            "H": company["sector"] or "",
            "I": company["stage"] or "",
            "J": ", ".join(view.founders),
            # Blank means unknown, and £0 means "known none". Never collapse them.
            "K": "" if company["total_funding_gbp"] is None
                 else _num(company["total_funding_gbp"], 0),
            "L": " · ".join(view.signals),
            "Q": best["fund_key"] if best is not None else "",
            "R": (best["vehicle_key"] or "") if best is not None else "",
            "S": _num(best["fund_fit_pct"]) if best is not None else "",
            "T": _num(best["discovery_edge"]) if best is not None else "",
            "U": _num(best["coverage"], 2) if best is not None else "",
            "V": _num(best["priority"]) if best is not None else "",
            "W": best["tier"] if best is not None else "",
            "X": best["explanation"] if best is not None else "",
            "Y": hyperlink(view.sources[0] if view.sources else None,
                           f"{len(view.sources)} source(s)" if view.sources else ""),
            "Z": owner.get("verdict", ""),
            "AA": owner.get("notes", ""),
            "AB": owner.get("contacted", ""),
            "AC": owner.get("fund_sent", ""),
            "AD": company["website_url"] or "",
            "AE": " | ".join(view.sources),
        }
        for position, key in enumerate(order):
            cells[col_letter(col_index("M") + position)] = _num(view.per_fund.get(key))
        rows.append(Row(key=company["id"], number=index, cells=cells))
    return rows


def build_today(db: Any, cfg: Any, user: Mapping[str, Mapping[str, str]],
                *, today: date) -> list[Row]:
    """The tab Aryan opens: a summary band, then one block per company.

    Not a wall of columns — he said on 17 and 24 July that the fund breakdown
    was hard to scan (07-interfaces tab 1).
    """
    views = _gather(db)
    shortlisted = [v for v in views.values()
                   if v.best is not None and v.best["tier"] == "shortlist"]
    shortlisted.sort(key=lambda v: -v.best["priority"])
    cap = int(getattr(cfg.settings, "daily_digest_max", 10)) if cfg else 10
    shortlisted = shortlisted[:cap]

    last_run = db.one("SELECT * FROM run ORDER BY id DESC LIMIT 1")
    scanned = last_run["items_fetched"] if last_run else 0
    gated = last_run["gated_out"] if last_run else 0
    passed = max(scanned - gated, 0) if last_run else 0

    ages = sorted(m for m in (_months_between(v.row["incorporated_on"], today)
                              for v in shortlisted) if m is not None)
    median = f"{ages[len(ages) // 2]:.0f}" if ages else "—"

    by_fund: dict[str, int] = defaultdict(int)
    for view in shortlisted:
        by_fund[view.best["fund_key"]] += 1
    breakdown = "   ·   ".join(
        f"{FUND_DISPLAY.get(key, key.title())} {by_fund.get(key, 0)}"
        for key in _fund_order(cfg)) or "no funds configured"

    rows: list[Row] = [
        Row("__summary__:1", 2, {"A": "", "B": f"Scanned {scanned}  →  {passed} passed the "
                                              f"age & funding gates  →  {len(shortlisted)} shortlisted"}),
        Row("__summary__:2", 3, {"A": "", "B": f"Median age of today's shortlist: {median} months"}),
        Row("__summary__:3", 4, {"A": "", "B": breakdown}),
        Row("__summary__:4", 5, {"A": "", "B": ""}),
    ]

    start = 6
    for index, view in enumerate(shortlisted):
        company, best = view.row, view.best
        months = _months_between(company["incorporated_on"], today)
        owner = user.get(company["id"], {})
        also = " · ".join(f"{k} {v:.0f} ({t})" for k, v, t in view.also[:3])
        evidence = " · ".join(headline for headline, _ in view.evidence[:3])
        block = {
            "Company": (f'{company["canonical_name"]} · {company["domain"] or ""} · '
                        f'{company["hq_city"] or company["hq_region"] or ""} · '
                        f'incorporated {_relative_age(months)}'),
            "Send to": f'{best["fund_key"]} — {best["vehicle_key"] or "fund level"}',
            "Fit / Edge": (f'{best["fund_fit_pct"]:.0f} / {best["discovery_edge"]:.0f} '
                           f'→ priority {best["priority"]:.0f}'),
            "Why": best["explanation"],
            "Evidence": evidence,
            "Also fits": also,
            "Your call": " · ".join(x for x in (owner.get("verdict", ""),
                                                owner.get("notes", "")) if x),
        }
        base = start + index * TODAY_BLOCK_HEIGHT
        for offset, label in enumerate(TODAY_BLOCK_LABELS):
            cells = {"A": label, "B": block[label], "C": "", "D": ""}
            if label == "Company":
                cells["B"] = hyperlink(company["website_url"], block["Company"])
                cells["D"] = company["website_url"] or ""
            elif label == "Evidence":
                cells["D"] = " | ".join(url for _, url in view.evidence[:3])
            rows.append(Row(f'{company["id"]}:{label}', base + offset, cells))
        rows.append(Row(f'{company["id"]}:blank', base + len(TODAY_BLOCK_LABELS),
                        {"A": "", "B": "", "C": "", "D": ""}))
    return rows


def build_needs_review(db: Any, *, today: date) -> list[Row]:
    rows: list[Row] = []
    query = db.query(
        "SELECT * FROM company WHERE needs_review = 1 AND merged_into IS NULL "
        "ORDER BY first_seen DESC, id")
    for index, company in enumerate(query, start=2):
        rows.append(Row(company["id"], index, {
            "A": company["id"],
            "B": str(company["first_seen"] or "")[:10],
            "C": company["canonical_name"],
            "D": hyperlink(company["website_url"], company["domain"] or ""),
            "E": _review_reason(company),
            "F": company["extraction_method"] or "",
            "G": "0.3" if company["extraction_method"] == "heuristic" else "",
            "H": "",
        }))
    return rows


def _review_reason(company: Any) -> str:
    reasons = []
    if company["extraction_method"] == "heuristic":
        reasons.append("heuristic extraction")
    if company["age_unknown"]:
        reasons.append("age unknown")
    if company["uk_unverified"]:
        reasons.append("UK presence unverified")
    return " · ".join(reasons) or "flagged for review"


def build_sources(db: Any, cfg: Any, *, today: date) -> list[Row]:
    """Source health, and the only place a source failure is ever shown.

    Client, 24 July: "I don't think we need the Source Failed section. I'd
    probably just leave that out." It moves here and nowhere else.
    """
    health: dict[str, list[Any]] = defaultdict(list)
    for row in db.query("SELECT * FROM source_health ORDER BY observed_on DESC"):
        health[row["source_key"]].append(row)

    failures: dict[str, str] = {}
    last_ok: dict[str, str] = {}
    for row in db.query(
        "SELECT rs.source_key, rs.status, rs.error, r.started_at "
        "FROM run_source rs JOIN run r ON r.id = rs.run_id ORDER BY r.id DESC"
    ):
        key = row["source_key"]
        if row["status"] == "ok" and key not in last_ok:
            last_ok[key] = str(row["started_at"] or "")[:16].replace("T", " ")
        if row["status"] == "failed" and key not in failures:
            failures[key] = row["error"] or "failed"

    configured = list(getattr(cfg, "sources", []) or [])
    keys = [s.key for s in configured] or sorted(set(health) | set(last_ok) | set(failures))
    by_key = {s.key: s for s in configured}

    rows: list[Row] = []
    for index, key in enumerate(keys, start=2):
        source = by_key.get(key)
        entries = health.get(key, [])
        recent = entries[:7]
        items_today = _txt(entries[0]["items"]) if entries else ""
        average = f"{sum(e['items'] for e in recent) / len(recent):.1f}" if recent else ""
        enabled = source.enabled if source else True
        if not enabled:
            status, note = "⏸", (source.note if source else "Disabled")
        elif key in failures:
            status, note = "❌", failures[key]
        elif entries and entries[0]["status"] != "ok":
            status, note = "⚠️", (entries[0]["note"] or "")
        elif recent and all(e["items"] == 0 for e in recent[:3]):
            status, note = "⚠️", f"0 items for {min(3, len(recent))} days"
        else:
            status, note = "✅", (source.note if source else "")
        rows.append(Row(key, index, {
            "A": source.key if source else key,
            "B": source.track if source else "A",
            "C": "TRUE" if enabled else "FALSE",
            "D": last_ok.get(key, "—"),
            "E": items_today or "—",
            "F": average or "—",
            "G": status,
            "H": note,
        }))
    return rows


def build_run_log(db: Any) -> list[Row]:
    failed: dict[int, list[str]] = defaultdict(list)
    for row in db.query("SELECT run_id, source_key FROM run_source WHERE status = 'failed'"):
        failed[row["run_id"]].append(row["source_key"])
    rows: list[Row] = []
    for index, run in enumerate(db.query("SELECT * FROM run ORDER BY id DESC LIMIT 500"),
                                start=2):
        rows.append(Row(f'run:{run["id"]}', index, {
            "A": str(run["id"]),
            "B": str(run["started_at"] or ""),
            "C": str(run["finished_at"] or ""),
            "D": run["mode"],
            "E": run["scope"] or "",
            "F": _txt(run["items_fetched"]),
            "G": _txt(run["items_extracted"]),
            "H": _txt(run["companies_new"]),
            "I": _txt(run["companies_merged"]),
            "J": _txt(run["gated_out"]),
            "K": _txt(run["shortlisted"]),
            "L": _txt(run["llm_calls"]),
            "M": _num(run["llm_cost_usd"], 4),
            "N": run["status"],
            "O": ", ".join(failed.get(run["id"], [])),
        }))
    return rows


def build_meta(db: Any, cfg: Any, counts: Mapping[str, int]) -> list[Row]:
    """The hidden tab: schema version, config hash, last-good marker.

    ponytail: deliberately carries no timestamp. A "last run at" cell would
    change on every render and make `test_no_change_means_no_writes` a lie.
    """
    last_good = db.scalar(
        "SELECT config_hash FROM config_snapshot WHERE is_last_good = 1 "
        "ORDER BY created_at DESC LIMIT 1") or ""
    pairs = [
        ("schema_version", db.get_meta("schema_version", "?") or "?"),
        ("layout_version", LAYOUT_VERSION),
        ("config_hash", cfg.hash() if cfg else ""),
        ("last_good_config_hash", last_good),
        ("companies", str(counts.get("companies", 0))),
        ("shortlisted", str(counts.get("shortlisted", 0))),
        ("needs_review", str(counts.get("needs_review", 0))),
    ]
    return [Row(key, index, {"A": key, "B": value})
            for index, (key, value) in enumerate(pairs, start=2)]


def build_status_rows(cells: Sequence[Any], tab: str, column: str) -> list[Row]:
    return [Row(f"{tab}:{column}:{c.row}", c.row, {column: c.text})
            for c in cells if c.tab == tab and c.column == column]


# ------------------------------------------------------------------ seeding


def _default_config() -> Any:
    """Lazily imported so the renderer is usable before Phase 6's sibling lands."""
    from radar.config.models import Config

    try:
        from radar.config.defaults import default_config
    except Exception:                       # noqa: BLE001 - a missing seed is not fatal
        return Config()
    return default_config()


def _is_blank(grid: Sequence[Sequence[Any]]) -> bool:
    return not any(any(str(c).strip() for c in row if c is not None) for row in grid or [])


def seed_grids(existing: Mapping[str, Sequence[Sequence[Any]]]) -> dict[str, list[list[str]]]:
    """Write the editable tabs exactly once, when they are empty.

    After that they are Aryan's: the pipeline reads them and writes only the
    status column. Re-seeding on every run would silently delete his edits,
    which is the one thing this design must never do.
    """
    cfg = _default_config()
    out: dict[str, list[list[str]]] = {}

    if _is_blank(existing.get(SETTINGS, [])):
        grid = [list(HEADERS[SETTINGS])]
        for key, type_hint, description in SETTING_SPECS:
            value = getattr(cfg.settings, key, "")
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            grid.append([key, _txt(value), type_hint, "", description])
        out[SETTINGS] = grid

    if _is_blank(existing.get(FUND_CRITERIA, [])):
        grid = [list(HEADERS[FUND_CRITERIA])]
        for vehicle in cfg.all_vehicles():
            grid.append([
                vehicle.fund_key, vehicle.vehicle_key, vehicle.fund_name,
                vehicle.vehicle_name, "TRUE" if vehicle.active else "FALSE",
                vehicle.stage_min or "", vehicle.stage_max or "",
                _txt(vehicle.cheque_min), _txt(vehicle.cheque_max),
                vehicle.geo_rule, ", ".join(vehicle.geo_values),
                _txt(vehicle.max_age_years),
                " · ".join(f"{k}:{_reject_value(v)}" for k, v in vehicle.hard_rejects.items()),
                ", ".join(vehicle.sectors_plus), ", ".join(vehicle.sectors_minus),
                vehicle.one_liner, "",
            ])
        out[FUND_CRITERIA] = grid

    if _is_blank(existing.get(SCORING_WEIGHTS, [])):
        out[SCORING_WEIGHTS] = _weights_grid(cfg)

    if _is_blank(existing.get(LISTS, [])):
        out[LISTS] = _lists_grid(cfg)

    if _is_blank(existing.get(SOURCES, [])):
        grid = [list(HEADERS[SOURCES])]
        for source in cfg.sources:
            grid.append([source.key, source.track,
                         "TRUE" if source.enabled else "FALSE",
                         "—", "—", "—", "✅", source.note])
        out[SOURCES] = grid

    return out


def _reject_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return _txt(value)


def _weights_grid(cfg: Any) -> list[list[str]]:
    """Block 1 (the 0–4 matrix), a banner, then block 2 (0–10 importance)."""
    from radar.config.loader import ATTRIBUTE_VOCAB_KEY, vocab

    funds = _fund_order(cfg) or list(FUND_ORDER)
    header = ["Attribute", "Category"] + [FUND_DISPLAY.get(k, k.title()) for k in funds] \
        + ["Unknown policy", "Notes", "Status"]
    grid: list[list[str]] = [header]
    for attribute in ("stage", "sector", "geography", "founder_signal", "traction_signal"):
        values = cfg.weights.matrix.get(attribute) or {}
        order = list(values) or list(vocab(cfg.lists or {}, ATTRIBUTE_VOCAB_KEY[attribute]))
        for value in order:
            per_fund = values.get(value, {})
            grid.append([attribute, value]
                        + [_txt(per_fund.get(k, "")) for k in funds]
                        + [cfg.weights.unknown_policy.get(attribute, "neutral"), "", ""])
    grid.append([""] * len(header))
    grid.append([IMPORTANCE_BANNER] + [""] * (len(header) - 1))
    grid.append(["Attribute"] + [FUND_DISPLAY.get(k, k.title()) for k in funds]
                + [""] * (len(header) - 1 - len(funds)))
    for attribute in ("stage", "sector", "geography", "founder_signal", "traction_signal"):
        per_fund = cfg.weights.importance.get(attribute, {})
        grid.append([attribute] + [_txt(per_fund.get(k, "")) for k in funds]
                    + [""] * (len(header) - 1 - len(funds)))
    return grid


def _lists_grid(cfg: Any) -> list[list[str]]:
    from radar.config.loader import DEFAULT_LISTS

    columns = list(HEADERS[LISTS])
    data: dict[str, list[str]] = {}
    for name in columns:
        values = (cfg.lists or {}).get(name) or list(DEFAULT_LISTS.get(name, ()))
        data[name] = [str(v) for v in values]
    depth = max((len(v) for v in data.values()), default=0)
    grid = [columns]
    for index in range(depth):
        grid.append([data[name][index] if index < len(data[name]) else "" for name in columns])
    return grid


# ------------------------------------------------------------------- entry


def sync_sheet(db: Any, *, gateway: SheetGateway | None = None,
               today: date | None = None) -> dict[str, Any]:
    """Render every tab the pipeline owns, in as few API calls as possible.

    Steady state on an unchanged database: three reads, zero writes.
    A 200-row first render: two reads, two value writes, one format write.
    """
    from radar.config.loader import (
        FUND_CRITERIA_STATUS_COL,
        SETTINGS_STATUS_COL,
        WEIGHTS_STATUS_COL,
        load_config,
    )

    gw = gateway or open_gateway()
    today = today or date.today()

    sheets = dict(gw.sheets())
    missing = [tab for tab in EXPECTED_TABS if tab not in sheets]
    if missing:
        sheets.update(gw.add_tabs(missing))

    ranges = list(READ_RANGES.values())
    got = gw.batch_get(ranges)
    raw: dict[str, list[list[str]]] = {
        tab: list(got.get(rng, []) or []) for tab, rng in READ_RANGES.items()
    }

    seeds = seed_grids(raw)
    seed_writes: list[ValueRange] = []
    for tab, grid in seeds.items():
        raw[tab] = grid
        width = col_letter(max(len(r) for r in grid) - 1)
        seed_writes.append(ValueRange(a1(tab, "A", 1, width, len(grid)), grid))

    user = read_user_columns(raw.get(COMPANIES, []))
    save_user_fields(db, user)
    if not user:
        user = user_fields_from_db(db)

    result = load_config(raw, db=db)
    cfg = result.config

    counts = {
        "companies": db.scalar(
            "SELECT COUNT(*) FROM company WHERE merged_into IS NULL") or 0,
        "shortlisted": db.scalar(
            "SELECT COUNT(DISTINCT company_id) FROM score WHERE tier = 'shortlist'") or 0,
        "needs_review": db.scalar(
            "SELECT COUNT(*) FROM company WHERE needs_review = 1 AND merged_into IS NULL") or 0,
    }

    existing_companies = max(len(raw.get(COMPANIES, [])), 0)

    # A manual re-sort makes the positional diff lie; the whole tab is rewritten.
    companies_resorted = resorted(db, COMPANIES, raw.get(COMPANIES, []))

    plan = WritePlan()
    plan.extend(_header_plan(db, seeds))
    plan.extend(plan_tab(db, COMPANIES, build_companies(db, cfg, user, today=today),
                         columns_for(COMPANIES),
                         hyperlink_cols=HYPERLINK_COLUMNS[COMPANIES],
                         force=companies_resorted,
                         existing_rows=existing_companies))
    plan.extend(plan_tab(db, TODAY, build_today(db, cfg, user, today=today),
                         ["A", "B", "C", "D"],
                         hyperlink_cols=HYPERLINK_COLUMNS[TODAY]))
    plan.extend(plan_tab(db, NEEDS_REVIEW, build_needs_review(db, today=today),
                         columns_for(NEEDS_REVIEW),
                         hyperlink_cols=HYPERLINK_COLUMNS[NEEDS_REVIEW]))
    plan.extend(plan_tab(db, SOURCES, build_sources(db, cfg, today=today),
                         ["A", "B", "C", "D", "E", "F", "G", "H"]))
    plan.extend(plan_tab(db, RUN_LOG, build_run_log(db), columns_for(RUN_LOG)))
    plan.extend(plan_tab(db, META, build_meta(db, cfg, counts), ["A", "B"]))

    for tab, column in ((SETTINGS, SETTINGS_STATUS_COL),
                        (FUND_CRITERIA, FUND_CRITERIA_STATUS_COL),
                        (SCORING_WEIGHTS, WEIGHTS_STATUS_COL)):
        plan.extend(plan_tab(db, tab, build_status_rows(result.status_cells, tab, column),
                             [column], state_tab=f"{tab}#{column}"))

    requests: list[dict] = []
    if db.get_meta("sheet_layout_version") != LAYOUT_VERSION or missing:
        requests += layout_requests(
            sheets,
            verdicts=cfg.lists.get("verdict", []),
            fund_names=[f.name for f in cfg.funds],
            stages=cfg.lists.get("stage", []),
        )
    status_signature = json.dumps(
        [(c.tab, c.column, c.row, c.is_error) for c in result.status_cells], sort_keys=True)
    if db.get_meta("sheet_status_signature") != status_signature:
        requests += status_format_requests(
            sheets, [(c.tab, c.column, c.row, c.is_error) for c in result.status_cells])

    raw_writes = seed_writes + plan.raw
    calls = 0
    if raw_writes:
        gw.batch_set(raw_writes, RAW)
        calls += 1
    if plan.user_entered:
        gw.batch_set(plan.user_entered, USER_ENTERED)
        calls += 1
    if requests:
        gw.batch_requests(requests)
        calls += 1
        db.set_meta("sheet_layout_version", LAYOUT_VERSION)
        db.set_meta("sheet_status_signature", status_signature)

    if plan.state:
        save_state(db, plan)

    # 02-architecture §7, "Google Sheets API down": rows stay in SQLite with
    # `synced = 0` and the next run upserts them. The column existed and nothing
    # ever set it, so the promise was unenforced — a sheet outage was
    # indistinguishable from a clean render. Reaching this line means every
    # gateway call returned, so everything currently in the database is on the
    # sheet. A failure anywhere above raises out of `sync_sheet` and the flag
    # stays 0, which is exactly the state the next run needs to find.
    db.execute("UPDATE company SET synced = 1 WHERE merged_into IS NULL AND synced = 0")

    return {
        "tabs_created": missing,
        "tabs_seeded": sorted(seeds),
        "rows": counts,
        "ranges_written": len(raw_writes) + len(plan.user_entered),
        "write_calls": calls,
        "config_hash": cfg.hash(),
        "errors": result.errors,
        "warnings": result.warnings,
        "user_columns_preserved": len(user),
        "companies_resorted": companies_resorted,
    }


def _header_plan(db: Any, seeds: Mapping[str, Any]) -> WritePlan:
    """Header rows for the tabs the pipeline owns. Written once, then diffed
    away like everything else. Tabs that were just seeded already have theirs."""
    plan = WritePlan()
    owned = {
        COMPANIES: columns_for(COMPANIES),
        NEEDS_REVIEW: columns_for(NEEDS_REVIEW),
        RUN_LOG: columns_for(RUN_LOG),
        TUNING: columns_for(TUNING),
        META: columns_for(META),
        TODAY: ["A", "B", "C", "D"],
    }
    for tab, columns in owned.items():
        if tab in seeds:
            continue
        headers = HEADERS[tab]
        cells = {column: (headers[index] if index < len(headers) else "")
                 for index, column in enumerate(columns)}
        plan.extend(plan_tab(db, tab, [Row("__header__", 1, cells)], list(columns),
                             state_tab=f"{tab}#header"))
    return plan
