"""Openable evidence URLs for Sheet, Kept, Today, and QA surfaces.

Stored citations may point at an Innovate UK workbook (``.xlsx``). Humans and
QA agents should open a project page or the publication, never trigger a
spreadsheet download. Today already mapped this via ``page_url``; every other
evidence surface uses this module so they stay aligned.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from radar.sources.innovate_uk import page_url as _page_url


def is_workbook_url(url: str | None) -> bool:
    """True when the path is an ``.xlsx`` (query/fragment ignored)."""
    try:
        path = urlsplit(str(url or "")).path.lower()
    except ValueError:
        path = str(url or "").lower().split("?", 1)[0].split("#", 1)[0]
    return path.endswith(".xlsx")


def openable_url(url: str | None, external_id: str | None = None) -> str:
    """A URL a person can open without downloading a workbook."""
    text = str(url or "").strip()
    if not text:
        return ""
    return _page_url(text, external_id)


def _as_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def prefer_evidence_sources(
    rows: Sequence[Any],
) -> list[dict[str, Any]]:
    """HTTP ``company_source`` rows ordered like Today: pages before workbooks/CH.

    Preference:
    1. Non-Companies-House, non-workbook URLs
    2. Other non-Companies-House URLs (e.g. Innovate workbook citations)
    3. Remaining HTTP rows (Companies House last)
    """
    http: list[dict[str, Any]] = []
    for row in rows:
        item = dict(_as_mapping(row))
        url = str(item.get("source_url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        http.append(item)

    pages = [
        item for item in http
        if item.get("source_key") != "companies_house"
        and not is_workbook_url(item.get("source_url"))
    ]
    non_ch = [
        item for item in http
        if item.get("source_key") != "companies_house" and item not in pages
    ]
    rest = [item for item in http if item not in pages and item not in non_ch]
    return pages + non_ch + rest


def primary_source(rows: Sequence[Any]) -> dict[str, Any] | None:
    """The first preferred HTTP source, or ``None``."""
    ordered = prefer_evidence_sources(rows)
    return ordered[0] if ordered else None


def openable_evidence_urls(
    url: str | None,
    *,
    external_id: str | None = None,
) -> str:
    """Alias kept for call sites that want the emitting-side name."""
    return openable_url(url, external_id)
