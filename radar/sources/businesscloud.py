"""BusinessCloud — RSS with full article text. The reference RSS adapter.

The reason this source is Tier 1 rather than Tier 2 is one detail: it puts the
whole article in `content:encoded`. Genuine North-of-England coverage at
15–30 UK companies a month, for exactly one HTTP request a day. Every other
news source in the ledger costs one extra fetch per article.

So the adapter's only real job is to *not throw that away*: prefer
`content:encoded`, fall back to `description`, and record which one it got so
the health tab can show the day the feed silently switches to excerpts.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, rss_entries, selector_fingerprint, unique_by_id
from radar.sources.base import FetchContext, RawItem

BASE = "https://businesscloud.co.uk"
FEED = f"{BASE}/feed/"

#: Below this, `content:encoded` is a teaser and the item needs the article
#: page. Full BusinessCloud articles run to several thousand characters.
FULL_TEXT_MIN = 600


class BusinessCloudAdapter:
    key = "businesscloud"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    track = "A"
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
        entries = rss_entries(payload, self.key)
        self.last_fingerprint = selector_fingerprint(
            ["rss>channel>item"] + sorted({k for e in entries[:5] for k in e})
        )
        return [self._item(entry) for entry in entries]

    def _item(self, entry: dict) -> RawItem:
        body = entry["body"]
        full = entry["has_full_text"] and len(body) >= FULL_TEXT_MIN
        return RawItem(
            source_key=self.key,
            source_url=entry["link"],
            external_id=str(entry["id"]),
            published_at=entry["date"],
            title=entry["title"],
            body_text=body or None,
            structured={
                "date_confidence": "exact",
                # False tells stage ③ this item still needs the article page —
                # the flag exists so a feed that quietly drops to excerpts
                # degrades to an extra fetch instead of to empty extractions.
                "full_text_in_feed": full,
                "tags": entry["tags"],
            },
            kind_hint="funding_round" if _looks_like_funding(entry) else "news_mention",
        )


FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "backs", "secures")


def _looks_like_funding(entry: dict) -> bool:
    haystack = f"{entry['title']} {' '.join(entry['tags'])}".lower()
    return any(word in haystack for word in FUNDING_WORDS)


ADAPTER = BusinessCloudAdapter()
