"""09-test-plan §2.4 — Discovery Edge (UI label: Fresh).

The number that encodes Aryan's actual job: *"there's a good chance these
funds have already come across them, and I'm not really adding much value."*
Ranking on Fund Fit alone produced the version-1 complaint, so the edge term
is not decoration — it is what puts a less-visible company above a famous one
with an identical fit.
"""

from __future__ import annotations

import pytest

from radar.config.defaults import default_config
from radar.score.discovery_edge import (
    curve_score,
    discovery_edge,
    discovery_edge_component,
    discovery_edge_components,
)

from tests.factories import C, score_one


@pytest.fixture
def cfg():
    return default_config()


def test_discovery_edge_ranking(cfg):
    """Identical on every scored attribute; different only on visibility."""
    common = dict(sector="climate_tech", geography="north_east",
                  stage="pre_seed", founder_signal="technical_founder",
                  traction_signal="pilot_customers")
    obscure = C(**common, age_months=3,  news_mention_count=0,
                funding=0, discovery_route="registry")
    famous  = C(**common, age_months=30, news_mention_count=8,
                funding=2_000_000, discovery_route="news")
    a, b = score_one(obscure, "northstar", cfg=cfg), score_one(famous, "northstar", cfg=cfg)
    assert a.fund_fit_pct == b.fund_fit_pct        # identical fit, by construction
    assert a.discovery_edge > b.discovery_edge
    assert a.priority > b.priority


def test_unknown_funding_is_not_known_zero():
    """The one invariant this system holds to, applied to Discovery Edge."""
    known_none = C(funding=0)
    unknown    = C(funding=None)
    assert discovery_edge_component(known_none, "funding").sub_score == 1.0
    assert discovery_edge_component(unknown,    "funding").sub_score == 0.5


def test_portfolio_company_is_gated_not_just_scored(cfg):
    """Being in a tracked portfolio is a hard reject, not a low score —
    which is exactly why it is NOT a Discovery Edge component."""
    c = C(on_vc_portfolio=True)
    s = score_one(c, "northstar", cfg=cfg)
    assert s.tier == "reject"
    assert s.reject_reason == "already_on_vc_portfolio"


def test_discovery_edge_has_no_portfolio_component():
    """A component every scored company gets identically is a constant,
    not a signal. Guard against it being re-added."""
    keys = {c.key for c in discovery_edge_components(C())}
    assert "vc_portfolio" not in keys
    assert keys == {"age", "press_coverage", "disclosed_funding", "discovery_route"}


def test_age_curve_interpolates():
    """Knots define a continuous decline — not a flat early plateau."""
    curve = [[0, 1.0], [12, 0.85], [24, 0.55], [36, 0.15]]
    assert curve_score(0, curve, 0.5) == pytest.approx(1.0)
    assert curve_score(6, curve, 0.5) == pytest.approx(0.925)
    assert curve_score(12, curve, 0.5) == pytest.approx(0.85)
    assert curve_score(None, curve, 0.5) == 0.5
    assert curve_score(48, curve, 0.5) == 0.0


def test_registry_siblings_separate_on_age(cfg):
    """Same route/press/funding — different ages must produce different Fresh.

    Coarse month bands used to pin every ≤6-month register find to the same
    Edge number, which is why the client saw identical tiles across Today.
    """
    common = dict(news_mention_count=0, funding=None, discovery_route="registry")
    younger = discovery_edge(C(**common, age_months=3), cfg)
    older = discovery_edge(C(**common, age_months=14), cfg)
    assert younger > older
    assert younger - older >= 2.0


@pytest.mark.parametrize(
    ("route", "expected"),
    [("spinout", 1.0), ("accelerator", 0.9), ("grant", 0.8),
     ("registry", 0.7), ("news", 0.5)],
)
def test_route_edge_bands_match_spec(cfg, route, expected):
    """The configured route bands are exact, not only correctly ordered."""
    company = C(news_mention_count=0, funding=None, age_months=6,
                discovery_route=route)

    component = discovery_edge_component(company, "route", cfg)

    assert component.sub_score == expected


def test_registry_with_press_uses_its_configured_band(cfg):
    """Press makes a registry find less obscure than the registry-only band."""
    company = C(news_mention_count=1, funding=None, age_months=6,
                discovery_route="registry")

    component = discovery_edge_component(company, "route", cfg)

    assert component.sub_score == 0.6
