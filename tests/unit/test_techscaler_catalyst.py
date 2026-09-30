from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar.fetch.layout import LayoutChanged
from radar.sources import REGISTRY, fetch_all
from radar.sources.base import FetchContext
from radar.sources.techscaler_catalyst import TechscalerCatalystAdapter, BASE, INDEX

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'sources'
URL = BASE + '/resources/introducing-the-2026-autumn-catalyst-cohort'
ARTICLE = (FIXTURES / 'techscaler_catalyst.html').read_text()
INDEX_HTML = (FIXTURES / 'techscaler_resources.html').read_text()


class HTTP:
    def __init__(self, article=ARTICLE):
        self.article = article
        self.calls = []

    def get(self, url, **options):
        assert options == dict(max_bytes=1_000_000, timeout=8, max_retries=0, follow_redirects=False)
        self.calls.append(url)
        return SimpleNamespace(status=200, ok=True, headers={}, text=INDEX_HTML if url == INDEX else self.article)


def test_actual_named_current_cohort_only_and_unknown_facts():
    items = TechscalerCatalystAdapter().parse(ARTICLE, URL)
    names = [i.structured['company_name'] for i in items]
    assert len(names) == len(set(names))
    assert names.count('MADGenesis Limited') == 1
    assert {'Husu', 'Nuuri', 'XYNQ', 'N/a', 'Nil'}.isdisjoint(names)
    assert 'FutureTherma Labs (SAHP HUB LTD)' in names
    assert 'Baltic Comfort (I intend to change this to Boreal Horizons)' in names
    assert 'AURA / Windsor Brain' in names
    assert len(items) > 90
    for item in items:
        assert item.title == item.structured['company_name']
        assert item.source_url == URL
        assert item.published_at == date(2026, 9, 29)
        assert item.kind_hint == 'accelerator_cohort'
        assert item.structured['program'] == 'Techscaler Catalyst'
        assert item.structured['company_link_evidence'] == []
        assert not {'sector', 'stage', 'company_number', 'hq_region', 'country', 'total_prior_funding_gbp'} & item.structured.keys()
        assert 'Husu' not in item.body_text


def test_exact_company_anchor_inside_section_only():
    article = ARTICLE.replace('Better Surgery,', '<a href="https://better.example">Better Surgery</a>,')
    items = TechscalerCatalystAdapter().parse(article, URL)
    assert items[0].structured['company_link_evidence'] == [{'label': 'Better Surgery', 'url': 'https://better.example'}]
    assert all(not i.structured['company_link_evidence'] for i in items[1:])


def test_actual_cohort_item_records_canonical_signal_without_guessed_geography(db, config):
    from radar.pipeline import resolve_item
    item = TechscalerCatalystAdapter().parse(ARTICLE, URL)[0]
    cid = resolve_item(db, item, cfg=config)
    company = db.one('SELECT discovery_route, country_iso2, hq_region, companies_house_no FROM company WHERE id=?', (cid,))
    assert company['discovery_route'] == 'accelerator'
    assert company['country_iso2'] is None
    assert company['hq_region'] is None
    assert company['companies_house_no'] is None
    signal = db.one('SELECT kind, source_url FROM signal WHERE company_id=?', (cid,))
    assert signal['kind'] == 'accelerator_cohort'
    assert signal['source_url'] == URL


def test_fetch_since_and_registration():
    adapter = TechscalerCatalystAdapter()
    assert REGISTRY[adapter.key].kind == 'accelerator'
    http = HTTP()
    assert adapter.fetch(FetchContext(http=http, config=None, since=date(2026, 9, 30))) == []
    assert http.calls == [INDEX]
    assert len(adapter.fetch(FetchContext(http=HTTP(), config=None, since=date(2026, 9, 29)))) > 90


@pytest.mark.parametrize('article', [ARTICLE.replace('Meet the cohort:', 'Our network:'),
    ARTICLE.replace('Up next', 'A different boundary'), ARTICLE.replace('September 29, 2026', 'Date unknown')])
def test_malformed_article_is_degraded_not_healthy_zero(article):
    adapter = TechscalerCatalystAdapter()
    with pytest.raises(LayoutChanged):
        adapter.parse(article, URL)
    result = fetch_all(FetchContext(http=HTTP(article), config=None), [adapter])
    assert result.sources[0].status == 'degraded'
    assert result.sources[0].error
    assert result.items == []


def test_missing_index_cards_is_not_healthy_zero():
    with pytest.raises(LayoutChanged):
        TechscalerCatalystAdapter().parse_index('<html><p>Moved resource list</p></html>')


def test_streaming_byte_cap_stops_download_and_closes_response():
    import httpx
    from radar.fetch.http import HttpClient

    class Stream(httpx.SyncByteStream):
        emitted = 0
        closed = False
        def __iter__(self):
            for _ in range(200):
                self.emitted += 16_384
                yield b'x' * 16_384
        def close(self):
            self.closed = True

    stream = Stream()
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, headers={'content-type': 'text/html'}, stream=stream)
    client = HttpClient(transport=httpx.MockTransport(respond), obey_robots=False, sleep=lambda _: None)
    try:
        with pytest.raises(ValueError, match='byte limit'):
            TechscalerCatalystAdapter().fetch(FetchContext(http=client, config=None))
        assert requests == [INDEX]
        assert 1_000_000 < stream.emitted <= 1_000_000 + 16_384
        assert stream.closed
    finally:
        client.close()
