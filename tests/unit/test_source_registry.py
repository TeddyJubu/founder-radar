"""Phase 5's other done-criteria — the ones that are checkable offline.

`test_sources.py` proves the adapters parse. This file proves the things around
them that a broken deployment gets wrong quietly:

* `founder-radar sources --list` shows **every** source with a robots verdict,
  `--test <key>` runs one adapter and reports what it found, and `--sniff <url>`
  walks the discovery ladder (04-sources §4.4).
* robots.txt is obeyed — including `Crawl-delay`, which Entrepreneur First and
  Antler both set to 10 — and a 5xx robots.txt fails **closed**.
* the User-Agent is honest: a real product token, a working contact URL, and no
  browser impersonation, ever (04-sources §5).
* the VC portfolio denylist sets `on_vc_portfolio` on companies we already
  hold, and never creates one — reading those pages as a lead source is the
  version-1 behaviour the client rejected.

Everything here injects its HTTP client. The suite blocks sockets session-wide,
and a CLI entry point that can only be exercised live is one that rots.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from radar.fetch.http import DEFAULT_UA, HttpClient, RobotsDenied, user_agent
from radar.fetch.robots import RobotsCache
from radar.sources import REGISTRY, SOURCE_MODULES, cli_sources, sniff
from radar.sources import entrepreneur_first, vc_portfolios

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------- robots

JOINEF_ROBOTS = """User-agent: *
Disallow: /admin/
Crawl-delay: 10

