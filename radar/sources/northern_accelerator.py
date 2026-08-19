"""Northern Accelerator — WordPress JSON with an RSS fallback.

Durham, Newcastle, Northumbria, Sunderland and York spinouts, aggregated in one
place. 04-sources ranks it a direct match to Northstar's mandate with no
competing source, which is why it is Tier 1 despite low volume — and why the
four universities are *not* scraped individually.

Shape of every adapter in this package, established here:

    fetch()  — politeness, pagination, conditional GET, date filtering
    parse()       — pure function from JSON bytes to `RawItem[]`
    parse_feed()  — pure function from RSS bytes to `RawItem[]`

Neither parser touches the network or sees `ctx`, so committed fixtures are
complete tests of both public representations.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from radar.sources._common import (
    after,
    require_ok,
    rss_entries,
    selector_fingerprint,
    unique_by_id,
    wp_fingerprint,
    wp_posts,
)
from radar.sources.base import FetchContext, RawItem, SourceBlocked

BASE = "https://northernaccelerator.org"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
FEED = f"{BASE}/feed/"
PER_PAGE = 50


class NorthernAcceleratorAdapter:
    key = "northern_accelerator"
    kind = "spinout"
    schedule = "daily"
    requires_browser = False
    track = "A"
    endpoint = ENDPOINT
    homepage = BASE

    #: Universities the aggregator covers, used to pre-fill `university_name`
    #: without an AI call when the post names one.
    UNIVERSITIES = (
        "Durham University", "Newcastle University", "Northumbria University",
        "University of Sunderland", "University of York",
    )

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        # `per_page`/`page` are ordinary WordPress query args and this host's
        # robots.txt does not disallow query strings. (UKTN's does — see
        # uktn.py, which is why that adapter looks different.)
        resp = ctx.http.get(ENDPOINT, params={"per_page": PER_PAGE, "page": 1})
        if resp.status == 304:
            return []
        try:
            require_ok(resp, self.key, ENDPOINT)
        except SourceBlocked:
            # LiteSpeed currently protects the JSON route with a CAPTCHA, but
            # the site's official RSS feed remains a robots-permitted public
            # route and carries content:encoded. Keep the richer JSON path when
            # it is available, then recover source coverage without pretending
            # to be a browser or bypassing the block.
            return self._fetch_feed(ctx)
        items = self.parse(resp.text)
        return list(after(unique_by_id(items), ctx.since))

    def _fetch_feed(self, ctx: FetchContext) -> Iterable[RawItem]:
        resp = ctx.http.get(FEED)
        if resp.status == 304:
            return []
        require_ok(resp, self.key, FEED)
        return list(after(unique_by_id(self.parse_feed(resp.text)), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        posts = wp_posts(payload, self.key)
        self.last_fingerprint = wp_fingerprint(posts)
        return [self._item(post) for post in posts]

    def parse_feed(self, payload: str | bytes) -> list[RawItem]:
        entries = rss_entries(payload, self.key)
        self.last_fingerprint = selector_fingerprint(
            ["rss>channel>item"] + sorted({k for e in entries[:5] for k in e})
        )
        return [self._item_from_feed(entry) for entry in entries]

    def _item_from_feed(self, entry: dict) -> RawItem:
        return self._item({
            "id": str(entry["id"]),
            "link": entry["link"],
            "date": entry["date"],
            "title": entry["title"],
            "body": entry["body"],
            "excerpt": "",
        })

    # --------------------------------------------------------------- private

    def _item(self, post: dict) -> RawItem:
        body = post["body"] or post["excerpt"]
        haystack = f"{post['title']} {body}".lower()
        universities = [u for u in self.UNIVERSITIES if u.lower() in haystack]
        structured: dict = {"date_confidence": "exact"}
        # Only claim "spinout" when the post says so. Northern Accelerator also
        # publishes events and programme news, and asserting
        # `is_university_spinout` on all of them would hand the scorer a fact
        # nobody stated — None means unknown and must stay that way.
        if "spinout" in haystack or "spin-out" in haystack or "spin out" in haystack:
            structured["is_university_spinout"] = True
        if universities:
            structured["university_name"] = universities[0]
        return RawItem(
            source_key=self.key,
            source_url=post["link"],
            external_id=post["id"],
            published_at=post["date"],
            title=post["title"],
            body_text=body or None,
            structured=structured,
            kind_hint="spinout",
        )


ADAPTER = NorthernAcceleratorAdapter()
