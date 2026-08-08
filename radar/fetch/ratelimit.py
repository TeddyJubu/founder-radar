"""Per-host token bucket, plus the hard Companies House window guard.

Two different limits, deliberately kept separate:

* a polite per-host rate (robots `Crawl-delay`, 1 s floor even when a site is
  silent), and
* Companies House's contractual **600 requests per rolling 5 minutes** — going
  over returns 429 and repeated breaches ban the application, so this one is a
  hard block, not a smoothing function.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class TokenBucket:
    """Classic bucket. `rate` tokens per second, burst up to `capacity`."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(rate, 1.0)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self, tokens: float = 1.0, *, sleep=time.sleep) -> float:
        """Block until `tokens` are available. Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = (tokens - self._tokens) / self.rate
            sleep(deficit)
            waited += deficit


class WindowLimiter:
    """At most `limit` events in any rolling `window` seconds. Hard, not smoothed."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] >= self.window:
            self._events.popleft()

    def count(self, *, now: float | None = None) -> int:
        with self._lock:
            t = time.monotonic() if now is None else now
            self._prune(t)
            return len(self._events)

    def take(self, *, sleep=time.sleep) -> float:
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                if len(self._events) < self.limit:
                    self._events.append(now)
                    return waited
                delay = self.window - (now - self._events[0]) + 0.001
            sleep(delay)
            waited += delay


class RateLimiter:
    """Per-host buckets with a default polite rate and named hard windows."""

    #: Companies House: 600 requests / 5 minutes (04-sources §3.1).
    CH_HOST = "api.company-information.service.gov.uk"

    def __init__(self, default_delay: float = 1.0) -> None:
        self.default_delay = default_delay
        self._buckets: dict[str, TokenBucket] = {}
        self._windows: dict[str, WindowLimiter] = {
            self.CH_HOST: WindowLimiter(600, 300.0),
        }
        self._lock = threading.Lock()

    def set_delay(self, host: str, delay: float) -> None:
        """Honour robots Crawl-delay, with a 1 s floor."""
        delay = max(delay, 1.0)
        with self._lock:
            self._buckets[host] = TokenBucket(1.0 / delay, capacity=1.0)

    def bucket(self, host: str) -> TokenBucket:
        with self._lock:
            if host not in self._buckets:
                self._buckets[host] = TokenBucket(1.0 / self.default_delay, capacity=1.0)
            return self._buckets[host]

    def acquire(self, host: str, *, sleep=time.sleep) -> float:
        waited = self.bucket(host).take(sleep=sleep)
        window = self._windows.get(host)
        if window is not None:
            waited += window.take(sleep=sleep)
        return waited

    def window_count(self, host: str) -> int:
        window = self._windows.get(host)
        return window.count() if window else 0
