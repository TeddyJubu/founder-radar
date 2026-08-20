"""Founders Factory — HTML articles (04-sources Tier 2).

A venture studio. Its **"Investing in X"** posts used to be treated as early
discovery ("at or before incorporation"). The client rejected that pattern for
Zinc, and the same rule applies here: a company Founders Factory has already
backed is not a scout lead. Those titles are inverted into the denylist.

Non-investment articles (trends, awards write-ups) stay as ordinary news —
they do not name a closed cheque in the title, so they are not demoted.

Two layout details worth keeping:

* **The class names are styled-components hashes** —
  `NewsSection__NewsArticleBox-sc-13vxttl-0`. The `-sc-13vxttl-0` suffix is
  generated at build time and changes whenever the component tree does, so an
  exact-match selector would break on a deploy that changed nothing visible.
  The adapter matches on the *stable* half with `[class*="NewsArticleBox"]`.
* **The dates are relative** — "a month ago", not a date. There is nothing to
  parse, so `published_at` is None and `date_confidence` is `unknown`.
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import (
    absolute_url,
    after,
    clean_text,
    first_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    select_any,
    slug_of,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem
from radar.sources.denylist import listing

BASE = "https://foundersfactory.com"
ARTICLES = f"{BASE}/articles/"

CARD_SELECTORS = ('[class*="NewsArticleBox"]', "article", ".article-card")
TITLE_SELECTORS = ("h2", "h3", "h4")

#: "Investing in Halden Robotics" / "Investment in Marrow Bio"
INVESTMENT_TITLE = re.compile(
    r"(?:investing|investment)\s+in\s+(?P<name>.+?)"
    r"(?:\s*[—–\-|:].*)?$",
    re.I,
)


class FoundersFactoryAdapter:
    key = "founders_factory"
    kind = "accelerator"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    tier = 2
    endpoint = ARTICLES
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(ARTICLES)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {ARTICLES}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no article card matched any of {CARD_SELECTORS}",
            document=payload if isinstance(payload, str)
            else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        self.last_fingerprint = node_fingerprint(cards)
        items = [self._item(card) for card in cards]
        return [item for item in items if item is not None]

    @staticmethod
    def company_from_title(title: str) -> str | None:
        match = INVESTMENT_TITLE.search(clean_text(title))
        if not match:
            return None
        name = match.group("name").strip(" .,’'\"")
        if not name or len(name) > 60 or len(name.split()) > 5:
            return None
        return name

    def _item(self, card) -> RawItem | None:
        title = first_text(card, TITLE_SELECTORS)
        link = card.css_first("a[href]")
        href = link.attributes.get("href") if link else None
        if not title or not href:
            return None

        blob = clean_text(card.text(separator=" ", strip=True))
        # The first card on the page carries an inlined <style> block, so the
        # blurb can start with CSS. Dropping anything that looks like a rule
        # keeps that out of the body text handed to stage ③.
        if blob.startswith(".css-") or "{box-sizing:" in blob[:200]:
            blob = ""

        source_url = absolute_url(BASE, href) or ARTICLES
        external_id = slug_of(href) or title.lower().replace(" ", "-")
        name = self.company_from_title(title)
        if name:
            return listing(
                source_key=self.key,
                source_url=source_url,
                external_id=external_id,
                published_at=None,
                title=title,
                body_text=blob or None,
                company_name=name,
                vc_slug="founders_factory",
                vc_name="Founders Factory",
                date_confidence="unknown",
                extra={"age_source": "unknown"},
            )

        return RawItem(
            source_key=self.key,
            source_url=source_url,
            external_id=external_id,
            published_at=None,
            title=title,
            body_text=blob or None,
            structured={
                "date_confidence": "unknown",
                "age_source": "unknown",
                "full_text_in_feed": False,
                "accelerator_name": "Founders Factory",
                "hq_city": "London",
                "hq_region": "london",
            },
            kind_hint="news_mention",
        )


ADAPTER = FoundersFactoryAdapter()
