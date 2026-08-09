"""Carbon13 — climate venture builder, RSS (04-sources Tier 2).

A venture builder rather than a news outlet: its posts announce companies it
has just formed, which makes them pre-seed by construction and roughly as
early as a public announcement can be. The ledger routes it at Northstar,
whose North East vehicles have a climate mandate.

The feed is WordPress, so `content:encoded` carries the article and the
adapter shape is identical to `businesscloud`. What differs is the `kind_hint`
default: a venture builder's posts are cohort announcements first and funding
news second, so an item with no funding language is an `accelerator_cohort`
rather than a bare `news_mention`.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, rss_entries, selector_fingerprint, unique_by_id
from radar.sources.base import FetchContext, RawItem

BASE = "https://carbonthirteen.com"
FEED = f"{BASE}/feed/"

FULL_TEXT_MIN = 600

FUNDING_WORDS = ("raise", "raises", "raised", "funding", "investment", "seed",
                 "pre-seed", "series a", "backs", "secures", "closes")
COHORT_WORDS = ("cohort", "launch", "launches", "founded", "joins", "venture builder")


class Carbon13Adapter:
    key = "carbon13"
    kind = "accelerator"
    schedule = "weekly"
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
        body = entry["body"]
        haystack = f"{entry['title']} {' '.join(entry['tags'])}".lower()
        if any(word in haystack for word in FUNDING_WORDS):
            kind = "funding_round"
        elif any(word in haystack for word in COHORT_WORDS):
            kind = "accelerator_cohort"
        else:
            kind = "news_mention"
        return RawItem(
            source_key=self.key,
            source_url=entry["link"],
            external_id=str(entry["id"]),
            published_at=entry["date"],
            title=entry["title"],
            body_text=body or None,
            structured={
                "date_confidence": "exact",
                "full_text_in_feed": entry["has_full_text"] and len(body) >= FULL_TEXT_MIN,
                "accelerator_name": "Carbon13",
                "sector": "climate_tech",
                "stage": "pre_seed",
                "tags": entry["tags"],
            },
            kind_hint=kind,
        )


ADAPTER = Carbon13Adapter()
