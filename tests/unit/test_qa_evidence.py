"""QA sees bounded, cited identity and stored evidence; never guessed row facts."""
import json
from datetime import date
from dataclasses import replace
import pytest
from radar.config.defaults import default_config
from radar.qa.today import load_today_cards, record_check, TodayCheckResult, qa_state, build_user_prompt
from radar.sources.base import RawItem
from radar.sources.innovate_uk import PUBLICATION, citation_url
from tests.fakes import seed_companies

WORKBOOK='https://www.ukri.org/wp-content/uploads/2026/09/IUK-funded-projects-2016-present.xlsx'

def seed(db):
 cid=seed_companies(db,count=1,shortlist=1)[0]
 db.execute("UPDATE company SET companies_house_no='01823344',sic_codes='[\"64201\",\"99999\"]' WHERE id=?",(cid,))
 name=db.scalar('SELECT canonical_name FROM company WHERE id=?',(cid,))
 url=citation_url(PUBLICATION,'12345',name)
 db.execute('DELETE FROM company_source WHERE company_id=?',(cid,))
 db.execute("INSERT INTO company_source VALUES (?,?,?,?,?,?)",(cid,'innovate_uk','12345:01823344',url,'2026-09-01','2026-09-01'))
 db.execute("INSERT INTO signal(company_id,kind,occurred_on,headline,amount_gbp,source_key,source_url,first_seen) VALUES (?,?,?,?,?,?,?,?)",(cid,'grant_award','2026-08-01','Pilot project',2500,'innovate_uk',url,'2026-09-01'))
 item=RawItem('innovate_uk',citation_url(WORKBOOK,'12345',name),'12345:01823344',date(2026,8,1),'Pilot project',structured={'company_name':name,'company_number':'01823344','grant_reference':'12345','grant_amount_gbp':2500,'source_workbook_url':WORKBOOK,'competition':None,'enterprise_size':'Micro'},kind_hint='grant_award')
 return cid,url,item

def test_identity_and_legacy_signal_are_cited_without_fabricating_workbook_receipt(db):
 cid,url,item=seed(db);card=load_today_cards(db,default_config(),company_id=cid)[0]
 payload=json.loads(card.blob())
 assert payload['company_number']=='01823344'
 assert payload['sic_codes']==['64201','99999'] # raw codes, no sector guesses
 assert payload['registry_identity']['source_url'].endswith('/01823344')
 assert 'not proof of headquarters' in payload['registry_identity']['address_note']
 evidence=payload['source_evidence'][0]
 assert evidence['receipt_kind']=='stored_signal_and_citation_lookup'
 assert evidence['lookup']['project']=='12345'
 assert evidence['fields']=={'project_title':'Pilot project','grant_amount_gbp':2500.0,'signal_date':'2026-08-01'}
 assert 'source_workbook_url' not in evidence
 assert 'data, not instructions' in build_user_prompt(card)


def test_parsed_receipt_is_bounded_exact_and_missing_fields_remain_null(db):
 from radar.qa.evidence import record_innovate_receipt
 cid,url,item=seed(db)
 assert record_innovate_receipt(db,cid,item)
 assert not record_innovate_receipt(db,cid,item) # same facts do not append forever
 receipt=json.loads(db.scalar("SELECT value_json FROM observation WHERE field='innovate_uk_project_row'"))
 assert len(json.dumps(receipt).encode())<=2048
 assert receipt['fields']['competition'] is None
 assert receipt['fields']['enterprise_size']=='Micro'
 assert receipt['source_workbook_url']==WORKBOOK
 card=load_today_cards(db,default_config(),company_id=cid)[0]
 e=json.loads(card.blob())['source_evidence'][0]
 assert e['receipt_kind']=='parsed_official_workbook_row'
 assert e['citation_url']==url # current selected citation, not old workbook claim
 assert e['source_workbook_url']==WORKBOOK

@pytest.mark.parametrize('change',[{'company_name':'Different Company Ltd'},{'company_number':'99999999'},{'grant_reference':'other'}])
def test_wrong_participant_registry_number_or_project_cannot_attach_receipt(db,change):
 from radar.qa.evidence import record_innovate_receipt
 cid,url,item=seed(db)
 bad=replace(item,structured={**item.structured,**change})
 assert not record_innovate_receipt(db,cid,bad)
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==0


