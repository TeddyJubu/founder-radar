"""Bounded primary-source context for QA; never scores or approves a company."""
from __future__ import annotations
import json
import math
import re
from urllib.parse import parse_qs, urlsplit
from radar.resolve.normalise import norm_ch_number, norm_key

ROW_FIELD = 'innovate_uk_project_row'
MAX_RECEIPT_BYTES = 2048
FIELDS = ('company_name','company_number','grant_reference','project_title',
          'competition','enterprise_size','grant_amount_gbp','project_start_date','project_description')

def _query(db, sql, params=()):
    return list(db.query(sql, params)) if hasattr(db,'query') else list(db.execute(sql, params))

def _number(value):
    text = norm_ch_number(value)
    return text if text and re.fullmatch(r'(?:[0-9]{8}|[A-Z]{2}[0-9]{6})', text) else None

def _official(url, *, workbook=False):
    try:
        p=urlsplit(str(url or ''))
        return (p.scheme=='https' and p.hostname=='www.ukri.org' and
                not p.username and not p.password and p.port in (None,443) and
                (p.path.lower().endswith('.xlsx') if workbook else
                 (p.path.lower().endswith('.xlsx') or
                  p.path.rstrip('/')=='/publications/innovate-uk-funded-projects-since-2004')))
    except ValueError:
        return False

def lookup(url):
    """Fragment is a lookup aid, never proof that a workbook row was read."""
    if not _official(url):
        return {}
    q=parse_qs(urlsplit(url).fragment)
    return {k:str(q[k][0])[:200] for k in ('project','participant') if q.get(k)}

def _identity(db, cid):
    rows=_query(db,'SELECT canonical_name,companies_house_no FROM company WHERE id=?',(cid,))
    return dict(rows[0]) if rows else None

def _matches(identity, fields, project):
    return bool(identity and project and fields.get('grant_reference')==project and
                norm_key(str(fields.get('company_name') or ''))==norm_key(identity['canonical_name']) and
                _number(fields.get('company_number')) and
                _number(fields.get('company_number'))==_number(identity['companies_house_no']))

def receipt_for(db, cid, item):
    """Only an adapter-parsed row with the exact existing three-part identity."""
    if item.source_key!='innovate_uk' or item.kind_hint!='grant_award':
        return None
    s=item.structured or {}
    workbook=s.get('source_workbook_url')
    if not _official(workbook,workbook=True) or not _official(item.source_url):
        return None
    project=str(s.get('grant_reference') or '')
    fields={k:s.get(k) for k in FIELDS}
    fields.update(company_number=s.get('company_number') or s.get('crn'),
                  project_title=item.title,
                  project_start_date=str(item.published_at) if item.published_at else None)
    if not _matches(_identity(db,cid),fields,project):
        return None
    item_lookup=lookup(item.source_url)
    if item_lookup.get('project')!=project or norm_key(item_lookup.get('participant',''))!=norm_key(str(fields['company_name'])):
        return None
    sources=_query(db,"SELECT source_url FROM company_source WHERE company_id=? AND source_key='innovate_uk'",(cid,))
    if not any(lookup(r['source_url']).get('project')==project and norm_key(lookup(r['source_url']).get('participant',''))==norm_key(str(fields['company_name'])) for r in sources):
        return None
    for key,value in fields.items():
        if value is None:
            continue
        if key=='grant_amount_gbp':
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                return None
        elif not isinstance(value,str) or len(value)>(800 if key=='project_description' else 400):
            return None # do not silently turn a truncated name into identity evidence
    receipt={'receipt_kind':'parsed_official_workbook_row','source_key':'innovate_uk',
             'source_url':item.source_url,'source_workbook_url':workbook,
             'fields':fields}
    # The project prose has lowest priority. Keep identifiers and actual row
    # facts intact if a long description would exceed the receipt byte budget.
    while len(json.dumps(receipt,ensure_ascii=False).encode())>MAX_RECEIPT_BYTES and fields.get('project_description'):
        fields['project_description']=fields['project_description'][:-100] or None
    return receipt if len(json.dumps(receipt,ensure_ascii=False).encode())<=MAX_RECEIPT_BYTES else None

def _store(db,cid,receipt):
    raw=json.dumps(receipt,sort_keys=True,ensure_ascii=False)
    old=_query(db,"SELECT value_json FROM observation WHERE company_id=? AND field=? AND source_key='innovate_uk' ORDER BY id DESC LIMIT 1",(cid,ROW_FIELD))
    if old and old[0]['value_json']==raw:
        return False
    from radar.store.db import now_iso
    sql='''INSERT INTO observation(company_id,field,value_json,source_key,source_type,source_url,confidence,observed_at,extractor_ver)
           VALUES (?,?,?,?,?,?,?,?,?)'''
    args=(cid,ROW_FIELD,raw,'innovate_uk','grant',receipt['source_url'],1.0,now_iso(),'innovate-row-v1')
    db.execute(sql,args)
    return True

def record_innovate_receipt(db,cid,item):
    receipt=receipt_for(db,cid,item)
    return bool(receipt and _store(db,cid,receipt))

def record_source_receipt(db, cid, item):
    """Source-specific evidence stays outside the shared ingestion pipeline."""
    return record_innovate_receipt(db, cid, item)

