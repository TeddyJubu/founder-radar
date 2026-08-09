"""09-test-plan §2.3 — vehicle routing.

The point of modelling eleven vehicles instead of four funds: the same company
can be a hard reject for one fund and a strong fit for another, for a reason
that has nothing to do with quality. A Durham spinout must land in Spinout
Inspire, not the catch-all Innovation Fund; a Leeds company must be rejected
by all four North East vehicles but accepted by Finance Yorkshire.
"""

from __future__ import annotations

import pytest

from radar.config.defaults import default_config
from radar.score.gates import evaluate_vehicle_gates

from tests.factories import C, score_one


@pytest.fixture
def cfg():
    return default_config()


# The eleven rows of 09-test-plan §2.3, in order.
#
# (label, company_kwargs, fund, expect, vehicle)
#   vehicle is None -> route the whole fund. `expect` is the winning vehicle
#                      key, or None when every vehicle rejects.
#   vehicle is set  -> evaluate that vehicle's gate alone; `expect` is its
#                      reject reason. Row 7 needs this shape: DSW's SEIS fund
#                      rejects an Oxford company on the golden triangle while
#                      DSW *as a fund* still routes, to eis_service. A
#                      fund-level assertion cannot say that.
ROUTES = [
    # Durham University spinout, Newcastle → the specialist vehicle, not the catch-all
    ("durham spinout", dict(is_university_spinout=True, spinout_university="durham",
                            geography="north_east"),
     "northstar", "spinout_inspire", None),
    # Newcastle, NOT a spinout — the spinout vehicle fails, the fund does not
    ("newcastle, not a spinout", dict(is_university_spinout=False, geography="north_east",
                                      hq_postcode="NE1 4ST"),
     "northstar", "ne_innovation_fund", None),
    # Sunderland software company
    ("sunderland software", dict(is_university_spinout=False, geography="north_east",
                                 hq_city="Sunderland", hq_postcode="SR1 1AA"),
     "northstar", "venture_sunderland", None),
    # Leeds: fails all four North East vehicles; eis_growth passes on the SOFT rule
    ("leeds", dict(geography="yorkshire", hq_postcode="LS1 4AP",
                   stage="seed", seis_eis_qualifying=True),
     "northstar", "eis_growth", None),
    # Leeds → Finance Yorkshire Seedcorn (Yorkshire is a hard mandate there)
    ("leeds", dict(geography="yorkshire", hq_postcode="LS1 4AP"),
     "anticus", "fy_seedcorn", None),
    # Newcastle → Anticus is a hard reject: Yorkshire is the mandate
    ("newcastle", dict(geography="north_east", hq_postcode="NE1 4ST"),
     "anticus", None, None),
    # Oxford fintech → DSW's SEIS fund rejects on the golden triangle. This is
    # an outcode-prefix check, not a fuzzy city match (06-scoring §2.2): OX1 is
    # inside the triangle. DSW as a fund still routes, to eis_service, which is
    # why this row has to name the vehicle.
    ("oxford fintech", dict(sector="fintech", geography="uk_regions", hq_postcode="OX1 1AA"),
     "dsw", "geography:outside_golden_triangle", "seis_fund"),
    # Oxford fintech → Outward passes (UK-wide), though DSW's SEIS fund rejects it
    ("oxford fintech", dict(sector="fintech", geography="uk_regions", hq_postcode="OX1 1AA"),
     "outward", "fund_ii", None),
    # London fintech that raised £22m → Outward rejects on prior_total_max
    ("london fintech raised 22m", dict(sector="fintech", geography="london",
                                       prior_total_gbp=22_000_000, last_round_gbp=2_000_000),
     "outward", None, None),
    # Lending business → DSW rejects on the SEIS/EIS excluded trade
    ("lending", dict(sector="lending"),
     "dsw", None, None),
    # ...but the SAME lending business passes Outward, which has no EIS constraint
    ("lending", dict(sector="lending"),
     "outward", "fund_ii", None),

    # --- beyond §2.3: the two DSW vehicles the canonical table never routes to.
    # Without these, six of the eleven vehicles were never asserted as a
    # destination, so a config edit could silently retire one.
    # Leeds, still at idea stage → DSW SEIS (stage_max pre_seed, outside the
    # triangle). It scores below the floor, but the *route* is the claim here.
    ("leeds seis, idea stage", dict(stage="idea", geography="yorkshire",
                                    hq_postcode="LS1 4AP", seis_eis_qualifying=True,
                                    sector="b2b_saas"),
     "dsw", "seis_fund", None),
    # The same company one stage later: SEIS closes at pre_seed, so the EIS
    # service takes it. This pair is what makes the two vehicles distinguishable.
    ("leeds eis, seed stage", dict(stage="seed", geography="yorkshire",
                                   hq_postcode="LS1 4AP", seis_eis_qualifying=True,
                                   sector="b2b_saas"),
     "dsw", "eis_service", None),
]


