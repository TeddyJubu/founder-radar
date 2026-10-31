"""University of Leeds Nexus — startup community news, for Anticus.

The university news pages do not publish a feed. Nexus does, at
https://nexusleeds.co.uk/feed/, and that is the Leeds source Aryan asked for.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, rss_feed, unique_by_id
from radar.sources.base import FetchContext, RawItem

BASE = "https://nexusleeds.co.uk"
FEED = f"{BASE}/feed/"
REGION = "yorkshire"
FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "spinout", "spin-out", "launch", "accelerator")


class NexusLeedsAdapter:
    key = "nexus_leeds"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    track = "A"
    tier = 2
    endpoint = FEED
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(FEED)
        if resp.status == 304:
            return []
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {FEED}")
        return list(after(unique_by_id(self.parse(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        entries, self.last_fingerprint = rss_feed(payload, self.key)
        return [self._item(entry) for entry in entries]

    def _item(self, entry: dict) -> RawItem:
        haystack = f"{entry['title']} {' '.join(entry['tags'])}".lower()
        return RawItem(
            source_key=self.key,
            source_url=entry["link"],
            external_id=str(entry["id"]),
            published_at=entry["date"],
            title=entry["title"],
            body_text=entry["body"] or None,
            structured={
                "date_confidence": "exact" if entry["date"] else "unknown",
                "full_text_in_feed": bool(entry.get("has_full_text")),
                "hq_region": REGION,
                "tags": entry["tags"],
            },
            kind_hint=("funding_round"
                       if any(word in haystack for word in FUNDING_WORDS)
                       else "news_mention"),
        )


ADAPTER = NexusLeedsAdapter()
