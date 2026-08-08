"""Companies House filing history — SH01 detection.

An `SH01` ("Return of Allotment of Shares") filed within 18 months of
incorporation is, in practice, a pre-seed or seed round being papered. It hits
the public register within days, it costs one free request, and **no portfolio
page scraper will ever see it** (04-sources §3.5).

This is the single highest-value query in the system: "incorporated in the last
90 days + tech SIC + in-region" crossed with "SH01 since incorporation" is a
very short, very high-conviction list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

log = logging.getLogger(__name__)

CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_FILING_URL = (
    "https://find-and-update.company-information.service.gov.uk/company/{}/filing-history"
)

SH01 = "SH01"
CAPITAL_CATEGORY = "capital"

#: 04-sources §3.5 — beyond this an allotment is routine housekeeping, not a round.
SHARE_ISSUE_WINDOW_MONTHS = 18


@dataclass(frozen=True, slots=True)
class ShareIssue:
    """One SH01 on the register."""

    filed_on: str | None
    transaction_id: str | None
    description: str | None = None
    category: str | None = None
    type: str = SH01

    @property
    def filed_date(self) -> date | None:
        return _parse_date(self.filed_on)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def months_between(earlier: date, later: date) -> int:
    """Whole months from `earlier` to `later`. Negative if `later` is before it."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def find_share_issues(raw: Mapping[str, Any] | None) -> list[ShareIssue]:
    """Every SH01 in a filing-history response, newest first.

    Matches on `type == "SH01"`; `category == "capital"` is accepted as a
    corroborating signal but not required, because CH has historically shipped
    items with the category missing and losing an SH01 is expensive.
    """
    out: list[ShareIssue] = []
    for item in (raw or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        filing_type = str(item.get("type") or "").strip().upper()
        category = str(item.get("category") or "").strip().lower() or None
        if filing_type != SH01:
            continue
        out.append(
            ShareIssue(
                filed_on=item.get("date") or item.get("action_date") or None,
                transaction_id=item.get("transaction_id") or None,
                description=item.get("description") or None,
                category=category,
            )
        )
    out.sort(key=lambda s: s.filed_on or "", reverse=True)
    return out


def latest_share_issue(raw: Mapping[str, Any] | None) -> ShareIssue | None:
    issues = find_share_issues(raw)
    return issues[0] if issues else None


def has_share_issue(
    raw: Mapping[str, Any] | None,
    incorporated_on: Any = None,
    *,
    within_months: int = SHARE_ISSUE_WINDOW_MONTHS,
) -> bool:
    """True when an SH01 was filed within `within_months` of incorporation.

    With no incorporation date, any SH01 counts — an unknown date must not
    silently turn a real signal into a `False`.
    """
    issues = find_share_issues(raw)
    if not issues:
        return False

    incorporated = _parse_date(incorporated_on)
    if incorporated is None:
        return True

    for issue in issues:
        filed = issue.filed_date
        if filed is None:
            return True  # dated-unknown SH01: keep it rather than lose the signal
        gap = months_between(incorporated, filed)
        if 0 <= gap <= within_months:
            return True
    return False


def qualifying_share_issues(
    raw: Mapping[str, Any] | None,
    incorporated_on: Any = None,
    *,
    within_months: int = SHARE_ISSUE_WINDOW_MONTHS,
) -> list[ShareIssue]:
    incorporated = _parse_date(incorporated_on)
    issues = find_share_issues(raw)
    if incorporated is None:
        return issues
    out = []
    for issue in issues:
        filed = issue.filed_date
        if filed is None or 0 <= months_between(incorporated, filed) <= within_months:
            out.append(issue)
    return out


def fetch_filing_history(
    http: Any,
    number: str,
    *,
    api_key: str,
    base_url: str = CH_API_BASE,
    items_per_page: int = 100,
) -> Any:
    """One request. Category filtering is done client-side.

    ponytail: the API accepts `category=capital`, but a young company's whole
    filing history fits in one page anyway, and filtering locally means an item
    with a missing `category` still gets seen.
    """
    resp = http.get(
        f"{base_url.rstrip('/')}/company/{number}/filing-history",
        params={"items_per_page": items_per_page},
        auth=(api_key, ""),
        check_robots=False,
    )
    if resp.status == 404:
        return None
    if not resp.ok:
        log.warning("companies_house: HTTP %s for filing-history of %s", resp.status, number)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("companies_house: unparseable filing-history for %s", number)
        return None


def share_issue_headline(issue: ShareIssue, company_name: str) -> str:
    when = f" on {issue.filed_on}" if issue.filed_on else ""
    return f"SH01 share allotment filed{when} — {company_name}"


def record_share_issues(
    db: Any,
    company_id: str,
    company_number: str,
    company_name: str,
    issues: Sequence[ShareIssue],
    *,
    source_key: str = "companies_house",
    now: str | None = None,
) -> int:
    """Write `has_share_issue` plus one `share_issue` signal. Idempotent."""
    from radar.store.db import now_iso

    if not issues:
        return 0
    stamp = now or now_iso()
    url = CH_FILING_URL.format(company_number)
    latest = issues[0]
    db.execute(
        """INSERT OR IGNORE INTO signal
             (company_id, kind, occurred_on, headline, detail, source_key,
              source_url, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            company_id, "share_issue", latest.filed_on,
            share_issue_headline(latest, company_name),
            latest.description, source_key, url, stamp,
        ),
    )
    db.execute(
        "UPDATE company SET has_share_issue = 1, updated_at = ? WHERE id = ?",
        (stamp, company_id),
    )
    return len(issues)
