"""Companies House advanced search — the date-windowed, SIC-batched sweep.

This is the backbone of Track B and the thing that fixes the client's
complaint: it is the only free source with a true `incorporated_from` /
`incorporated_to` filter, so it *structurally cannot* return an old company
(04-sources §2, §3).

Two properties matter more than anything else in this file:

* **Never approach the result ceiling.** The API returns `500` past ~10,000
  items, so the sweep slices by incorporation date — which is also exactly the
  axis we care about. A 90-day backfill of the whole UK is 13 seven-day windows
  × 3 SIC tiers = **39 requests**.
* **Always check for truncation.** Results are silently truncated at `size`.
  Tier 1 contains `62020` (IT consultancy), one of the most-used codes on the
  register, so a seven-day window *can* exceed 5,000. If `hits > len(items)` we
  halve the window and recurse. Without this, companies vanish with no error —
  the exact "200 OK, looks like a quiet week" failure this system exists to
  avoid (04-sources §3.2).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Iterator, Mapping, Sequence

from radar.fetch.layout import LayoutChanged
from radar.resolve.normalise import is_placeholder_name, norm_key
from radar.sources.base import FetchContext, RawItem, SourceError, SourceKind

log = logging.getLogger(__name__)

# --------------------------------------------------------------- constants

CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_HOST = "api.company-information.service.gov.uk"
CH_PROFILE_URL = "https://find-and-update.company-information.service.gov.uk/company/{}"

#: 04-sources §3.2. Three tiers, one request each per window.
SIC_TIERS: dict[str, tuple[str, ...]] = {
    "tier1": ("62012", "62020", "62090", "63110", "63120", "72190"),
    "tier2": ("72110", "71121", "71122", "71129", "26110", "26120", "26200",
              "26511", "26600", "26701", "21100", "21200", "32500"),
    "tier3": ("62011", "58210", "58290", "63990", "64205", "66190", "72200", "74909"),
}

#: Formation-agent dumping grounds. A company whose SIC codes are *all* in here
#: is dropped before it costs a single enrichment request (04-sources §3.4 #1).
SIC_DENYLIST: frozenset[str] = frozenset({"82990", "70229"})

SIC_TO_TIER: dict[str, str] = {
    code: tier for tier, codes in SIC_TIERS.items() for code in codes
}

DEFAULT_WINDOW_DAYS = 7
DEFAULT_SIZE = 5000

_ENV_KEYS = ("CH_API_KEY", "COMPANIES_HOUSE_API_KEY", "RADAR_CH_API_KEY")


# ------------------------------------------------------------- small helpers


def normalise_ch_number(value: Any) -> str | None:
    """Companies House numbers are **strings**, forever.

    Leading zeros (`00445790`) and the `SC` / `NI` / `OC` / `SO` prefixes are
    real data. `int()` silently destroys both (03-data-model §2).
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text.isdigit():
        return text.zfill(8)
    return text