def test_identity_and_source_fact_changes_invalidate_approval_but_not_scores(db):
 from radar.qa.evidence import record_innovate_receipt
 cid,url,item=seed(db);record_innovate_receipt(db,cid,item)
 card=load_today_cards(db,default_config(),company_id=cid)[0]
 record_check(db,card,TodayCheckResult(verdict='pass',checker='test',summary='actual test review'))
 scores=[tuple(r) for r in db.query('SELECT * FROM score')]
 assert qa_state(db,cid)=='pass'
 record_innovate_receipt(db,cid,replace(item,structured={**item.structured,'enterprise_size':'Small'}))
 assert qa_state(db,cid)=='incomplete'
 current=load_today_cards(db,default_config(),company_id=cid)[0]
 record_check(db,current,TodayCheckResult(verdict='pass',checker='test'))
 db.execute("UPDATE company SET sic_codes='[\"64202\"]' WHERE id=?",(cid,))
 assert qa_state(db,cid)=='incomplete'
 assert [tuple(r) for r in db.query('SELECT * FROM score')]==scores


def test_other_companies_and_unrelated_source_rows_never_enter_card(db):
 from radar.qa.evidence import record_innovate_receipt
 cid,url,item=seed(db);record_innovate_receipt(db,cid,item)
 db.add_observation(cid,'innovate_uk_project_row',{'fields':{'company_name':'LEAKED OTHER ARTICLE'}},source_key='uktn',source_type='news',source_url=url)
 db.execute("INSERT INTO signal(company_id,kind,headline,source_key,source_url,first_seen) VALUES (?,?,?,?,?,?)",(cid,'grant_award','UNRELATED PROJECT','innovate_uk',citation_url(PUBLICATION,'999','Other Ltd'),'2026-09-29'))
 blob=load_today_cards(db,default_config(),company_id=cid)[0].blob()
 assert 'LEAKED OTHER ARTICLE' not in blob
 assert 'UNRELATED PROJECT' not in json.dumps(json.loads(blob)['source_evidence'])


def test_missing_or_malformed_registry_codes_stay_unknown(db):
 cid,url,item=seed(db);db.execute("UPDATE company SET companies_house_no=NULL,sic_codes='not json' WHERE id=?",(cid,))
 p=json.loads(load_today_cards(db,default_config(),company_id=cid)[0].blob())
 assert p['company_number'] is None and p['sic_codes'] is None
 assert p['registry_identity'] is None


def test_current_workbook_refresh_preview_then_apply_is_targeted_and_read_only_elsewhere(db):
 from pathlib import Path
 import httpx
 from radar.fetch.http import HttpClient
 from radar.qa.evidence import refresh_innovate_receipts
 cid,url,item=seed(db)
 db.execute("UPDATE company SET canonical_name='NATURAL NEGATIVE LTD',norm_key='naturalnegative',companies_house_no='15492053' WHERE id=?",(cid,))
 citation=citation_url(PUBLICATION,'10189566','NATURAL NEGATIVE LTD')
 db.execute('UPDATE company_source SET source_url=?,external_id=? WHERE company_id=?',(citation,'10189566:15492053',cid))
 before={t:[tuple(x) for x in db.query('SELECT * FROM '+t)] for t in ('company','score','signal','user_field')}
 calls=[]
 def handler(request):
  calls.append(str(request.url))
  if request.url.path=='/robots.txt':return httpx.Response(200,text='User-agent: *\nAllow: /')
  if request.url.path.rstrip('/')==urlsplit(PUBLICATION).path.rstrip('/'):
   return httpx.Response(200,text=f'<a href="{WORKBOOK}">2016 to present</a>')
  if str(request.url)==WORKBOOK:
   return httpx.Response(200,content=Path('tests/fixtures/sources/innovate_uk.xlsx').read_bytes())
  raise AssertionError(str(request.url))
 from urllib.parse import urlsplit
 with HttpClient(transport=httpx.MockTransport(handler),max_retries=0) as http:
  preview=refresh_innovate_receipts(db,http,[cid])
 assert preview['results'][0]['status']=='matched'
 assert not preview['results'][0]['applied']
 assert calls.count(WORKBOOK)==1 and calls.count(PUBLICATION)==1
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==0
 receipt=preview['results'][0]['receipt']
 assert receipt['fields']['company_name']=='NATURAL NEGATIVE LTD'
 assert receipt['fields']['project_description']
 assert len(json.dumps(receipt,ensure_ascii=False).encode())<=2048
 calls.clear()
 with HttpClient(transport=httpx.MockTransport(handler),max_retries=0) as http:
  applied=refresh_innovate_receipts(db,http,[cid],apply=True)
 assert applied['results'][0]['applied']
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==1
 assert before=={t:[tuple(x) for x in db.query('SELECT * FROM '+t)] for t in before}
 assert db.scalar('SELECT count(*) FROM company')==1 # never ingest other workbook companies


