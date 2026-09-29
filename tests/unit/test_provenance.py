from datetime import datetime, timezone, timedelta
import httpx
import pytest
from radar.qa.provenance import verify_source, cached_outcome, repair_plan, apply_repair
from radar.fetch.http import HttpClient
from radar.fetch.ratelimit import RateLimiter
from radar.sources.innovate_uk import ADAPTER, PUBLICATION
from tests.unit.test_phase8_sources import load_bytes


def client(handler):
    return HttpClient(transport=httpx.MockTransport(handler), obey_robots=False,
                      max_retries=0, limiter=RateLimiter(default_delay=0.00001))


@pytest.mark.parametrize('status,state', [(200,'reachable'),(404,'dead'),(410,'dead'),(403,'blocked'),(429,'blocked'),(500,'error')])
def test_link_get_classification_and_cache(db, status, state):
    calls=[]
    def handle(req):
        calls.append(req.method)
        return httpx.Response(status, text='page')
    with client(handle) as http:
        result=verify_source(db,'https://source.test/article',http=http,resolve=False)
        assert result.state==state and result.status==status
        assert verify_source(db,'https://source.test/article',http=http,resolve=False).cached
    assert calls==['GET']
    assert cached_outcome(db,'https://source.test/article').state==state


def test_redirects_checked_and_private_destination_refused(db):
    seen=[]
    def handle(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={'Location':'http://127.0.0.1/secret'})
    with client(handle) as http:
        result=verify_source(db,'https://source.test/a',http=http,resolve=False)
    assert result.state=='invalid' and len(seen)==1


def test_redirect_success_timeout_and_expiry(db):
    def handle(req):
        if req.url.path=='/old':
            return httpx.Response(301,headers={'Location':'/new'})
        return httpx.Response(200)
    now=datetime(2026,9,30,tzinfo=timezone.utc)
    with client(handle) as http:
        result=verify_source(db,'https://source.test/old',http=http,resolve=False,now=now)
    assert result.final_url=='https://source.test/new' and result.state=='reachable'
    assert cached_outcome(db,result.url,now=now+timedelta(days=2)) is None
    def timeout(req):
        raise httpx.ReadTimeout('too slow')
    with client(timeout) as http:
        assert verify_source(db,'https://source.test/slow',http=http,resolve=False).state=='timeout'


def test_innovate_cites_actual_workbook_and_identifies_participant():
    url='https://www.ukri.org/wp-content/uploads/2026/09/2016-to-present.xlsx'
    item=ADAPTER.parse(load_bytes('innovate_uk.xlsx'), source_url=url)[0]
    assert item.source_url.startswith(url+'#')
    assert item.structured['grant_reference'] in item.source_url
    assert item.structured['company_name'].replace(' ','%20') in item.source_url
    assert ADAPTER.parse(load_bytes('innovate_uk.xlsx'))[0].source_url.startswith(PUBLICATION+'#')


def test_repair_is_reviewed_exact_and_invalidates_old_checks(db):
    from tests.fakes import seed_companies
    cid=seed_companies(db,count=1)[0]
    old='https://gtr.ukri.org/projects?ref=12345'
    db.execute("INSERT INTO company_source VALUES (?,?,?,?,?,?)",(cid,'innovate_uk','12345:12345678',old,'2020','2020'))
    db.execute("INSERT INTO observation(company_id,field,value_json,source_key,source_type,source_url,confidence,observed_at,extractor_ver) VALUES (?,?,?,?,?,?,?,?,?)",(cid,'sector','null','innovate_uk','grant',old,1,'2020','test'))
    db.execute("INSERT INTO signal(company_id,kind,headline,source_key,source_url,first_seen) VALUES (?,?,?,?,?,?)",(cid,'grant_award','Grant','innovate_uk',old,'2020'))
    plan=repair_plan(db)
    assert len(plan['changes'])==3
    with pytest.raises(ValueError): apply_repair(db,plan,expected_hash='wrong')
    apply_repair(db,plan,expected_hash=plan['hash'])
    assert repair_plan(db)['changes']==[]
    assert not db.scalar('SELECT count(*) FROM today_check WHERE company_id=?',(cid,))
    assert all(PUBLICATION in db.scalar(f'SELECT source_url FROM {table} WHERE source_key=?',('innovate_uk',)) for table in ['company_source','signal','observation'])


def test_workbook_participant_fragments_share_one_document_check(db):
    calls=[]
    def handle(req):
        calls.append(str(req.url))
        return httpx.Response(200)
    with client(handle) as http:
        verify_source(db,'https://source.test/book.xlsx#project=1',http=http,resolve=False)
        assert verify_source(db,'https://source.test/book.xlsx#project=2',http=http,resolve=False).cached
    assert calls==['https://source.test/book.xlsx']
    assert cached_outcome(db,'https://source.test/book.xlsx#project=3').state=='reachable'


@pytest.mark.parametrize('url',['http://[broken','ftp://source.test/file','https://user:secret@source.test/','http://localhost/'])
def test_invalid_urls_record_outcome_without_get(db,url):
    def no_get(req):
        raise AssertionError('Invalid URL must never be fetched')
    with client(no_get) as http:
        assert verify_source(db,url,http=http,resolve=False).state=='invalid'
