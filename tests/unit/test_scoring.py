"""09-test-plan §2.2 — the scoring engine: derivation, percentage-of-known,
the unknown-never-zero invariant, reproducibility and the worked example."""

from __future__ import annotations

import re
from datetime import date

import pytest

from radar.config.defaults import default_config
from radar.config.models import SCORED_ATTRIBUTES
from radar.pipeline import evaluate
from radar.score.derive import Company, Signal, derive_attributes
from radar.score.fund_fit import fund_fit

from tests.factories import C, F, months_ago, registry_company, score_all, score_one


@pytest.fixture
def cfg():
    return default_config()


# ------------------------------------------------------------- the METzero fix


# 06-scoring §11, verbatim. `seis_eis_qualifying` is set because §7 says the
# tax-scheme eligibility split is real and DSW is an SEIS/EIS-only house — a
# spinout with a disclosed £450k round qualifies. Without it, Northstar's
# `eis_growth` (also `requires_seis_eis`) would flag `gate_unverified` and the
# flagship "already on their radar" explanation could never appear.
METZERO_FIXTURE = Company(
    id="metzero",
    canonical_name="METzero Technologies",
    norm_key="metzerotechnologies",
    country_iso2="GB",
    hq_region="north_east",
    hq_postcode="NE1 4ST",
    incorporated_on=date(2024, 3, 11),
    sector="climate_tech",
    stage="seed",
    founder_signal="research_spinout",
    traction_signal="clinical_grant_validation",
    total_funding_gbp=450_000,
    news_mention_count=2,
    on_vc_portfolio=False,
    discovery_route="spinout",
    is_university_spinout=True,
    spinout_university="durham",
    seis_eis_qualifying=True,
    qualifiers=["spinout", "press", "grant"],
    signals=[
        Signal(kind="spinout", headline="Northern Accelerator spinout announcement",
               occurred_on=date(2026, 7, 28)),
        Signal(kind="grant_award", headline="Innovate UK award matched",
               occurred_on=date(2026, 6, 1)),
        Signal(kind="press", headline="Regional press article",
               occurred_on=date(2026, 6, 20)),
    ],
)


def test_worked_example_metzero(cfg):
    """Every number in 06-scoring.md §11, asserted."""
    s = score_all(METZERO_FIXTURE, cfg)
    assert s["northstar"].vehicle_key    == "spinout_inspire"
    assert s["northstar"].fund_fit_pct   == pytest.approx(92.2, abs=0.1)
    assert s["northstar"].coverage       == 1.00
    assert s["northstar"].discovery_edge == pytest.approx(51.0, abs=0.1)
    assert s["northstar"].priority       == pytest.approx(75.7, abs=0.1)
    assert s["northstar"].tier           == "watchlist"      # edge 51 < 55
    assert "already on their radar"      in s["northstar"].explanation
    assert s["anticus"].reject_reason    == "no_eligible_vehicle"
    assert s["outward"].fund_fit_pct     == pytest.approx(15.0, abs=0.1)
    assert s["outward"].tier             == "reject"
    assert s["dsw"].vehicle_key          == "eis_service"    # seis_fund fails on stage
    assert s["dsw"].fund_fit_pct         == pytest.approx(54.7, abs=0.1)
    assert s["dsw"].tier                 == "watchlist"


# ------------------------------------------------------------ the invariants


def test_percentage_of_known_not_raw_sum(cfg):
    """Adding a criterion must not inflate every existing score."""
    company = C(sector="climate_tech")
    before = fund_fit(company, cfg.fund("northstar"), cfg)
    cfg2 = cfg.model_copy(deep=True)
    cfg2.lists["scored_attributes"] = list(SCORED_ATTRIBUTES) + ["bonus_attr"]
    after = fund_fit(company, cfg.fund("northstar"), cfg2)
    assert abs(before.pct - after.pct) < 1.0


def test_unknown_never_becomes_zero(cfg):
    """The single most important invariant in the scoring code."""
    c = C(sector=None)
    comp = next(x for x in fund_fit(c, cfg.fund("dsw"), cfg).components if x.key == "sector")
    assert comp.sub_score is None          # not 0.0
    assert comp.evidence == "unknown"


