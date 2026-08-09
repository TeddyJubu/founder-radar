"""The source registry, the per-source isolation wrapper, and `sources --*`.

Three things live here and nothing else:

* **`REGISTRY`** — `{key: adapter}`. Every import is lazy, so a half-built
  adapter, a missing optional dependency, or a module another agent has not
  finished writing yet costs exactly one entry, not the whole CLI. Adding a
  source really is one line (02-architecture §4).
* **`fetch_all`** — stage ② of 05-pipeline: run every enabled adapter *in
  isolation*, record health, never let one failure end a run. This is the
  behaviour `test_one_source_failure_does_not_stop_run` exists to pin.
* **`cli_sources`** — what `founder-radar sources --list/--test/--sniff` calls.

The pipeline never asks "which source is this from?" when deciding anything.
Source identity affects trust weighting and Discovery Edge, both table lookups.
"""

from __future__ import annotations

import importlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urljoin, urlsplit

from radar.sources.base import FetchContext, RawItem, SourceBlocked, SourceError

# Declaration order is fetch order. Track B first: Companies House has the
# tightest rate-limit budget and the most valuable output, and news sources are
# the most likely to be slow (05-pipeline §2).
SOURCE_MODULES: dict[str, str] = {
    "companies_house": "radar.sources.companies_house",
    "oxford_innovation": "radar.sources.oxford_innovation",
    "northern_accelerator": "radar.sources.northern_accelerator",
    "cambridge_enterprise": "radar.sources.cambridge_enterprise",
    "conception_x": "radar.sources.conception_x",
    "entrepreneur_first": "radar.sources.entrepreneur_first",
    "zinc_vc": "radar.sources.zinc_vc",
    "govuk_search": "radar.sources.govuk_search",
    "ukri_gtr": "radar.sources.ukri_gtr",
    "innovate_uk": "radar.sources.innovate_uk",
    "businesscloud": "radar.sources.businesscloud",
    "uktn": "radar.sources.uktn",
    "vc_portfolios": "radar.sources.vc_portfolios",
}

#: 04-sources Tier 1 — the fourteen that have to work. `TIER_1_SOURCES` is
#: written out rather than derived from `SOURCE_MODULES`, because the moment
#: Tier 2 entries joined the registry the derived version quietly promoted
#: them and the Monday live check started gating on sources that are allowed
#: to be flaky.
TIER_1_SOURCES: tuple[str, ...] = tuple(SOURCE_MODULES)

#: 04-sources Tier 2 — "add after Tier 1 is proven". Quality and volume, not
#: freshness: none of these is on the critical path, and each one failing is a
#: row on the Sources tab rather than a problem.
TIER_2_MODULES: dict[str, str] = {
    "startups_magazine": "radar.sources.startups_magazine",
    "bdaily_regional": "radar.sources.bdaily_regional",
    "edinburgh_innovations": "radar.sources.edinburgh_innovations",
    "ucl_ventures": "radar.sources.ucl_ventures",
    "bethnal_green": "radar.sources.bethnal_green",
    "carbon13": "radar.sources.carbon13",
    "converge": "radar.sources.converge",
    "sheffield": "radar.sources.sheffield",
    "founders_factory": "radar.sources.founders_factory",
    "techstars_london": "radar.sources.techstars_london",
}

TIER_2_SOURCES: tuple[str, ...] = tuple(TIER_2_MODULES)

SOURCE_MODULES.update(TIER_2_MODULES)

ALL_SOURCES: tuple[str, ...] = TIER_1_SOURCES + TIER_2_SOURCES


class SourceUnavailable(KeyError):
    """The adapter module could not be imported. One entry, not a broken CLI."""


def _adapter_from_module(module, key: str):
    """Find the adapter without demanding a naming convention.

    `ADAPTER` first, then any object in the module whose `key` matches and
    which has a `fetch`. Companies House is being written by another agent
    right now; guessing its class name would be a needless coupling.
    """
    candidate = getattr(module, "ADAPTER", None)
    if candidate is not None and getattr(candidate, "key", None) == key:
        return candidate
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if getattr(obj, "key", None) != key or not hasattr(obj, "fetch"):
            continue
        return obj() if isinstance(obj, type) else obj
    if candidate is not None:
        return candidate
    raise SourceUnavailable(f"{module.__name__} defines no adapter with key {key!r}")


