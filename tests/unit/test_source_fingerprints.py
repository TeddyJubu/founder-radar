"""October 2026: three news feeds went dark for ten days on a false "layout changed".

The RSS fingerprint hashed the keys of the entry dicts *we* build, so adding
`company_link_evidence` to them moved bdaily_regional, businesscloud and carbon13
to the same new hash at once, and the fail-closed guard dropped every item.
These tests pin the fix: hash only what the site controls, re-learn when our own
recipe changes, and give the operator a way to accept a checked layout.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from radar.fetch.layout import (
    LayoutChanged,
    check_fingerprint,
    fingerprint_scheme,
    forget_fingerprints,
    stored_fingerprint,
)
from radar.sources import bdaily_regional, businesscloud, carbon13, cli_sources, fetch_all
from radar.sources._common import rss_feed
from radar.sources.base import FetchContext, RawItem

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"
LEGACY_FEED_FINGERPRINT = "f58918b6beeb6f0e"   # what production had stored


def _feed(items: str) -> str:
    return ('<?xml version="1.0"?><rss version="2.0" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>'
            f'{items}</channel></rss>')


ITEM = ('<item><title>Example Instruments raises £2m</title><link>https://example.test/a{n}</link>'
        '<guid>https://example.test/?p={n}</guid><pubDate>Fri, 09 Oct 2026 08:00:00 +0000</pubDate>'
        '<description>Excerpt {n}</description>{extra}</item>')


def test_scheme_is_the_prefix_and_legacy_values_have_none():
    assert fingerprint_scheme("rss:abc") == "rss"
    assert fingerprint_scheme("vc:abc") == "vc"
    assert fingerprint_scheme(LEGACY_FEED_FINGERPRINT) == ""
    assert fingerprint_scheme(None) == ""


def test_a_new_recipe_is_relearned_not_reported(db):
    db.set_meta("layout:bdaily_regional", LEGACY_FEED_FINGERPRINT)
    check_fingerprint(db, "bdaily_regional", "rss:0123456789abcdef")    # must not raise
    assert stored_fingerprint(db, "bdaily_regional") == "rss:0123456789abcdef"


def test_a_real_change_under_the_same_recipe_still_fails_closed_and_says_what_to_do(db):
    check_fingerprint(db, "carbon13", "rss:aaaa")
    with pytest.raises(LayoutChanged, match="sources --accept-layout carbon13"):
        check_fingerprint(db, "carbon13", "rss:bbbb")
    # A per-site key names the adapter, not the site, in the remedy.
    check_fingerprint(db, "vc_portfolios:dsw", "vc:aaaa")
    with pytest.raises(LayoutChanged, match="--accept-layout vc_portfolios`"):
        check_fingerprint(db, "vc_portfolios:dsw", "vc:bbbb")


def test_feed_fingerprint_describes_the_feed_not_our_entry_dicts():
    plain = _feed(ITEM.format(n=1, extra="") + ITEM.format(n=2, extra=""))
    other_posts = _feed(ITEM.format(n=7, extra="") + ITEM.format(n=8, extra=""))
    full_text = _feed(ITEM.format(n=1, extra="<content:encoded><![CDATA[<p>Full body</p>]]>"
                                             "</content:encoded>"))
    entries, fp = rss_feed(plain, "t")
    assert fp.startswith("rss:")
    assert set(entries[0]) >= {"company_link_evidence", "has_full_text"}   # our keys exist…
    assert rss_feed(other_posts, "t")[1] == fp                             # …but content churn is not a change
    assert rss_feed(full_text, "t")[1] != fp                               # a feed growing full text is


@pytest.mark.parametrize("adapter,fixture", [
    (bdaily_regional.ADAPTER, "bdaily_regional.xml"),
    (businesscloud.ADAPTER, "businesscloud.xml"),
    (carbon13.ADAPTER, "carbon13.xml"),
])
def test_the_three_dark_feeds_recover_on_the_first_run_after_deploy(db, config, adapter, fixture):
    db.set_meta(f"layout:{adapter.key}", LEGACY_FEED_FINGERPRINT)
    payload = (FIXTURES / fixture).read_text()
    wrapper = SimpleNamespace(key=adapter.key, kind="other", last_fingerprint=None)

    def fetch(ctx):
        items = adapter.parse(payload)
        wrapper.last_fingerprint = adapter.last_fingerprint
        return items

    wrapper.fetch = fetch
    result = fetch_all(FetchContext(http=None, config=config, db=db), [wrapper])
    assert result.sources[0].status == "ok"
    assert result.items
    assert stored_fingerprint(db, adapter.key).startswith("rss:")


def test_accept_layout_forgets_the_source_and_its_sites(db):
    db.set_meta("layout:vc_portfolios", "x")
    db.set_meta("layout:vc_portfolios:dsw", "vc:1")
    db.set_meta("layout:vc_portfolios:mercia", "vc:2")
    db.set_meta("layout:vc_portfolios_other", "keep")
    report = cli_sources(db, accept_layout="vc_portfolios")
    assert report["cleared"] == ["layout:vc_portfolios", "layout:vc_portfolios:dsw",
                                 "layout:vc_portfolios:mercia"]
    assert db.get_meta("layout:vc_portfolios_other") == "keep"
    assert forget_fingerprints(db, "vc_portfolios") == []


def test_accept_layout_refuses_an_unknown_source(db):
    report = cli_sources(db, accept_layout="no_such_source")
    assert report["status"] == "unknown source"
    assert "bdaily_regional" in report["known_keys"]


def test_after_accepting_the_next_run_learns_and_passes(db, config):
    item = RawItem("test", "https://example.org", "1", None, "Acme")
    adapter = SimpleNamespace(key="bdaily_regional", last_fingerprint="rss:old", fetch=lambda ctx: [item])
    ctx = FetchContext(http=None, config=config, db=db)
    fetch_all(ctx, [adapter])
    adapter.last_fingerprint = "rss:new"
    assert fetch_all(ctx, [adapter]).sources[0].status == "layout_changed"
    cli_sources(db, accept_layout="bdaily_regional")
    assert fetch_all(ctx, [adapter]).sources[0].status == "ok"


# --------------------------------------------------------------- noindex scope


def _client(status: int, body: str, headers: dict | None = None):
    from radar.fetch.http import HttpClient
    return HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, headers={"content-type": "text/html", **(headers or {})}, text=body)),
        obey_robots=False, sleep=lambda _: None, max_retries=0)


NOINDEX_PAGE = '<html><head><meta name="robots" content="noindex, nofollow"></head><body>x</body></html>'


@pytest.mark.parametrize("status", [403, 404, 503])
def test_an_error_page_is_a_status_not_a_noindex_refusal(status):
    resp = _client(status, NOINDEX_PAGE).get("https://example.test/portfolio/")
    assert resp.status == status            # Cloudflare challenge, 404 template, outage


def test_a_served_page_that_asks_not_to_be_indexed_is_still_refused():
    from radar.fetch.http import RobotsDenied
    with pytest.raises(RobotsDenied, match="HTML meta noindex"):
        _client(200, NOINDEX_PAGE).get("https://example.test/article")
    with pytest.raises(RobotsDenied, match="X-Robots-Tag"):
        _client(200, "<p>x</p>", {"X-Robots-Tag": "noindex"}).get("https://example.test/api")
