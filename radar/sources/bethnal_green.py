"""Bethnal Green Ventures — HTML portfolio, snapshot-diff (04-sources Tier 2).

The UK's tech-for-good accelerator: pre-seed by construction, roughly twelve
ventures a cohort, and the companies are exactly the kind a generalist fund
does not see early. 04-sources records the verified cohort pattern —
"Spring 2026 cohort", 21 Apr 2026, 12 ventures.

The page is undated, so freshness comes from **what changed** since the last
run, the same mechanism as `conception_x` and `entrepreneur_first`
(04-sources §4.3). The bootstrap run returns everything and says so, and
downstream declines to treat a 2018 cohort as this week's news.

Two details worth keeping:

* Cards are `.grid_item`, not the generic `.w-dyn-item`. This is a Webflow
  site and the loose class also matches theme filters, carousel slides and
  configuration rows — 35 nodes of which 12 are ventures. `conception_x` shipped
  that exact bug: the guard counted cards, the names came back empty, and the
  source reported `ok (0)` for a total failure.
* An `Exited` tag means the company has already had its outcome. It is kept as
  a signal rather than dropped here, because the freshness gate owns that
  decision and an exited venture is still useful as `on_vc_portfolio` evidence.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import (
    absolute_url,
    clean_text,
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
    kind = "accelerator"
    schedule = "weekly"
    requires_browser = False
    track = "A"
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
            if item.external_id not in new_ids:
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
        name = _first_text(card, NAME_SELECTORS)
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

        return RawItem(
            source_key=self.key,
            # The portfolio entry, not the company's own site — same rule as
            # `oxford_innovation`. A third of these venture sites are still
            # plain http, and a provenance link has to be one we control the
            # shape of.
            source_url=PORTFOLIO,
            external_id=slug_of(website or "") or name.lower().replace(" ", "-"),
            published_at=None,          # stamped by `diff` on the run that finds it
            title=name,
            body_text=blob or None,
            structured={
                "company_name": name,
                "company_website": website,
                "one_line_description": _first_text(
                    card, (".card_text", "p"), exclude=name) or None,
                "accelerator_name": "Bethnal Green Ventures",
                "stage": "pre_seed",
                "hq_country_iso2": "GB",
                # Undated page: the date is when *we* first saw it, never a
                # claim about when the company was founded.
                "date_confidence": "inferred",
                "age_source": "unknown",
                "themes": sorted({t for t in tags if EXITED not in t.lower()}),
                **({"exited": True} if exited else {}),
            },
            kind_hint="accelerator_cohort",
        )


def _first_text(card, selectors, *, exclude: str | None = None) -> str:
    for selector in selectors:
        for node in card.css(selector):
            text = clean_text(node.text(strip=True))
            if text and text != exclude:
                return text
    return ""


ADAPTER = BethnalGreenAdapter()
