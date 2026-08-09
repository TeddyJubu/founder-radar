"""UCL Ventures — HTML news (04-sources Tier 2).

UCL's technology-transfer arm. London deeptech spinouts, which makes them
Outward's UK-wide vehicle rather than Northstar's or Anticus's regional ones,
and a hard reject for DSW's SEIS fund — London is inside the golden triangle.

⚠️ `uclb.com` is dead and 302s here; the endpoint is `uclventures.com/news`.

The site is Drupal, so `.base-card` is a theme class rather than a utility
soup, and the card carries a real `<time datetime>`. That is worth noting
because it makes this the only Tier 2 HTML source with a machine-readable
date — the other two are parsed out of display text.
"""

from __future__ import annotations

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

BASE = "https://www.uclventures.com"
NEWS = f"{BASE}/news"

CARD_SELECTORS = (".base-card", "article", ".views-row")
TITLE_SELECTORS = ("h3", "a.link span", ".base-card__title")
DATE_SELECTORS = ("time[datetime]", "time")

#: The first `.base-card__paragraph` is the section kicker. "Spinout &
#: portfolio news" is the one that carries companies; events and programmes
#: are people-news and score nothing, so the kicker is kept as a tag rather
#: than used to drop items — the prefilter owns that decision, not the adapter.
SPINOUT_SECTION = "spinout"


class UclVenturesAdapter:
    key = "ucl_ventures"
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
        link = card.css_first("a[href]")
        href = link.attributes.get("href") if link else None
        url = absolute_url(BASE, href) or NEWS

        published = None
        for node in card.css(DATE_SELECTORS[0]) or card.css(DATE_SELECTORS[1]):
            published = parse_date(node.attributes.get("datetime")
                                   or node.text(strip=True))
            if published:
                break

        paragraphs = [clean_text(p.text(strip=True))
                      for p in card.css(".base-card__paragraph")]
        section = paragraphs[0] if paragraphs else ""
        blob = clean_text(card.text(separator=" ", strip=True))

        return RawItem(
            source_key=self.key,
            source_url=url,
            external_id=slug_of(href or "") or title.lower().replace(" ", "-"),
            published_at=published,
            title=title,
            body_text=blob or None,
            structured={
                "date_confidence": "exact" if published else "unknown",
                "full_text_in_feed": False,
                "university_name": "University College London",
                "hq_city": "London",
                "hq_region": "london",
                "tags": [p for p in paragraphs if p],
                **({"is_university_spinout": True}
                   if SPINOUT_SECTION in section.lower() else {}),
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


ADAPTER = UclVenturesAdapter()
