"""Currency conversion, generic feed sources and source→fund mapping."""
from datetime import date
from types import SimpleNamespace

from radar import fx
from radar.config.models import Config, SourceConfig
from radar.selection import source_funds
from radar.sources import enabled_adapters
from radar.sources.feed import FeedAdapter

ECB = """<Cube><Cube time='2026-10-16'>
<Cube currency='USD' rate='1.1253'/><Cube currency='GBP' rate='0.84385'/>
<Cube currency='CHF' rate='0.9300'/></Cube></Cube>"""


def test_ecb_rates_become_gbp_per_unit():
    as_of, rates = fx.parse_ecb(ECB)
    assert as_of == "2026-10-16"
    assert round(rates["USD"], 4) == round(0.84385 / 1.1253, 4)
    assert rates["EUR"] == 0.84385 and rates["GBP"] == 1.0
    assert fx.to_gbp(4_000_000, "USD", rates) == round(4_000_000 * 0.84385 / 1.1253, 2)
    assert fx.to_gbp(1_000, "XYZ", rates) is None        # unknown, not guessed
    assert fx.to_gbp(None, "USD", rates) is None


def test_rates_are_fetched_once_a_day_and_cached(db):
    calls = []

    class Http:
        def get(self, url, **kw):
            calls.append(url)
            return SimpleNamespace(ok=True, text=ECB)

    fx.load_rates(db, Http(), today=date(2026, 10, 18))
    fx.load_rates(db, Http(), today=date(2026, 10, 18))
    assert calls == [fx.ECB_DAILY]
    # The next day a dead ECB falls back to the cached set, not to nothing.
    class Dead:
        def get(self, url, **kw):
            raise OSError("down")
    rates = fx.load_rates(db, Dead(), today=date(2026, 10, 19))
    assert round(rates["USD"], 4) == round(0.84385 / 1.1253, 4)


def test_short_money_reads_like_a_card():
    assert fx.short_money(4_000_000, "USD") == "$4.0M"
    assert fx.short_money(250_000) == "£250k"
    assert fx.short_money(3_000_000, "CHF") == "CHF 3.0M"


def test_funding_phrase_shows_both_amounts_when_converted():
    from prototype.server import _funding_phrase
    assert _funding_phrase(3_000_000, "USD 4000000") == "$4.0M ≈ £3.0M raised"
    assert _funding_phrase(1_200_000, None) == "£1.2M raised"
    assert _funding_phrase(None, None) == ""


def test_a_feed_source_with_a_url_runs_as_an_adapter():
    cfg = Config(sources=[SourceConfig(key="feed_sifted", url="https://sifted.eu/feed"),
                          SourceConfig(key="feed_off", url="https://x.test/feed", enabled=False),
                          SourceConfig(key="uktn")])
    keys = [a.key for a in enabled_adapters(cfg)]
    assert "feed_sifted" in keys and "uktn" in keys and "feed_off" not in keys


def test_feed_items_are_news_that_needs_the_article_when_the_feed_is_short():
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Acme raises £2m seed</title><link>https://n.test/acme</link>
    <guid>a1</guid><pubDate>Fri, 16 Oct 2026 09:00:00 GMT</pubDate>
    <description>Short excerpt.</description></item></channel></rss>"""
    item = FeedAdapter("feed_n", "https://n.test/feed").parse(rss)[0]
    assert item.source_key == "feed_n" and item.kind_hint == "funding_round"
    assert item.structured["full_text_in_feed"] is False


def test_source_fund_mapping_limits_the_suggested_fund(db):
    from tests.fakes import seed_companies
    ids = seed_companies(db, count=1, shortlist=1)          # its source is uktn
    open_cfg = Config(sources=[SourceConfig(key="uktn")])
    assert source_funds(db, ids[0], open_cfg) is None
    mapped = Config(sources=[SourceConfig(key="uktn", funds=["dsw", "outward"])])
    assert source_funds(db, ids[0], mapped) == {"dsw", "outward"}
