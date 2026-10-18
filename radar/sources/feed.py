"""Any RSS or Atom feed, added from the Control room — no code per source.

A feed source is a `Sources` row whose key starts with `feed_` and which
carries a feed URL. Items are news: the article page is fetched (within the
shared per-source budget and robots rules) unless the feed already carries the
full text, and stage ③ extracts the company from the prose like any news site.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, require_ok, rss_feed, unique_by_id
from radar.sources.base import FetchContext, RawItem

PREFIX = "feed_"
#: Below this, the feed carries an excerpt and the article page is fetched.
FULL_TEXT_MIN = 600
FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "backs", "secures", "grant", "cohort")


class FeedAdapter:
    kind = "news"
    schedule = "daily"
    requires_browser = False
    track = "A"

    def __init__(self, key: str, url: str) -> None:
        self.key = key
        self.endpoint = url
        self.homepage = url

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(self.endpoint)
        if resp.status == 304:
            return []
        require_ok(resp, self.key, self.endpoint)
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        entries, self.last_fingerprint = rss_feed(payload, self.key)
        return [self._item(entry) for entry in entries]

    def _item(self, entry: dict) -> RawItem:
        body = entry["body"]
        full = entry["has_full_text"] and len(body) >= FULL_TEXT_MIN
        haystack = f"{entry['title']} {' '.join(entry['tags'])}".lower()
        return RawItem(
            source_key=self.key,
            source_url=entry["link"],
            external_id=str(entry["id"]),
            published_at=entry["date"],
            title=entry["title"],
            body_text=body or None,
            structured={
                "date_confidence": "exact" if entry["date"] else "unknown",
                "full_text_in_feed": full,
                "company_link_evidence": entry.get("company_link_evidence", []),
                "tags": entry["tags"],
            },
            kind_hint="funding_round" if any(w in haystack for w in FUNDING_WORDS)
            else "news_mention",
        )


def feed_adapters(configured) -> list[FeedAdapter]:
    """Adapters for every enabled `feed_*` source that has a URL."""
    return [FeedAdapter(s.key, s.url) for s in configured
            if s.key.startswith(PREFIX) and getattr(s, "url", "") and s.enabled]
