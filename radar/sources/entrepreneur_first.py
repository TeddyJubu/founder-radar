"""Entrepreneur First — HTML portfolio, snapshot-diff, `Crawl-delay: 10`.

Company-builder output is pre-seed by construction: EF forms the company, so
everything on this page was incorporated recently by definition. 04-sources
gives it ~5 UK companies a month with a London filter and a founded-year filter.

**The politeness detail that makes this file different.** joinef.com publishes
`Crawl-delay: 10`. `HttpClient` already reads that from robots.txt and hands it
to the per-host limiter, but this adapter fetches one page and stops rather than
walking pagination, because at ten seconds a request a paginated crawl is
minutes of wall-clock for five companies a month. `ensure_crawl_delay` also
asserts the floor explicitly, so the day someone constructs an `HttpClient`
with `obey_robots=False` for debugging, this source still waits.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from radar.sources._common import (
    absolute_url,
    attr_of,
    clean_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    select_any,
    slug_of,
    snapshot_diff,
    text_of,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.joinef.com"
PORTFOLIO = f"{BASE}/portfolio/"

#: joinef.com robots.txt. Enforced here as well as by `HttpClient`.
CRAWL_DELAY = 10.0

# `.tile--company` is the live joinef.com layout as of August 2026. Outward VC
# runs the same template, so `vc_portfolios` carries the selector too.
CARD_SELECTORS = (
    ".tile--company",
    ".portfolio__item",
    ".portfolio-item",
    ".w-dyn-item",
    "article.company",
    ".company-card",
)
NAME_SELECTORS = (".portfolio__name", ".tile__name", "h3", "h4", "h2", ".title")
DESC_SELECTORS = (".portfolio__desc", ".tile__description", ".description", "p")
#: Location moved onto a tag link; it drives the London filter, so a miss here
#: silently drops every company rather than mislabelling one.
LOCATION_SELECTORS = (".portfolio__location", ".locationtag")

LONDON = ("london", "uk", "united kingdom", "gb")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


class EntrepreneurFirstAdapter:
    key = "entrepreneur_first"
    kind = "accelerator"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    endpoint = PORTFOLIO
    homepage = BASE
    crawl_delay = CRAWL_DELAY

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        self.ensure_crawl_delay(ctx.http)
        resp = ctx.http.get(PORTFOLIO)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {PORTFOLIO}")

        min_year = _min_year(ctx.now)
        items = [i for i in self.parse(resp.text) if self._wanted(i, min_year)]
        return self.diff(items, ctx)

    def ensure_crawl_delay(self, http) -> None:
        """Belt and braces: 10 s between requests to this host, always."""
        limiter = getattr(http, "limiter", None)
        if limiter is not None:
            limiter.set_delay("www.joinef.com", CRAWL_DELAY)
            limiter.set_delay("joinef.com", CRAWL_DELAY)

    # ------------------------------------------------------------------ parse

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no portfolio card matched any of {CARD_SELECTORS}",
            document=payload if isinstance(payload, str) else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        self.last_fingerprint = node_fingerprint(cards)
        items = [self._item(card) for card in cards]
        return [item for item in items if item is not None]

    def diff(self, items: list[RawItem], ctx: FetchContext) -> list[RawItem]:
        new_ids, bootstrap = snapshot_diff(
            ctx.db, self.key, [item.external_id for item in items])
        out = []
        for item in items:
            if item.external_id not in new_ids:
                continue
            structured = dict(item.structured or {})
            structured["bootstrap"] = bootstrap
            out.append(RawItem(
                source_key=item.source_key, source_url=item.source_url,
                external_id=item.external_id, published_at=ctx.now,
                title=item.title, body_text=item.body_text,
                structured=structured, kind_hint=item.kind_hint,
            ))
        return out

    # --------------------------------------------------------------- private

    def _wanted(self, item: RawItem, min_year: int | None) -> bool:
        """London filter and founded-year filter (04-sources §2, row 10)."""
        structured = item.structured or {}
        location = (structured.get("location") or "").lower()
        if location and not any(word in location for word in LONDON):
            return False
        year = structured.get("founded_year")
        if min_year is not None and year is not None and year < min_year:
            return False
        return True

    def _item(self, card) -> RawItem | None:
        name = _first_text(card, NAME_SELECTORS)
        if not name:
            return None
        # Identity, carefully. The live card's first `a[href]` is a *tag* link
        # ("/location/london/"), so taking it blindly gave 48 companies six
        # external ids — 35 of them "london". Every one of those would have
        # collapsed into one row on the next snapshot diff. Prefer the slug the
        # page states outright, then a link that is not a tag, then the name.
        slug = (attr_of(card, None, "data-companyslug")
                or attr_of(card, ".tile__link", "data-companyslug"))
        href = attr_of(card, None, "href")
        if not href:
            for node in card.css("a[href]"):
                classes = node.attributes.get("class") or ""
                if "locationtag" in classes or "categorytag" in classes:
                    continue
                href = node.attributes.get("href")
                break
        website = None
        for node in card.css("a[href]"):
            link = node.attributes.get("href", "")
            if link.startswith("http") and "joinef.com" not in link:
                website = link
                break

        location = (attr_of(card, None, "data-location")
                    or _first_text(card, LOCATION_SELECTORS))
        year_text = attr_of(card, None, "data-year") or text_of(card, ".portfolio__year")
        year_match = _YEAR.search(year_text or "")
        founded_year = int(year_match.group(0)) if year_match else None

        structured = {
            "company_name": name,
            "one_line_description": _first_text(card, DESC_SELECTORS, exclude=name) or None,
            "company_website": website,
            "location": location or None,
            "founded_year": founded_year,
            "hq_city": "London" if location and "london" in location.lower() else None,
            "hq_country_iso2": "GB" if location and any(
                w in location.lower() for w in LONDON) else None,
            "stage": "pre_seed",
            "date_confidence": "inferred",
            "age_source": "unknown",
        }
        return RawItem(
            source_key=self.key,
            source_url=absolute_url(BASE, href) or PORTFOLIO,
            external_id=slug or slug_of(href or "") or name.lower().replace(" ", "-"),
            published_at=None,
            title=name,
            body_text=structured["one_line_description"],
            structured=structured,
            kind_hint="accelerator_cohort",
        )


def _min_year(now: date | None) -> int | None:
    """Only ventures formed in roughly the last three years are in scope.

    Three years mirrors the default `max_company_age_months = 36`, but is only
    a *fetch* filter: the real gate lives in scoring, reads the sheet, and
    stays authoritative. This just avoids carrying EF's 2016 alumni around.
    """
    if now is None:
        return None
    return now.year - 3


def _first_text(card, selectors, *, exclude: str | None = None) -> str:
    for selector in selectors:
        value = text_of(card, selector)
        if value and value != exclude:
            return value
    return ""


ADAPTER = EntrepreneurFirstAdapter()