class _Registry(Mapping):
    """`{key: adapter}` with lazy, individually-failing imports."""

    def __init__(self, modules: Mapping[str, str]) -> None:
        self._modules = dict(modules)
        self._cache: dict[str, Any] = {}
        self._errors: dict[str, str] = {}

    def __getitem__(self, key: str):
        if key in self._cache:
            return self._cache[key]
        if key not in self._modules:
            raise KeyError(key)
        try:
            module = importlib.import_module(self._modules[key])
            adapter = _adapter_from_module(module, key)
        except Exception as exc:                         # noqa: BLE001
            self._errors[key] = f"{type(exc).__name__}: {exc}"
            raise SourceUnavailable(f"{key} is not available — {exc}") from exc
        self._errors.pop(key, None)
        self._cache[key] = adapter
        return adapter

    def __iter__(self) -> Iterator[str]:
        return iter(self._modules)

    def __len__(self) -> int:
        return len(self._modules)

    # -- convenience beyond Mapping ---------------------------------------

    def available(self) -> dict[str, Any]:
        """Only the adapters that actually import. Never raises."""
        out = {}
        for key in self._modules:
            try:
                out[key] = self[key]
            except SourceUnavailable:
                continue
        return out

    def error(self, key: str) -> str | None:
        return self._errors.get(key)

    def register(self, key: str, module_path: str) -> None:
        self._modules[key] = module_path
        self._cache.pop(key, None)


#: The one line a new source has to touch.
REGISTRY = _Registry(SOURCE_MODULES)


# --------------------------------------------------------------- stage ② glue


@dataclass
class SourceRun:
    key: str
    status: str                 # ok | failed | skipped | disabled | layout_changed | degraded
    items: int = 0
    duration_ms: int = 0
    error: str | None = None
    warning: str | None = None


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    sources: list[SourceRun] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(s.status in {"failed", "layout_changed"} for s in self.sources):
            return "partial"
        return "ok"

    def source(self, key: str) -> SourceRun | None:
        return next((s for s in self.sources if s.key == key), None)

    def summary(self) -> dict:
        return {
            "items": len(self.items),
            "status": self.status,
            "sources": [vars(s) for s in self.sources],
        }


def enabled_adapters(cfg: Any, keys: Sequence[str] | None = None) -> list[Any]:
    """Registry order, filtered by the `Sources` sheet tab and by `--source`."""
    wanted = set(keys) if keys else None
    disabled = {
        s.key for s in getattr(cfg, "sources", []) or [] if not getattr(s, "enabled", True)
    }
    out = []
    for key, adapter in REGISTRY.available().items():
        if wanted is not None and key not in wanted:
            continue
        if key in disabled:
            continue
        out.append(adapter)
    return out


def fetch_all(
    ctx: FetchContext,
    adapters: Iterable[Any] | None = None,
    *,
    db: Any | None = None,
    keys: Sequence[str] | None = None,
    run_id: int | None = None,
    observed_on: date | None = None,
) -> FetchResult:
    """Stage ②. One broken source never stops a run — that is the whole point.

    Every adapter is wrapped individually. Exceptions become a `failed` row and
    a log line; `LayoutChanged` becomes its own status so the `Sources` tab can
    distinguish "the site is down" from "the site moved". The item counts go to
    `source_health` either way, because the *dangerous* failure is the one that
    raises nothing at all and returns an empty list.
    """
    from radar.fetch.layout import LayoutChanged, check_health

    db = db if db is not None else ctx.db
    adapters = list(adapters) if adapters is not None else enabled_adapters(ctx.config, keys)
    result = FetchResult()

    for adapter in adapters:
        key = getattr(adapter, "key", adapter.__class__.__name__)
        started = time.monotonic()
        status, error, got = "ok", None, []
        try:
            got = list(adapter.fetch(ctx))
        except LayoutChanged as exc:
            status, error = "layout_changed", str(exc)
        except SourceBlocked as exc:
            # The site answered but refused the crawler (401/403/429/451) —
            # degraded, not failed: a block is usually fixable by allowlisting
            # and, left alone, is the early warning for a quietly dying source.
            status, error = "degraded", str(exc)
        except SourceError as exc:
            status, error = "failed", str(exc)
        except Exception as exc:                         # noqa: BLE001 — isolation
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        elapsed = int((time.monotonic() - started) * 1000)
        run = SourceRun(key=key, status=status, items=len(got),
                        duration_ms=elapsed, error=error)

        if db is not None:
            # `degraded` is its own health status, preserved through to the
            # Sources tab and the heartbeat's consecutive-blocked check;
            # everything else that is not ok is a failed day.
            health_status = status if status in ("ok", "degraded") else "failed"
            verdict = check_health(
                db, key, len(got),
                status=health_status,
                observed_on=observed_on, note=error,
            )
            run.warning = verdict.warning
            if run_id is not None:
                db.execute(
                    """INSERT INTO run_source(run_id, source_key, status, items,
                                              duration_ms, error)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(run_id, source_key) DO UPDATE SET
                         status=excluded.status, items=excluded.items,
                         duration_ms=excluded.duration_ms, error=excluded.error""",
                    (run_id, key, status, len(got), elapsed, error),
                )

        result.sources.append(run)
        result.items.extend(got)

    return result


