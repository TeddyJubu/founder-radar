"""In-memory doubles for stage ⑦ — the sheet renderer.

`radar.render.sheet.SheetGateway` is a `Protocol` for exactly one reason: so the
whole of stage ⑦ can run against a dictionary instead of Google. This module is
that dictionary.

`FakeSheetGateway` implements the five verbs, holds each tab as a plain list of
lists, and records every call. That is what makes the three invariants the
client's primary interface depends on assertable **offline**, in CI, with no
scratch spreadsheet and no credential:

* the FR-7.7 budget of ten API calls for a 200-row render,
* "no change means no writes" — a steady-state run issues zero writes,
* Aryan's columns Z–AC follow their company through a manual re-sort.

Two behaviours are modelled rather than faked, because tests that ignore them
would pass against a double and fail against Google:

* **Trailing blanks are trimmed on read.** `values.batchGet` returns rows only
  as far as the last non-empty one, and each row only as far as its last
  non-empty cell. Code that indexes past that must cope, and here it has to.
* **`=HYPERLINK()` reads back as its label.** Written `USER_ENTERED`, a formula
  is evaluated; the plain URL is gone. That is precisely why `Companies` carries
  the hidden AD/AE read-back columns (07-interfaces tab 1), and the fake keeps
  that fact visible.

`CountingGateway` is the same call log wrapped around a *real* gateway, so
`tests/integration/test_sheet_live.py` can assert the identical budgets against
a live scratch spreadsheet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from radar.render.formatting import col_index
from radar.render.sheet import USER_ENTERED, ValueRange

# Every range this system emits is fully qualified and single-quoted, because
# '📌 Today' and 'Fund Criteria' both need it (formatting.a1).
_RANGE = re.compile(r"^'(?P<tab>[^']+)'!(?P<c0>[A-Z]+)(?P<r0>\d+):(?P<c1>[A-Z]+)(?P<r1>\d+)$")
_HYPERLINK = re.compile(r'^=HYPERLINK\(".*?","(?P<label>.*)"\)$', re.DOTALL)

WRITE_METHODS = frozenset({"add_tabs", "batch_set", "batch_requests"})


def parse_a1(rng: str) -> tuple[str, int, int, int, int]:
    """`'Companies'!A2:AC201` → `(tab, r0, c0, r1, c1)`, all 0-based inclusive."""
    match = _RANGE.match(rng)
    if match is None:
        raise ValueError(f"not a range this system writes: {rng!r}")
    return (
        match["tab"],
        int(match["r0"]) - 1,
        col_index(match["c0"]),
        int(match["r1"]) - 1,
        col_index(match["c1"]),
    )


def display(value: Any, value_input_option: str) -> str:
    """What the cell reads back as after a write with `value_input_option`."""
    text = "" if value is None else str(value)
    if value_input_option == USER_ENTERED:
        match = _HYPERLINK.match(text)
        if match:
            return match["label"].replace('""', '"')
    return text


def _trim_row(row: Sequence[str]) -> list[str]:
    cells = list(row)
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _trim(grid: Sequence[Sequence[str]]) -> list[list[str]]:
    rows = [_trim_row(r) for r in grid]
    while rows and not rows[-1]:
        rows.pop()
    return rows


@dataclass
class Call:
    """One API call. `detail` is whatever makes the assertion readable."""

    method: str
    detail: Any = None


class _CallLog:
    """The counters every sheet test asserts on."""

    calls: list[Call]

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def write_calls(self) -> int:
        return sum(1 for c in self.calls if c.method in WRITE_METHODS)

    @property
    def read_calls(self) -> int:
        return sum(1 for c in self.calls if c.method not in WRITE_METHODS)

    def methods(self) -> list[str]:
        return [c.method for c in self.calls]

    def reset(self) -> None:
        self.calls.clear()


class FakeSheetGateway(_CallLog):
    """An in-memory spreadsheet behind the five-verb `SheetGateway` Protocol."""

    def __init__(self, tabs: Sequence[str] = ()) -> None:
        self.grids: dict[str, list[list[str]]] = {t: [] for t in tabs}
        self.ids: dict[str, int] = {t: i for i, t in enumerate(tabs)}
        self._next_id = len(tabs)
        self.calls: list[Call] = []
        self.requests: list[dict] = []                    # every format request ever sent
        self.writes: list[tuple[str, str]] = []           # (value_input_option, range)

    # ------------------------------------------------------------ the verbs

    def sheets(self) -> dict[str, int]:
        self.calls.append(Call("sheets"))
        return dict(self.ids)

    def add_tabs(self, titles: Sequence[str]) -> dict[str, int]:
        self.calls.append(Call("add_tabs", list(titles)))
        out: dict[str, int] = {}
        for title in titles:
            if title not in self.ids:
                self.ids[title] = self._next_id
                self._next_id += 1
                self.grids.setdefault(title, [])
            out[title] = self.ids[title]
        return out

    def batch_get(self, ranges: Sequence[str]) -> dict[str, list[list[str]]]:
        self.calls.append(Call("batch_get", list(ranges)))
        return {rng: self._read(rng) for rng in ranges}

    def batch_set(self, data: Sequence[ValueRange], value_input_option: str) -> None:
        self.calls.append(Call("batch_set", [vr.range for vr in data]))
        for vr in data:
            self.writes.append((value_input_option, vr.range))
            self._apply(vr, value_input_option)

    def batch_requests(self, requests: Sequence[dict]) -> None:
        self.calls.append(Call("batch_requests", list(requests)))
        self.requests.extend(requests)

    # ------------------------------------------------------------- internals

    def _read(self, rng: str) -> list[list[str]]:
        tab, r0, c0, r1, c1 = parse_a1(rng)
        grid = self.grids.get(tab, [])
        window = [list(row[c0:c1 + 1]) for row in grid[r0:r1 + 1]]
        return _trim(window)

    def _apply(self, vr: ValueRange, value_input_option: str) -> None:
        tab, r0, c0, _, _ = parse_a1(vr.range)
        grid = self.grids.setdefault(tab, [])
        for offset, values in enumerate(vr.values):
            index = r0 + offset
            while len(grid) <= index:
                grid.append([])
            row = grid[index]
            for step, value in enumerate(values):
                column = c0 + step
                while len(row) <= column:
                    row.append("")
                row[column] = display(value, value_input_option)

    # ----------------------------------------------------------- inspection

    def tab_names(self) -> list[str]:
        return list(self.ids)

    def grid(self, tab: str) -> list[list[str]]:
        return _trim(self.grids.get(tab, []))

    def rows(self, tab: str) -> list[list[str]]:
        """Data rows only — row 1 is the header everywhere the pipeline writes."""
        return self.grid(tab)[1:]

    def at(self, tab: str, row: int, column: str) -> str:
        """One cell, 1-based row and a column letter, blank if never written."""
        grid = self.grids.get(tab, [])
        index = col_index(column)
        if row - 1 >= len(grid) or index >= len(grid[row - 1]):
            return ""
        return grid[row - 1][index]

    def cell(self, tab: str, ref: str) -> str:
        column, row = re.match(r"^([A-Z]+)(\d+)$", ref).groups()   # type: ignore[union-attr]
        return self.at(tab, int(row), column)

    def set_cell(self, tab: str, ref: str, value: str) -> None:
        """Aryan typing into a cell. Deliberately not a recorded API call."""
        self._apply(ValueRange(f"'{tab}'!{ref}:{ref}", [[value]]), "RAW")

    def column(self, tab: str, letter: str) -> list[str]:
        index = col_index(letter)
        return [row[index] if index < len(row) else "" for row in self.rows(tab)]

    def row_of(self, tab: str, key: str, column: str = "A") -> int | None:
        """1-based sheet row whose `column` holds `key` — the join column A."""
        index = col_index(column)
        for offset, row in enumerate(self.grid(tab), start=1):
            if index < len(row) and row[index] == key:
                return offset
        return None

    def sort_by(self, tab: str, column: str) -> None:
        """Aryan selecting the data range and sorting it — whole rows move.

        The header stays put; every column travels with its row, which is what
        makes a verdict in Z arrive next to a different company id.
        """
        grid = self.grids.get(tab, [])
        if len(grid) < 3:
            return
        index = col_index(column)
        body = grid[1:]
        body.sort(key=lambda row: row[index] if index < len(row) else "")
        self.grids[tab] = [grid[0]] + body

    def requests_of(self, kind: str) -> list[dict]:
        return [r[kind] for r in self.requests if kind in r]

    def touched_tabs(self) -> set[str]:
        return {parse_a1(rng)[0] for _, rng in self.writes}

    def formatted_sheet_ids(self) -> set[int]:
        """Every sheetId named anywhere in any format request."""
        found: set[int] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "sheetId" and isinstance(value, int):
                        found.add(value)
                    else:
                        walk(value)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(self.requests)
        return found

    def text_of(self, tab: str) -> str:
        """Every cell of a tab as one string — for "did X leak in here?" checks."""
        return "\n".join("\t".join(row) for row in self.grid(tab))


class CountingGateway(_CallLog):
    """The same call log, wrapped around a real gateway.

    The integration suite drives a live scratch spreadsheet through this so the
    call-budget assertions are byte-for-byte the ones the offline suite makes.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Call] = []
        self.requests: list[dict] = []
        self.writes: list[tuple[str, str]] = []

    def sheets(self) -> dict[str, int]:
        self.calls.append(Call("sheets"))
        return self.inner.sheets()

    def add_tabs(self, titles: Sequence[str]) -> dict[str, int]:
        self.calls.append(Call("add_tabs", list(titles)))
        return self.inner.add_tabs(titles)

    def batch_get(self, ranges: Sequence[str]) -> dict[str, list[list[str]]]:
        self.calls.append(Call("batch_get", list(ranges)))
        return self.inner.batch_get(ranges)

    def batch_set(self, data: Sequence[ValueRange], value_input_option: str) -> None:
        self.calls.append(Call("batch_set", [vr.range for vr in data]))
        self.writes += [(value_input_option, vr.range) for vr in data]
        self.inner.batch_set(data, value_input_option)

    def batch_requests(self, requests: Sequence[dict]) -> None:
        self.calls.append(Call("batch_requests", list(requests)))
        self.requests.extend(requests)
        self.inner.batch_requests(requests)


