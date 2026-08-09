"""Bdaily North East — regional RSS, excerpt only (04-sources Tier 2).

Bdaily matters for one reason: it is North East England business coverage, and
three of Northstar's five vehicles require a North East connection. A company
that only ever appears in a regional outlet is precisely the company no fund
in London has seen — high Discovery Edge by construction.

The cost is that the feed carries **excerpts**, not articles. So unlike
`businesscloud`, every item here sets `full_text_in_feed = False` and stage ③
pays one article fetch per item it decides to keep. That is the whole reason
this is Tier 2: same shape as the reference RSS adapter, an order of magnitude
more requests per useful company.

The excerpt is still passed through as `body_text`. It is enough for the free
prefilter to reject most items before any fetch or any AI call.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, rss_entries, selector_fingerprint, unique_by_id
from radar.sources.base import FetchContext, RawItem

BASE = "https://bdaily.co.uk"
FEED = f"{BASE}/region/north-east/rss"

#: The region this feed is scoped to. Set on every item so the derivation step
#: has a geography hint before any postcode is known — Bdaily publishes no
#: address, and a North East hint is the point of reading it at all.
REGION = "north_east"

FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "backs", "secures", "invests")


class BdailyRegionalAdapter:
    key = "bdaily_regional"
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
        entries = rss_entries(payload, self.key)
        self.last_fingerprint = selector_fingerprint(
            ["rss>channel>item"] + sorted({k for e in entries[:5] for k in e})
        )
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
                "date_confidence": "exact",
                # Always False: this feed is excerpt-only by design, so the
                # flag is a statement about the source, not a measurement of
                # one item. If Bdaily ever ships full text, the layout-change
                # detector will say so before this constant becomes a lie.
                "full_text_in_feed": False,
                "hq_region": REGION,
                "tags": entry["tags"],
            },
            kind_hint=("funding_round"
                       if any(w in haystack for w in FUNDING_WORDS)
                       else "news_mention"),
        )


ADAPTER = BdailyRegionalAdapter()
