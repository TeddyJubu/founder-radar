"""Zinc VC — WordPress JSON, read **inverted** as a denylist feed.

Posts are literally *"Announcing Zinc's investment in X"*. That used to be
treated as a discovery source ("first money in"). The client rejected that:
a company Zinc has already backed is exactly the kind of name a scout should
*not* surface. Version 2 therefore reads these posts the same way
`vc_portfolios` reads portfolio pages — to demote companies we already hold,
never to add leads.

The regex is deliberately narrow. A near-miss that hands the pipeline a wrong
company name is worse than a miss that hands it nothing: without a name the
item is ignored; a wrong string would flag the wrong company on the denylist.
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import (
    after,
    clean_text,
    require_ok,
    unique_by_id,
    wp_fingerprint,
    wp_posts,
)
from radar.sources.base import FetchContext, RawItem
from radar.sources.denylist import listing

BASE = "https://www.zinc.vc"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
PER_PAGE = 50

#: "Announcing Zinc's investment in Acme" / "Zinc invests in Acme Robotics"
INVESTMENT_TITLE = re.compile(
    r"(?:announcing\s+)?(?:zinc(?:'s|’s)?\s+)?"
    r"(?:investment\s+in|invests?\s+in|backs)\s+(?P<name>.+?)"
    r"(?:\s*[—–\-|:].*)?$",
    re.I,
)


class ZincVcAdapter:
    key = "zinc_vc"
    kind = "portfolio"
    schedule = "weekly"
    requires_browser = False
    # Inverted like `vc_portfolios` (04-sources row 14): discovers nothing —
    # marks what Zinc has already backed so freshness gates reject it.
    track = "—"
    endpoint = ENDPOINT
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(ENDPOINT, params={"per_page": PER_PAGE, "page": 1})
        if resp.status == 304:
            return []
        require_ok(resp, self.key, ENDPOINT)
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        posts = wp_posts(payload, self.key)
        self.last_fingerprint = wp_fingerprint(posts)
        return [self._item(post) for post in posts]

    @staticmethod
    def company_from_title(title: str) -> str | None:
        match = INVESTMENT_TITLE.search(clean_text(title))
        if not match:
            return None
        name = match.group("name").strip(" .,’'\"")
        # Guard against the regex eating a sentence. Real names are short.
        if not name or len(name) > 60 or len(name.split()) > 5:
            return None
        return name

    def _item(self, post: dict) -> RawItem:
        body = post["body"] or post["excerpt"]
        name = self.company_from_title(post["title"])
        if name:
            return listing(
                source_key=self.key,
                source_url=post["link"],
                external_id=post["id"],
                published_at=post["date"],
                title=post["title"],
                body_text=body or None,
                company_name=name,
                vc_slug="zinc",
                vc_name="Zinc",
                date_confidence="exact",
            )
        return RawItem(
            source_key=self.key,
            source_url=post["link"],
            external_id=post["id"],
            published_at=post["date"],
            title=post["title"],
            body_text=body or None,
            structured={"date_confidence": "exact"},
            kind_hint="news",
        )


ADAPTER = ZincVcAdapter()
