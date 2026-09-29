"""Bethnal Green Ventures — invested portfolio, denial evidence.

The official portfolio and offer pages state every portfolio member receives
BGV equity investment. Active as well as exited companies therefore feed the
existing VC denylist, rather than claiming fresh pre-investment discovery.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import (
    absolute_url,
    clean_text,
    first_text,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    select_any,
    slug_of,
    snapshot_diff,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://bethnalgreenventures.com"
PORTFOLIO = f"{BASE}/portfolio"

CARD_SELECTORS = (".grid_item", ".portfolio-item", ".w-dyn-item", ".company-card")
NAME_SELECTORS = ("h4", "h3", ".card_title", ".heading-style-h5")

EXITED = "exited"


class BethnalGreenAdapter:
    key = "bethnal_green"
    kind = "portfolio"
    schedule = "weekly"
    requires_browser = False
    track = "—"
    tier = 2
    endpoint = PORTFOLIO
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(PORTFOLIO)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {PORTFOLIO}")
        return self.diff(self.parse(resp.text), ctx)

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no venture card matched any of {CARD_SELECTORS}",
            document=payload if isinstance(payload, str)
            else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        self.last_fingerprint = node_fingerprint(cards)
        items = [self._item(card) for card in cards]
        return [item for item in items if item is not None]

    def diff(self, items: list[RawItem], ctx: FetchContext) -> list[RawItem]:
        """Keep only ventures not seen on a previous run."""
        new_ids, bootstrap = snapshot_diff(
            ctx.db, self.key, [item.external_id for item in items])
        out: list[RawItem] = []
        for item in items:
            if item.external_id not in new_ids and item.kind_hint != "vc_portfolio_listing":
                continue
            structured = dict(item.structured or {})
            structured["bootstrap"] = bootstrap
            out.append(RawItem(
                source_key=item.source_key,
                source_url=item.source_url,
                external_id=item.external_id,
                published_at=ctx.now,        # when *we* saw it, never a founding date
                title=item.title,
                body_text=item.body_text,
                structured=structured,
                kind_hint=item.kind_hint,
            ))
        return out

    # --------------------------------------------------------------- private

    def _item(self, card) -> RawItem | None:
        name = first_text(card, NAME_SELECTORS)
        if not name:
            return None

        # The first link is the venture's own site, not a BGV page — which is
        # also the company website, so it is worth keeping as identity evidence.
        website = None
        for node in card.css("a[href]"):
            href = node.attributes.get("href", "")
            if href.startswith("http") and "bethnalgreenventures.com" not in href:
                website = href
                break

        blob = clean_text(card.text(separator=" ", strip=True))
        tags = [clean_text(t.text(strip=True))
                for t in card.css("[class*=tag], [class*=theme]") if t.text(strip=True)]
        exited = any(EXITED in t.lower() for t in tags)
        themes = sorted({t for t in tags if EXITED not in t.lower()})
        external_id = slug_of(website or "") or name.lower().replace(" ", "-")
        one_liner = first_text(card, (".card_text", "p"), exclude=name) or None

        from radar.sources.denylist import listing
        return listing(
            source_key=self.key, source_url=PORTFOLIO, external_id=external_id,
            published_at=None, title=name, body_text=blob or None,
            company_name=name, vc_slug="bethnal_green", vc_name="Bethnal Green Ventures",
            date_confidence="inferred",
            extra={"exited": exited, "company_website": website,
                   "one_line_description": one_liner, "age_source": "unknown",
                   "themes": themes},
        )

ADAPTER = BethnalGreenAdapter()