# ------------------------------------------------------------------- sniffer


WP_PROBES = ("/wp-json/wp/v2/posts", "/wp-json/")
FEED_PROBES = ("/feed/", "/rss", "/atom.xml", "/index.xml", "/feed.xml")
MAP_PROBES = ("/sitemap.xml", "/news-sitemap.xml", "/sitemap_index.xml")

PLATFORM_MARKERS = (
    ("wordpress", re.compile(r'rel=["\']https://api\.w\.org/', re.I), "/wp-json/wp/v2/posts?per_page=100"),
    ("next_pages", re.compile(r'id=["\']__NEXT_DATA__["\']'), "parse <script id=__NEXT_DATA__>"),
    ("next_app", re.compile(r"self\.__next_f\.push"), "concatenate self.__next_f chunks"),
    ("nuxt", re.compile(r"window\.__NUXT__\s*="), "regex the JSON literal"),
    ("squarespace", re.compile(r"Static\.SQUARESPACE_CONTEXT"), "append ?format=json to any URL"),
    ("ghost", re.compile(r'<meta name="generator" content="Ghost', re.I), "/ghost/api/content/posts/?key=…"),
    ("webflow", re.compile(r"w-dyn-item"), "sitemap.xml + parse .w-dyn-item"),
    ("algolia", re.compile(r"algolia", re.I), "POST https://{APPID}-dsn.algolia.net/1/indexes/*/queries"),
    ("json_ld", re.compile(r'type=["\']application/ld\+json["\']'), "free structured data in <script>"),
)


def sniff(url: str, http: Any) -> dict:
    """The discovery ladder from 04-sources §4.4, in one command.

    Cheapest rungs first: robots `Sitemap:` lines, then the platform markers in
    the HTML, then live probes of the endpoints those markers imply. It exists
    so onboarding source fifteen is ten minutes rather than an afternoon in
    DevTools.
    """
    if not urlsplit(url).scheme:
        url = "https://" + url
    root = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    found: dict = {"url": url, "root": root, "platform": None,
                   "endpoints": [], "hints": [], "sitemaps": [], "errors": []}

    # Rung 1 — robots.txt sitemaps, free via Protego.
    try:
        entry = http.robots._entry(root + "/")          # noqa: SLF001 - same package intent
        parser = getattr(entry, "parser", None)
        if parser is not None and hasattr(parser, "sitemaps"):
            found["sitemaps"] = list(parser.sitemaps)
    except Exception as exc:                             # noqa: BLE001
        found["errors"].append(f"robots: {exc}")

    # Rung 2 — the page itself.
    body = ""
    try:
        resp = http.get(url)
        body = resp.text if resp.ok else ""
        if not resp.ok:
            found["errors"].append(f"GET {url} -> {resp.status}")
    except Exception as exc:                             # noqa: BLE001
        found["errors"].append(f"GET {url}: {exc}")

    for name, pattern, hint in PLATFORM_MARKERS:
        if pattern.search(body):
            found["hints"].append({"platform": name, "how": hint})
            if found["platform"] is None and name != "json_ld":
                found["platform"] = name

    # Rung 3 — probe the endpoints the markers imply, cheapest first.
    probes = list(WP_PROBES) + list(FEED_PROBES) + list(MAP_PROBES)
    for path in probes:
        candidate = urljoin(root, path)
        try:
            resp = http.get(candidate)
        except Exception as exc:                         # noqa: BLE001
            found["errors"].append(f"{path}: {exc}")
            continue
        if not resp.ok:
            continue
        kind = "json" if path.startswith("/wp-json") else (
            "sitemap" if "sitemap" in path else "feed")
        found["endpoints"].append({
            "url": candidate, "kind": kind, "status": resp.status,
            "bytes": len(resp.text),
        })
        if kind == "json" and found["platform"] is None:
            found["platform"] = "wordpress"

    found["recommended"] = _recommend(found)
    return found


