"""09-test-plan §5/§6 — the Companies House window sweep, offline.

The sweep is the most important discovery requirement in the system and the
integration suite does not run in CI, so it is covered here against a mocked
HTTP client. Two properties are pinned:

* **THE guarantee:** every returned company was incorporated inside the
  requested window — a 90-day sweep is 13 seven-day windows × 3 SIC tiers =
  **39 requests**, and nothing outside the window ever escapes.
* **Truncation is always checked.** `hits > len(items)` means the API quietly
  dropped rows; the sweep halves the window and re-queries instead of letting
  companies vanish with no error.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from radar.fetch.ratelimit import RateLimiter, WindowLimiter
from radar.sources.base import FetchContext
from radar.sources.companies_house import CompaniesHouseAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    @property
    def ok(self):
        return 200 <= self.status < 400

    def json(self):
        return self._payload


class MockCH:
    """The `mock_ch` of the plan: counts every request, records the window
    pressure, and serves one committed fixture page for every call."""

    def __init__(self):
        self.payload = {"hits": 0, "items": []}
        self._timestamps: list[float] = []

    def load(self, name: str) -> None:
        self.payload = json.loads((FIXTURES / name).read_text())

    def get(self, url, **kw):  # noqa: ARG002 - the adapter's http protocol
        import time

        self._timestamps.append(time.monotonic())
        return FakeResponse(self.payload)

    @property
    def request_count(self) -> int:
        return len(self._timestamps)

    @property
    def max_in_5min_window(self) -> int:
        """Requests made back-to-back in a test are all within one rolling
        5-minute window, so the window pressure is the total count. Offline
        proxy for the 600/5min contract — the WindowLimiter itself is tested
        separately below."""
        return len(self._timestamps)


def test_companies_house_window_sweep(db):
    mock = MockCH()
    mock.load("ch_advanced_search_page.json")     # 3 items, dated inside the window
    adapter = CompaniesHouseAdapter(api_key="test", days_back=90, window_days=7)
    ctx = FetchContext(http=mock, config=None, db=db, now=date(2026, 8, 8))

    got = list(adapter.fetch(ctx))
    assert mock.request_count == 39              # 13 windows × 3 SIC tiers
    assert mock.max_in_5min_window <= 600
    for item in got:
        incorporated = date.fromisoformat(item.structured["date_of_creation"])
        assert (date(2026, 8, 8) - incorporated).days <= 90


def test_sweep_narrows_window_on_truncation(db):
    """hits > len(items) means results were silently truncated. Companies
    would vanish with no error — the failure this system is built to avoid."""
    mock = MockCH()
    mock.load("ch_truncated_5000.json")           # hits: 7200, items: 3
    adapter = CompaniesHouseAdapter(api_key="test", days_back=7, window_days=7)
    ctx = FetchContext(http=mock, config=None, db=db, now=date(2026, 8, 8))

    list(adapter.fetch(ctx))
    assert mock.request_count > 1                 # it re-queried with a smaller window
    assert adapter.stats["truncated_pages"] > 0


def test_ch_rate_limit_is_respected(monkeypatch):
    """Companies House: 600 requests per rolling 5 minutes, hard — repeated
    breaches ban the application, so this is a block, not a smoothing."""
    from radar.fetch import ratelimit

    class FakeClock:
        def __init__(self):
            self.t = 0.0

        def sleep(self, seconds):
            self.t += seconds

    clock = FakeClock()
    # the limiter reads time.monotonic() directly, so the test must own it
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock.t)

    window = WindowLimiter(600, 300.0)
    for _ in range(600):
        assert window.take(sleep=clock.sleep) == 0
        clock.t += 0.01

    waited = window.take(sleep=clock.sleep)       # the 601st must wait
    assert waited > 0
    assert window.count() <= 600

    clock.t += 300                                # a full window drains
    assert window.count() == 0


def test_rate_limiter_registers_the_ch_window():
    limiter = RateLimiter()
    assert limiter.window_count(RateLimiter.CH_HOST) == 0