def is_denylisted_only(sic_codes: Sequence[str], denylist: Iterable[str] = SIC_DENYLIST) -> bool:
    """True when *every* SIC code is a formation-agent code.

    An empty list is **not** denylisted: absence of evidence is not evidence.
    """
    codes = {str(c).strip() for c in (sic_codes or []) if str(c).strip()}
    if not codes:
        return False
    return codes.issubset(set(denylist))


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def date_windows(
    days_back: int, window_days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> list[tuple[date, date]]:
    """Inclusive `[start, end]` slices covering `today - days_back` … `today`.

    90 days back is 91 calendar days inclusive, which is exactly 13 windows of
    7 — the 39-request figure in 04-sources §3.2.
    """
    if days_back < 0:
        raise ValueError("days_back must be >= 0")
    window_days = max(int(window_days), 1)
    today = today or date.today()
    start = today - timedelta(days=days_back)
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= today:
        end = min(cursor + timedelta(days=window_days - 1), today)
        out.append((cursor, end))
        cursor = end + timedelta(days=1)
    return out


def split_window(start: date, end: date, window_days: int) -> list[tuple[date, date]]:
    """Chop an existing window into smaller inclusive slices."""
    window_days = max(int(window_days), 1)
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=window_days - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def api_key_from_env() -> str | None:
    for key in _ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return None


# ------------------------------------------------------------------ results


@dataclass(frozen=True)
class SweepPage:
    """One advanced-search response, with the truncation verdict attached."""

    start: date
    end: date
    tier: str
    hits: int
    items: list[dict]
    truncated: bool = False

    @property
    def complete(self) -> bool:
        return not self.truncated


# ----------------------------------------------------------------- adapter


class CompaniesHouseAdapter:
    """The sweep. One request per (window, SIC tier), plus halving on truncation."""

    key = "companies_house"
    kind: SourceKind = "registry"
    schedule = "daily"
    requires_browser = False
    # 04-sources §2, row 1. The register IS Track B — the whole reason version 2
    # exists. Left to the "A" default, the Sources tab tells Aryan his edge
    # comes from the same signal-first places version 1 used.
    track = "B"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        days_back: int | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
        size: int = DEFAULT_SIZE,
        tiers: Mapping[str, Sequence[str]] | None = None,
        denylist: Iterable[str] = SIC_DENYLIST,
        base_url: str = CH_API_BASE,
    ) -> None:
        self.api_key = api_key
        self.days_back = days_back
        self.window_days = max(int(window_days), 1)
        self.size = int(size)
        self.tiers = dict(tiers or SIC_TIERS)
        self.denylist = frozenset(denylist)
        self.base_url = base_url.rstrip("/")
        self.stats: dict[str, int] = self._blank_stats()

    # ------------------------------------------------------------- protocol

    def fetch(self, ctx: FetchContext) -> Iterator[RawItem]:
        http = ctx.http
        api_key = self.api_key or _api_key_from_ctx(ctx) or api_key_from_env()
        if not api_key:
            raise SourceError(
                self.key,
                "no API key — set CH_API_KEY (HTTP Basic: key as username, empty password)",
            )

        today = ctx.now or date.today()
        days_back = self._days_back(ctx)
        earliest = today - timedelta(days=days_back)
        self.stats = self._blank_stats()

        seen: set[str] = set()
        for start, end in date_windows(days_back, self.window_days, today):
            self.stats["windows"] += 1
            for tier, codes in self.tiers.items():
                for page in self._sweep_window(http, api_key, start, end, tier, tuple(codes),
                                               self.window_days):
                    self.stats["pages"] += 1
                    if page.truncated:
                        self.stats["truncated_pages"] += 1
                    for raw in page.items:
                        item = self._to_raw_item(raw, tier, earliest, today)
                        if item is None:
                            continue
                        if item.external_id in seen:
                            self.stats["dropped_duplicate"] += 1
                            continue
                        seen.add(item.external_id)
                        self.stats["yielded"] += 1
                        yield item

    # -------------------------------------------------------------- private

    @staticmethod
    def _blank_stats() -> dict[str, int]:
        return {
            "windows": 0,
            "pages": 0,
            "truncated_pages": 0,
            "raw_items": 0,
            "dropped_denylist": 0,
            "dropped_out_of_window": 0,
            "dropped_duplicate": 0,
            "placeholder_names": 0,
            "yielded": 0,
        }

    def _days_back(self, ctx: FetchContext) -> int:
        if self.days_back is not None:
            return int(self.days_back)
        extra = getattr(ctx, "extra", None) or {}
        if "days_back" in extra:
            return int(extra["days_back"])
        explicit = getattr(ctx, "days_back", None)
        if explicit is not None:
            return int(explicit)
        if ctx.since is not None:
            today = ctx.now or date.today()
            return max((today - ctx.since).days, 0)
        settings = getattr(getattr(ctx, "config", None), "settings", None)
        return int(getattr(settings, "ch_daily_window_days", 10) or 10)

    def _sweep_window(
        self,
        http: Any,
        api_key: str,
        start: date,
        end: date,
        tier: str,
        codes: tuple[str, ...],
        window_days: int,
    ) -> Iterator[SweepPage]:
        hits, items = self._request(http, api_key, start, end, codes)

        if hits <= len(items):
            yield SweepPage(start, end, tier, hits, items)
            return

        # MANDATORY truncation check (04-sources §3.2). `hits > len(items)` means
        # the API quietly dropped rows; re-query with a halved window.
        if start == end:
            # ponytail: a single day that still overflows `size` cannot be split
            # further on the date axis. Rather than lose companies silently we
            # surface it — the caller/digest must treat this as a real failure.
            log.error(
                "companies_house: %s tier=%s still truncated at a 1-day window "
                "(hits=%s items=%s) — increase `size` or split the SIC tier",
                start.isoformat(), tier, hits, len(items),
            )
            yield SweepPage(start, end, tier, hits, items, truncated=True)
            return

        halved = max(window_days // 2, 1)
        log.info(
            "companies_house: truncation at %s..%s tier=%s (hits=%s items=%s) "
            "— halving window to %s days",
            start.isoformat(), end.isoformat(), tier, hits, len(items), halved,
        )
        for sub_start, sub_end in split_window(start, end, halved):
            if (sub_start, sub_end) == (start, end):
                # Halving produced the same window; step down to single days so
                # the recursion always makes progress.
                for day_start, day_end in split_window(start, end, 1):
                    yield from self._sweep_window(http, api_key, day_start, day_end,
                                                  tier, codes, 1)
                return
            yield from self._sweep_window(http, api_key, sub_start, sub_end, tier,
                                          codes, halved)

    def _request(
        self, http: Any, api_key: str, start: date, end: date, codes: tuple[str, ...]
    ) -> tuple[int, list[dict]]:
        params = {
            "incorporated_from": start.isoformat(),
            "incorporated_to": end.isoformat(),
            "sic_codes": ",".join(codes),
            "company_status": "active",
            "company_type": "ltd",
            "size": self.size,
        }
        # HTTP Basic auth: API key as username, empty password (04-sources §3.1).
        # ponytail: robots.txt is deliberately skipped for the authenticated API
        # host — it is a contracted API with published terms, not a crawl, and
        # fetching /robots.txt would spend a request from the 600/5min budget.
        resp = http.get(
            f"{self.base_url}/advanced-search/companies",
            params=params,
            auth=(api_key, ""),
            check_robots=False,
        )
        if resp.status == 401:
            raise SourceError(self.key, "401 — bad API key (key is the USERNAME, password empty)")
        if resp.status == 429:
            raise SourceError(self.key, "429 — rate limit breached; back off before retrying")
        if not resp.ok:
            raise SourceError(self.key, f"HTTP {resp.status} from advanced-search")

        data = resp.json()
        if not isinstance(data, dict) or ("items" not in data and "hits" not in data):
            shape = sorted(data)[:8] if isinstance(data, dict) else type(data).__name__
            raise LayoutChanged(
                self.key,
                f"advanced-search response carries neither `items` nor `hits` ({shape})",
            )

        items = data.get("items") or []
        if not isinstance(items, list):
            raise LayoutChanged(
                self.key, f"`items` is not an array, got {type(items).__name__}")

        hits = data.get("hits")
        hits = len(items) if hits is None else int(hits)

        if hits > 0 and not items:
            # 200 OK, the API says there are matches, and the array we know how
            # to read is empty: the response shape moved. Falling through would
            # send `_sweep_window` recursing over empty pages down to one-day
            # windows and then report a quiet week — the exact silent failure
            # 04-sources §3.2 exists to prevent.
            raise LayoutChanged(
                self.key,
                f"{hits} hits but 0 items for {start.isoformat()}..{end.isoformat()} "
                "— the advanced-search payload changed shape",
            )
        return hits, list(items)

    def _to_raw_item(
        self, raw: dict, tier: str, earliest: date, today: date
    ) -> RawItem | None:
        self.stats["raw_items"] += 1

        number = normalise_ch_number(raw.get("company_number"))
        name = (raw.get("company_name") or "").strip()
        if not number or not name:
            return None

        created = parse_iso_date(raw.get("date_of_creation"))
        if created is None or not (earliest <= created <= today):
            # THE guarantee: nothing outside the requested window ever escapes.
            self.stats["dropped_out_of_window"] += 1
            return None

        sic_codes = [str(c).strip() for c in (raw.get("sic_codes") or []) if str(c).strip()]
        if is_denylisted_only(sic_codes, self.denylist):
            # 04-sources §3.4 #1 — dropped before it costs an enrichment request.
            self.stats["dropped_denylist"] += 1
            return None

        address = raw.get("registered_office_address") or {}
        placeholder = is_placeholder_name(name)
        if placeholder:
            self.stats["placeholder_names"] += 1

        links = raw.get("links") or {}
        structured = {
            "company_number": number,
            "company_name": name,
            "norm_key": norm_key(name),
            "company_status": raw.get("company_status"),
            "company_type": raw.get("company_type"),
            "date_of_creation": created.isoformat(),
            "sic_codes": sic_codes,
            "sic_tier": tier,
            "postal_code": (address.get("postal_code") or "").strip() or None,
            "locality": (address.get("locality") or "").strip() or None,
            "address_country": (address.get("country") or "").strip() or None,
            "links_self": links.get("company_profile") or links.get("self"),
            "placeholder_name": placeholder,
        }
        return RawItem(
            source_key=self.key,
            source_url=CH_PROFILE_URL.format(number),
            external_id=number,
            published_at=created,
            title=name,
            structured=structured,
            kind_hint="incorporation",
        )


def _api_key_from_ctx(ctx: FetchContext) -> str | None:
    extra = getattr(ctx, "extra", None) or {}
    for key in ("ch_api_key", "api_key"):
        if extra.get(key):
            return str(extra[key])
    settings = getattr(getattr(ctx, "config", None), "settings", None)
    for key in ("ch_api_key", "companies_house_api_key"):
        value = getattr(settings, key, None)
        if value:
            return str(value)
    return None


def sweep(
    http: Any,
    *,
    api_key: str,
    days_back: int,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    config: Any = None,
    size: int = DEFAULT_SIZE,
) -> Iterator[RawItem]:
    """Convenience wrapper: the sweep without building a `FetchContext` by hand."""
    adapter = CompaniesHouseAdapter(
        api_key=api_key, days_back=days_back, window_days=window_days, size=size
    )
    ctx = FetchContext(http=http, config=config, now=today or date.today())
    yield from adapter.fetch(ctx)
