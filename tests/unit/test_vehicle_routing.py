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


# (label, company_kwargs, fund, expected_vehicle_or_None_if_rejected)
ROUTES = [
    # Durham University spinout, Newcastle → the specialist vehicle, not the catch-all
    ("durham spinout", dict(is_university_spinout=True, spinout_university="durham",
                            geography="north_east"),
     "northstar", "spinout_inspire"),
    # Newcastle, NOT a spinout — the spinout vehicle fails, the fund does not
    ("newcastle, not a spinout", dict(is_university_spinout=False, geography="north_east",
                                      hq_postcode="NE1 4ST"),
     "northstar", "ne_innovation_fund"),
    # Sunderland software company
    ("sunderland software", dict(is_university_spinout=False, geography="north_east",
                                 hq_city="Sunderland", hq_postcode="SR1 1AA"),
     "northstar", "venture_sunderland"),
    # Leeds: fails all four North East vehicles; eis_growth passes on the SOFT rule
    ("leeds", dict(geography="yorkshire", hq_postcode="LS1 4AP",
                   stage="seed", seis_eis_qualifying=True),
     "northstar", "eis_growth"),
    # Leeds → Finance Yorkshire Seedcorn (Yorkshire is a hard mandate there)
    ("leeds", dict(geography="yorkshire", hq_postcode="LS1 4AP"),
     "anticus", "fy_seedcorn"),
    # Newcastle → Anticus is a hard reject: Yorkshire is the mandate
    ("newcastle", dict(geography="north_east", hq_postcode="NE1 4ST"),
     "anticus", None),
    # Oxford fintech → Outward passes (UK-wide), though DSW's SEIS fund rejects it
    ("oxford fintech", dict(sector="fintech", geography="uk_regions", hq_postcode="OX1 1AA"),
     "outward", "fund_ii"),
    # London fintech that raised £22m → Outward rejects on prior_total_max
    ("london fintech raised 22m", dict(sector="fintech", geography="london",
                                       prior_total_gbp=22_000_000, last_round_gbp=2_000_000),
     "outward", None),
    # Lending business → DSW rejects on the SEIS/EIS excluded trade
    ("lending", dict(sector="lending"),
     "dsw", None),
    # ...but the SAME lending business passes Outward, which has no EIS constraint
    ("lending", dict(sector="lending"),
     "outward", "fund_ii"),
]


@pytest.mark.parametrize("label,kw,fund,expect_vehicle", ROUTES)
def test_vehicle_routing(cfg, label, kw, fund, expect_vehicle):
    """The route is what is pinned here: which vehicle's mandate the company
    satisfies. A company can route to a vehicle and still score below the
    watchlist floor — the gate decides eligibility, the score decides
    preference within it (06-scoring §4.6)."""
    s = score_one(C(**kw), fund, cfg=cfg)
    if expect_vehicle is None:
        assert s.tier == "reject", label
        assert s.reject_reason == "no_eligible_vehicle", label
    else:
        assert s.vehicle_key == expect_vehicle, label


def test_oxford_fintech_fails_dsw_seis_on_golden_triangle(cfg):
    """The golden-triangle rule is an outcode-prefix check, not a fuzzy city
    match (06-scoring §2.2): OX1 is inside the triangle, so DSW's SEIS fund
    rejects it even though the fund as a whole still has a route (eis_service)."""
    c = C(sector="fintech", geography="uk_regions", hq_postcode="OX1 1AA")
    seis_fund = cfg.fund("dsw").vehicles[0]      # seis_fund is the first vehicle
    verdict = evaluate_vehicle_gates(c, seis_fund, cfg)
    assert verdict.passed is False
    assert verdict.reason == "geography:outside_golden_triangle"


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
