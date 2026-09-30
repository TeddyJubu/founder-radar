import json
from datetime import date
import pytest
from radar.enrich import RequestBudget
from radar.enrich.website_identity import retain_company_links, verify_missing_crns, RETRY_PREFIX
from radar.fetch.http import Response
from radar.resolve.match import Record
from radar.resolve.merge import create_company
from radar.sources._article import hydrate_articles
from radar.sources.base import RawItem, FetchContext

SITE = 'https://acme.example.org/'
PROFILE = dict(company_name='Acme Ltd', company_number='12345678', company_status='active',
               jurisdiction='england-wales', date_of_creation='2025-01-01',
               registered_office_address={'locality':'London','postal_code':'SW1A 1AA'})
STATEMENT = 'Acme Ltd is registered in England and Wales. Company number 12345678.'


class HTTP:
    def __init__(self, pages): self.pages=list(pages); self.calls=[]
    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        value=self.pages.pop(0)
        if isinstance(value,Exception): raise value
        return value


def page(text=STATEMENT):
    return Response(SITE,200,'<footer><p>'+text+'</p></footer>',{})


def profile(**changes):
    return Response('https://api.company-information.service.gov.uk/company/12345678',200,
                    json.dumps({**PROFILE,**changes}),{})


def company(db,name='Acme Ltd',country=None):
    cid=create_company(db,Record(name=name,country_iso2=country),source_key='news',source_url='https://publisher.org/story',external_id=name)
    retain_company_links(db,cid,name,[{'label':name,'url':SITE}],source_key='news',source_url='https://publisher.org/story')
    return cid


def verify(db,http,**kwargs):
    return verify_missing_crns(db,http,api_key='test-key',budget=RequestBudget(20),**kwargs)


def test_verifies_explicit_identity_preserving_hq_and_id(db):
    cid=company(db,country='GB')
    http=HTTP([page(),profile()])
    assert verify(db,http)['verified']==1
    row=db.one('SELECT * FROM company WHERE id=?',(cid,))
    assert row['companies_house_no']=='12345678' and row['incorporated_on']=='2025-01-01'
    assert row['hq_city'] is None and row['hq_postcode'] is None
    assert row['country_iso2']=='GB'
    assert db.one('SELECT 1 FROM observation WHERE company_id=? AND field=?',(cid,'registered_office_address'))
    assert db.one('SELECT 1 FROM observation WHERE company_id=? AND field=?',(cid,'verified_company_registration'))
    assert http.calls[-1][1]['auth']==('test-key','')
    assert all(c[1]['follow_redirects'] is False and c[1]['max_retries']==0 for c in http.calls)


@pytest.mark.parametrize('statement',[
    'Acme Ltd is registered in England and Wales. VAT number 12345678.',
    'Acme Ltd is registered in England and Wales. Phone 12345678.',
    'Acme Ltd is registered in England and Wales. Postcode 12345678.',
    'Our client Acme Ltd is registered in England and Wales. Company number 12345678.',
    'Example: Acme Ltd is registered in England and Wales. Company number 12345678.',
    'Other Ltd is registered in England and Wales. Company number 12345678.',
    'Acme Ltd is registered in England and Wales. Company number 12345678. Company number 87654321.',
    'Acme Ltd. Company number 12345678.',
])
def test_misleading_or_ambiguous_legal_evidence_stays_unknown(db,statement):
    cid=company(db); http=HTTP([page(statement),profile()])
    assert verify(db,http)['verified']==0
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None


@pytest.mark.parametrize('changes',[dict(company_name='Other Ltd'),dict(company_status='dissolved'),dict(jurisdiction='jersey'),dict(company_number='87654321'),dict(date_of_creation='bad')])
def test_registry_mismatch_does_not_attach(db,changes):
    cid=company(db); verify(db,HTTP([page(),profile(**changes)]))
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None


