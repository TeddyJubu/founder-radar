"""The final checker must honor a preference versus an eligibility ban."""
from dataclasses import replace
import pytest
from radar.qa.today import TodayCard, TodayCheckResult, rules_precheck, build_user_prompt, record_check, cached_check


def card(**kwargs):
    base=TodayCard(company_id='company',name='Example startup',city='London',
                   fund_key='northstar',vehicle_key='eis_growth',geo_rule='SOFT',
                   geo_values=('north_england',))
    return replace(base,**kwargs)


@pytest.mark.parametrize('city',['London','Oxford','Cambridge'])
@pytest.mark.parametrize('region',['north_england','north_east','yorkshire'])
def test_preference_does_not_become_strict_city_veto(city,region):
    c=card(city=city,geo_values=(region,))
    assert rules_precheck(c) is None # still needs genuine final review
    assert 'not a hard eligibility restriction' in build_user_prompt(c)
    hard=replace(c,geo_rule='HARD')
    assert rules_precheck(hard).reason=='geography_mismatch'


def test_soft_focus_does_not_override_other_disqualifications():
    assert rules_precheck(card(on_vc_portfolio=True)).reason=='already_backed'
    assert rules_precheck(card(headlines=('Closes Series C funding',))).reason=='late_stage'


def test_only_affected_approvals_change_and_need_real_recheck(db,monkeypatch):
    from tests.fakes import seed_companies
    import radar.qa.today as qa
    ids=seed_companies(db,count=3,shortlist=3)
    affected=card(company_id=ids[0])
    hard=card(company_id=ids[1],geo_rule='HARD')
    ukwide=card(company_id=ids[2],fund_key='dsw',geo_values=('uk_regions',))
    # Record genuine seam results under the exact prior policy payload.
    with monkeypatch.context() as old:
        old.setattr(qa,'_soft_regional_focus',lambda _:False)
        old_hashes=[c.snapshot_hash() for c in (affected,hard,ukwide)]
        for c in (affected,hard,ukwide):
            record_check(db,c,TodayCheckResult(verdict='pass',checker='test'))
    assert affected.snapshot_hash()!=old_hashes[0]
    assert cached_check(db,affected) is None
    assert hard.snapshot_hash()==old_hashes[1] and cached_check(db,hard).verdict=='pass'
    assert ukwide.snapshot_hash()==old_hashes[2] and cached_check(db,ukwide).verdict=='pass'
    assert 'not a hard eligibility restriction' not in build_user_prompt(hard)