Sitemap: https://www.joinef.com/sitemap.xml
"""

UKTN_ROBOTS = """User-agent: *
Disallow: /feed
Disallow: /*/feed
Disallow: /page/
Disallow: /*?

Sitemap: https://www.uktech.news/sitemap_index.xml
"""


def robots_cache(bodies: dict[str, str] | None = None) -> RobotsCache:
    """A cache with no client: unknown hosts are allowed, known hosts obeyed."""
    cache = RobotsCache(client=None)
    for host_url, body in (bodies or {}).items():
        cache.preload(host_url, body)
    return cache


class StubResponse:
    def __init__(self, text: str = "", status: int = 200) -> None:
        self.status = status
        self.text = text
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        import json

        return json.loads(self.text)


class StubHttp:
    """The `http` seam `cli_sources` takes. Serves committed bytes per path."""

    def __init__(self, body: str = "", *, routes: dict[str, str] | None = None,
                 robots: RobotsCache | None = None, status: int = 200) -> None:
        self.body = body
        self.routes = routes or {}
        self.status = status
        self.robots = robots if robots is not None else robots_cache()
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for fragment, body in self.routes.items():
            if fragment in url:
                return StubResponse(body)
        if self.routes and not self.body:
            return StubResponse("", status=404)
        return StubResponse(self.body, status=self.status)


def test_robots_crawl_delay_is_honoured_for_entrepreneur_first():
    """joinef.com publishes `Crawl-delay: 10`. Ten seconds a request is the
    reason this adapter fetches one page instead of walking pagination."""
    cache = robots_cache({"https://www.joinef.com/": JOINEF_ROBOTS})
    agent = user_agent()

    assert cache.allowed("https://www.joinef.com/portfolio/", agent) is True
    assert cache.crawl_delay("https://www.joinef.com/portfolio/", agent) == 10.0
    assert cache.allowed("https://www.joinef.com/admin/", agent) is False

    # A silent site still gets the 1 s politeness floor, never 0.
    assert robots_cache().crawl_delay("https://example.org/x", agent) == 1.0


def test_entrepreneur_first_adapter_asserts_the_delay_itself():
    """Belt and braces: a debugging client built with `obey_robots=False` must
    still wait 10 s on this host."""
    class Limiter:
        def __init__(self):
            self.delays = {}

        def set_delay(self, host, delay):
            self.delays[host] = delay

    class Http:
        def __init__(self):
            self.limiter = Limiter()

    http = Http()
    entrepreneur_first.ADAPTER.ensure_crawl_delay(http)
    assert entrepreneur_first.ADAPTER.crawl_delay == 10.0
    assert http.limiter.delays["www.joinef.com"] == 10.0
    assert http.limiter.delays["joinef.com"] == 10.0


def test_uktn_query_strings_are_disallowed_by_robots():
    """`Disallow: /*?` is why the UKTN adapter never appends a query string —
    and why Protego is used instead of the stdlib parser, which cannot read it."""
    cache = robots_cache({"https://www.uktech.news/": UKTN_ROBOTS})
    agent = user_agent()

    assert cache.allowed("https://www.uktech.news/wp-json/wp/v2/posts/latest", agent) is True
    assert cache.allowed(
        "https://www.uktech.news/wp-json/wp/v2/posts/latest?per_page=50", agent) is False
    assert cache.allowed("https://www.uktech.news/feed", agent) is False
    assert cache.allowed("https://www.uktech.news/page/2", agent) is False


@pytest.mark.parametrize("status,expected", [(200, False), (404, True), (503, False)])
def test_robots_fetch_failures_fail_closed(status, expected):
    """2xx → obey. 4xx → no robots.txt published, so nothing is restricted.
    5xx → the site has not granted permission; guessing in our own favour is
    the wrong default (04-sources §5)."""
    body = "User-agent: *\nDisallow: /\n" if status == 200 else "nope"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = RobotsCache(client)
    assert cache.allowed("https://example.org/anything", user_agent()) is expected


def test_http_client_refuses_a_disallowed_url_and_sends_the_honest_agent():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    cache = robots_cache({
        "https://www.joinef.com/": JOINEF_ROBOTS,
        "https://www.uktech.news/": UKTN_ROBOTS,
    })
    with HttpClient(transport=httpx.MockTransport(handler), robots=cache) as http:
        resp = http.get("https://www.joinef.com/portfolio/")
        assert resp.ok
        # Crawl-delay reached the limiter, not just the robots cache.
        assert http.limiter.bucket("www.joinef.com").rate == pytest.approx(0.1)

        with pytest.raises(RobotsDenied):
            http.get("https://www.uktech.news/wp-json/wp/v2/posts/latest?per_page=50")

    assert len(seen) == 1
    assert seen[0].headers["user-agent"] == user_agent()


# --------------------------------------------------------------- user agent


def test_user_agent_is_honest_and_carries_a_contact_url(monkeypatch):
    """04-sources §5: a real, working URL and a contact address, so a webmaster
    who wants us to stop can say so. Never impersonate a browser, never rotate."""
    monkeypatch.delenv("RADAR_USER_AGENT", raising=False)
    agent = user_agent()

    assert agent == DEFAULT_UA
    assert agent.startswith("founder-radar/")
    assert "+https://" in agent                       # a URL a human can open
    assert "@" in agent                               # and an address to write to
    url = agent.split("+", 1)[1].split(";", 1)[0]
    assert url.startswith("https://") and len(url.split("/")) > 3
    assert not any(token in agent for token in
                   ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko"))

    # Deployment overrides it with the real domain; the shape must survive.
    monkeypatch.setenv("RADAR_USER_AGENT",
                       "founder-radar/2.0 (+https://radar.example.co.uk/crawler; ops@example.co.uk)")
    assert "radar.example.co.uk" in user_agent()


# ------------------------------------------------------- sources --list/--test


def test_sources_list_shows_every_source_with_its_robots_verdict(db):
    """`founder-radar sources --list`. Every registered key appears — including
    one whose module fails to import, which must cost exactly one row."""
    http = StubHttp(robots=robots_cache({"https://www.joinef.com/": JOINEF_ROBOTS}))
    rows = cli_sources(db, list_=True, http=http)

    assert [r["key"] for r in rows] == list(SOURCE_MODULES)
    assert len(rows) == len(REGISTRY)
    for row in rows:
        assert "robots" in row, row["key"]
        assert row["robots"]["verdict"] in {"allowed", "disallowed", "unknown"}
        assert "health" in row
        if not row["available"]:
            # An adapter that will not import costs one row and an error, never
            # a broken command.
            assert row["error"], row["key"]

    by_key = {r["key"]: r for r in rows}
    ef = by_key["entrepreneur_first"]
    assert ef["available"] and ef["robots"]["verdict"] == "allowed"
    assert ef["robots"]["crawl_delay"] == 10.0
    assert ef["endpoint"] == "https://www.joinef.com/portfolio/"
    assert by_key["companies_house"]["available"]
    assert by_key["companies_house"]["kind"] == "registry"

    # Without --list the same call still reports the crawler's identity.
    summary = cli_sources(db, http=http)
    assert summary["user_agent"] == user_agent()
    assert len(summary["sources"]) == len(rows)


def test_cli_exposes_list_test_and_sniff(tmp_path, monkeypatch):
    """The three flags exist on the real command and reach `cli_sources` — the
    tests above drive the function directly, so this pins the wiring."""
    from click.testing import CliRunner

    import radar.sources as sources_pkg
    from radar.cli import cli

    seen: dict = {}

    def fake_cli_sources(db, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sources_pkg, "cli_sources", fake_cli_sources)
    runner = CliRunner()
    db_path = str(tmp_path / "radar.db")

    assert {p.name for p in cli.commands["sources"].params} == {
        "list_", "test_key", "sniff_url"}

    result = runner.invoke(cli, ["--db", db_path, "--json", "sources", "--list"])
    assert result.exit_code == 0, result.output
    assert seen == {"list_": True, "test_key": None, "sniff_url": None}

    runner.invoke(cli, ["--db", db_path, "sources", "--test", "uktn"])
    assert seen["test_key"] == "uktn"
    runner.invoke(cli, ["--db", db_path, "sources", "--sniff", "https://zinc.vc"])
    assert seen["sniff_url"] == "https://zinc.vc"


def test_sources_test_runs_one_adapter_and_reports_what_it_found(db):
    """`founder-radar sources --test <key>` — the command that answers "is this
    source still working?" without running the pipeline."""
    http = StubHttp(load("northern_accelerator.json"))
    result = cli_sources(db, test_key="northern_accelerator", http=http, now=date(2026, 8, 8))

    assert result["key"] == "northern_accelerator"
    assert result["status"] == "ok"
    assert result["items"] == 4
    assert result["error"] is None
    assert result["user_agent"] == user_agent()
    assert len(result["sample"]) == 3
    assert all(s["url"].startswith("https://") for s in result["sample"])

    # The run is recorded, so `--list` can show last success / 7-day average.
    health = db.one("SELECT items, status FROM source_health WHERE source_key = ?",
                    ("northern_accelerator",))
    assert health["items"] == 4 and health["status"] == "ok"


def test_fetch_all_records_a_blocked_source_as_degraded(db, config):
    """A WAF-style refusal (401/403/429/451) is `degraded`, not `failed`.

    The site is up, the crawler is not welcome, and a block is usually fixable
    by allowlisting — unlike an outage, and unlike a quiet week, it raises a
    real exception so it never needs the zero-streak machinery. This is the
    classification `require_ok` produces and the heartbeat's blocked-source
    check reads (02-architecture §7).
    """
    from radar.sources import fetch_all
    from radar.sources.base import FetchContext, SourceBlocked

    class BlockedAdapter:
        key = "blocked_test"
        kind = "news"
        schedule = "daily"
        requires_browser = False

        def fetch(self, ctx):  # noqa: ARG002 - the adapter protocol
            raise SourceBlocked(
                self.key, "HTTP 403 from https://example.org/feed — "
                "the site is refusing us (possible anti-bot block)")

    result = fetch_all(FetchContext(http=None, config=config),
                       adapters=[BlockedAdapter()], db=db)

    run = result.sources[0]
    assert run.status == "degraded"
    assert "403" in run.error
    assert result.status == "ok"          # a block is a warning, not an outage

    health = db.one(
        "SELECT status, note FROM source_health WHERE source_key = ?",
        ("blocked_test",))
    assert health["status"] == "degraded"
    assert "403" in health["note"]

    # a plain outage stays `failed` — the two must not blur together
    class DownAdapter(BlockedAdapter):
        key = "down_test"

        def fetch(self, ctx):  # noqa: ARG002
            raise ConnectionError("simulated outage")

    result2 = fetch_all(FetchContext(http=None, config=config),
                        adapters=[DownAdapter()], db=db)
    assert result2.sources[0].status == "failed"


def test_sources_test_surfaces_a_layout_change_instead_of_a_quiet_zero(db):
    """A source that returns 200 and nothing must not read as "0 items today"."""
    http = StubHttp(load("northern_accelerator_CHANGED.json"))
    result = cli_sources(db, test_key="northern_accelerator", http=http, now=date(2026, 8, 8))

    assert result["status"] == "layout_changed"
    assert result["items"] == 0
    assert "layout changed" in result["error"]


def test_sources_test_reports_an_unknown_key_without_crashing(db):
    result = cli_sources(db, test_key="not_a_source", http=StubHttp())
    assert result["status"] == "unavailable"
    assert "northern_accelerator" in result["known_keys"]


def test_sources_sniff_walks_the_discovery_ladder(db):
    """`founder-radar sources --sniff <url>` — 04-sources §4.4, in one command.
    Onboarding source fifteen should be ten minutes, not an afternoon."""
    homepage = (
        '<html><head><link rel="https://api.w.org/" href="/wp-json/" />'
        '<script type="application/ld+json">{"@type":"Organization"}</script>'
        "</head><body>hello</body></html>"
    )
    http = StubHttp(routes={
        "northernaccelerator.org/wp-json/wp/v2/posts": '[{"id":1}]',
        "northernaccelerator.org/wp-json/": '{"name":"Northern Accelerator"}',
        "northernaccelerator.org/feed/": "<rss></rss>",
        "northernaccelerator.org/sitemap.xml": "<urlset></urlset>",
    }, body=homepage, robots=robots_cache({
        "https://northernaccelerator.org/":
            "User-agent: *\nSitemap: https://northernaccelerator.org/sitemap.xml\n",
    }))

    found = cli_sources(db, sniff_url="northernaccelerator.org", http=http)

    assert found["url"].startswith("https://")
    assert found["platform"] == "wordpress"
    assert found["sitemaps"] == ["https://northernaccelerator.org/sitemap.xml"]
    assert {h["platform"] for h in found["hints"]} >= {"wordpress", "json_ld"}
    assert found["recommended"].endswith("/wp-json/wp/v2/posts")
    assert {e["kind"] for e in found["endpoints"]} == {"json", "feed", "sitemap"}


def test_sniff_survives_a_dead_site():
    """The discovery ladder is a diagnostic tool. It reports failures rather
    than raising, or it is useless on exactly the sites you need it for."""
    class Dead:
        robots = robots_cache()

        def get(self, url, **kwargs):
            raise ConnectionError("no route to host")

    found = sniff("https://example.invalid/portfolio", Dead())
    assert found["endpoints"] == []
    assert found["recommended"] is None
    assert any("no route to host" in e for e in found["errors"])


# ------------------------------------------------------------- the denylist


def test_vc_portfolio_denylist_populates_on_vc_portfolio(db):
    """A company on a tracked portfolio page has already been found by a fund.
    The denylist demotes what we already hold — it never creates a company,
    which is precisely the version-1 behaviour the client rejected."""
    from radar.resolve.normalise import norm_key
    from tests.factories import registry_company, store_company

    held = registry_company(canonical_name="Acme Robotics",
                            norm_key=norm_key("Acme Robotics"))
    store_company(db, held)
    unrelated = registry_company(canonical_name="Quantia", norm_key=norm_key("Quantia"))
    store_company(db, unrelated)

    items = vc_portfolios.ADAPTER.parse(load("vc_portfolios.html"))
    report = vc_portfolios.apply_denylist(db, items)

    assert report["listings"] == len(items)
    assert report["matched"] == ["Acme Robotics"]        # "Acme Robotics Ltd" matched
    assert report["companies_flagged"] == 1

    assert db.one("SELECT on_vc_portfolio FROM company WHERE id = ?",
                  (held.id,))["on_vc_portfolio"] == 1
    assert db.one("SELECT on_vc_portfolio FROM company WHERE id = ?",
                  (unrelated.id,))["on_vc_portfolio"] == 0

    signal = db.one("SELECT kind, source_key, source_url FROM signal WHERE company_id = ?",
                    (held.id,))
    assert signal["kind"] == "vc_portfolio_listing"
    assert signal["source_key"] == "vc_portfolios"
    assert signal["source_url"].startswith("https://")

    # Five listings, two companies: a portfolio page is never a lead source.
    assert db.scalar("SELECT COUNT(*) FROM company") == 2


def test_denylist_is_idempotent(db):
    """The weekly re-read must not stack duplicate signals on the same company."""
    from radar.resolve.normalise import norm_key
    from tests.factories import registry_company, store_company

    held = registry_company(canonical_name="Loamweave", norm_key=norm_key("Loamweave"))
    store_company(db, held)

    items = vc_portfolios.ADAPTER.parse(load("vc_portfolios.html"))
    vc_portfolios.apply_denylist(db, items)
    vc_portfolios.apply_denylist(db, items)

    assert db.scalar(
        "SELECT COUNT(*) FROM signal WHERE company_id = ? AND kind = 'vc_portfolio_listing'",
        (held.id,)) == 1


# --------------------------------------------- client-requested categories

# Client-issues plan §3.8 (A5): Aryan's exact ask — "expand [sources] with
# more early-stage sources like university spinouts, accelerator/demo day
# cohorts, Innovate UK announcements". Each category must exist as a registered
# adapter AND have at least one member switched on by default, or the request
# silently becomes sheet-configuration that nobody enabled.
#
# All keys here are registry keys — the adapter `key` attributes. The seeded
# config must use exactly these keys (see
# `test_every_default_source_key_resolves_in_the_registry`), or the Enabled
# toggle and the Sources-tab health join silently stop working.
CATEGORY_KEYS: dict[str, set[str]] = {
    "university spinouts": {
        "cambridge_enterprise", "oxford_innovation", "ucl_ventures",
        "edinburgh_innovations", "sheffield", "converge",
    },
    "accelerator cohorts": {
        "northern_accelerator", "conception_x", "zinc_vc",
        "founders_factory", "techstars_london", "carbon13", "bethnal_green",
    },
    "innovate_uk / grants": {"innovate_uk", "ukri_gtr", "govuk_search"},
}


def test_client_requested_source_categories_are_registered_and_enabled():
    from radar.config.defaults import DEFAULT_SOURCES

    registered = set(REGISTRY)
    enabled = {s.key for s in DEFAULT_SOURCES}
    for category, keys in CATEGORY_KEYS.items():
        assert registered & keys, f"{category}: no adapter registered"
        assert enabled & keys, f"{category}: nothing enabled by default"


def test_dedicated_innovate_uk_feeds_are_enabled_by_default():
    """Client ask A5 pinned exactly: the generic `govuk_search` passing the
    category test above is not enough — the *dedicated* Innovate UK award
    feeds (`ukri_gtr` weekly, `innovate_uk` monthly) must be on by default
    or the "first appearance" grant announcements never reach the pipeline.
    """
    from radar.config.defaults import DEFAULT_SOURCES

    enabled = {s.key for s in DEFAULT_SOURCES}
    missing = {"innovate_uk", "ukri_gtr"} - enabled
    assert not missing, (
        f"dedicated Innovate UK feeds not enabled by default: {missing}"
    )


# --------------------------------------- config keys must equal registry keys


def test_every_default_source_key_resolves_in_the_registry():
    """A seeded key that is not a registry key is a toggle that does nothing
    and a health column that never fills.

    This is the exact `oxford_university_innovation` vs `oxford_innovation`
    bug, stated generally: the Sources tab names the adapter by `SourceConfig
    .key`, the registry runs the adapter by `adapter.key`, and the two must be
    the same string or the client's `Enabled` flip is ignored.
    """
    from radar.config.defaults import DEFAULT_SOURCES

    unknown = {s.key for s in DEFAULT_SOURCES} - set(REGISTRY)
    assert not unknown, f"Sources-tab keys with no adapter: {unknown}"


def test_disabling_a_configured_source_removes_its_adapter():
    """The Enabled toggle must control the adapter it names.

    Behavioral pin on the same bug: with the seed key fixed, flipping Oxford
    off actually removes `oxford_innovation` from the run; with it on, the
    adapter is included.
    """
    from radar.config.models import SourceConfig
    from radar.sources import enabled_adapters

    disabled_cfg = SimpleNamespace(sources=[
        SourceConfig(key="oxford_innovation", track="A", enabled=False),
    ])
    keys = {a.key for a in enabled_adapters(disabled_cfg)}
    assert "oxford_innovation" not in keys

    enabled_cfg = SimpleNamespace(sources=[
        SourceConfig(key="oxford_innovation", track="A", enabled=True),
    ])
    keys = {a.key for a in enabled_adapters(enabled_cfg)}
    assert "oxford_innovation" in keys


def test_unknown_configured_key_warns_instead_of_staying_silent(caplog):
    """A stale key in the sheet (e.g. an old `oxford_university_innovation`
    row that predates the fix) must log a warning, not quietly do nothing —
    that silence is how this bug lived on the box for weeks."""
    from types import SimpleNamespace as NS

    from radar.config.models import SourceConfig
    from radar.sources import enabled_adapters

    cfg = NS(sources=[SourceConfig(key="oxford_university_innovation", track="A")])
    with caplog.at_level(logging.WARNING, logger="radar.sources"):
        enabled_adapters(cfg)
    assert "oxford_university_innovation" in caplog.text
    assert "no effect" in caplog.text
