"""Oxford University Innovation — HTML. The reference HTML adapter.

The only source in the ledger verified to publish a literal **incorporation
date** per company, alongside name, website, sector and department. That makes
it the one Track A source that needs no AI call at all: everything the scorer
wants is on the page, so the adapter fills `RawItem.structured` and stage ③
skips it entirely.

Two rules every HTML adapter in this package follows, established here:

1. **Try a list of candidate selectors, record which one fired.** Sites change
   theme, not information architecture — `.portfolio-company` becoming
   `.w-dyn-item` should cost one line, not a rewrite.
2. **A parse that yields zero cards from a real page raises `LayoutChanged`.**
   Silent zero-results is the failure mode this whole phase exists to prevent
   (04-sources §4.3).
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import (
    absolute_url,
    attr_of,
    clean_text,
    first_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    parse_date,
    require_ok,
    select_any,
    slug_of,
    text_of,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://innovation.ox.ac.uk"
PORTFOLIO = f"{BASE}/investing/our-portfolio-companies"

CARD_SELECTORS = (
    ".portfolio-company",
    ".portfolio-item",
    "article.company",
    ".views-row",
    ".w-dyn-item",
)
NAME_SELECTORS = (".company-name", "h3", "h2", ".title", "a")
DESC_SELECTORS = (".company-description", ".description", "p")

_INCORPORATED = re.compile(r"incorporat\w*[:\s]+(.+)", re.I)


class OxfordInnovationAdapter:
    key = "oxford_innovation"
    kind = "spinout"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    endpoint = PORTFOLIO
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(PORTFOLIO)
        if resp.status == 304:
            return []
        require_ok(resp, self.key, PORTFOLIO)
        return self.parse(resp.text)

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

    # --------------------------------------------------------------- private

    def _item(self, card) -> RawItem | None:
        name = first_text(card, NAME_SELECTORS)
        if not name:
            # A card with no name is not a company; skipping one card is fine,
            # skipping *all* of them already raised above.
            return None

        website = None
        for node in card.css("a[href]"):
            href = node.attributes.get("href", "")
            if href.startswith("http") and BASE not in href:
                website = href
                break

        incorporated = self._incorporation_date(card)
        description = first_text(card, DESC_SELECTORS, exclude=name)
        sector = _meta(card, ("sector", "industry"))
        department = _meta(card, ("department", "faculty", "division"))

        detail = attr_of(card, "a[href]", "href")
        source_url = absolute_url(BASE, detail) or PORTFOLIO
        external_id = attr_of(card, None, "data-company") or slug_of(detail or "") \
            or name.lower().replace(" ", "-")

        structured = {
            "company_name": name,
            "one_line_description": description or None,
            "company_website": website,
            "sector_raw": sector,
            "department": department,
            "university_name": "University of Oxford",
            "is_university_spinout": True,
            "hq_city": "Oxford",
            "hq_country_iso2": "GB",
            # The whole reason this source is Tier 1.
            "incorporated_on": incorporated.isoformat() if incorporated else None,
            "age_source": "source_stated" if incorporated else "unknown",
            "date_confidence": "stated" if incorporated else "inferred",
            # No prose to read: the page IS the extraction.
            "extraction_method": "structured",
        }
        return RawItem(
            source_key=self.key,
            source_url=source_url,
            external_id=external_id,
            # ponytail: the page carries no publication date per card, so the
            # incorporation date doubles as `published_at`. It is the only real
            # date the source states; `structured.incorporated_on` carries it
            # unambiguously for anything that must not conflate the two.
            published_at=incorporated,
            title=name,
            body_text=description or None,
            structured=structured,
            kind_hint="spinout",
        )

    def _incorporation_date(self, card):
        for node in card.css("li, span, p, dd, td, time"):
            text = clean_text(node.text(separator=" ", strip=True))
            match = _INCORPORATED.search(text)
            if match:
                parsed = parse_date(match.group(1))
                if parsed:
                    return parsed
        datetime_attr = attr_of(card, "time[datetime]", "datetime")
        return parse_date(datetime_attr) if datetime_attr else None


def _meta(card, names) -> str | None:
    """Read `.meta-sector` / `data-sector` style fields without guessing."""
    for name in names:
        value = attr_of(card, None, f"data-{name}")
        if value:
            return value
        value = text_of(card, f".meta-{name}") or text_of(card, f".company-{name}")
        if value:
            return value
    for node in card.css("li, dd, span"):
        text = clean_text(node.text(separator=" ", strip=True))
        for name in names:
            prefix = f"{name}:"
            if text.lower().startswith(prefix):
                return text[len(prefix):].strip()
    return None


ADAPTER = OxfordInnovationAdapter()
