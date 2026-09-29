"""Startups Magazine — the public articles index.

The API carries noindex. Read the permitted public article cards, preserving
stated dates and excerpts; extraction must fetch the individual article.
Archived JSON captures remain parseable for replay and fixture compatibility.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://startupsmagazine.co.uk"
ENDPOINT = f"{BASE}/articles"
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
        resp = ctx.http.get(ENDPOINT)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {ENDPOINT}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        body = payload.decode('utf-8', 'replace') if isinstance(payload, bytes) else payload
        if body.lstrip().startswith('<'):
            from radar.sources._public_news import public_posts
            posts, self.last_fingerprint = public_posts(body,self.key,BASE,'.post')
        else:
            # Backward-compatible parsing of already captured JSON fixtures.
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
                "full_text_in_feed": post.get("full_text_in_feed", True),
            },
            kind_hint=("funding_round"
                       if any(w in haystack for w in FUNDING_WORDS)
                       else "news_mention"),
        )


ADAPTER = StartupsMagazineAdapter()
