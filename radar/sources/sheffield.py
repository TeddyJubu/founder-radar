"""Sheffield Commercialisation — HTML news (04-sources Tier 2).

Yorkshire spinouts, which is the point: Anticus manages Finance Yorkshire and
both its vehicles carry Yorkshire as a **hard** mandate, so a Sheffield spinout
is one of the few things that can clear that gate at all. Northstar's
`eis_growth` also passes it on the soft "north of England" rule.

The card is `a.news-teaser-link`, which wraps the whole teaser — thumbnail,
`h2` title and excerpt — and carries the href. Selecting the inner
`.news-teaser` instead would find the text and lose the link, which is the
kind of half-parse that produces items with no provenance URL.
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import (
    absolute_url,
    after,
    clean_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    parse_date,
    select_any,
    slug_of,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.sheffield.ac.uk"
NEWS = f"{BASE}/commercialisation/commercialisation-news"

CARD_SELECTORS = ("a.news-teaser-link", ".news-teaser", "article")
TITLE_SELECTORS = ("h2", "h3", ".teaser-text h2")
#: "1 June 2026"
DATE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b")

SPINOUT_WORDS = ("spinout", "spin-out", "spin out", "launches", "founded",
                 "incorporated", "raises", "seed round", "pre-seed")


class SheffieldAdapter:
    key = "sheffield"
    kind = "spinout"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    tier = 2
    endpoint = NEWS
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(NEWS)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {NEWS}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no news card matched any of {CARD_SELECTORS}",
            document=payload if isinstance(payload, str)
            else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        self.last_fingerprint = node_fingerprint(cards)
        items = [self._item(card) for card in cards]
        return [item for item in items if item is not None]

    def _item(self, card) -> RawItem | None:
        title = _first_text(card, TITLE_SELECTORS)
        if not title:
            return None
        href = card.attributes.get("href") or (
            card.css_first("a[href]").attributes.get("href")
            if card.css_first("a[href]") else None)

        blob = clean_text(card.text(separator=" ", strip=True))
        match = DATE.search(blob)
        published = parse_date(match.group(1)) if match else None

        return RawItem(
            source_key=self.key,
            source_url=absolute_url(BASE, href) or NEWS,
            external_id=slug_of(href or "") or title.lower().replace(" ", "-"),
            published_at=published,
            title=title,
            body_text=blob or None,
            structured={
                "date_confidence": "exact" if published else "unknown",
                "full_text_in_feed": False,
                "university_name": "University of Sheffield",
                # Yorkshire is a hard mandate for both Anticus vehicles, so
                # this hint is doing real routing work, not decoration.
                "hq_region": "yorkshire",
                **({"is_university_spinout": True}
                   if any(w in blob.lower() for w in SPINOUT_WORDS) else {}),
            },
            kind_hint="spinout",
        )


def _first_text(card, selectors, *, exclude: str | None = None) -> str:
    for selector in selectors:
        for node in card.css(selector):
            text = clean_text(node.text(strip=True))
            if text and text != exclude:
                return text
    return ""


ADAPTER = SheffieldAdapter()
