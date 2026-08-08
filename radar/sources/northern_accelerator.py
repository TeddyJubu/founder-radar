"""Northern Accelerator — WordPress JSON. The reference JSON adapter.

Durham, Newcastle, Northumbria, Sunderland and York spinouts, aggregated in one
place. 04-sources ranks it a direct match to Northstar's mandate with no
competing source, which is why it is Tier 1 despite low volume — and why the
four universities are *not* scraped individually.

Shape of every JSON adapter in this package, established here:

    fetch()  — politeness, pagination, conditional GET, date filtering
    parse()  — pure function from bytes to `RawItem[]`, the test seam

`parse` never touches the network and never sees `ctx`, so a committed fixture
is a complete test of the interesting half.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://northernaccelerator.org"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
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
        if not resp.ok:
            raise RuntimeError(f"{self.key}: HTTP {resp.status} from {ENDPOINT}")
        items = self.parse(resp.text)
        return list(after(unique_by_id(items), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        posts = wp_posts(payload, self.key)
        self.last_fingerprint = wp_fingerprint(posts)
        return [self._item(post) for post in posts]

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
