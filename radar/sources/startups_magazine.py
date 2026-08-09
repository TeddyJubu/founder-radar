"""Startups Magazine — WordPress JSON (04-sources Tier 2).

The third wp-json adapter, and it exists mostly to prove the helper was worth
writing: host, `kind_hint` and a funding-word list are the whole difference
from `cambridge_enterprise`.

One thing the endpoint choice is doing: `startupsmagazine.co.uk` also
publishes RSS, and the RSS carries **summaries only**. wp-json carries the
full `content.rendered`, which is what stage ③ needs to extract a company from
prose. Taking the feed would have meant one extra article fetch per item for
strictly less text.

Volume is the trade-off. This is a general startup outlet rather than a UK
regional one, so the prefilter drops a lot of it — which is exactly why the
source is Tier 2 and off the critical path.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://startupsmagazine.co.uk"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
PER_PAGE = 50

FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "backs", "secures", "closes")


class StartupsMagazineAdapter:
    key = "startups_magazine"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    track = "A"
    tier = 2
    endpoint = ENDPOINT
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(ENDPOINT, params={"per_page": PER_PAGE, "page": 1})
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {ENDPOINT}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        posts = wp_posts(payload, self.key)
        self.last_fingerprint = wp_fingerprint(posts)
        return [self._item(post) for post in posts]

    def _item(self, post: dict) -> RawItem:
        body = post["body"] or post["excerpt"]
        haystack = f"{post['title']} {body}".lower()
        return RawItem(
            source_key=self.key,
            source_url=post["link"],
            external_id=post["id"],
            published_at=post["date"],
            title=post["title"],
            body_text=body or None,
            structured={
                "date_confidence": "exact",
                # wp-json gives the whole article, so stage ③ never needs a
                # second request for this source.
                "full_text_in_feed": True,
            },
            kind_hint=("funding_round"
                       if any(w in haystack for w in FUNDING_WORDS)
                       else "news_mention"),
        )


ADAPTER = StartupsMagazineAdapter()
