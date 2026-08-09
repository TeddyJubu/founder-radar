"""Edinburgh Innovations — HTML news (04-sources Tier 2).

Scotland's largest TTO. Scottish spinouts sit outside every North East and
Yorkshire mandate, so these route to DSW (whose SEIS fund explicitly targets
companies **outside** the London–Oxbridge triangle) and to Outward's UK-wide
vehicle. That is the reason to read it at all.

⚠️ The endpoint is `edinburgh-innovations.ed.ac.uk/news`, not
`ed.ac.uk/edinburgh-innovations` — 04-sources records that the latter redirects
to a thin page with no listing on it.

The card is the `<a>` itself. Its classes are Tailwind utilities
(`group bg-teal text-white ...`), which change whenever the palette does, so
the href pattern is the selector: a link into `/news/` is what a news card
*is*, and it survives a restyle that would break any class-based hook.
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

BASE = "https://www.edinburgh-innovations.ed.ac.uk"
NEWS = f"{BASE}/news"

CARD_SELECTORS = ('a[href*="/news/"]', ".news-card", "article")
TITLE_SELECTORS = (".line-clamp-4", ".text-lg", "h3", "h2")
#: "20 Jul 2026". The date shares a container with a kicker ("News"), so it is
#: pulled by pattern rather than by position.
DATE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b")

SPINOUT_WORDS = ("spinout", "spin-out", "spin out", "launches", "founded",
                 "incorporated", "raises", "seed round", "pre-seed",
                 "investment", "startup")


class EdinburghInnovationsAdapter:
    key = "edinburgh_innovations"
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
        url = absolute_url(BASE, href) or NEWS

        blob = clean_text(card.text(separator=" ", strip=True))
        match = DATE.search(blob)
        published = parse_date(match.group(1)) if match else None

        tags = [clean_text(t.text(strip=True))
                for t in card.css(".text-2xs") if t.text(strip=True)]

        return RawItem(
            source_key=self.key,
            source_url=url,
            external_id=slug_of(href or "") or title.lower().replace(" ", "-"),
            published_at=published,
            title=title,
            body_text=blob or None,
            structured={
                # The listing carries a day-level date; the article is not
                # fetched here, so the text is the card blurb only.
                "date_confidence": "exact" if published else "unknown",
                "full_text_in_feed": False,
                "university_name": "University of Edinburgh",
                "hq_region": "uk_regions",
                "tags": tags,
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


ADAPTER = EdinburghInnovationsAdapter()