def test_brand_requires_explicit_trading_relationship(db):
    cid=company(db,name='Acme')
    http=HTTP([page('Acme is a trading name of Nova Technologies Ltd. Nova Technologies Ltd is registered in England and Wales. Company number 12345678.'),profile(company_name='Nova Technologies Ltd')])
    assert verify(db,http)['verified']==1
    assert db.one('SELECT canonical_name FROM company WHERE id=?',(cid,))[0]=='Acme'


def test_uk_affiliate_does_not_overwrite_nonuk_company(db):
    cid=company(db,country='US'); http=HTTP([])
    assert verify(db,http)['attempted']==0 and not http.calls
    assert db.one('SELECT country_iso2 FROM company WHERE id=?',(cid,))[0]=='US'


def test_stored_crn_collision_leaves_both_ids_and_verdicts_untouched(db):
    cid=company(db)
    other=create_company(db,Record(name='Acme Legal',ch_number='12345678'),source_key='registry',external_id='12345678')
    db.execute("INSERT INTO user_field(company_id,field,value,updated_at) VALUES (?,?,?,?)",(cid,'notes','"saved decision"','2026-01-01'))
    before=[tuple(r) for r in db.query('SELECT * FROM user_field')]
    assert verify(db,HTTP([page(),profile()]))['verified']==0
    assert [tuple(r) for r in db.query('SELECT * FROM user_field')]==before
    assert db.one('SELECT companies_house_no,merged_into FROM company WHERE id=?',(cid,))[0] is None
    assert db.one('SELECT merged_into FROM company WHERE id=?',(other,))[0] is None


def test_no_key_no_budget_and_retry_are_bounded(db):
    cid=company(db); http=HTTP([])
    for key,budget in [('',RequestBudget(10)),('key',RequestBudget(0))]:
        assert verify_missing_crns(db,http,api_key=key,budget=budget)['attempted']==0
    http=HTTP([page('No registration evidence')])
    verify(db,http)
    assert verify(db,HTTP([]))['skipped']==1
    db.execute('UPDATE _meta SET value=? WHERE key=?',('2020-01-01T00:00:00+00:00',RETRY_PREFIX+cid))
    assert verify(db,HTTP([page(),profile()]))['verified']==1


def test_private_redirect_and_cross_domain_are_not_followed(db):
    cid=company(db)
    for target in ['http://127.0.0.1/secret','http://169.254.169.254/metadata','https://unrelated.org/legal']:
        db.execute('DELETE FROM _meta WHERE key=?',(RETRY_PREFIX+cid,))
        http=HTTP([Response(SITE,302,'',{'location':target})])
        assert verify(db,http)['verified']==0 and len(http.calls)==1


def test_anchor_grounding_and_hydration_preserve_only_named_company_links(db):
    cid=company(db)
    db.execute('DELETE FROM observation WHERE company_id=?',(cid,))
    html='<nav><a href="https://wrong.org">Acme Ltd</a></nav><div class="post-content"><a href="'+SITE+'">Acme Ltd</a>'+('Evidence '*40)+'</div>'
    raw=RawItem('businesscloud','https://publisher.org/story','1',date.today(),'Acme raises funding','excerpt',{'full_text_in_feed':False})
    items,fail=hydrate_articles([raw],FetchContext(HTTP([Response(raw.source_url,200,html,{})]),{}),kind='news')
    assert not fail
    links=items[0].structured['company_link_evidence']
    assert links==[{'label':'Acme Ltd','url':SITE}]
    retain_company_links(db,cid,'Acme Ltd',links+[{'label':'Different','url':'https://other.org'},{'label':'Acme Ltd','url':'http://localhost/private'},{'label':'Acme Ltd','url':'https://linkedin.com/company/acme'}],source_key='news',source_url=raw.source_url)
    assert len(db.query('SELECT * FROM observation WHERE field=?',('company_website_link',)))==1


@pytest.mark.parametrize('statement',[
    'Acme Limited, a company registered in England and Wales. Company number 12345678.',
    'Acme Ltd, registered in England and Wales. Company number 12345678.',
])
def test_equivalent_legal_suffix_and_ownership_formats(db,statement):
    company(db)
    assert verify(db,HTTP([page(statement),profile()]))['verified']==1


