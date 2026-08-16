"""postcodes.io outcode → UK region, with a permanent local cache.

Three traps, all of them encoded here rather than in a comment somewhere else:

1. **`region`, `country` and `admin_district` come back as ARRAYS.** An outcode
   can straddle a boundary, so postcodes.io returns every value it touches.
   Take element `[0]` (03-data-model §3).
2. **`region` is populated for England only.** Scotland, Wales and Northern
   Ireland come back with an empty `region`, so the column MUST be nullable and
   `country` is what carries the answer there.
3. **Outcodes are stable**, so the cache is permanent — one lookup per outcode,
   ever. After the first run the second run should make almost no calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from radar.resolve.normalise import outcode_of

log = logging.getLogger(__name__)

POSTCODES_IO_BASE = "https://api.postcodes.io"
POSTCODES_IO_HOST = "api.postcodes.io"

#: ONS region name → the `geography` vocabulary in 03-data-model §4/§5.
REGION_TO_GEOGRAPHY: dict[str, str] = {
    "north east": "north_east",
    "yorkshire and the humber": "yorkshire",
    "london": "london",
}

UK_COUNTRIES = {"england", "scotland", "wales", "northern ireland"}

#: 03-data-model §5 — DSW's SEIS fund targets outside London–Oxbridge, and the
#: rule is an outcode-prefix check, never a fuzzy city-name match.
GOLDEN_TRIANGLE_PREFIXES = ("OX", "CB")


@dataclass(frozen=True, slots=True)
class PostcodeInfo:
    """What a postcode tells us. `region` is nullable; `country` is not."""

    outcode: str
    region: str | None
    country: str
    district: str | None = None
    from_cache: bool = False

    @property
    def geography(self) -> str | None:
        return region_to_geography(self.region, self.country)

    @property
    def in_golden_triangle(self) -> bool:
        return self.outcode.upper().startswith(GOLDEN_TRIANGLE_PREFIXES) or \
            self.geography == "london"


def _first(value: Any) -> str | None:
    """postcodes.io hands back arrays. Element [0], or None for an empty list."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        for entry in value:
            if entry not in (None, ""):
                return str(entry)
        return None
    text = str(value).strip()
    return text or None


def region_to_geography(region: str | None, country: str | None) -> str | None:
    """Map (region, country) onto the `geography` vocabulary.

    Scotland/Wales/NI have no `region`, so `country` decides — they are
    `uk_regions`, which is exactly what the fund criteria expect.
    """
    if region:
        return REGION_TO_GEOGRAPHY.get(region.strip().lower(), "uk_regions")
    if country and country.strip().lower() in UK_COUNTRIES:
        return "uk_regions"
    return None


def geography_enabled(geography: str | None, regions_enabled: Iterable[str]) -> bool:
    """`uk_wide` accepts everything.

    An unknown geography **passes and is flagged** rather than being dropped —
    the same NULL-passes policy the freshness gates use (06-scoring §1). Silently
    binning a company because postcodes.io was down is the failure mode to avoid.
    """
    enabled = {str(r).strip().lower() for r in (regions_enabled or [])}
    if not enabled or "uk_wide" in enabled:
        return True
    if geography is None:
        return True
    return geography in enabled


# ------------------------------------------------------------------- cache


def cached_outcode(db: Any, outcode: str) -> PostcodeInfo | None:
    row = db.one(
        "SELECT outcode, region, country, district FROM postcode_region WHERE outcode = ?",
        (outcode,),
    )
    if row is None:
        return None
    return PostcodeInfo(
        outcode=row["outcode"],
        region=row["region"],
        country=row["country"],
        district=row["district"],
        from_cache=True,
    )


def _store(db: Any, info: PostcodeInfo) -> None:
    db.execute(
        """INSERT INTO postcode_region(outcode, region, country, district, cached_at)
           VALUES (?,?,?,?,datetime('now'))
           ON CONFLICT(outcode) DO UPDATE SET
             region=excluded.region, country=excluded.country,
             district=excluded.district, cached_at=excluded.cached_at""",
        (info.outcode, info.region, info.country, info.district),
    )


def parse_outcode_response(payload: Any, outcode: str) -> PostcodeInfo | None:
    """Turn a postcodes.io `/outcodes/{outcode}` body into a `PostcodeInfo`."""
    if not isinstance(payload, dict):
        return None
    if int(payload.get("status") or 0) != 200:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None

    country = _first(result.get("country"))
    if not country:
        # `country` is NOT NULL in the cache table, and a row without it is
        # worthless — better to leave the outcode uncached than to invent 'GB'.
        return None
    return PostcodeInfo(
        outcode=(_first(result.get("outcode")) or outcode).upper(),
        region=_first(result.get("region")),          # England only — stays NULL elsewhere
        country=country,
        district=_first(result.get("admin_district")),
    )


def lookup_outcode(
    db: Any,
    http: Any,
    outcode: str,
    *,
    base_url: str = POSTCODES_IO_BASE,
) -> PostcodeInfo | None:
    """Cache-first outcode lookup. Cached forever — outcodes do not move."""
    outcode = (outcode or "").strip().upper()
    if not outcode:
        return None

    hit = cached_outcode(db, outcode)
    if hit is not None:
        return hit

    # ponytail: postcodes.io is a keyless public JSON API; robots.txt governs
    # crawlers, not documented API endpoints, and fetching it would double the
    # cold-cache call count. Politeness is handled by the per-host limiter.
    resp = http.get(f"{base_url.rstrip('/')}/outcodes/{outcode}", check_robots=False)
    if resp.status == 404:
        log.info("postcodes.io: unknown outcode %s", outcode)
        return None
    if not resp.ok:
        log.warning("postcodes.io: HTTP %s for %s", resp.status, outcode)
        return None

    try:
        info = parse_outcode_response(resp.json(), outcode)
    except ValueError:
        log.warning("postcodes.io: unparseable body for %s", outcode)
        return None
    if info is None:
        return None

    _store(db, info)
    return info


def resolve_postcode(
    db: Any, http: Any, postcode: Any, *, base_url: str = POSTCODES_IO_BASE
) -> PostcodeInfo | None:
    """Full postcode or outcode → region/country, via the permanent cache."""
    outcode = outcode_of(postcode)
    if outcode is None:
        return None
    return lookup_outcode(db, http, outcode, base_url=base_url)


def seed_cache(db: Any, rows: Iterable[tuple[str, str | None, str, str | None]]) -> None:
    """Pre-load the cache (bulk import, tests, or a restored backup)."""
    for outcode, region, country, district in rows:
        _store(db, PostcodeInfo(outcode.upper(), region, country, district))
