"""Sheet layout: every tab, every column, every format request.

Two jobs:

1. **The layout constants.** Tab names, headers, which columns Aryan owns, which
   are clickable. `radar.render.sheet` imports them; nothing here imports
   `sheet`, so the pair stays acyclic.
2. **`layout_requests()`** — freezing, colours, number formats, conditional
   rules, dropdowns, hidden columns and protected ranges, all as one flat list
   of Sheets API requests destined for a **single** `batchUpdate`
   (05-pipeline ⑦). Never a request per cell.

Protected ranges are `warningOnly` on purpose: hard protection on a range owned
by a service account can lock the human out of his own spreadsheet
(05-pipeline ⑦).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# Bump when the layout changes. Stored in `_meta` so a steady-state run sends no
# format requests at all — which is what keeps `test_no_change_means_no_writes`
# honest.
LAYOUT_VERSION = "1"

# --------------------------------------------------------------------- tabs

TODAY = "📌 Today"
COMPANIES = "Companies"
NEEDS_REVIEW = "Needs Review"
FUND_CRITERIA = "Fund Criteria"
SCORING_WEIGHTS = "Scoring Weights"
SETTINGS = "Settings"
OUTREACH = "Outreach"
SOURCES = "Sources"
RUN_LOG = "Run Log"
TUNING = "Tuning"
LISTS = "Lists"
META = "_meta"

# 07-interfaces §1: eleven visible + one hidden, in this order.
EXPECTED_TABS: tuple[str, ...] = (
    TODAY, COMPANIES, NEEDS_REVIEW, FUND_CRITERIA, SCORING_WEIGHTS, SETTINGS,
    OUTREACH, SOURCES, RUN_LOG, TUNING, LISTS, META,
)

# "The Outreach tab is never touched by the system. At all." It is created once,
# because it is one of the twelve, and then never read, written or formatted.
UNTOUCHABLE_TABS: frozenset[str] = frozenset({OUTREACH})

# ------------------------------------------------------------------ headers

COMPANIES_HEADERS: tuple[str, ...] = (
    "ID", "First Seen", "Company", "Website", "Incorporated", "Age (months)",
    "Region", "Sector", "Stage", "Founders", "Funding known", "Signals",
    "DSW fit", "Northstar fit", "Outward fit", "Anticus fit",
    "Best fund", "Vehicle", "Fit %", "Discovery Edge", "Coverage", "Priority",
    "Tier", "Why", "Sources",
    "Verdict", "Notes", "Contacted", "Fund sent to",
    # Hidden read-back columns. Client request, 24 July: "would it be possible
    # to include the actual URL as well?" — D and Y hold =HYPERLINK formulas,
    # which read back as their label, so the plain URL lives here too.
    "Website URL", "Source URLs",
)

TODAY_HEADERS: tuple[str, ...] = ("UK FOUNDER RADAR", "", "", "Links")

NEEDS_REVIEW_HEADERS: tuple[str, ...] = (
    "ID", "First Seen", "Company", "Website", "Why flagged", "Extraction",
    "Confidence", "Sources", "Action",
)

FUND_CRITERIA_HEADERS: tuple[str, ...] = (
    "Fund key", "Vehicle key", "Fund", "Vehicle", "Active", "Stage min",
    "Stage max", "Cheque min", "Cheque max", "Geo rule", "Geo values",
    "Max age (yrs)", "Hard rejects", "Sectors +", "Sectors −", "One-liner",
    # ponytail: 07-interfaces tab 4 stops at "One-liner", but the same section
    # requires "unknown keys produce a warning in the status column". A status
    # column therefore has to exist; Q is the first free column.
    "Status",
)

SCORING_WEIGHTS_HEADERS: tuple[str, ...] = (
    "Attribute", "Category", "DSW", "Northstar", "Outward", "Anticus",
    "Unknown policy", "Notes", "Status",
)

IMPORTANCE_BANNER = "ATTRIBUTE IMPORTANCE"
IMPORTANCE_HEADERS: tuple[str, ...] = (
    "Attribute", "DSW", "Northstar", "Outward", "Anticus", "", "", "", "Status",
)

SETTINGS_HEADERS: tuple[str, ...] = ("Key", "Value", "Type", "Status", "What it does")

SOURCES_HEADERS: tuple[str, ...] = (
    "Source", "Track", "Enabled", "Last OK", "Items today", "7-day avg",
    "Status", "Note",
)

RUN_LOG_HEADERS: tuple[str, ...] = (
    "Run", "Started", "Finished", "Mode", "Scope", "Fetched", "Extracted",
    "New", "Merged", "Gated out", "Shortlisted", "LLM calls", "Cost USD",
    "Status", "Sources failed",
)

TUNING_HEADERS: tuple[str, ...] = (
    "Metric", "Threshold", "Would shortlist", "Precision", "Recall", "F1",
)

LISTS_HEADERS: tuple[str, ...] = (
    "stage", "sector", "geography", "founder_signal", "traction_signal",
    "tier", "verdict", "gate_geography", "sic_code", "sic_sector",
    "region_ons", "region_value",
)

META_HEADERS: tuple[str, ...] = ("key", "value")

HEADERS: dict[str, tuple[str, ...]] = {
    TODAY: TODAY_HEADERS,
    COMPANIES: COMPANIES_HEADERS,
    NEEDS_REVIEW: NEEDS_REVIEW_HEADERS,
    FUND_CRITERIA: FUND_CRITERIA_HEADERS,
    SCORING_WEIGHTS: SCORING_WEIGHTS_HEADERS,
    SETTINGS: SETTINGS_HEADERS,
    SOURCES: SOURCES_HEADERS,
    RUN_LOG: RUN_LOG_HEADERS,
    TUNING: TUNING_HEADERS,
    LISTS: LISTS_HEADERS,
    META: META_HEADERS,
}

# ------------------------------------------------- Companies column geometry

# Aryan's columns. The pipeline never originates a value here; it only relocates
# one after a re-sort (07-interfaces §4).
USER_COLUMNS: tuple[str, ...] = ("Z", "AA", "AB", "AC")
USER_FIELDS: tuple[str, ...] = ("verdict", "notes", "contacted", "fund_sent")
USER_COLUMN_FIELD: dict[str, str] = dict(zip(USER_COLUMNS, USER_FIELDS))

# Generated columns, warning-only protected.
PIPELINE_COLUMNS_LAST = "Y"
HIDDEN_COLUMNS: tuple[str, ...] = ("AD", "AE")

# =HYPERLINK() lives here, so these ranges — and only these — go out with
# value_input_option=USER_ENTERED. Everything else is RAW, because USER_ENTERED
# turns "0114" into 114 and "1-2" into a date (05-pipeline ⑦).
HYPERLINK_COLUMNS: dict[str, frozenset[str]] = {
    COMPANIES: frozenset({"D", "Y"}),
    NEEDS_REVIEW: frozenset({"D", "H"}),
    TODAY: frozenset({"B"}),
}

AGE_COLUMN = "F"
COVERAGE_COLUMN = "U"
TIER_COLUMN = "W"

# ------------------------------------------------------------------- colours

WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
HEADER_GREEN = {"red": 0.06, "green": 0.44, "blue": 0.29}
AGE_GREEN = {"red": 0.79, "green": 0.94, "blue": 0.80}
AGE_YELLOW = {"red": 1.0, "green": 0.95, "blue": 0.72}
AGE_AMBER = {"red": 0.99, "green": 0.85, "blue": 0.62}
AGE_RED = {"red": 0.98, "green": 0.73, "blue": 0.71}
LOW_COVERAGE_AMBER = {"red": 1.0, "green": 0.90, "blue": 0.72}
WATCHLIST_GREY = {"red": 0.93, "green": 0.93, "blue": 0.93}
ERROR_RED = {"red": 0.80, "green": 0.0, "blue": 0.0}
ID_GREY = {"red": 0.55, "green": 0.55, "blue": 0.55}


# ------------------------------------------------------------------ helpers


def col_letter(index: int) -> str:
    """0-based column index → spreadsheet letter. 0→A, 25→Z, 26→AA."""
    if index < 0:
        raise ValueError("column index must be >= 0")
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def col_index(letter: str) -> int:
    """Spreadsheet letter → 0-based column index. Inverse of `col_letter`."""
    total = 0
    for ch in letter.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"not a column letter: {letter!r}")
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total - 1


def columns_for(tab: str) -> tuple[str, ...]:
    return tuple(col_letter(i) for i in range(len(HEADERS.get(tab, ()))))


def a1(tab: str, first_col: str, first_row: int, last_col: str, last_row: int) -> str:
    """Always single-quote the tab: '📌 Today' and 'Fund Criteria' both need it."""
    return f"'{tab}'!{first_col}{first_row}:{last_col}{last_row}"


def _grid(sheet_id: int, *, r0: int = 0, r1: int | None = None,
          c0: int = 0, c1: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"sheetId": sheet_id, "startRowIndex": r0, "startColumnIndex": c0}
    if r1 is not None:
        out["endRowIndex"] = r1
    if c1 is not None:
        out["endColumnIndex"] = c1
    return out


def _text_format(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


# ------------------------------------------------------------- the requests


def header_requests(sheet_id: int, width: int) -> list[dict[str, Any]]:
    """Green header band, bold white text, frozen."""
    return [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {
            "range": _grid(sheet_id, r0=0, r1=1, c0=0, c1=width),
            "cell": {"userEnteredFormat": {
                "backgroundColor": HEADER_GREEN,
                "horizontalAlignment": "LEFT",
                "textFormat": _text_format(bold=True, foregroundColor=WHITE)}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"}},
    ]


def age_conditional_rules(sheet_id: int) -> list[dict[str, Any]]:
    """Companies column F. Green ≤12, yellow 13–24, amber 25–36, red above.

    A screen full of red means the age gate has drifted — the version 1 failure
    mode, made visible (07-interfaces tab 2).
    """
    col = col_index(AGE_COLUMN)
    rng = _grid(sheet_id, r0=1, c0=col, c1=col + 1)
    specs = [
        ("NUMBER_LESS_THAN_EQ", ["12"], AGE_GREEN),
        ("NUMBER_BETWEEN", ["13", "24"], AGE_YELLOW),
        ("NUMBER_BETWEEN", ["25", "36"], AGE_AMBER),
        ("NUMBER_GREATER", ["36"], AGE_RED),
    ]
    return [
        {"addConditionalFormatRule": {
            "index": i,
            "rule": {"ranges": [rng],
                     "booleanRule": {
                         "condition": {"type": kind,
                                       "values": [{"userEnteredValue": v} for v in values]},
                         "format": {"backgroundColor": colour}}}}}
        for i, (kind, values, colour) in enumerate(specs)
    ]


def coverage_and_tier_rules(sheet_id: int, width: int) -> list[dict[str, Any]]:
    """Amber where coverage < 0.5, grey for anything on watchlist (07-interfaces tab 1)."""
    whole = _grid(sheet_id, r0=1, c0=0, c1=width)
    cov = f"${COVERAGE_COLUMN}2"
    tier = f"${TIER_COLUMN}2"
    return [
        {"addConditionalFormatRule": {
            "index": 0,
            "rule": {"ranges": [whole],
                     "booleanRule": {
                         "condition": {"type": "CUSTOM_FORMULA",
                                       "values": [{"userEnteredValue":
                                                   f'=AND(N({cov})>0,{cov}<0.5)'}]},
                         "format": {"backgroundColor": LOW_COVERAGE_AMBER}}}}},
        {"addConditionalFormatRule": {
            "index": 1,
            "rule": {"ranges": [whole],
                     "booleanRule": {
                         "condition": {"type": "CUSTOM_FORMULA",
                                       "values": [{"userEnteredValue": f'={tier}="watchlist"'}]},
                         "format": {"backgroundColor": WATCHLIST_GREY}}}}},
    ]


def dropdown(sheet_id: int, column: str, values: Sequence[str], *,
             strict: bool = False) -> dict[str, Any]:
    """One-of-list validation on a whole column, header excluded.

    `strict=False` (show a warning, accept the value) everywhere Aryan types,
    for the same reason protected ranges are warning-only.
    """
    c = col_index(column)
    return {"setDataValidation": {
        "range": _grid(sheet_id, r0=1, c0=c, c1=c + 1),
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": v} for v in values]},
                 "showCustomUi": True,
                 "strict": strict}}}


def number_range_validation(sheet_id: int, r0: int, r1: int | None,
                            c0: int, c1: int, low: int, high: int) -> dict[str, Any]:
    return {"setDataValidation": {
        "range": _grid(sheet_id, r0=r0, r1=r1, c0=c0, c1=c1),
        "rule": {"condition": {"type": "NUMBER_BETWEEN",
                               "values": [{"userEnteredValue": str(low)},
                                          {"userEnteredValue": str(high)}]},
                 "inputMessage": f"Whole numbers {low}–{high}",
                 "strict": False}}}


def protect(sheet_id: int, description: str, *, c0: int = 0,
            c1: int | None = None, r0: int = 0) -> dict[str, Any]:
    """warningOnly — 'are you sure?', never a lockout (05-pipeline ⑦)."""
    return {"addProtectedRange": {
        "protectedRange": {"range": _grid(sheet_id, r0=r0, c0=c0, c1=c1),
                           "description": description,
                           "warningOnly": True}}}


def hide_columns(sheet_id: int, columns: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for column in columns:
        c = col_index(column)
        out.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": c, "endIndex": c + 1},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser"}})
    return out


def column_width(sheet_id: int, column: str, pixels: int) -> dict[str, Any]:
    c = col_index(column)
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": c, "endIndex": c + 1},
        "properties": {"pixelSize": pixels},
        "fields": "pixelSize"}}


def number_format(sheet_id: int, column: str, pattern: str,
                  kind: str = "NUMBER") -> dict[str, Any]:
    c = col_index(column)
    return {"repeatCell": {
        "range": _grid(sheet_id, r0=1, c0=c, c1=c + 1),
        "cell": {"userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}},
        "fields": "userEnteredFormat.numberFormat"}}


def red_text(sheet_id: int, column: str, row: int) -> dict[str, Any]:
    """The status cell for a bad value, in red, next to the offending cell."""
    c = col_index(column)
    return {"repeatCell": {
        "range": _grid(sheet_id, r0=row - 1, r1=row, c0=c, c1=c + 1),
        "cell": {"userEnteredFormat": {
            "textFormat": _text_format(foregroundColor=ERROR_RED, bold=True)}},
        "fields": "userEnteredFormat.textFormat"}}


def plain_text(sheet_id: int, column: str, row: int) -> dict[str, Any]:
    c = col_index(column)
    return {"repeatCell": {
        "range": _grid(sheet_id, r0=row - 1, r1=row, c0=c, c1=c + 1),
        "cell": {"userEnteredFormat": {"textFormat": _text_format(
            foregroundColor={"red": 0.0, "green": 0.0, "blue": 0.0}, bold=False)}},
        "fields": "userEnteredFormat.textFormat"}}


# ----------------------------------------------------------------- assembly


def layout_requests(sheet_ids: Mapping[str, int], *,
                    verdicts: Sequence[str] = (),
                    fund_names: Sequence[str] = (),
                    stages: Sequence[str] = (),
                    tiers: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Every format, rule, dropdown and protected range — one flat list.

    The caller sends this in a **single** `batchUpdate`. Dropdown contents come
    from the Lists tab at runtime (03-data-model §4), which is why the
    vocabularies are arguments and not constants.

    `Outreach` is absent by construction: it is in `UNTOUCHABLE_TABS`.
    """
    reqs: list[dict[str, Any]] = []

    for tab, headers in HEADERS.items():
        sid = sheet_ids.get(tab)
        if sid is None or tab in UNTOUCHABLE_TABS:
            continue
        reqs += header_requests(sid, len(headers))

    companies = sheet_ids.get(COMPANIES)
    if companies is not None:
        width = len(COMPANIES_HEADERS)
        reqs += age_conditional_rules(companies)
        reqs += coverage_and_tier_rules(companies, width)
        reqs += hide_columns(companies, HIDDEN_COLUMNS)
        reqs.append(column_width(companies, "A", 60))
        reqs.append({"repeatCell": {
            "range": _grid(companies, r0=1, c0=0, c1=1),
            "cell": {"userEnteredFormat": {"textFormat": _text_format(
                foregroundColor=ID_GREY, fontSize=8)}},
            "fields": "userEnteredFormat.textFormat"}})
        reqs.append(column_width(companies, "X", 420))
        reqs.append(column_width(companies, "AA", 320))
        reqs.append(number_format(companies, AGE_COLUMN, "0"))
        reqs.append(number_format(companies, "K", '"£"#,##0'))
        for column in ("S", "T", "V"):
            reqs.append(number_format(companies, column, "0.0"))
        reqs.append(number_format(companies, COVERAGE_COLUMN, "0.00"))
        for column in ("B", "E"):
            reqs.append(number_format(companies, column, "dd mmm yyyy", kind="DATE"))
        reqs.append(number_format(companies, "AB", "dd mmm yyyy", kind="DATE"))
        if verdicts:
            reqs.append(dropdown(companies, "Z", verdicts))
        if fund_names:
            reqs.append(dropdown(companies, "AC", fund_names))
        # A–Y generated, warning-only. Z–AC deliberately outside it.
        reqs.append(protect(companies, "Generated columns A–Y — pipeline owned",
                            c0=0, c1=col_index(PIPELINE_COLUMNS_LAST) + 1))

    today = sheet_ids.get(TODAY)
    if today is not None:
        reqs.append(column_width(today, "A", 130))
        reqs.append(column_width(today, "B", 720))
        reqs += hide_columns(today, ["D"])
        reqs.append(protect(today, "Today — pipeline owned"))

    review = sheet_ids.get(NEEDS_REVIEW)
    if review is not None:
        reqs.append(protect(review, "Needs Review — generated columns",
                            c0=0, c1=col_index("H") + 1))

    criteria = sheet_ids.get(FUND_CRITERIA)
    if criteria is not None:
        reqs.append(dropdown(criteria, "E", ["TRUE", "FALSE"]))
        reqs.append(dropdown(criteria, "J", ["HARD", "SOFT"]))
        if stages:
            reqs.append(dropdown(criteria, "F", stages))
            reqs.append(dropdown(criteria, "G", stages))
        reqs.append(column_width(criteria, "M", 380))
        reqs.append(column_width(criteria, "Q", 320))
        reqs.append(protect(criteria, "Fund key / Vehicle key are canonical — do not edit",
                            c0=0, c1=2))

    weights = sheet_ids.get(SCORING_WEIGHTS)
    if weights is not None:
        # Block 1 is the 0–4 matrix; block 2, below the ATTRIBUTE IMPORTANCE
        # banner, is 0–10. Two tables, two functions — confusing them gets every
        # score wrong (07-interfaces tab 5), so they get different validations.
        reqs.append(number_range_validation(weights, 1, 60, 2, 6, 0, 4))
        reqs.append(number_range_validation(weights, 61, None, 1, 5, 0, 10))
        reqs.append(dropdown(weights, "G", ["neutral", "pessimistic", "assume"]))
        reqs.append(column_width(weights, "I", 320))

    settings = sheet_ids.get(SETTINGS)
    if settings is not None:
        reqs.append(column_width(settings, "D", 380))
        reqs.append(column_width(settings, "E", 320))
        reqs.append(protect(settings, "Key / Type / Status are pipeline owned", c0=0, c1=1))

    sources = sheet_ids.get(SOURCES)
    if sources is not None:
        reqs.append(dropdown(sources, "C", ["TRUE", "FALSE"]))
        reqs.append(column_width(sources, "H", 380))
        # Enabled (C) stays editable so Aryan can switch a noisy source off.
        reqs.append(protect(sources, "Source health — pipeline owned", c0=3, c1=8))

    for tab in (RUN_LOG, TUNING):
        sid = sheet_ids.get(tab)
        if sid is not None:
            reqs.append(protect(sid, f"{tab} — pipeline owned"))

    meta = sheet_ids.get(META)
    if meta is not None:
        reqs.append({"updateSheetProperties": {
            "properties": {"sheetId": meta, "hidden": True},
            "fields": "hidden"}})
        reqs.append(protect(meta, "_meta — pipeline owned"))

    if tiers:
        pass  # tiers are rendered, never typed; no dropdown needed.

    return reqs


def status_format_requests(sheet_ids: Mapping[str, int],
                           cells: Iterable[tuple[str, str, int, bool]]) -> list[dict[str, Any]]:
    """Red for a reported error, plain black once the human fixes it.

    `cells` yields `(tab, column, row, is_error)`.
    """
    out: list[dict[str, Any]] = []
    for tab, column, row, is_error in cells:
        sid = sheet_ids.get(tab)
        if sid is None or tab in UNTOUCHABLE_TABS:
            continue
        out.append(red_text(sid, column, row) if is_error else plain_text(sid, column, row))
    return out
