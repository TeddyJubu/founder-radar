"""Innovate UK funded projects — the official XLSX, read with the standard library.

04-sources row 12: the most current official Innovate UK award data available
free, republished every 2–4 weeks. Like `ukri_gtr` it is a **quality** signal
feeding the `grant` qualifier (06-scoring §3), and it carries three facts the
API does not: the participant's **CRN** (its Companies House number), its
postcode, and its enterprise size.

Verified live on 8 August 2026:

* The publication page lists two workbooks; only *"…from financial year 2016 to
  2017 to present"* is in scope. Its filename carries the release date
  (`IUK-060726-…`), so **the URL is discovered from the page, never hard-coded**
  — a pinned link 404s within a month.
* 51,899 data rows, 27 columns, `Project Start Date` stored as an Excel serial.
* `www.ukri.org/robots.txt` disallows only `/wp-admin/`.

**No new dependency.** An `.xlsx` is a zip of XML, and `zipfile` +
`ElementTree.iterparse` reads this one in ~30 s with a 66 MB peak — the same
shape of work `openpyxl` does in read-only mode, in about forty lines. The
whole file is streamed because the sheet is ordered by competition, not by
date, so the recent awards are scattered through it.

Two deliberate omissions:

* **`Public Description`** is never read. The structured columns already say
  who won what, where and when, so there is nothing for stage ③ to infer and no
  reason to spend a model call on 100 rows a month.
* **`company_number`** is not set in `structured`, even though the CRN is right
  there. `pipeline._route_of` reads that key as "this came off the register"
  and returns `discovery_route = "registry"`, which would put a Track **A**
  source behind the Track B qualification gate. The CRN is carried as `crn`
  instead: a fact, recorded, with no side effect.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta
from typing import Iterable, Iterator
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from radar.sources._common import (
    BLOCKED_STATUSES,
    LayoutChanged,
    SourceBlocked,
    SourceError,
    clean_text,
    guard_nonempty,
    norm_key,
    parse_date,
    selector_fingerprint,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.ukri.org"
PUBLICATION = f"{BASE}/publications/innovate-uk-funded-projects-since-2004/"
#: The current workbook, whose filename changes with every release.
FILE_RE = re.compile(
    r"https://www\.ukri\.org/wp-content/uploads/[^\"'\s>]*"
    r"FundedProjectsFromFinancialYear[^\"'\s>]*\.xlsx",
    re.I,
)
#: A monthly source with no `since` must not hand the pipeline ten years of awards.
LOOKBACK_DAYS = 365

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SHARED_STRINGS = "xl/sharedStrings.xml"
EXCEL_EPOCH = date(1899, 12, 30)

REQUIRED_COLUMNS = (
    "Participant Name", "Project Number", "Project Title",
    "Project Start Date", "Award Offered (£)",
)


class InnovateUkAdapter:
    key = "innovate_uk"
    kind = "grant"
    schedule = "monthly"
    requires_browser = False
    track = "A"
    endpoint = PUBLICATION
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        page = ctx.http.get(PUBLICATION)
        if page.status == 304:
            return []
        if not page.ok:
            raise RuntimeError(f"{self.key}: HTTP {page.status} from {PUBLICATION}")
        url = self.discover(page.text)
        today = ctx.now or date.today()
        since = ctx.since or today - timedelta(days=LOOKBACK_DAYS)
        return list(unique_by_id(self.parse(_download(ctx.http, url), since=since)))

    def discover(self, html: str) -> str:
        """The dated download link, from the page that always exists."""
        match = FILE_RE.search(html)
        if match is None:
            raise LayoutChanged(self.key, f"no funded-projects .xlsx link on {PUBLICATION}")
        return match.group(0)

    def parse(self, payload: bytes | str, *, since: date | None = None) -> list[RawItem]:
        blob = payload.encode("utf-8", "replace") if isinstance(payload, str) else payload
        try:
            book = zipfile.ZipFile(io.BytesIO(blob))
            sheet = _worksheet(book)
            strings = _shared_strings(book)
            rows = _rows(book, sheet, strings)
            header = next(rows, None)
        except zipfile.BadZipFile as exc:
            raise LayoutChanged(self.key, f"not a readable xlsx: {exc}") from exc

        if not header:
            raise LayoutChanged(self.key, "worksheet has no header row")
        column = {clean_text(name): pos for pos, name in header.items() if clean_text(name)}
        missing = [name for name in REQUIRED_COLUMNS if name not in column]
        if missing:
            raise LayoutChanged(self.key, f"worksheet is missing columns {missing}")
        self.last_fingerprint = selector_fingerprint(column)

        seen = 0
        out: list[RawItem] = []
        for row in rows:
            seen += 1

            def value(name: str, _row=row) -> str:
                return clean_text(_row.get(column.get(name)))

            company = value("Participant Name")
            if not company or not _is_sme(value("Enterprise Size")):
                continue
            if "withdraw" in value("Participant Withdrawn From Project").lower():
                continue
            awarded = _excel_date(value("Project Start Date"))
            # An undated row cannot be shown to be recent, so it is not new.
            if since is not None and (awarded is None or awarded < since):
                continue

            reference = value("Project Number")
            crn = value("CRN") or None
            out.append(RawItem(
                source_key=self.key,
                source_url=f"https://gtr.ukri.org/projects?ref={reference}"
                           if reference else PUBLICATION,
                external_id=f"{reference}:{crn or norm_key(company)}",
                published_at=awarded,
                title=value("Project Title") or company,
                body_text=None,             # the structured columns say it all
                structured={
                    "company_name": company,
                    "crn": crn,
                    "grant_amount_gbp": _money(value("Award Offered (£)")),
                    "funder": "Innovate UK",
                    "grant_reference": reference or None,
                    "competition": value("Competition Title") or None,
                    "postal_code": value("Postcode") or None,
                    "enterprise_size": value("Enterprise Size") or None,
                    "date_confidence": "exact",
                    # 06-scoring §3 — the reason this source exists.
                    "qualifiers": ["grant"],
                },
                kind_hint="grant_award",
            ))

        # Zero *rows* is a layout change; zero rows *since `since`* is a quiet
        # month, which for a source republished every 2–4 weeks is normal.
        guard_nonempty(self.key, [seen] if seen else [],
                       detail="worksheet has a header but no data rows", document=blob)
        return out


# ---------------------------------------------------------------- xlsx reading


def _worksheet(book: zipfile.ZipFile) -> str:
    names = book.namelist()
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    for name in names:
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            return name
    raise LayoutChanged("innovate_uk", "workbook contains no worksheet")


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    """The string pool every text cell points into. ~145k entries, ~66 MB."""
    if SHARED_STRINGS not in book.namelist():
        return []
    out: list[str] = []
    with book.open(SHARED_STRINGS) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag == NS + "si":
                out.append("".join(t.text or "" for t in element.iter(NS + "t")))
                element.clear()
    return out


def _rows(book: zipfile.ZipFile, sheet: str, strings: list[str]) -> Iterator[dict[int, str]]:
    """Stream `{column index: text}` per row. Never holds the sheet in memory."""
    try:
        with book.open(sheet) as handle:
            for _, element in ET.iterparse(handle, events=("end",)):
                if element.tag != NS + "row":
                    continue
                row: dict[int, str] = {}
                for cell in element.iter(NS + "c"):
                    node = cell.find(NS + "v")
                    text = node.text if node is not None else None
                    if cell.get("t") == "s" and text is not None:
                        index = int(text)
                        text = strings[index] if 0 <= index < len(strings) else ""
                    elif cell.get("t") == "inlineStr":
                        text = "".join(t.text or "" for t in cell.iter(NS + "t"))
                    row[_column_index(cell.get("r") or "")] = text or ""
                element.clear()
                yield row
    except ET.ParseError as exc:
        raise LayoutChanged("innovate_uk", f"worksheet XML did not parse: {exc}") from exc


def _column_index(ref: str) -> int:
    """`"AA12"` → 26. Cells for empty columns are simply absent from the row."""
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - 64)
    return index - 1


def _excel_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return EXCEL_EPOCH + timedelta(days=int(float(raw)))
    except (TypeError, ValueError, OverflowError):
        return parse_date(raw)


def _money(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "").replace("£", ""))
    except (AttributeError, ValueError):
        return None


def _is_sme(size: str) -> bool:
    """The file spells one idea eight ways: Micro/small, Micro, Small, SME…

    Everything else — Academic, Large, Catapult, PSO, RTO, Charity — is either
    not a company or not a company at this stage.
    """
    lowered = size.lower()
    return "micro" in lowered or "small" in lowered or "sme" in lowered


def _download(http, url: str) -> bytes:
    """Fetch the workbook as bytes, politely.

    `HttpClient.Response.text` is a `str`: httpx decodes the body as UTF-8 with
    `errors="replace"`, which destroys a zip. So this borrows the shared
    client's httpx session — same User-Agent, timeouts and redirects — after
    asking the same robots cache and the same rate limiter that `get` asks.
    """
    from radar.fetch.http import RobotsDenied, user_agent

    client = getattr(http, "_client", None)
    if client is None:
        raise RuntimeError("innovate_uk: http client cannot return bytes")
    if getattr(http, "obey_robots", True) and not http.robots.allowed(url, user_agent()):
        raise RobotsDenied(url)
    http.limiter.acquire(urlsplit(url).netloc)
    resp = client.get(url)
    if resp.status_code != 200:
        # innovate_uk fetches bytes through the raw httpx client, so it cannot
        # use the `require_ok` helper; classify the same way here so a WAF
        # block is recorded as `degraded`, not `failed` (sources/base.py).
        if resp.status_code in BLOCKED_STATUSES:
            raise SourceBlocked(
                "innovate_uk",
                f"HTTP {resp.status_code} from {url} — the site is refusing us "
                "(possible anti-bot block)",
            )
        raise SourceError("innovate_uk", f"HTTP {resp.status_code} from {url}")
    return resp.content


ADAPTER = InnovateUkAdapter()
