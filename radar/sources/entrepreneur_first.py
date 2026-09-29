"""Entrepreneur First — public portfolio and explicit alumni evidence.

A portfolio includes mature alumni. Read stated Founded/Funded by metadata;
do not infer pre-seed or turn founder social profiles into company websites.
Explicit third-party funding is denylist evidence, including old alumni.
Unfunded/unstated records still require the normal company age and stage gates.

joinef.com publishes Crawl-delay: 10; the shared HTTP client respects it and
this adapter retains its explicit floor. No pagination crawl is performed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable
from urllib.parse import urlsplit

from radar.sources._common import (
    absolute_url,
    attr_of,
    first_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    require_ok,
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
        require_ok(resp, self.key, PORTFOLIO)

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
            if item.external_id not in new_ids and item.kind_hint != 'vc_portfolio_listing':
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
        if item.kind_hint == 'vc_portfolio_listing':
            return True  # Old funded alumni are denial evidence, not fresh leads.
        structured = item.structured or {}
        location = (structured.get("location") or "").lower()
        if location and not any(word in location for word in LONDON):
            return False
        year = structured.get("founded_year")
        if min_year is not None and year is not None and year < min_year:
            return False
        return True

    def _item(self, card) -> RawItem | None:
        name = first_text(card, NAME_SELECTORS)
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
            host = (urlsplit(link).hostname or '').lower()
            social = any(host == h or host.endswith('.'+h) for h in
                         ('linkedin.com','twitter.com','x.com','facebook.com','instagram.com'))
            if link.startswith("http") and "joinef.com" not in host and not social:
                website = link
                break

        location = (attr_of(card, None, "data-location")
                    or first_text(card, LOCATION_SELECTORS))
        year_text = attr_of(card, None, "data-year") or text_of(card, ".portfolio__year")
        metadata = {}
        for row in card.css('.meta__row'):
            cells = row.css('.meta__row__name')
            if len(cells) == 2:
                metadata[cells[0].text(strip=True).lower()] = cells[1].text(strip=True)
        year_text = year_text or metadata.get('founded')
        year_match = _YEAR.search(year_text or "")
        founded_year = int(year_match.group(0)) if year_match else None

        structured = {
            "company_name": name,
            "one_line_description": first_text(card, DESC_SELECTORS, exclude=name) or None,
            "company_website": website,
            "location": location or None,
            "founded_year": founded_year,
            "hq_city": "London" if location and "london" in location.lower() else None,
            "hq_country_iso2": "GB" if location and any(
                w in location.lower() for w in LONDON) else None,
            "stage": None,  # A portfolio includes mature alumni, not just new cohorts.
            "date_confidence": "inferred",
            "age_source": "unknown",
        }
        source_url = PORTFOLIO
        if href and 'joinef.com' in (urlsplit(absolute_url(BASE, href) or '').hostname or ''):
            source_url = absolute_url(BASE, href) or PORTFOLIO
        funded_by = metadata.get('funded by')
        if funded_by:
            from radar.sources.denylist import listing
            return listing(source_key=self.key,source_url=source_url,
                           external_id=slug or name.lower().replace(' ','-'),
                           published_at=None,title=name,body_text=structured['one_line_description'],
                           company_name=name,vc_slug='entrepreneur_first',vc_name=funded_by,
                           date_confidence='inferred',extra=structured)
        return RawItem(
            source_key=self.key,
            source_url=source_url,
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


ADAPTER = EntrepreneurFirstAdapter()