def test_one_known_attribute_cannot_shortlist(cfg):
    """Without a coverage floor, the shortlist fills with companies we
    know nothing about. This is the trap percentage-of-known creates.
    NOTE geography is present — a NULL region would trip min_uk_presence
    and make this a reject, testing the wrong thing."""
    c = C(sector="climate_tech", geography="north_east",
          stage=None, founder_signal=None, traction_signal=None)
    s = score_one(c, fund="northstar", cfg=cfg)
    assert s.fund_fit_pct == 100.0
    assert s.coverage < 0.5
    assert s.tier == "watchlist"           # NOT shortlist


def test_derivation_lets_a_registry_company_shortlist(cfg):
    """THE regression guard on the registry-first fix. Without the
    derivation rules in 06-scoring §2 this company scores on geography
    alone, fails the coverage floor, and the whole Track B idea is dead."""
    c = registry_company(                       # ONLY register-derived facts
        incorporated_on=months_ago(5), sic_codes=["72110"],
        hq_postcode="NE1 4ST", has_share_issue=True,
        is_university_spinout=True, spinout_university="durham",
        founders=[F(prior_appointments=0)], discovery_route="registry")
    d = derive_attributes(c, cfg)
    assert d.sector          == "life_sciences"      # from SIC 72110
    assert d.geography       == "north_east"         # from NE1
    assert d.stage           == "pre_seed"           # from SH01 on a young co
    assert d.founder_signal  == "research_spinout"   # from the spinout flag
    s = score_one(d, fund="northstar", cfg=cfg)
    assert s.coverage >= 0.5
    assert s.tier == "shortlist"


def test_scoring_is_reproducible(cfg):
    """The same record scored twice is byte-identical — which is what makes
    `config_hash` + `scored_at` a complete answer to "why did this drop off
    my shortlist?" (06-scoring §10)."""
    company = C()
    a = score_one(company, fund="northstar", cfg=cfg)
    b = score_one(company, fund="northstar", cfg=cfg)
    assert a.model_dump() == b.model_dump()


def test_explanation_arithmetic_reconciles(cfg):
    """explain() emits '— X of Y total'. The two numbers must be the
    real top-3 contribution and the real headline score."""
    s = score_one(C(), fund="northstar", cfg=cfg)
    m = re.search(r"— (\d+) of (\d+) total", s.explanation)
    assert m, "explanation must carry the reconciliation clause"
    top3, headline = int(m.group(1)), int(m.group(2))
    assert headline == round(s.fund_fit_pct)
    assert top3 <= headline


# ---------------------------------------------- unknown-value policies (§6)


@pytest.mark.parametrize("policy,sub,expect_num,expect_den", [
    ("neutral",     None, 0, 0), ("pessimistic", None, 0, 1),
    ("assume",      None, 1, 1), ("neutral",     0.5, 1, 1),
])
def test_unknown_value_policies(cfg, policy, sub, expect_num, expect_den):
    """FR-4.6: the three unknown policies decide what counts in the
    numerator (earned) and the denominator (max achievable)."""
    cfg2 = cfg.model_copy(deep=True)
    cfg2.lists["scored_attributes"] = ["sector"]     # isolate one attribute
    cfg2.weights.unknown_policy["sector"] = policy
    if policy == "assume":
        cfg2.lists["assume_values"] = {"sector": "ai_data"}

    # `sub` is the expected sub-score, not the vocabulary value. A known
    # 0.5 sub-score is `healthcare` (2/4) in the Northstar column; None means
    # the attribute is genuinely unknown.
    value = None if sub is None else "healthcare"
    c = C(sector=value)
    fit = fund_fit(c, cfg2.fund("northstar"), cfg2)
    weight = cfg2.attribute_weight("sector", "northstar")
    if expect_num:
        assert fit.earned > 0
    else:
        assert fit.earned == 0
    if expect_den:
        assert fit.max_achievable == weight
    else:
        assert fit.max_achievable == 0


# ------------------------------------------- gate-reject rows exist per fund


def test_gate_reject_returns_one_row_per_fund(cfg):
    old = C(age_months=60)
    rows = evaluate(old, cfg)
    assert {s.fund_key for s in rows} == {"outward", "dsw", "northstar", "anticus"}
    assert all(s.tier == "reject" for s in rows)
    assert all(s.reject_reason == "max_company_age_months" for s in rows)
