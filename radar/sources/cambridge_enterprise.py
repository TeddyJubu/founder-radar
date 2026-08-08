"""Cambridge Enterprise — WordPress JSON. The pattern generalises.

Second JSON adapter, and the point of it is how little there is: same helper,
same shape, different host and a different `kind_hint` policy. If this file had
needed new machinery, the machinery would have belonged in `_common.py`.

Cambridge's TTO announces both spinouts and licensing deals. Only the first is
a company; the second is not, so the adapter marks its guess rather than
inventing a company name.
"""

from __future__ import annotations

from typing import Iterable

from radar.sources._common import after, unique_by_id, wp_fingerprint, wp_posts
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.enterprise.cam.ac.uk"
ENDPOINT = f"{BASE}/wp-json/wp/v2/posts"
PER_PAGE = 50

SPINOUT_WORDS = ("spinout", "spin-out", "spin out", "launches", "founded",
                 "incorporated", "raises", "seed round", "pre-seed")


class CambridgeEnterpriseAdapter:
    key = "cambridge_enterprise"
    kind = "spinout"
    schedule = "weekly"
    requires_browser = False
    track = "A"
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
        structured: dict = {
            "date_confidence": "exact",
            "university_name": "University of Cambridge",
        }
        if any(word in haystack for word in SPINOUT_WORDS):
            structured["is_university_spinout"] = True
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


ADAPTER = CambridgeEnterpriseAdapter()