def test_name_inside_another_name_is_not_identity(db):
    cid=company(db)
    verify(db,HTTP([page('SpamAcme Ltd is registered in England and Wales. Company number 12345678.'),profile()]))
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None


def test_atomic_identity_write_rolls_back_on_late_observation_failure(db,monkeypatch):
    import radar.enrich.website_identity as module
    cid=company(db)
    before_sources=[tuple(r) for r in db.query('SELECT * FROM company_source')]
    before_identifiers=[tuple(r) for r in db.query('SELECT * FROM identifier')]
    original=module.observe
    def broken(*args,**kwargs):
        if args[2]=='verified_company_registration':
            raise RuntimeError('simulated storage failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'observe',broken)
    assert verify(db,HTTP([page(),profile()]))['verified']==0
    row=db.one('SELECT companies_house_no,incorporated_on FROM company WHERE id=?',(cid,))
    assert tuple(row)==(None,None)
    assert [tuple(r) for r in db.query('SELECT * FROM company_source')]==before_sources
    assert [tuple(r) for r in db.query('SELECT * FROM identifier')]==before_identifiers
    assert not db.one('SELECT 1 FROM observation WHERE field=?',('registered_office_address',))


def test_optional_legal_404_keeps_primary_identity_with_failure_provenance(db):
    cid=company(db)
    home=page().text+'<a href="/terms">Terms</a>'
    http=HTTP([Response(SITE,200,home,{}),Response(SITE+'terms',404,'Not found',{}),profile()])
    assert verify(db,http)['verified']==1
    evidence=json.loads(db.one('SELECT value_json FROM observation WHERE company_id=? AND field=?',(cid,'verified_company_registration'))[0])
    assert evidence['unavailable_legal_pages'][0]['url']==SITE+'terms'


def test_legal_page_competing_number_is_not_chosen(db):
    cid=company(db)
    http=HTTP([Response(SITE,200,page().text+'<a href="/terms">Terms</a>',{}),page(STATEMENT.replace('12345678','87654321'))])
    assert verify(db,http)['verified']==0
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None
    assert len(http.calls)==2


def test_request_budget_stops_before_authenticated_profile(db):
    cid=company(db); http=HTTP([page()]); budget=RequestBudget(1)
    assert verify_missing_crns(db,http,api_key='key',budget=budget)['verified']==0
    assert len(http.calls)==1 and budget.spent==1
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None


def test_http_stream_byte_cap_closes_oversized_body_and_preserves_small_response():
    import httpx
    from radar.fetch.http import HttpClient
    consumed=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                consumed.append(1)
                yield b'x'*8192
        def close(self): consumed.append('closed')
    client=HttpClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Stream(),request=request)),obey_robots=False)
    try:
        with pytest.raises(ValueError,match='byte limit'):
            client.get('https://example.org',max_bytes=1000,max_retries=0)
        assert consumed==[1,1,'closed']  # first decoded 16KiB chunk, not the entire body
    finally: client.close()
    client=HttpClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text='small',request=request)),obey_robots=False)
    try:
        assert client.get('https://example.org',max_bytes=1000).text=='small'
    finally: client.close()


def test_historical_collection_is_exact_company_scoped_and_bounded(db):
    from radar.enrich.website_identity import collect_source_links
    cid=create_company(db,Record(name='Acme Ltd'),source_key='businesscloud',source_url='https://publisher.org/story',external_id='1')
    other=create_company(db,Record(name='Other Ltd'),source_key='businesscloud',source_url='https://publisher.org/other',external_id='2')
    html='<div class="post-content"><a href="'+SITE+'">Acme Ltd</a>'+('Evidence '*40)+'</div>'
    http=HTTP([Response('https://publisher.org/story',200,html,{})])
    assert collect_source_links(db,http,budget=RequestBudget(1),company_ids=[cid])==dict(articles_read=1,companies_linked=1,unavailable=0)
    assert db.one('SELECT 1 FROM observation WHERE company_id=? AND field=?',(cid,'company_website_link'))
    assert not db.one('SELECT 1 FROM observation WHERE company_id=?',(other,))


