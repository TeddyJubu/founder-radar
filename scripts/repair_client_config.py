"""Preview a narrow client configuration repair; one atomic Sheets batch on apply.

Run from the installed application with its normal Sheet credentials. No
company/verdict tabs are read or written. FORMULA reads protect custom cells;
preview prints only intended default-cell changes and opaque approval hashes.
Google Sheets offers no conditional compare-and-swap: pause other editors
between review and apply. The helper re-reads immediately before writing.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

# Also support direct execution from the checkout, without installing scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar.config.defaults import default_config, VEHICLES, DEFAULT_SOURCES
from radar.render.sheet import open_gateway
from radar.render.formatting import IMPORTANCE_BANNER, col_letter

TABS=('Scoring Weights','Lists','Fund Criteria','Sources')
NEW_SECTORS=('legaltech','proptech','cybersecurity','hr_tech','data_infrastructure')
OLD_OUTWARD=('fintech','insurtech','regtech','lending','wealthtech','ai_data')
OLD_LINE='Send if finance is the product or an essential layer in the workflow.'


def read_state(gateway):
    ids=gateway.sheets()
    if any(tab not in ids for tab in TABS):
        raise ValueError('Required configuration tab missing; do not create or reseed it automatically')
    if hasattr(gateway,'batch_get_formulas'):
        grids=gateway.batch_get_formulas(TABS)
    else:
        ranges=[f"'{tab}'" for tab in TABS]
        reply=gateway._sh.values_batch_get(ranges,params={'valueRenderOption':'FORMULA'})
        grids={tab:entry.get('values',[]) for tab,entry in zip(TABS,reply['valueRanges'])}
    return {'ids':{tab:ids[tab] for tab in TABS},'grids':grids}


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _column(grid,name):
    found=[i for i,value in enumerate(grid[0]) if str(value).strip().casefold()==name.casefold()]
    if len(found)!=1:
        raise ValueError(f'Missing or ambiguous {name} header')
    return found[0]


def _value(row,col):
    return row[col] if col<len(row) else ''


def plan_repair(state):
    grids,ids=state['grids'],state['ids']
    requests,changes,skipped=[],[],[]
    def put(tab,row,col,old,new):
        cell=f"{col_letter(col)}{row+1}"
        value={} if new=='' else {'numberValue':new} if isinstance(new,(int,float)) else {'stringValue':str(new)}
        requests.append({'updateCells':{'range':{'sheetId':ids[tab],'startRowIndex':row,'endRowIndex':row+1,
                                               'startColumnIndex':col,'endColumnIndex':col+1},
                                        'rows':[{'values':[{'userEnteredValue':value}]}],
                                        'fields':'userEnteredValue'}})
        changes.append({'tab':tab,'cell':cell,'before':old,'after':new})
    weights=grids['Scoring Weights']
    if not weights:
        raise ValueError('Scoring Weights is empty; refusing to reseed client weights')
    attr,category=_column(weights,'Attribute'),_column(weights,'Category')
    banners=[i for i,row in enumerate(weights) if str(_value(row,attr)).strip()==IMPORTANCE_BANNER]
    if len(banners)!=1:
        raise ValueError('Exactly one attribute importance banner required')
    banner=banners[0]
    existing={str(_value(row,category)).strip() for row in weights[1:banner]
              if str(_value(row,attr)).strip()=='sector'}
    missing=[sector for sector in NEW_SECTORS if sector not in existing]
    if missing:
        requests.append({'insertDimension':{'range':{'sheetId':ids['Scoring Weights'],'dimension':'ROWS',
                                                    'startIndex':banner,'endIndex':banner+len(missing)},
                                            'inheritFromBefore':True}})
        cfg=default_config()
        funds={fund:_column(weights,fund.title()) for fund in ('dsw','northstar','outward','anticus')}
        policy=_column(weights,'Unknown policy')
        for offset,sector in enumerate(missing):
            row=banner+offset
            for col,value in [(attr,'sector'),(category,sector),(policy,'neutral')]:
                put('Scoring Weights',row,col,'',value)
            for fund,col in funds.items():
                put('Scoring Weights',row,col,'',cfg.weights.matrix['sector'][sector][fund])
    lists=grids['Lists']
    sector_col=_column(lists,'sector')
    present={str(_value(row,sector_col)).strip() for row in lists[1:]}
    last=max([i for i,row in enumerate(lists) if _value(row,sector_col)!=''],default=0)
    for sector in NEW_SECTORS:
        if sector not in present:
            last+=1
            put('Lists',last,sector_col,'',sector)
    code_col,mapped_col=_column(lists,'sic_code'),_column(lists,'sic_sector')
    for row_index,row in enumerate(lists[1:],1):
        code=str(_value(row,code_col)).strip()
        mapped=_value(row,mapped_col)
        if code in ('62012','62020','62090'):
            if mapped=='b2b_saas':
                put('Lists',row_index,code_col,_value(row,code_col),'')
                put('Lists',row_index,mapped_col,mapped,'')
            elif mapped:
                skipped.append({'tab':'Lists','cell':f'{col_letter(mapped_col)}{row_index+1}',
                                'reason':'Custom SIC mapping preserved'})
    criteria=grids['Fund Criteria']
    fund_col,vehicle_col=_column(criteria,'Fund key'),_column(criteria,'Vehicle key')
    outward=next(vehicle for vehicle in VEHICLES if vehicle['fund_key']=='outward' and vehicle['vehicle_key']=='fund_ii')
    for row_index,row in enumerate(criteria[1:],1):
        if _value(row,fund_col)!='outward' or _value(row,vehicle_col)!='fund_ii':
            continue
        for header,old,new in [('Sectors +',','.join(OLD_OUTWARD),','.join(outward['sectors_plus'])),
                               ('One-liner',OLD_LINE,outward['one_liner'])]:
            col=_column(criteria,header);current=_value(row,col)
            matches=(not str(current).startswith('=') and {token.strip() for token in str(current).replace(';',',').split(',')}==set(OLD_OUTWARD)) if header=='Sectors +' else current==old
            if matches:
                put('Fund Criteria',row_index,col,current,new)
            elif current!=new:
                skipped.append({'tab':'Fund Criteria','cell':f'{col_letter(col)}{row_index+1}',
                                'reason':'Custom Outward value preserved'})
    sources=grids['Sources'];source_col,note_col=_column(sources,'Source'),_column(sources,'Note')
    note=next(source.note for source in DEFAULT_SOURCES if source.key=='bethnal_green')
    for row_index,row in enumerate(sources[1:],1):
        if _value(row,source_col)=='bethnal_green':
            current=_value(row,note_col)
            if current=='Bethnal Green Ventures portfolio':
                put('Sources',row_index,note_col,current,note)
            elif current!=note:
                skipped.append({'tab':'Sources','cell':f'{col_letter(note_col)}{row_index+1}',
                                'reason':'Custom BGV note preserved'})
    result={'state_hash':_hash(state),'changes':changes,'skipped':skipped,'requests':requests}
    result['plan_hash']=_hash(result)
    return result


def apply_repair(gateway,approved_hash):
    current=plan_repair(read_state(gateway))
    if current['plan_hash']!=approved_hash:
        raise ValueError('Sheet or plan changed; preview and review again')
    if current['requests']:
        gateway.batch_requests(current['requests'])
    return len(current['changes'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--plan-hash')
    args=parser.parse_args()
    if args.apply and not args.plan_hash:
        parser.error('--apply requires the reviewed --plan-hash')
    gateway=open_gateway()
    if args.apply:
        print(json.dumps({'changed_cells':apply_repair(gateway,args.plan_hash)}))
    else:
        plan=plan_repair(read_state(gateway))
        print(json.dumps({key:plan[key] for key in ('plan_hash','changes','skipped')},indent=2))


if __name__=='__main__':
    main()
