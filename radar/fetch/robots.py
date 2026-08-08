"""robots.txt via Protego, cached 24 h, fail-closed on 5xx.

Protego rather than the stdlib `robotparser`: the stdlib implements a 1996
draft with no wildcard support and would mis-parse `Disallow: /*?`, which UKTN
actually uses (02-architecture §9). Getting that wrong means appending a query
string to a UKTN URL and crawling a path the site has explicitly refused.

Fail-closed on 5xx is deliberate: a site whose robots.txt is erroring has not
granted permission, and guessing in our own favour is the wrong default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

CACHE_TTL = 24 * 3600.0
CRAWL_DELAY_FLOOR = 1.0


@dataclass
class RobotsEntry:
    parser: object | None       # protego.Protego, or None when unavailable
    allow_all: bool             # used when robots.txt is 404 / empty
    fetched_at: float
    reachable: bool = True


def robots_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))


class RobotsCache:
    def __init__(self, client: httpx.Client | None = None, *, ttl: float = CACHE_TTL,
                 clock=time.monotonic) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, RobotsEntry] = {}

    # ---------------------------------------------------------------- public

    def allowed(self, url: str, agent: str) -> bool:
        entry = self._entry(url)
        if not entry.reachable:
            return False                       # fail-closed
        if entry.allow_all or entry.parser is None:
            return True
        return bool(entry.parser.can_fetch(url, agent))

    def crawl_delay(self, url: str, agent: str) -> float:
        entry = self._entry(url)
        if entry.parser is None:
            return CRAWL_DELAY_FLOOR
        delay = entry.parser.crawl_delay(agent)
        return max(float(delay or 0.0), CRAWL_DELAY_FLOOR)

    def preload(self, host_url: str, body: str) -> None:
        """Inject a robots body — used by fixtures so tests stay offline."""
        self._cache[urlsplit(host_url).netloc] = RobotsEntry(
            parser=self._parse(body), allow_all=not body.strip(),
            fetched_at=self._clock(), reachable=True)

    # --------------------------------------------------------------- private

    def _parse(self, body: str):
        try:
            from protego import Protego
        except ImportError:                    # pragma: no cover - dep missing
            return None
        return Protego.parse(body)

    def _entry(self, url: str) -> RobotsEntry:
        host = urlsplit(url).netloc
        entry = self._cache.get(host)
        if entry is not None and self._clock() - entry.fetched_at < self._ttl:
            return entry

        if self._client is None:
            entry = RobotsEntry(None, allow_all=True, fetched_at=self._clock())
        else:
            try:
                r = self._client.get(robots_url(url), timeout=10.0)
            except httpx.HTTPError:
                entry = RobotsEntry(None, False, self._clock(), reachable=False)
            else:
                if r.status_code >= 500:
                    entry = RobotsEntry(None, False, self._clock(), reachable=False)
                elif r.status_code >= 400:
                    # No robots.txt published means no restriction.
                    entry = RobotsEntry(None, allow_all=True, fetched_at=self._clock())
                else:
                    entry = RobotsEntry(self._parse(r.text), allow_all=not r.text.strip(),
                                        fetched_at=self._clock())

        self._cache[host] = entry
        return entry