# --------------------------------------------------------------- test data


FUND_KEYS: tuple[str, ...] = ("dsw", "northstar", "outward", "anticus")


def seed_companies(db: Any, count: int = 200, *, shortlist: int = 6) -> list[str]:
    """`count` scored companies, in a stable, deliberately non-alphabetical order.

    Priority descends with the index while the *name* ascends against it, so
    the render order is the exact reverse of alphabetical: a manual sort by
    column C (Company) is guaranteed to move every row, which is the whole
    point of `test_user_columns_survive_a_resort`.

    Returns the company ids in render order (highest priority first).
    """
    from radar.store.db import now_iso
    from tests.factories import C, store_company

    stamp = now_iso()
    ids: list[str] = []
    for index in range(count):
        company = C(
            canonical_name=f"Company {count - index:04d} Ltd",
            norm_key=f"company{index:04d}",
            domain=f"co{index:04d}.example",
            website_url=f"https://co{index:04d}.example/",
            hq_city="Newcastle",
            age_months=6 + index % 24,
        )
        store_company(db, company)
        db.execute(
            "INSERT INTO company_source(company_id, source_key, external_id, "
            "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            (company.id, "uktn", f"ext-{index}",
             f"https://uktn.co.uk/story-{index}", stamp, stamp),
        )
        db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company.id, FUND_KEYS[index % len(FUND_KEYS)], None,
             90.0 - index * 0.1, 0.9, 80.0 - index * 0.1, 99.0 - index * 0.1,
             "shortlist" if index < shortlist else "watchlist", None,
             f"Matches on geography and sector ({index}).", None,
             "testhash", "1", stamp),
        )
        ids.append(company.id)
    return ids


def seed_failed_source(db: Any, source_key: str = "uktn",
                       error: str = "SourceFailedProbe HTTP 503") -> str:
    """A run in which one source failed. `error` is the needle a leak test hunts.

    The key must be one the `Sources` tab knows about: `build_sources` lists the
    *configured* sources, so a failure attributed to anything else is invisible.
    """
    from radar.store.db import now_iso

    stamp = now_iso()
    db.execute(
        "INSERT INTO run(started_at, finished_at, mode, scope, items_fetched, "
        "items_extracted, companies_new, companies_merged, gated_out, shortlisted, "
        "llm_calls, llm_cost_usd, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stamp, stamp, "daily", None, 412, 38, 6, 1, 374, 6, 0, 0.0, "partial"),
    )
    run_id = db.scalar("SELECT MAX(id) FROM run")
    db.execute(
        "INSERT INTO run_source(run_id, source_key, status, items, duration_ms, error) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, source_key, "failed", 0, 120, error),
    )
    return error


__all__ = [
    "Call",
    "CountingGateway",
    "FakeSheetGateway",
    "FUND_KEYS",
    "display",
    "parse_a1",
    "seed_companies",
    "seed_failed_source",
]
