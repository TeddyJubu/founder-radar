"""One HTTP client for the whole system: retries, timeouts, conditional GET.

Every outbound request goes through here so politeness and rate limiting cannot
be forgotten in an adapter. Retries use full jitter and honour `Retry-After`.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
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


_PLACEHOLDER_HOST = re.compile(r"(?<![\w.-])example\.(?:com|org|net)\b", re.I)


def user_agent_is_placeholder(agent: str | None = None) -> bool:
    """True while the contact details are the template's `example.com` ones.

    The point of an honest User-Agent is that a webmaster who is being
    crawled can write to someone. A placeholder address reaches nobody, and
    the sites that block the crawler (403 on every request) have no way to
    ask for an allowlisting — so `doctor` says so instead of it staying a
    silent default.
    """
    return bool(_PLACEHOLDER_HOST.search(agent if agent is not None else user_agent()))


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
        timeout: float | None = None,
        max_retries: int | None = None,
        follow_redirects: bool | None = None,
        max_bytes: int | None = None,
    ) -> Response:
        host = urlsplit(url).netloc

        if check_robots if check_robots is not None else self.obey_robots:
            if not self.robots.allowed(url, user_agent()):
                raise RobotsDenied(url)
            delay = self.robots.crawl_delay(url, user_agent())
            if delay:
                self.limiter.set_delay(host, delay)

        retries = self.max_retries if max_retries is None else max_retries
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self.limiter.acquire(host, sleep=self._sleep)
            try:
                self.request_count += 1
                options = dict(params=params, headers=dict(headers or {}), auth=auth,
                               timeout=self.timeout if timeout is None else timeout,
                               follow_redirects=self._client.follow_redirects if follow_redirects is None else follow_redirects)
                if max_bytes is None:
                    r = self._client.get(url, **options)
                else:
                    if max_bytes <= 0:
                        raise ValueError('max_bytes must be positive')
                    with self._client.stream('GET', url, **options) as streamed:
                        chunks, size = [], 0
                        for chunk in streamed.iter_bytes(chunk_size=16_384):
                            size += len(chunk)
                            if size > max_bytes:
                                raise ValueError('response exceeds byte limit')
                            chunks.append(chunk)
                        # iter_bytes already decodes gzip/deflate. Reconstructing
                        # with Content-Encoding would decode the plain body again.
                        decoded_headers = dict(streamed.headers)
                        decoded_headers.pop('content-encoding', None)
                        decoded_headers.pop('content-length', None)
                        r = httpx.Response(streamed.status_code, headers=decoded_headers,
                                           content=b''.join(chunks), request=streamed.request)
            except httpx.HTTPError as exc:      # transport failure
                last_exc = exc
                if attempt == retries:
                    raise
                self._backoff(attempt, None)
                continue

            if r.status_code in RETRY_STATUS and attempt < retries:
                self._backoff(attempt, r.headers.get("Retry-After"))
                continue

            # noindex is a property of a page that was served. Error pages
            # carry it too — Cloudflare's 403 challenge, most 404 templates —
            # and reading theirs reported "HTML meta noindex" for what was
            # really a refusal or a moved page (Outward, Mercia and Sheffield,
            # Oct 2026). Non-2xx answers go back to the adapter as statuses.
            if 200 <= r.status_code < 300:
                from radar.sources._common import header_noindex, meta_noindex
                if header_noindex(r.headers):
                    raise RobotsDenied(f"X-Robots-Tag noindex: {r.url}")
                if "html" in r.headers.get("content-type", "").lower():
                    from selectolax.parser import HTMLParser
                    if meta_noindex(HTMLParser(r.text)):
                        raise RobotsDenied(f"HTML meta noindex: {r.url}")
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
