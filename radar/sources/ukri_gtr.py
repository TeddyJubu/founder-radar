"""UKRI Gateway to Research — Innovate UK awards. Free, keyless, one request.

04-sources row 11. A public R&D grant means a panel of assessors already read
the pitch, so this is a **quality** signal, not a freshness one: it feeds the
`grant` qualifier in 06-scoring §3, and the award is evidence about a company
we probably found somewhere else.

Verified live on 8 August 2026, and the ledger's endpoint needed one
correction:

* `gtr.ukri.org/gtr/api/projects` (the endpoint in 04-sources) is real and
  keyless, but a project there carries organisations only as `links.link[]`
  hrefs with `rel="LEAD_ORG"` — **no organisation name**. Using it costs one
  extra request per project just to learn who won the money, which is the one
  thing we need.
* `gtr.ukri.org/api/search/project` — the endpoint GtR's own search page
  calls — returns `leadResearchOrganisation.name`, `fund.valuePounds`,
  `fund.start` and `fund.funder.name` inline, so a page of 100 awards is one
  request. `gtr.ukri.org/robots.txt` is a 404, which 04-sources §5 says to
  treat as fully allowed.

`selectedFacets` takes the site's own base64 facet ids, so "funded by Innovate
UK" is `base64("funder|Innovate UK|string")` rather than a magic constant, and
`selectedSortableField=pro.sd` sorts by start date so page 1 is the newest
awards rather than the most relevant ones.

Two things are deliberately dropped in the adapter rather than at render time
(01-product-requirements FR-8): `personRoles` carries named principal
investigators — personal data we have no use for — and academic lead
organisations are not companies. Knowledge Transfer Partnerships are led by the
university, so that filter removes about a third of the feed.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from typing import Iterable
from urllib.parse import quote

from radar.sources._common import (
    LayoutChanged,
    after,
    clean_text,
    guard_nonempty,
    require_ok,
    selector_fingerprint,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://gtr.ukri.org"
ENDPOINT = f"{BASE}/api/search/project"

#: GtR's own facet id, spelled out rather than pasted as a base64 blob.
INNOVATE_UK_FACET = base64.b64encode(b"funder|Innovate UK|string").decode()
PER_PAGE = 100
PAGES = 2                       # ~200 newest awards a week; the source is weekly
ABSTRACT_CHARS = 2000

#: Lead organisations that are not companies. Small on purpose — every token
#: here is a name a real startup will never carry, and `LayoutChanged` is what
#: catches the day the shape moves, not this list.
ACADEMIC = (
    "universit", "college", "catapult", " nhs", "nhs ", "school of",
    "institute of", "research council",
)


def _is_academic(name: str) -> bool:
    lowered = f" {name.lower()} "
    return any(token in lowered for token in ACADEMIC)


def _epoch_date(value) -> date | None:
    """GtR stamps `fund.start` in epoch milliseconds. Day-level, UTC."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


class UkriGtrAdapter:
    key = "ukri_gtr"
    kind = "grant"
    schedule = "weekly"
    requires_browser = False
    track = "A"
    endpoint = ENDPOINT
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        items: list[RawItem] = []
        for page in range(1, PAGES + 1):
            resp = ctx.http.get(ENDPOINT, params={
                "term": "*",
                "fetchSize": PER_PAGE,
                "page": page,
                "selectedFacets": INNOVATE_UK_FACET,
                "selectedSortableField": "pro.sd",
                "selectedSortOrder": "DESC",
            }, headers={"Accept": "application/json"})
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
        bean = data.get("facetedSearchResultBean") if isinstance(data, dict) else None
        if not isinstance(bean, dict):
            raise LayoutChanged(self.key, "response has no `facetedSearchResultBean`")

        results = bean.get("results")
        if not isinstance(results, list):
            raise LayoutChanged(self.key, "`results` is not an array")
        guard_nonempty(self.key, results, detail="search returned no projects", document=body)

        keys: set[str] = set()
        out: list[RawItem] = []
        for result in results:
            composition = result.get("projectComposition") if isinstance(result, dict) else None
            if not isinstance(composition, dict):
                raise LayoutChanged(self.key, "result has no `projectComposition`")
            project = composition.get("project")
            if not isinstance(project, dict):
                raise LayoutChanged(self.key, "`projectComposition` has no `project`")
            keys.update(project.keys())

            reference = project.get("grantReference") or project.get("id")
            title = clean_text(project.get("title"))
            if not reference or not title:
                raise LayoutChanged(self.key, "project has no grantReference or title")

            lead = composition.get("leadResearchOrganisation") or {}
            company = clean_text(lead.get("name") if isinstance(lead, dict) else None)
            # Not a failure: a KTP's lead is the university, and an award with
            # no named organisation tells us nothing about a company.
            if not company or _is_academic(company):
                continue

            fund = project.get("fund") if isinstance(project.get("fund"), dict) else {}
            funder = fund.get("funder") if isinstance(fund.get("funder"), dict) else {}
            awarded = _epoch_date(fund.get("start"))
            structured = {
                "company_name": company,
                "grant_amount_gbp": fund.get("valuePounds"),
                "funder": clean_text(funder.get("name")) or "Innovate UK",
                "grant_reference": str(reference),
                "grant_category": clean_text(project.get("grantCategory")) or None,
                # 06-scoring §3 — this is the whole point of the source.
                "qualifiers": ["grant"],
            }
            if awarded:
                structured["date_confidence"] = "exact"

            abstract = clean_text(project.get("abstractText"))
            out.append(RawItem(
                source_key=self.key,
                source_url=f"{BASE}/projects?ref={quote(str(reference))}",
                external_id=str(reference),
                published_at=awarded,
                title=title,
                body_text=abstract[:ABSTRACT_CHARS] or None,
                structured=structured,
                kind_hint="grant_award",
            ))

        self.last_fingerprint = selector_fingerprint(keys)
        return out


ADAPTER = UkriGtrAdapter()
