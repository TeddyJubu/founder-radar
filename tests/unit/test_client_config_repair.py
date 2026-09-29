from copy import deepcopy
import pytest
from scripts.repair_client_config import read_state,plan_repair,apply_repair,OLD_LINE,OLD_OUTWARD,NEW_SECTORS
from radar.config.defaults import default_config
from radar.render.sheet import _weights_grid,_lists_grid,fund_criteria_seed_grid
from radar.render.formatting import IMPORTANCE_BANNER

class Gateway:
    def __init__(self):
        cfg=default_config()
        self.grids={'Scoring Weights':_weights_grid(cfg),'Lists':_lists_grid(cfg),
                    'Fund Criteria':fund_criteria_seed_grid(cfg),
                    'Sources':[['Source','Track','Enabled','Last OK','Items today','7-day avg','Status','Note'],
                               ['bethnal_green','A','TRUE','','','','','Bethnal Green Ventures portfolio']]}
        self.ids={tab:i for i,tab in enumerate(self.grids)}
        self.calls=[]
    def sheets(self):return self.ids
    def batch_get_formulas(self,tabs):return deepcopy(self.grids)
    def batch_requests(self,requests):
        self.calls.append(requests)
        for req in requests:
            if 'insertDimension' in req:
                r=req['insertDimension']['range'];tab=next(t for t,i in self.ids.items() if i==r['sheetId'])
                self.grids[tab][r['startIndex']:r['startIndex']]=[[] for _ in range(r['endIndex']-r['startIndex'])]
            else:
                data=req['updateCells'];r=data['range'];tab=next(t for t,i in self.ids.items() if i==r['sheetId'])
                while len(self.grids[tab])<=r['startRowIndex']:self.grids[tab].append([])
                row=self.grids[tab][r['startRowIndex']]
                while len(row)<=r['startColumnIndex']:row.append('')
                v=data['rows'][0]['values'][0]['userEnteredValue']
                row[r['startColumnIndex']]=next(iter(v.values()),'')

def legacy():
    gateway=Gateway()
    w=gateway.grids['Scoring Weights']
    gateway.grids['Scoring Weights']=[row for row in w if not(row and row[0]=='sector' and row[1] in NEW_SECTORS)]
    l=gateway.grids['Lists'];c=l[0].index('sector')
    for row in l[1:]:
        if len(row)>c and row[c] in NEW_SECTORS:row[c]=''
    code,mapping=l[0].index('sic_code'),l[0].index('sic_sector')
    for i,value in enumerate(['62012','62020','62090'],1):
        while len(l[i])<=max(code,mapping):l[i].append('')
        l[i][code]=value;l[i][mapping]='b2b_saas'
    f=gateway.grids['Fund Criteria'];sector=f[0].index('Sectors +');line=f[0].index('One-liner')
    for row in f[1:]:
        if row[0]=='outward':row[sector]=','.join(OLD_OUTWARD);row[line]=OLD_LINE
    return gateway

def test_repair_preserves_existing_formulas_weights_and_is_idempotent():
    gw=legacy();w=gw.grids['Scoring Weights']
    w[2][2]='=SUM(C3:C4)';w[3][4]=0
    banner=next(i for i,row in enumerate(w) if row[0]==IMPORTANCE_BANNER)
    w[banner+2][1]='=8+1'
    before=deepcopy(w)
    plan=plan_repair(read_state(gw))
    assert not gw.calls
    apply_repair(gw,plan['plan_hash'])
    assert len(gw.calls)==1
    after=gw.grids['Scoring Weights']
    assert after[:banner]==before[:banner]
    assert after[banner+5:]==before[banner:]
    assert not plan_repair(read_state(gw))['requests']

def test_custom_values_and_existing_new_rows_not_overwritten():
    gw=Gateway();w=gw.grids['Scoring Weights'];custom=next(row for row in w if row[:2]==['sector','legaltech'])
    custom[4]='=2+2'
    f=gw.grids['Fund Criteria'];row=next(row for row in f if row and row[0]=='outward')
    row[f[0].index('One-liner')]='Private client thesis';row[f[0].index('Sectors +')]='fintech,other'
    l=gw.grids['Lists'];c=l[0].index('sic_code');m=l[0].index('sic_sector');l[1][c]='62012';l[1][m]='industrial_tech'
    gw.grids['Sources'][1][7]='Custom note'
    plan=plan_repair(read_state(gw));assert not plan['requests']
    assert len(plan['skipped'])==4
    assert 'Private client thesis' not in str(plan)

def test_changed_sheet_refuses_stale_approval_without_writing():
    gw=legacy();plan=plan_repair(read_state(gw))
    gw.grids['Scoring Weights'][2][2]='=7*2'
    with pytest.raises(ValueError,match='changed'):apply_repair(gw,plan['plan_hash'])
    assert not gw.calls


def test_empty_bgv_note_gets_the_honest_denylist_description():
    gw=Gateway()
    gw.grids['Sources'][1][7]=''
    plan=plan_repair(read_state(gw))
    assert len(plan['changes'])==1
    assert plan['changes'][0]['before']==''
    assert 'already invested' in plan['changes'][0]['after']


def test_missing_banner_fails_before_any_write():
    gw=legacy()
    gw.grids['Scoring Weights']=[row for row in gw.grids['Scoring Weights'] if not(row and row[0]==IMPORTANCE_BANNER)]
    with pytest.raises(ValueError,match='banner'):plan_repair(read_state(gw))
    assert not gw.calls


def test_real_gateway_requests_formula_rendering():
    class Spreadsheet:
        def values_batch_get(self,ranges,params):
            assert params=={'valueRenderOption':'FORMULA'}
            assert ranges==["'Scoring Weights'","'Lists'","'Fund Criteria'","'Sources'"]
            return {'valueRanges':[{'values':[["=1+1"]]} for _ in ranges]}
    class RealStyleGateway:
        _sh=Spreadsheet()
        def sheets(self):return {tab:i for i,tab in enumerate(['Scoring Weights','Lists','Fund Criteria','Sources'])}
    state=read_state(RealStyleGateway())
    assert state['grids']['Scoring Weights'][0][0]=='=1+1'
