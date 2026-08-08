"""09-test-plan §1 — the test that matters most.

`test_freshness_gates` is the client's exact complaint, encoded: the version-1
system sent old companies because its age filter ran *after* discovery. Version
2 gates at the source, and every boundary of every gate is pinned here. If this
passes, the core complaint is structurally fixed.
"""

from __future__ import annotations

import statistics

import pytest

from radar.config.defaults import DEFAULT_SETTINGS
from radar.score.gates import apply_freshness_gates

from tests.factories import C, score_one


@pytest.mark.parametrize("case,company,expect_pass,expect_reason", [
    # --- AGE: the client's exact complaint ---
    ("brand new",        C(age_months=1),    True,  None),
    ("one year",         C(age_months=12),   True,  None),
    ("at the limit",     C(age_months=36),   True,  None),
    ("one day over",     C(age_months=36.1), False, "max_company_age_months"),
    ("the v1 problem",   C(age_months=60),   False, "max_company_age_months"),
    ("the v1 problem 2", C(age_months=84),   False, "max_company_age_months"),
    ("age unknown",      C(age_months=None), True,  None),   # passes, but flagged

    # --- FUNDING: "many have already raised" ---
    ("no funding known", C(funding=None),      True,  None),
    ("known zero",       C(funding=0),         True,  None),
    ("seed sized",       C(funding=800_000),   True,  None),
    ("at the limit",     C(funding=3_000_000), True,  None),
    ("one pound over",   C(funding=3_000_001), False, "max_total_funding_gbp"),
    ("series A sized",   C(funding=12_000_000), False, "max_total_funding_gbp"),

    # --- STAGE ---
    ("pre-seed",         C(stage="pre_seed"),      True,  None),
    ("series A",         C(stage="series_a"),      True,  None),
    ("series B",         C(stage="series_b_plus"), False, "max_stage"),
    ("growth",           C(stage="growth"),        False, "max_stage"),

    # --- ALREADY SEEN: the whole point of being a scout ---
    ("not on any portfolio", C(on_vc_portfolio=False), True,  None),
    ("already in a portfolio", C(on_vc_portfolio=True), False, "already_on_vc_portfolio"),

    # --- UK ---
    ("UK company",  C(country="GB"), True,  None),
    ("US company",  C(country="US"), False, "min_uk_presence"),
])
def test_freshness_gates(case, company, expect_pass, expect_reason):
    result = apply_freshness_gates(company, DEFAULT_SETTINGS)
    assert result.passed is expect_pass, case
    assert result.reason == expect_reason, case


def test_unknown_age_cannot_reach_shortlist():
    """Unknown age passes the gate but must never be shortlisted.
    Rejecting would lose good early candidates; shortlisting would
    let old companies back in through the gap. Watchlist is honest."""
    c = C(age_months=None)                     # everything else scores well
    s = score_one(c, fund="dsw")
    assert "age_unknown" in s.flags
    assert s.tier == "watchlist"
    assert "age unknown" in s.explanation.lower()


def test_gate_with_null_input_passes_but_flags():
    """A vehicle hard rule we cannot evaluate must not silently reject
    or silently ignore. It passes, flags, and stays off the shortlist."""
    c = C(is_university_spinout=None, hq_region="north_east")
    s = score_one(c, fund="northstar")
    assert s.vehicle_key == "spinout_inspire"
    assert "gate_unverified" in s.flags
    assert s.tier == "watchlist"


def test_median_shortlist_age_stays_under_24_months():
    """Regression guard on the headline product metric: a shortlist that
    drifts back towards version 1 (median age 30m+) is a source leaking old
    companies, not a scoring problem (09-test-plan §10)."""
    ages = []
    for months in (10, 14, 18, 22, 30, 40):
        # Not a spinout, with a Newcastle postcode: spinout_inspire fails on
        # the mandate and venture_sunderland on the outcode, leaving the
        # catch-all Innovation Fund — no unverified hard rule, so shortlisting
        # is actually possible (a NULL spinout flag would flag gate_unverified).
        c = C(age_months=months, sector="climate_tech", geography="north_east",
              founder_signal="research_spinout", discovery_route="registry",
              is_university_spinout=False, hq_postcode="NE1 4ST")
        s = score_one(c, fund="northstar")
        if s.tier == "shortlist":
            ages.append(months)
    assert ages, "expected at least one shortlisted company"
    assert statistics.median(ages) < 24, \
        f"median shortlist age {statistics.median(ages)}m — drifting back to v1"