def _recommend(found: dict) -> str | None:
    posts = [e for e in found["endpoints"] if e["kind"] == "json" and "posts" in e["url"]]
    if posts:
        return posts[0]["url"]
    feeds = [e for e in found["endpoints"] if e["kind"] == "feed"]
    if feeds:
        return feeds[0]["url"]
    maps = [e for e in found["endpoints"] if e["kind"] == "sitemap"]
    if maps:
        return maps[0]["url"]
    return found["sitemaps"][0] if found["sitemaps"] else None


# ----------------------------------------------------------------------- CLI


def _http(existing: Any = None):
    if existing is not None:
        return existing
    from radar.fetch.http import HttpClient

    return HttpClient()


def _robots_verdict(http: Any, adapter: Any) -> dict:
    """What `--list` shows: may we crawl it, and how slowly."""
    url = getattr(adapter, "endpoint", None) or getattr(adapter, "homepage", "")
    if not url:
        return {"verdict": "unknown", "crawl_delay": None, "checked": None}
    try:
        from radar.fetch.http import user_agent

        agent = user_agent()
        allowed = http.robots.allowed(url, agent)
        delay = http.robots.crawl_delay(url, agent)
        return {
            "verdict": "allowed" if allowed else "disallowed",
            "crawl_delay": delay,
            "checked": url,
        }
    except Exception as exc:                             # noqa: BLE001
        return {"verdict": "unknown", "crawl_delay": None,
                "checked": url, "error": str(exc)}


def describe(key: str, adapter: Any, http: Any = None) -> dict:
    row = {
        "key": key,
        "kind": getattr(adapter, "kind", None),
        "schedule": getattr(adapter, "schedule", None),
        "track": getattr(adapter, "track", "A"),
        "endpoint": getattr(adapter, "endpoint", None),
        "requires_browser": bool(getattr(adapter, "requires_browser", False)),
        "available": True,
    }
    if http is not None:
        row["robots"] = _robots_verdict(http, adapter)
    return row


def cli_sources(
    db: Any,
    *,
    list_: bool = False,
    test_key: str | None = None,
    sniff_url: str | None = None,
    http: Any = None,
    ctx: FetchContext | None = None,
    config: Any = None,
    since: date | None = None,
    now: date | None = None,
) -> Any:
    """`founder-radar sources --list | --test <key> | --sniff <url>`.

    `http` is injectable for the same reason `HttpClient` takes a transport:
    the test suite must make zero real network calls, and a CLI entry point
    that can only be exercised live is a CLI entry point that rots.
    """
    from radar.fetch.http import user_agent

    if sniff_url:
        return sniff(sniff_url, _http(http))

    if test_key:
        client = _http(http)
        try:
            adapter = REGISTRY[test_key]
        except KeyError as exc:
            return {"key": test_key, "status": "unavailable",
                    "error": REGISTRY.error(test_key) or str(exc),
                    "known_keys": sorted(REGISTRY)}
        context = ctx or FetchContext(
            http=client, config=config, db=db,
            since=since, now=now or date.today(),
        )
        result = fetch_all(context, [adapter], db=db, observed_on=now)
        run = result.sources[0]
        return {
            "key": test_key,
            "status": run.status,
            "items": run.items,
            "duration_ms": run.duration_ms,
            "error": run.error,
            "warning": run.warning,
            "user_agent": user_agent(),
            "sample": [
                {
                    "title": item.title,
                    "url": item.source_url,
                    "external_id": item.external_id,
                    "published_at": item.published_at,
                    "structured": item.structured,
                }
                for item in result.items[:3]
            ],
        }

    # --list, and the default when no flag is given.
    client = _http(http)
    rows = []
    for key in REGISTRY:
        try:
            adapter = REGISTRY[key]
        except SourceUnavailable:
            rows.append({"key": key, "available": False,
                         "error": REGISTRY.error(key),
                         "robots": {"verdict": "unknown", "crawl_delay": None}})
            continue
        rows.append(describe(key, adapter, client))

    if db is not None:
        from radar.fetch.layout import health_report

        health = {h["source_key"]: h for h in health_report(db, [r["key"] for r in rows])}
        for row in rows:
            row["health"] = health.get(row["key"])

    if list_:
        return rows
    return {"user_agent": user_agent(), "sources": rows}


__all__ = [
    "FetchResult",
    "REGISTRY",
    "SOURCE_MODULES",
    "SourceRun",
    "SourceUnavailable",
    "TIER_1_SOURCES",
    "TIER_2_SOURCES",
    "ALL_SOURCES",
    "cli_sources",
    "describe",
    "enabled_adapters",
    "fetch_all",
    "sniff",
]
