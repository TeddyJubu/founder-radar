"""Converge — Scotland's university startup challenge, public RSS.

The old JSON API carries noindex. The permitted public feed supplies article
links, dates and excerpts. Extraction must fetch each article rather than
claiming the excerpt is complete. Archived JSON captures remain parseable.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.convergechallenge.com"
ENDPOINT = f"{BASE}/feed/"
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
        resp = ctx.http.get(ENDPOINT)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {ENDPOINT}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        body = payload.decode('utf-8', 'replace') if isinstance(payload, bytes) else payload
        if body.lstrip().startswith('<'):
            from radar.sources._common import rss_feed
            entries, feed_fingerprint = rss_feed(payload, self.key)
            posts = [dict(id=e['id'],link=e['link'],title=e['title'],date=e['date'],
                          body=e['body'],excerpt='',full_text_in_feed=False) for e in entries]
            self.last_fingerprint = feed_fingerprint
        else:
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
                "full_text_in_feed": post.get("full_text_in_feed", True),
                "accelerator_name": "Converge",
                # Scotland is `uk_regions` in the geography vocabulary — not
                # London, not North East, not Yorkshire.
                "hq_region": "uk_regions",
            },
            kind_hint=kind,
        )


ADAPTER = ConvergeAdapter()
