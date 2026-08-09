"""GOV.UK Search API — keyless, rate-limit-free, day-level dates.

04-sources promises "ten lines of code" and it nearly is. `www.gov.uk/api/search.json`
needs no key, imposes no quota, and stamps every result with a
`public_timestamp` accurate to the day — which is more than most of the news
sources manage.

What it buys: Innovate UK award announcements, 5–10 UK companies a month, as a
*quality* signal. A company with a public R&D grant has been through a panel.
"""

from __future__ import annotations

import json
from typing import Iterable

from radar.sources._common import (
    LayoutChanged,
    after,
    clean_text,
    guard_nonempty,
    parse_date,
    require_ok,
    selector_fingerprint,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.gov.uk"
ENDPOINT = f"{BASE}/api/search.json"

QUERIES = (
    {"q": "Innovate UK funding competition winners", "count": 50},
    {"q": "Innovate UK grant award", "count": 50},
)
FIELDS = "title,link,description,public_timestamp,organisations,format"


class GovUkSearchAdapter:
    key = "govuk_search"
    kind = "grant"
    schedule = "daily"
    requires_browser = False
    track = "A"
    endpoint = ENDPOINT
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        items: list[RawItem] = []
        for query in QUERIES:
            resp = ctx.http.get(ENDPOINT, params={
                **query, "order": "-public_timestamp", "fields": FIELDS})
            if resp.status == 304:
                continue
            require_ok(resp, self.key, ENDPOINT)
            items.extend(self.parse(resp.text))
        return list(after(unique_by_id(items), ctx.since))

    def parse(self, payload: str | bytes) -> list[RawItem]:
        body = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LayoutChanged(self.key, f"response is not JSON: {exc}") from exc
        if not isinstance(data, dict) or "results" not in data:
            raise LayoutChanged(self.key, "response has no `results` key")

        results = data["results"]
        if not isinstance(results, list):
            raise LayoutChanged(self.key, "`results` is not an array")
        guard_nonempty(self.key, results, detail="search returned no results", document=body)

        keys: set[str] = set()
        out: list[RawItem] = []
        for result in results:
            if not isinstance(result, dict):
                raise LayoutChanged(self.key, "result is not an object")
            keys.update(result.keys())
            link, title = result.get("link"), result.get("title")
            if not link or not title:
                raise LayoutChanged(self.key, "result is missing link or title")
            url = link if link.startswith("http") else BASE + link
            out.append(RawItem(
                source_key=self.key,
                source_url=url,
                external_id=link,
                published_at=parse_date(result.get("public_timestamp")),
                title=clean_text(title),
                body_text=clean_text(result.get("description")) or None,
                structured={
                    "date_confidence": "exact",
                    "organisations": [
                        o.get("title") for o in result.get("organisations", []) or []
                        if isinstance(o, dict)
                    ],
                    "govuk_format": result.get("format"),
                },
                kind_hint="grant_award",
            ))
        self.last_fingerprint = selector_fingerprint(keys)
        return out


ADAPTER = GovUkSearchAdapter()
