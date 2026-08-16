"""Techstars — accelerator newsroom (04-sources Tier 2).

04-sources lists this as "Techstars London — cohort announcements". The
newsroom is **global**: a live read returns Boston kick-offs and MENAT
partnerships alongside anything London. There is no London-scoped feed, so the
adapter reads the whole newsroom and lets the UK gate throw the rest away.

That is the right division of labour — an adapter that filtered on "London"
would be making a geography decision, and geography is a gate, not a parse.
But it does mean this is the lowest-yield source in the ledger: most items are
rejected before they cost anything, and the value is the occasional London
cohort announcement.

The cards are Material UI (`.MuiCard-root`). Unlike Founders Factory's
styled-components hashes, MUI's component classes are stable across builds —
`mui-5kuuch` is the volatile emotion class, `MuiCard-root` is not, so that is
the hook.
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

BASE = "https://www.techstars.com"
NEWSROOM = f"{BASE}/newsroom"

CARD_SELECTORS = (".MuiCard-root", "article", ".news-card")
TITLE_SELECTORS = ("h2", "h3", "h4")

COHORT_WORDS = ("cohort", "class of", "accelerator class", "demo day",
                "kick-off", "kickoff", "programme", "program")


class TechstarsLondonAdapter:
    key = "techstars_london"
    kind = "accelerator"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    tier = 2
    endpoint = NEWSROOM
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(NEWSROOM)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {NEWSROOM}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no newsroom card matched any of {CARD_SELECTORS}",
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
        return RawItem(
            source_key=self.key,
            source_url=absolute_url(BASE, href) or NEWSROOM,
            external_id=slug_of(href) or title.lower().replace(" ", "-"),
            # The newsroom listing states no date. Unknown, not guessed.
            published_at=None,
            title=title,
            body_text=blob or None,
            structured={
                "date_confidence": "unknown",
                "age_source": "unknown",
                "full_text_in_feed": False,
                "accelerator_name": "Techstars",
                # Deliberately no geography hint: this feed is global, and
                # asserting London here would put a false fact into the record
                # that the geography gate would then trust.
            },
            kind_hint=("accelerator_cohort"
                       if any(w in f"{title} {blob}".lower() for w in COHORT_WORDS)
                       else "news_mention"),
        )


ADAPTER = TechstarsLondonAdapter()
