"""One HTTP client for the whole system: retries, timeouts, conditional GET.

Every outbound request goes through here so politeness and rate limiting cannot
be forgotten in an adapter. Retries use full jitter and honour `Retry-After`.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from radar.fetch.ratelimit import RateLimiter
from radar.fetch.robots import RobotsCache

DEFAULT_UA = (
    "founder-radar/2.0 (+https://example.com/privacy-notice; contact@example.com)"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


def user_agent() -> str:
    """Honest UA with a working contact URL. Never disguise the crawler."""
    return os.environ.get("RADAR_USER_AGENT", DEFAULT_UA)


def sha256_text(text: str) -> str:
    """Hash the EXTRACTED text, never the raw HTML — raw HTML changes every load."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class RobotsDenied(Exception):
    """robots.txt disallows this URL. Not an error to retry — a decision."""


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: Mapping[str, str]
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class HttpClient:
    """Polite, retrying, robots-respecting HTTP.

    `transport` exists purely so tests can inject fixtures — the suite must make
    zero real network calls.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        limiter: RateLimiter | None = None,
        robots: RobotsCache | None = None,
        transport: httpx.BaseTransport | None = None,
        obey_robots: bool = True,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter or RateLimiter()
        self.obey_robots = obey_robots
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent()},
            transport=transport,
        )
        self.robots = robots if robots is not None else RobotsCache(self._client)
        self.request_count = 0

    # ---------------------------------------------------------------- public

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        check_robots: bool | None = None,
    ) -> Response:
        host = urlsplit(url).netloc

        if check_robots if check_robots is not None else self.obey_robots:
            if not self.robots.allowed(url, user_agent()):
                raise RobotsDenied(url)
            delay = self.robots.crawl_delay(url, user_agent())
            if delay:
                self.limiter.set_delay(host, delay)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire(host, sleep=self._sleep)
            try:
                self.request_count += 1
                r = self._client.get(url, params=params, headers=dict(headers or {}), auth=auth)
            except httpx.HTTPError as exc:      # transport failure
                last_exc = exc
                if attempt == self.max_retries:
                    raise
                self._backoff(attempt, None)
                continue

            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                self._backoff(attempt, r.headers.get("Retry-After"))
                continue

            return Response(str(r.url), r.status_code, r.text, dict(r.headers),
                            from_cache=r.status_code == 304)

        raise last_exc or RuntimeError(f"unreachable: {url}")

    def get_conditional(self, url: str, db, **kwargs) -> Response | None:
        """Conditional GET via `fetch_log`. Returns None when nothing changed.

        A 304, or a 200 whose *extracted* text hashes to the stored value, both
        mean "no new content" — the second case is what catches servers that
        never send an ETag.
        """
        row = db.one("SELECT etag, last_modified, content_sha256 FROM fetch_log WHERE url = ?", (url,))
        headers = dict(kwargs.pop("headers", {}) or {})
        if row:
            if row["etag"]:
                headers["If-None-Match"] = row["etag"]
            if row["last_modified"]:
                headers["If-Modified-Since"] = row["last_modified"]

        resp = self.get(url, headers=headers, **kwargs)
        if resp.status == 304:
            return None

        digest = sha256_text(resp.text)
        unchanged = bool(row) and row["content_sha256"] == digest
        db.execute(
            """INSERT INTO fetch_log(url, etag, last_modified, content_sha256, status, fetched_at)
               VALUES (?,?,?,?,?,datetime('now'))
               ON CONFLICT(url) DO UPDATE SET
                 etag=excluded.etag, last_modified=excluded.last_modified,
                 content_sha256=excluded.content_sha256, status=excluded.status,
                 fetched_at=excluded.fetched_at""",
            (url, resp.headers.get("etag"), resp.headers.get("last-modified"),
             digest, resp.status),
        )
        return None if unchanged else resp

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --------------------------------------------------------------- private

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                self._sleep(min(float(retry_after), 60.0))
                return
            except ValueError:
                pass
        # full jitter: uniform(0, 2**attempt), capped
        self._sleep(random.uniform(0, min(2 ** attempt, 30.0)))