@pytest.mark.parametrize("label,kw,fund,expect,vehicle", ROUTES)
def test_vehicle_routing(cfg, label, kw, fund, expect, vehicle):
    """The route is what is pinned here: which vehicle's mandate the company
    satisfies. A company can route to a vehicle and still score below the
    watchlist floor — the gate decides eligibility, the score decides
    preference within it (06-scoring §4.6)."""
    company = C(**kw)
    if vehicle is not None:
        target = next(v for v in cfg.fund(fund).vehicles if v.vehicle_key == vehicle)
        verdict = evaluate_vehicle_gates(company, target, cfg)
        assert verdict.passed is False, label
        assert verdict.reason == expect, label
        return

    s = score_one(company, fund, cfg=cfg)
    if expect is None:
        assert s.tier == "reject", label
        assert s.reject_reason == "no_eligible_vehicle", label
    else:
        assert s.vehicle_key == expect, label


def test_every_active_vehicle_is_reachable_except_fy_growth(cfg):
    """Which vehicles can actually win, pinned as a fact rather than left to luck.

    `fy_growth` cannot be selected under the default settings, and that is
    consistent rather than broken: it opens at `stage_min='seed'` with the same
    Yorkshire mandate and cheque range as `fy_seedcorn`, so wherever both pass
    the tie goes to `fy_seedcorn` on document order. The only stages where
    `fy_growth` passes alone are `series_b_plus` and `growth`, and the universal
    freshness gate rejects both at `max_stage='series_a'`. A growth fund cannot
    match a radar that only looks at pre-Series-A companies.

    It is asserted here so that it is a known property. Raise `max_stage` in
    Settings and this test fails, which is the moment someone should notice
    that an eleventh vehicle just came into play.

    `bbi_coinvest` and `ne_social` are `active=False` and excluded by design.
    """
    routed = {expect for _, _, _, expect, veh in ROUTES if veh is None and expect}
    active = {v.vehicle_key for f in cfg.funds for v in f.active_vehicles}

    assert active - routed == {"fy_growth"}, (
        "a vehicle changed reachability — re-read the docstring before editing")
    assert "fy_growth" not in routed
    assert cfg.settings.max_stage == "series_a"      # the reason it is excluded


def test_prior_total_max_is_the_reject_reason(cfg):
    c = C(sector="fintech", geography="london",
          prior_total_gbp=22_000_000, last_round_gbp=2_000_000)
    fund_ii = cfg.fund("outward").vehicles[0]
    verdict = evaluate_vehicle_gates(c, fund_ii, cfg)
    assert verdict.passed is False
    assert verdict.reason == "prior_total_max"


def test_excluded_trade_is_the_reject_reason(cfg):
    c = C(sector="lending")
    eis_service = cfg.fund("dsw").vehicles[1]
    verdict = evaluate_vehicle_gates(c, eis_service, cfg)
    assert verdict.passed is False
    assert verdict.reason == "seis_eis_excluded_trade"
