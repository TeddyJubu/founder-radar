"""Converge — Scotland's university startup challenge, WordPress JSON.

Scottish university spinouts and student ventures, announced in cohort bursts.
Scotland sits outside every North East and Yorkshire mandate, so these route to
DSW — whose SEIS fund explicitly targets companies **outside** the
London–Oxbridge triangle — and to Outward's UK-wide vehicle.

⚠️ 04-sources lists this as `RSS` at `convergechallenge.com/updates/`. That URL
is an HTML page, not a feed. The site is WordPress and exposes both
`/feed/` and `/wp-json/wp/v2/posts`; the latter is used here for the same
reason as `startups_magazine` — wp-json carries `content.rendered` in full,
while the feed carries a summary. The ledger's row should be read as "there is
a feed", not as the endpoint to call.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.convergechallenge.com"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
PER_PAGE = 50

COHORT_WORDS = ("cohort", "winners", "finalists", "shortlist", "award",
                "challenge", "programme")
FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "secures")


class ConvergeAdapter:
    key = "converge"
    kind = "accelerator"
    schedule = "weekly"
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
        if any(word in haystack for word in FUNDING_WORDS):
            kind = "funding_round"
        elif any(word in haystack for word in COHORT_WORDS):
            kind = "accelerator_cohort"
        else:
            kind = "news_mention"
        return RawItem(
            source_key=self.key,
            source_url=post["link"],
            external_id=post["id"],
            published_at=post["date"],
            title=post["title"],
            body_text=body or None,
            structured={
                "date_confidence": "exact",
                "full_text_in_feed": True,
                "accelerator_name": "Converge",
                # Scotland is `uk_regions` in the geography vocabulary — not
                # London, not North East, not Yorkshire.
                "hq_region": "uk_regions",
            },
            kind_hint=kind,
        )


ADAPTER = ConvergeAdapter()
