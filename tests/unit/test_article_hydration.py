from datetime import date
from types import SimpleNamespace
import pytest
from radar.fetch.http import Response, HttpClient, RobotsDenied
from radar.sources._article import hydrate_articles
from radar.sources.base import RawItem, FetchContext
from radar.sources import fetch_all


def item(key='businesscloud', n=1, **metadata):
    return RawItem(key, f'https://example.org/story/{n}', str(n), date(2026,9,30),
                   'Startup raises funding', 'Short feed excerpt',
                   {'full_text_in_feed':False, **metadata})


class HTTP:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        result=self.responses.pop(0)
        if isinstance(result,Exception):raise result
        return result


def response(text=None,status=200,headers=None):
    return Response('https://example.org/story',status,text or '<nav>Menu</nav><div class="post-content">'+('Startup product and funding evidence. '*20)+'</div><footer>Other stories</footer>',headers or {})


def test_body_replaces_excerpt_with_explicit_limits_and_no_navigation():
    http=HTTP([response()]); got,fail=hydrate_articles([item()],FetchContext(http,{}),kind='news')
    assert not fail and len(got[0].body_text)>500
    assert 'Menu' not in got[0].body_text and 'Other stories' not in got[0].body_text
    assert got[0].structured['article_body_hydrated']
    assert http.calls[0][1]=={'timeout':8.0,'max_retries':0,'follow_redirects':False}


@pytest.mark.parametrize('failed',[response(status=403),response(headers={'X-Robots-Tag':'noindex'}),response('<meta name="robots" content="noindex"><div class="post-content">'+('Evidence '*100)+'</div>'), RobotsDenied('robots restriction'),response('<main>'+('Navigation '*100)+'</main>')])
def test_bad_article_withheld_without_killing_healthy_neighbor(failed):
    http=HTTP([failed,response()]); got,fail=hydrate_articles([item(n=1),item(n=2)],FetchContext(http,{}),kind='news')
    assert [x.external_id for x in got]==['2']
    assert len(fail)==1 and 'excerpt withheld' in fail[0]


def test_304_retries_without_validators_and_never_loses_full_body():
    http=HTTP([response(status=304),response()]); got,fail=hydrate_articles([item()],FetchContext(http,{}),kind='news')
    assert len(got)==1 and not fail
    assert http.calls[1][1]['headers']=={'Cache-Control':'no-cache'}
    http=HTTP([response(status=304),response(status=304)])
    got,fail=hydrate_articles([item()],FetchContext(http,{}),kind='news')
    assert not got and fail


def test_cap_and_previously_hydrated_item_do_not_fetch_again():
    http=HTTP([response()]); got,fail=hydrate_articles([item(n=1),item(n=2),item(n=3,needs_article_fetch=False)],FetchContext(http,{}),kind='news',limit=1)
    assert [x.external_id for x in got]==['1','3'] and len(http.calls)==1
    assert 'limit' in fail[0]


def test_registry_reports_partial_article_failure_as_degraded():
    adapter=SimpleNamespace(key='businesscloud',kind='news',fetch=lambda ctx:[item(n=1),item(n=2)])
    ctx=FetchContext(HTTP([response(status=404),response()]),{})
    result=fetch_all(ctx,adapters=[adapter])
    assert len(result.items)==1 and result.sources[0].status=='degraded'


def test_cambridge_collects_multiple_content_blocks_only():
    from radar.sources._article import article_text
    html='<main><section class="block--text"><div class="prose">'+('Company evidence '*30)+'</div></section><section class="block--related">Other companies</section><section class="block--text"><div class="prose">'+('Founder evidence '*30)+'</div></section></main>'
    text=article_text(html,'cambridge_enterprise')
    assert 'Company evidence' in text and 'Founder evidence' in text and 'Other companies' not in text


def test_redirect_to_private_host_is_never_requested():
    http=HTTP([response(status=302,headers={'location':'http://127.0.0.1/secret'})])
    got,fail=hydrate_articles([item()],FetchContext(http,{}),kind='news')
    assert not got and len(http.calls)==1 and 'private' in fail[0]


def test_each_public_redirect_is_requested_separately():
    http=HTTP([response(status=302,headers={'location':'https://example.net/new'}),response()])
    got,fail=hydrate_articles([item()],FetchContext(http,{}),kind='news')
    assert got and not fail
    assert http.calls[1][0]=='https://example.net/new'
    assert all(k['follow_redirects'] is False for _,k in http.calls)


def test_budget_is_warning_not_source_failure():
    adapter=SimpleNamespace(key='businesscloud',kind='news',fetch=lambda ctx:[item(n=n) for n in range(21)])
    result=fetch_all(FetchContext(HTTP([response() for _ in range(20)]),{}),adapters=[adapter])
    assert result.sources[0].status=='ok'
    assert 'budget' in result.sources[0].warning.lower()
    assert len(result.items)==20


def test_redirect_checks_robots_on_destination_with_real_client(monkeypatch):
    import httpx
    import radar.qa.provenance as provenance
    monkeypatch.setattr(provenance, '_public', lambda url, resolve: None)
    checked, requested = [], []
    class Robots:
        def allowed(self,url,ua):
            checked.append(url)
            return not url.endswith('/forbidden')
        def crawl_delay(self,url,ua): return None
    def transport(request):
        requested.append(str(request.url))
        return httpx.Response(302,headers={'Location':'https://example.org/forbidden'})
    client=HttpClient(transport=httpx.MockTransport(transport),robots=Robots(),max_retries=0)
    got,fail=hydrate_articles([item()],FetchContext(client,{}),kind='news')
    assert not got and 'RobotsDenied' in fail[0]
    assert checked[-1].endswith('/forbidden')
    assert len(requested)==1