def test_cli_preview_is_read_only_and_apply_is_explicit(tmp_path,monkeypatch):
    from click.testing import CliRunner
    from radar.cli import cli
    from radar.store.db import Db
    path=tmp_path/'radar.sqlite3'
    live=Db(path);live.migrate();cid=company(live);live.close()
    http=HTTP([page(),profile()]);http.close=lambda:None
    monkeypatch.setattr('radar.pipeline._make_http',lambda:http)
    monkeypatch.setattr('radar.sources.companies_house.api_key_from_env',lambda:'test-key')
    before=path.read_bytes()
    result=CliRunner().invoke(cli,['--db',str(path),'--json','verify-identities','--company',cid])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['verification']['verified']==1
    assert path.read_bytes()==before
    http=HTTP([page(),profile()]);http.close=lambda:None
    result=CliRunner().invoke(cli,['--db',str(path),'--json','verify-identities','--company',cid,'--apply'])
    assert result.exit_code==0,result.output
    live=Db(path)
    assert live.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0]=='12345678'
    live.close()


def test_company_number_with_separate_vat_number_is_valid(db):
    company(db)
    assert verify(db,HTTP([page(STATEMENT+' VAT number GB123456789.'),profile()]))['verified']==1


def test_scottish_number_uses_shared_normalisation(db):
    cid=company(db)
    http=HTTP([page('Acme Ltd is registered in Scotland. Company number SC12345.'),profile(company_number='SC012345',jurisdiction='scotland')])
    assert verify(db,http)['verified']==1
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0]=='SC012345'
    assert http.calls[-1][0].endswith('/SC012345')


def test_registration_does_not_invent_unknown_operating_country(db):
    cid=company(db)
    assert verify(db,HTTP([page(),profile()]))['verified']==1
    row=db.one('SELECT country_iso2,hq_city,hq_region,hq_postcode FROM company WHERE id=?',(cid,))
    assert tuple(row)==(None,None,None,None)


@pytest.mark.parametrize('failure',[
    Response(SITE,200,page().text,{'X-Robots-Tag':'noindex'}),
    Response(SITE,200,'<meta name="robots" content="noindex">'+page().text,{}),
    Response(SITE,304,'',{}),
    TimeoutError('transport timeout'),
])
def test_unavailable_site_does_not_verify_and_retains_retryable_unknown(db,failure):
    cid=company(db)
    assert verify(db,HTTP([failure]))['verified']==0
    assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None
    assert db.one('SELECT 1 FROM observation WHERE company_id=? AND field=?',(cid,'website_identity_result'))


def test_real_client_checks_robots_for_each_same_domain_redirect(db,monkeypatch):
    import httpx
    from radar.fetch.http import HttpClient, RobotsDenied
    monkeypatch.setattr('radar.enrich.website_identity._public',lambda url,resolve:None)
    visited=[]; checked=[]
    class Robots:
        def allowed(self,url,agent): checked.append(url);return not url.endswith('/blocked')
        def crawl_delay(self,url,agent): return None
    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302,headers={'location':'/blocked'},request=request)
    client=HttpClient(transport=httpx.MockTransport(handler),robots=Robots())
    cid=company(db)
    try:
        assert verify(db,client)['verified']==0
        assert visited==[SITE] and checked==[SITE,SITE+'blocked']
        assert db.one('SELECT companies_house_no FROM company WHERE id=?',(cid,))[0] is None
    finally: client.close()


def test_role_word_elsewhere_does_not_hide_own_legal_identity(db):
    company(db)
    assert verify(db,HTTP([page(STATEMENT+' Our payment provider handles subscriptions.'),profile()]))['verified']==1
