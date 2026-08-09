"""Zinc VC — WordPress JSON. The highest signal-to-noise source tested.

04-sources: posts are literally *"Announcing Zinc's investment in X"*, and Zinc
is first money in. That makes the title itself a reliable extractor, so this
adapter pulls the company name out with a regex and puts it in `structured`.
When the regex fires, stage ③ has nothing left to ask a model.

The regex is deliberately narrow. A near-miss that hands the pipeline a wrong
company name is worse than a miss that hands it nothing: `None` routes to the
AI extractor, a wrong string routes to entity resolution and merges.
"""

from __future__ import annotations

import re
from typing import Iterable

from radar.sources._common import after, clean_text, require_ok, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

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
    kind = "accelerator"
    schedule = "weekly"
    requires_browser = False
    track = "A"
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
        structured: dict = {"date_confidence": "exact"}
        name = self.company_from_title(post["title"])
        kind_hint = "news"
        if name:
            structured["company_name"] = name
            structured["stage"] = "pre_seed"     # Zinc is first money in
            kind_hint = "funding_round"
        return RawItem(
            source_key=self.key,
            source_url=post["link"],
            external_id=post["id"],
            published_at=post["date"],
            title=post["title"],
            body_text=body or None,
            structured=structured,
            kind_hint=kind_hint,
        )


ADAPTER = ZincVcAdapter()
