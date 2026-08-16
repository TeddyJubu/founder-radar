"""Founders Factory — HTML articles (04-sources Tier 2).

A venture studio: its "Investing in X" posts announce companies at or before
incorporation, which is as early as a public signal gets. London-based, so
these route to Outward's UK-wide vehicle and are a hard reject for DSW's SEIS
fund.

Two things make this the most fragile adapter in the ledger, and both are
worth stating rather than discovering later:

* **The class names are styled-components hashes** —
  `NewsSection__NewsArticleBox-sc-13vxttl-0`. The `-sc-13vxttl-0` suffix is
  generated at build time and changes whenever the component tree does, so an
  exact-match selector would break on a deploy that changed nothing visible.
  The adapter matches on the *stable* half with `[class*="NewsArticleBox"]`.
* **The dates are relative** — "a month ago", not a date. There is nothing to
  parse, so `published_at` is None and `date_confidence` is `unknown`. Unknown
  passes the freshness gate and sets a flag, which is the correct handling of
  an absent fact; it is not a licence to guess a date from the crawl day.
"""

from __future__ import annotations

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

BASE = "https://foundersfactory.com"
ARTICLES = f"{BASE}/articles/"

CARD_SELECTORS = ('[class*="NewsArticleBox"]', "article", ".article-card")
TITLE_SELECTORS = ("h2", "h3", "h4")

INVESTMENT_WORDS = ("investing in", "investment in", "we've invested",
                    "we have invested", "backs", "raises", "funding")


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

        return RawItem(
            source_key=self.key,
            source_url=absolute_url(BASE, href) or ARTICLES,
            external_id=slug_of(href) or title.lower().replace(" ", "-"),
            # Relative timestamps only. None is the honest answer.
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
            kind_hint=("funding_round"
                       if any(w in f"{title} {blob}".lower() for w in INVESTMENT_WORDS)
                       else "news_mention"),
        )


ADAPTER = FoundersFactoryAdapter()