def source_evidence(db,cid,key,url):
    """Current chosen citation plus exact parsed receipt, else honest legacy facts."""
    if key!='innovate_uk' or not _official(url):
        return ()
    aid=lookup(url);identity=_identity(db,cid)
    if not aid.get('project') or not identity or norm_key(aid.get('participant',''))!=norm_key(identity['canonical_name']):
        return ()
    rows=_query(db,"SELECT value_json,source_url,observed_at FROM observation WHERE company_id=? AND field=? AND source_key='innovate_uk' AND source_type='grant' ORDER BY id DESC LIMIT 20",(cid,ROW_FIELD))
    for row in rows:
        raw=row['value_json']
        if len(raw.encode())>MAX_RECEIPT_BYTES:
            continue
        try:r=json.loads(raw)
        except (ValueError,TypeError):continue
        if not isinstance(r,dict) or r.get('receipt_kind')!='parsed_official_workbook_row':continue
        fields=r.get('fields')
        if not isinstance(fields,dict) or set(fields)!=set(FIELDS):continue
        if not _matches(identity,fields,aid['project']) or not _official(r.get('source_workbook_url'),workbook=True):continue
        if r.get('source_key')!='innovate_uk' or r.get('source_url')!=row['source_url'] or not _official(row['source_url']):continue
        if lookup(row['source_url']).get('project')!=aid['project'] or norm_key(lookup(row['source_url']).get('participant',''))!=norm_key(identity['canonical_name']):continue
        # Stored download location is historical provenance, not a reachability claim.
        return ({**r,'citation_url':url,'recorded_at':row['observed_at'],'field_provenance':'All fields are from this named participant row in the historical workbook URL; null means the row did not supply a value.','workbook_status':'Historical download URL recorded when this row was parsed; not a current reachability claim.'},)
    signals=_query(db,"SELECT headline,amount_gbp,occurred_on FROM signal WHERE company_id=? AND source_key=? AND source_url=? AND kind='grant_award' ORDER BY id DESC LIMIT 1",(cid,key,url))
    if not signals:
        return ()
    s=signals[0]
    return ({'receipt_kind':'stored_signal_and_citation_lookup','source_key':key,
             'citation_url':url,'lookup':aid,
             'lookup_status':'Project and participant are citation lookup aids; no parsed workbook-row receipt is stored.',
             'field_provenance':{'project_title':'signal.headline','grant_amount_gbp':'signal.amount_gbp','signal_date':'signal.occurred_on'},
             'fields':{'project_title':str(s['headline'])[:400],
                       'grant_amount_gbp':s['amount_gbp'],'signal_date':s['occurred_on']}},)

def refresh_innovate_receipts(db,http,company_ids,*,apply=False):
    """Preview by default. One current official workbook for at most 500 IDs.

    Only evidence observations are written on apply; unmatched/ambiguous IDs
    remain unknown. No company facts, scores, signals or verdicts are changed.
    """
    ids=list(dict.fromkeys(company_ids))
    if len(ids)>500:raise ValueError('at most 500 target company IDs')
    from radar.qa.today import _http_source
    targets = {}
    by_identity = {}
    for cid in ids:
        co = _identity(db, cid)
        key, url = _http_source(db, cid)
        aid = lookup(url) if key == 'innovate_uk' else {}
        if co and aid.get('project') and _number(co['companies_house_no']):
            targets[cid] = aid
            by_identity.setdefault((norm_key(co['canonical_name']), _number(co['companies_house_no'])), []).append(cid)
    if not targets:
        return {'apply': apply, 'workbook_url': None, 'targets': len(ids),
                'results': [{'company_id': cid, 'matches': 0, 'status': 'unmatched', 'applied': False} for cid in ids]}
    from radar.sources.innovate_uk import InnovateUkAdapter,PUBLICATION,_download
    adapter=InnovateUkAdapter();page=http.get(PUBLICATION)
    if not page.ok:raise RuntimeError(f'official publication HTTP {page.status}')
    workbook=adapter.discover(page.text)
    if not _official(workbook,workbook=True):raise ValueError('non-official workbook')
    # Adapter already reads XLSX with the standard library. Do not ingest the
    # workbook's other companies or send each reviewer to download it again.
    items=adapter.parse(_download(http,workbook),since=None,source_url=workbook,
                        target_identities=set(by_identity))
    matches={cid:[] for cid in ids}
    for item in items:
        s=item.structured or {}
        for cid in by_identity.get((norm_key(str(s.get('company_name') or '')),_number(s.get('company_number') or s.get('crn'))),[]):
            if str(s.get('grant_reference') or '') != targets[cid]['project']:
                continue
            receipt=receipt_for(db,cid,item)
            if receipt:matches[cid].append(receipt)
    result=[]
    for cid,found in matches.items():
        # Identical duplicated rows are still ambiguous: never choose one by order.
        row={'company_id':cid,'matches':len(found),'status':'matched' if len(found)==1 else 'ambiguous' if found else 'unmatched','applied':False}
        if len(found)==1:
            row['receipt']=found[0]
            if apply:row['applied']=_store(db,cid,found[0])
        result.append(row)
    return {'apply':apply,'workbook_url':workbook,'targets':len(ids),'results':result}
