"""Control-room and Today fixes from the October call with Aryan."""
from __future__ import annotations

import httpx
import pytest

from prototype.server import build_today
from radar.admin import settings
from radar.config.defaults import default_config
from radar.config.loader import parse_sources
from radar.fetch.robots import RobotsCache
from radar.sources.fintech_north import FintechNorthAdapter
from radar.sources.nexus_leeds import NexusLeedsAdapter
from radar.sources.innovate_uk import page_url
from radar.sources.ukspa import UkspaAdapter
from tests.fakes import seed_companies


def test_workbook_links_open_a_project_page():
    book = "https://www.ukri.org/wp-content/uploads/IUK.xlsx#project=10012345&participant=Ada"
    assert page_url(book) == "https://gtr.ukri.org/projects?ref=10012345"
    assert page_url("https://gtr.ukri.org/projects?ref=10012345") == (
        "https://gtr.ukri.org/projects?ref=10012345")
    assert page_url("https://www.ukri.org/file.xlsx") .startswith("https://www.ukri.org/publications/")


def test_companies_house_is_off_by_default():
    source = next(s for s in default_config().sources if s.key == "companies_house")
    assert source.enabled is False


def test_a_runtime_error_does_not_replace_the_source_description():
    rows = parse_sources([
        ["Source", "Track", "Enabled", "", "", "", "", "Note"],
        ["ukri_gtr", "A", "TRUE", "", "", "", "", "RobotsDenied: https://gtr.ukri.org/api/search/project"],
    ])
    assert rows[0].note != "RobotsDenied: https://gtr.ukri.org/api/search/project"


def test_today_backfills_past_sourceless_rows_and_lists_every_name(db):
    ids = seed_companies(db, count=8, shortlist=8)
    db.execute(
        "UPDATE company_source SET source_url = '' WHERE company_id IN (?, ?)",
        (ids[0], ids[1]),
    )
    payload = build_today(db.conn, limit=3)
    assert len(payload["companies"]) == 3
    assert ids[0] not in {row["company_id"] for row in payload["companies"]}
    assert len(payload["directory"]) == 6
    assert {row["company_id"] for row in payload["directory"]} == set(ids[2:])


def test_a_fund_can_be_paused_and_another_added(db):
    paused = settings.set_fund_paused(db, "outward", True, gateway=None)
    assert paused.applied["outward"] == "paused"
    cfg, _ = settings.current_config(db)
    outward = next(f for f in cfg.funds if f.key == "outward")
    assert outward.vehicles and all(not v.active for v in outward.vehicles)

    added = settings.add_fund(
        db, "Harbour", geo_values="yorkshire", sectors_plus="fintech",
        one_liner="Yorkshire fintech, seed to series A", gateway=None,
    )
    assert "harbour" in added.applied
    cfg, _ = settings.current_config(db)
    harbour = next(f for f in cfg.funds if f.key == "harbour")
    assert harbour.vehicles[0].geo_values == ["yorkshire"]
    assert harbour.vehicles[0].sectors_plus == ["fintech"]
    assert "harbour" in cfg.weights.importance["sector"]

    settings.set_fund_paused(db, "outward", False, gateway=None)
    cfg, _ = settings.current_config(db)
    outward = next(f for f in cfg.funds if f.key == "outward")
    assert all(v.active for v in outward.vehicles)


def test_a_web_page_is_a_clear_error_and_an_rss_link_is_followed(monkeypatch):
    page = "https://www.fca.org.uk/firms/innovation/regulatory-sandbox/accept"
    html = "<html><head></head><body>sandbox</body></html>"
    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>A firm entered the sandbox</title>
      <link>https://www.fca.org.uk/news/a-firm</link></item>
      </channel></rss>"""
    linked = html.replace(
        "</head>",
        '<link rel="alternate" type="application/rss+xml" href="https://www.fca.org.uk/news/rss.xml"></head>',
    )

    class Resp:
        def __init__(self, text):
            self.ok = True
            self.status = 200
            self.text = text

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url):
            if url == page and not Client.follow:
                return Resp(html)
            if url == page:
                return Resp(linked)
            return Resp(rss)

    Client.follow = False
    monkeypatch.setattr("radar.fetch.http.HttpClient", Client)
    with pytest.raises(settings.SourceInvalid, match="not an RSS"):
        settings._check_feed("feed_fca", page)
    Client.follow = True
    assert settings._check_feed("feed_fca", page) == "https://www.fca.org.uk/news/rss.xml"


def test_html_robots_and_a_later_outage_do_not_block_the_site():
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="<!DOCTYPE html><html><body>challenge</body></html>")
        return httpx.Response(503, text="down")

    cache = RobotsCache(httpx.Client(transport=httpx.MockTransport(handler)))
    agent = "founder-radar"
    assert cache.allowed("https://gtr.ukri.org/api/search/project", agent) is True
    cache._cache["gtr.ukri.org"].fetched_at = 0
    assert cache.allowed("https://gtr.ukri.org/api/search/project", agent) is True


def test_hermes_qa_runs_as_the_profile_owner(monkeypatch, tmp_path):
    from radar.qa.today import hermes_argv

    home = tmp_path / "op"
    hermes = home / ".hermes"
    hermes.mkdir(parents=True)
    wrapper = tmp_path / "hermes-as-owner.sh"
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr("radar.qa.today.HERMES_AS_OWNER", wrapper)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("radar.qa.today.os.geteuid", lambda: 106)
    argv = hermes_argv("/home/aryan/.local/bin/hermes", ["chat", "-Q", "--query-file", "-"])
    assert argv[:4] == ["sudo", "-n", str(wrapper), "/home/aryan/.local/bin/hermes"]
    assert argv[4:] == ["chat", "-Q", "--query-file", "-"]


def test_new_regional_feeds_parse():
    payload = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Leeds firm raises seed</title>
      <link>https://fintechnorth.uk/leeds-firm</link>
      <description>A short note.</description></item></channel></rss>"""
    item = FintechNorthAdapter().parse(payload)[0]
    assert item.title == "Leeds firm raises seed" and item.source_url.startswith("https://")
    items = UkspaAdapter().parse(payload.replace("fintechnorth.uk", "www.ukspa.org.uk"))
    assert items and items[0].source_url.startswith("https://")
    nexus = NexusLeedsAdapter().parse(payload.replace("fintechnorth.uk", "nexusleeds.co.uk"))
    assert nexus and nexus[0].source_url.startswith("https://nexusleeds.co.uk")