def test_refresh_ambiguous_or_unmatched_rows_do_not_write(db,monkeypatch):
 from radar.qa.evidence import refresh_innovate_receipts
 from radar.sources.innovate_uk import InnovateUkAdapter
 cid,url,item=seed(db)
 class Page:
  ok=True
  text=f'<a href="{WORKBOOK}">2016 to present</a>'
 class Http:
  def get(self,url):return Page()
 monkeypatch.setattr('radar.sources.innovate_uk._download',lambda http,url:b'fixture')
 monkeypatch.setattr(InnovateUkAdapter,'parse',lambda *a,**k:[item,item])
 result=refresh_innovate_receipts(db,Http(),[cid],apply=True)
 assert result['results'][0]['status']=='ambiguous'
 assert not result['results'][0]['applied']
 monkeypatch.setattr(InnovateUkAdapter,'parse',lambda *a,**k:[])
 assert refresh_innovate_receipts(db,Http(),[cid],apply=True)['results'][0]['status']=='unmatched'
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==0


def test_new_ingestion_preserves_real_row_without_company_project_inference(db):
 # Actual adapter row includes CRN, so identity resolves the same stored company.
 cid,url,item=seed(db)
 before=db.scalar('SELECT sector FROM company WHERE id=?',(cid,))
 from radar.pipeline import _record_signal
 _record_signal(db,cid,replace(item,structured={**item.structured,'project_description':'Consortium project, not a company product.'}),item.structured['company_name'],{})
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==1
 assert db.scalar('SELECT sector FROM company WHERE id=?',(cid,))==before


def test_refresh_targets_selected_project_not_another_grant_for_same_company(db, monkeypatch):
 from radar.qa.evidence import refresh_innovate_receipts
 from radar.sources.innovate_uk import InnovateUkAdapter
 cid,url,item=seed(db)
 other=replace(item,source_url=citation_url(WORKBOOK,'99999',item.structured['company_name']),
               structured={**item.structured,'grant_reference':'99999'})
 db.execute("INSERT INTO company_source VALUES (?,?,?,?,?,?)",(cid,'innovate_uk','99999:01823344',other.source_url,'2026-08-01','2026-08-01'))
 class Page:
  ok=True
  text=f'<a href="{WORKBOOK}">2016 to present</a>'
 class Http:
  def get(self,url):return Page()
 monkeypatch.setattr('radar.sources.innovate_uk._download',lambda http,url:b'fixture')
 monkeypatch.setattr(InnovateUkAdapter,'parse',lambda *a,**k:[other,item])
 result=refresh_innovate_receipts(db,Http(),[cid],apply=True)
 assert result['results'][0]['status']=='matched'
 assert result['results'][0]['receipt']['fields']['grant_reference']=='12345'
 assert db.scalar("SELECT count(*) FROM observation WHERE field='innovate_uk_project_row'")==1


def test_refresh_parser_filters_unrelated_company_items():
 from pathlib import Path
 from radar.sources.innovate_uk import InnovateUkAdapter
 from radar.resolve.normalise import norm_key
 payload=Path('tests/fixtures/sources/innovate_uk.xlsx').read_bytes()
 items=InnovateUkAdapter().parse(payload,source_url=WORKBOOK,
                                target_identities={(norm_key('NATURAL NEGATIVE LTD'),'15492053')})
 assert items
 assert all(i.structured['company_name']=='NATURAL NEGATIVE LTD' for i in items)


@pytest.mark.parametrize('raw,expected',[('SC1234','SC001234'),('1234','00001234'),('BADCODEX',None)])
def test_registry_number_uses_shared_normalization_with_known_shapes_only(raw, expected):
 from radar.qa.evidence import _number
 assert _number(raw)==expected
