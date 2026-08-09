"""Conception X — HTML portfolio with no dates. The snapshot-diff pattern.

PhD deeptech ventures *at or before incorporation* — structurally the youngest
cohort in the entire ledger, which is why an undated page is worth the extra
machinery.

The page lists every venture from CX18 onwards with no publication date
anywhere, so "what is new" is the only freshness signal available. The adapter
stores the set of `external_id`s it saw (in `_meta`, via `_common.snapshot_diff`)
and returns only the additions, dated `run_date` with
`date_confidence = "inferred"` — exactly the contract in 04-sources §4.3.

Two consequences worth stating out loud:

* **The first run is a bootstrap.** There is no baseline, so everything comes
  back flagged `bootstrap: True`. The alternative — returning nothing on the
  first run — loses the current cohort permanently.
* **Removals are ignored.** A venture disappearing from the page is not a
  signal we can interpret, and treating it as one would delete real companies.
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import (
    absolute_url,
    attr_of,
    clean_text,
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

BASE = "https://conceptionx.org"
PORTFOLIO = f"{BASE}/portfolio"

# `.portfolio-collection-item` is the live Webflow layout as of August 2026 and
# must stay ahead of `.w-dyn-item`: the generic Webflow class also matches the
# 18 cohort filter radios, so the loose selector returned 42 "cards" of which
# none carried a name — a silent zero that `guard_nonempty` could not see,
# because it counts cards and the names were lost one step later.
CARD_SELECTORS = (
    ".portfolio-collection-item",
    ".venture-card",
    ".portfolio-item",
    ".w-dyn-item",
    "article.venture",
    ".company-card",
)
NAME_SELECTORS = (".venture-name", ".portfolio-title-wrap .para-xxl-24",
                  ".para-xxl-24", "h3", "h4", "h2", ".title")
BLURB_SELECTORS = (".venture-blurb", ".description", "p")
#: The live card carries cohort and sector as Finsweet list fields.
COHORT_SELECTORS = (".venture-cohort", '[fs-list-field="cohort"]')

#: Cohort codes CX18–CX26 (04-sources §2, row 6).
COHORT = re.compile(r"\bCX\s?(\d{2})\b", re.I)


class ConceptionXAdapter:
    key = "conception_x"
    kind = "accelerator"
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
        return self.diff(self.parse(resp.text), ctx)

    # ------------------------------------------------------------------ parse

    def parse(self, payload: str | bytes) -> list[RawItem]:
        doc = html_doc(payload, self.key)
        selector, cards = select_any(doc, CARD_SELECTORS)
        guard_nonempty(
            self.key, cards,
            detail=f"no venture card matched any of {CARD_SELECTORS}",
            document=payload if isinstance(payload, str) else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        self.last_fingerprint = node_fingerprint(cards)
        items = [self._item(card) for card in cards]
        return [item for item in items if item is not None]

    def diff(self, items: list[RawItem], ctx: FetchContext) -> list[RawItem]:
        """Keep only ventures not seen on a previous run."""
        run_date = ctx.now
        new_ids, bootstrap = snapshot_diff(
            ctx.db, self.key, [item.external_id for item in items])
        out = []
        for item in items:
            if item.external_id not in new_ids:
                continue
            structured = dict(item.structured or {})
            structured["bootstrap"] = bootstrap
            out.append(RawItem(
                source_key=item.source_key,
                source_url=item.source_url,
                external_id=item.external_id,
                published_at=run_date,
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
        href = attr_of(card, None, "href") or attr_of(card, "a[href]", "href")
        source_url = absolute_url(BASE, href) or PORTFOLIO
        external_id = slug_of(href or "") or name.lower().replace(" ", "-")

        blob = clean_text(card.text(separator=" ", strip=True))
        cohort_match = COHORT.search(
            attr_of(card, None, "data-cohort")
            or _first_text(card, COHORT_SELECTORS) or blob)
        cohort = f"CX{cohort_match.group(1)}" if cohort_match else None
        university = attr_of(card, None, "data-university") \
            or text_of(card, ".venture-university") or None

        structured = {
            "company_name": name,
            "one_line_description": _first_text(card, BLURB_SELECTORS, exclude=name) or None,
            "cohort": cohort,
            "university_name": university,
            "is_university_spinout": True,      # every venture is PhD-founded
            "founder_signal": "research_spinout",
            "stage": "pre_seed",
            "hq_country_iso2": "GB",
            "date_confidence": "inferred",
            "age_source": "unknown",
        }
        return RawItem(
            source_key=self.key,
            source_url=source_url,
            external_id=external_id,
            published_at=None,                  # set by `diff()` to the run date
            title=name,
            body_text=structured["one_line_description"],
            structured=structured,
            kind_hint="accelerator_cohort",
        )


def _first_text(card, selectors, *, exclude: str | None = None) -> str:
    for selector in selectors:
        value = text_of(card, selector)
        if value and value != exclude:
            return value
    return ""


ADAPTER = ConceptionXAdapter()
