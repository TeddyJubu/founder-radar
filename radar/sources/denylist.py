"""Inverted portfolio feeds — demote companies funds have already seen.

Adapters that read VC portfolio pages or first-party investment announcements
emit `kind_hint="vc_portfolio_listing"`. The pipeline routes those items here
instead of through resolve, so a listing can never become a lead. That is
precisely the version-1 behaviour the client rejected.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from radar.resolve.normalise import norm_key
from radar.sources.base import RawItem
from radar.store.db import now_iso


def listing(
    *,
    source_key: str,
    source_url: str,
    external_id: str | int | None,
    title: str,
    company_name: str,
    vc_slug: str,
    vc_name: str,
    published_at: date | datetime | None = None,
    body_text: str | None = None,
    date_confidence: str = "exact",
    extra: dict[str, Any] | None = None,
) -> RawItem:
    """Build a denylist `RawItem` for one already-backed company.

    Shared by inverted investment-announcement adapters so the pipeline sees
    one shape: `on_vc_portfolio=True` + `kind_hint=vc_portfolio_listing`.
    """
    structured: dict[str, Any] = {
        "company_name": company_name,
        "on_vc_portfolio": True,
        "vc_slug": vc_slug,
        "vc_name": vc_name,
        "norm_key": norm_key(company_name),
        "date_confidence": date_confidence,
    }
    if extra:
        structured.update(extra)
    return RawItem(
        source_key=source_key,
        source_url=source_url,
        external_id=external_id,
        published_at=published_at,
        title=title,
        body_text=body_text,
        structured=structured,
        kind_hint="vc_portfolio_listing",
    )


def apply_denylist(db, items: Sequence[RawItem]) -> dict:
    """Set `company.on_vc_portfolio` and write the negative signal.

    Runs on names we already hold. Deliberately does **not** create companies:
    a portfolio listing is a reason to *demote* a company we found elsewhere,
    never a reason to add one.
    """
    matched: list[str] = []
    keys: dict[str, RawItem] = {}
    for item in items:
        structured = item.structured or {}
        key = structured.get("norm_key") or norm_key(item.title)
        if key:
            keys.setdefault(key, item)

    if not keys:
        return {"listings": 0, "companies_flagged": 0, "matched": []}

    placeholders = ",".join("?" for _ in keys)
    rows = db.query(
        f"""SELECT id, canonical_name, norm_key FROM company
            WHERE merged_into IS NULL AND norm_key IN ({placeholders})""",
        list(keys),
    )
    for row in rows:
        item = keys[row["norm_key"]]
        structured = item.structured or {}
        db.execute(
            "UPDATE company SET on_vc_portfolio = 1, updated_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        source_key = getattr(item, "source_key", None) or "unknown"
        db.execute(
            """INSERT OR IGNORE INTO signal
               (company_id, kind, occurred_on, headline, detail, source_key,
                source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (row["id"], "vc_portfolio_listing", None,
             f"Listed on {structured.get('vc_name', 'a VC')} portfolio",
             structured.get("vc_slug"), source_key,
             item.source_url, now_iso()),
        )
        matched.append(row["canonical_name"])

    return {
        "listings": len(items),
        "companies_flagged": len(matched),
        "matched": sorted(matched),
    }
