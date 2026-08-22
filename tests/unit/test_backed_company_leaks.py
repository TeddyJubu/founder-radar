"""Why VC-backed / IPO-adjacent companies were still on Today.

The live cards (ionSIGHT, Respiro Diagnostics, Unibloom, Spine) shared a
shape: no incorporation date, stage "not known", a stated city that was
*not* Yorkshire, and a 47–56 Match from one regional fund. Three holes
stacked:

1. Oxford Innovation writes `structured.incorporated_on`, but resolve only
   read Companies House's `date_of_creation`, so every TTO card became
   age-unknown.
2. A stated `hq_city` of Oxford collapsed to `uk_wide`, and `uk_wide` was
   treated as a match for `north_england` and as *unknown* (pass) for HARD
   Yorkshire / North East.
3. Today stripped `age_unknown` for Track A and never re-checked stage or
   HARD geography, so undated portfolio cards occupied the morning queue.

These tests pin the stacked failure, not just each hole in isolation.
"""

from __future__ import annotations

from datetime import date

import pytest

from radar.config.defaults import default_config
from radar.score.derive import derive_attributes, geography_from_city
from radar.score.gates import evaluate_vehicle_gates
from tests.factories import C, score_all, score_one


@pytest.fixture
def cfg():
    return default_config()


def _ionsight(**kw):
    """The card as stored: Oxford spinout, no date, no stage, GB only."""
    base = dict(
        canonical_name="ionSIGHT",
        norm_key="ionsight",
        age_months=None,
        stage=None,
        sector="life_sciences",
        founder_signal="research_spinout",
        traction_signal=None,
        geography="uk_wide",
        hq_city="Oxford",
        hq_postcode=None,
        country="GB",
        is_university_spinout=True,
        spinout_university="University of Oxford",
        discovery_route="spinout",
        on_vc_portfolio=False,
        funding=None,
    )
    base.update(kw)
    return C(**base)


def test_oxford_city_is_not_uk_wide(cfg):
    assert geography_from_city("Oxford", cfg) == "uk_regions"
    assert geography_from_city("City of Oxford", cfg) == "uk_regions"
    derived = derive_attributes(_ionsight(), cfg)
    assert derived.hq_region == "uk_regions"


def test_oxford_spinout_fails_yorkshire_and_north_east_hard_gates(cfg):
    """ionSIGHT is in Oxford. Finance Yorkshire and NE Innovation cannot take it."""
    company = derive_attributes(_ionsight(), cfg)
    yorkshire = next(v for v in cfg.fund("anticus").vehicles if v.vehicle_key == "fy_seedcorn")
    ne = next(v for v in cfg.fund("northstar").vehicles if v.vehicle_key == "ne_innovation_fund")
    assert evaluate_vehicle_gates(company, yorkshire, cfg).passed is False
    assert evaluate_vehicle_gates(company, ne, cfg).reason.startswith("geography:")


def test_uk_wide_is_not_north_of_england(cfg):
    """A UK-confirmed-but-unresolved region must not satisfy north_england."""
    from radar.score.gates import _geo_rule_verdict

    company = C(geography="uk_wide", hq_city=None, hq_postcode=None)
    assert _geo_rule_verdict(company, "north_england", cfg) is None
    leeds = C(geography="yorkshire", hq_city="Leeds", hq_postcode="LS1 4AP")
    assert _geo_rule_verdict(leeds, "north_england", cfg) is True


def test_oxford_city_is_inside_the_golden_triangle(cfg):
    """DSW SEIS is outside London–Oxbridge. A stated Oxford city is inside."""
    from radar.score.derive import is_outside_golden_triangle

    assert is_outside_golden_triangle(_ionsight(), cfg) is False
    manchester = C(geography="uk_wide", hq_city="Manchester", hq_postcode=None)
    assert is_outside_golden_triangle(manchester, cfg) is True


def test_ionsight_shaped_card_does_not_route_to_anticus_or_northstar_hard_vehicles(cfg):
    scores = score_all(_ionsight(), cfg)
    assert scores["anticus"].tier == "reject"
    assert scores["anticus"].reject_reason == "no_eligible_vehicle"
    # HARD North East vehicles fail; eis_growth is SOFT so the fund may still
    # route — Today then hides the undated/un-staged card (see below).
    if scores["northstar"].tier != "reject":
        assert scores["northstar"].vehicle_key == "eis_growth"


def test_old_structured_incorporation_date_is_an_age_reject(db, cfg):
    """Oxford Innovation's `incorporated_on` must reach the age gate."""
    from radar.pipeline import resolve_item, score_company
    from radar.sources.base import RawItem

    item = RawItem(
        source_key="oxford_innovation",
        source_url="https://innovation.ox.ac.uk/investing/our-portfolio-companies/ionsight",
        external_id="ionsight",
        published_at=date(2018, 3, 1),
        title="ionSIGHT",
        structured={
            "company_name": "ionSIGHT",
            "one_line_description": (
                "Machine learning-powered native mass spectrometry for drug discovery."
            ),
            "hq_city": "Oxford",
            "hq_country_iso2": "GB",
            "is_university_spinout": True,
            "university_name": "University of Oxford",
            "incorporated_on": "2018-03-01",
            "age_source": "source_stated",
            "date_confidence": "stated",
            "extraction_method": "structured",
        },
        kind_hint="spinout",
    )
    cid = resolve_item(db, item, cfg)
    row = db.one(
        "SELECT incorporated_on, age_source, hq_city FROM company WHERE id = ?",
        (cid,),
    )
    assert row["incorporated_on"] == "2018-03-01"
    assert row["age_source"] == "source_stated"
    assert row["hq_city"] == "Oxford"

    score_company(db, cid, cfg, today=date(2026, 8, 21))
    reasons = {r["reject_reason"] for r in db.query(
        "SELECT reject_reason FROM score WHERE company_id = ?", (cid,))}
    assert reasons == {"max_company_age_months"}


def test_structured_adapter_skips_the_reader(cfg):
    from radar.pipeline import extract_stage
    from radar.sources.base import RawItem

    item = RawItem(
        source_key="oxford_innovation",
        source_url="https://innovation.ox.ac.uk/investing/our-portfolio-companies/ionsight",
        external_id="ionsight",
        published_at=date(2018, 3, 1),
        title="ionSIGHT",
        structured={
            "company_name": "ionSIGHT",
            "extraction_method": "structured",
            "incorporated_on": "2018-03-01",
        },
        kind_hint="spinout",
    )
    out = extract_stage([item], cfg, use_llm=True, db=None)
    assert out[0] is item
    assert not hasattr(out[0], "extraction")


def test_today_hides_undated_unspecified_stage_spinout(db, cfg):
    """The screenshot shape: Track A, incorporation unconfirmed, stage unknown."""
    from prototype.server import build_today
    from radar.store.db import now_iso
    from tests.factories import store_company

    company = _ionsight()
    cid = store_company(db, company)
    stamp = now_iso()
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "oxford_innovation", "ionsight",
         "https://innovation.ox.ac.uk/investing/our-portfolio-companies/ionsight",
         stamp, stamp),
    )
    from radar.pipeline import score_company
    score_company(db, cid, cfg, today=date(2026, 8, 21))

    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"] for row in payload["eligibility_diagnostics"]["reasons"]}
    assert "maturity_unknown" in reasons


def _stale_today_row(db, company, *, vehicle_key, fund="anticus"):
    """A watchlist row as the live DB looked *before* a rescore.

    The leak: Today used the stored vehicle even when city→region now makes
    that vehicle's HARD geo fail. `score_company` would reject; stale rows
    would not.
    """
    from radar.store.db import now_iso
    from tests.factories import store_company

    cid = store_company(db, company)
    stamp = now_iso()
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "oxford_innovation", f"ext-{cid}",
         "https://innovation.ox.ac.uk/investing/our-portfolio-companies/x",
         stamp, stamp),
    )
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, fund, vehicle_key, 56.0, 0.4, 70.0, 60, "watchlist", None,
         "Queued for review.", None, "testhash", "1", stamp),
    )
    return cid


def test_today_hides_oxford_on_a_stale_yorkshire_watchlist_row(db):
    """Known city, known early stage, already scored onto Finance Yorkshire."""
    from prototype.server import build_today

    cid = _stale_today_row(
        db,
        _ionsight(age_months=8, stage="seed"),
        vehicle_key="fy_seedcorn",
    )
    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"] for row in payload["eligibility_diagnostics"]["reasons"]}
    assert "geography_mismatch" in reasons


def test_today_hides_unresolved_uk_on_a_hard_regional_vehicle(db):
    """uk_wide with no city is not a Yorkshire match — it is unverified."""
    from prototype.server import build_today

    cid = _stale_today_row(
        db,
        C(canonical_name="Anywhere Ltd", norm_key="anywhereltd",
          age_months=8, stage="seed", discovery_route="spinout",
          country="GB", geography="uk_wide", hq_city=None, hq_postcode=None,
          sector="life_sciences"),
        vehicle_key="fy_seedcorn",
    )
    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"] for row in payload["eligibility_diagnostics"]["reasons"]}
    assert "geography_unverified" in reasons


def test_today_still_shows_a_grant_winner_with_unknown_age_and_known_early_stage(db):
    """J25: Innovate UK cards without a CH date stay on Today when stage is early."""
    from prototype.server import build_today
    from tests.unit.test_kept import _watchlist_row

    cid = _watchlist_row(
        db,
        C(canonical_name="Innovate Winner Ltd", norm_key="innovatewinner2",
          age_months=None, stage="pre_seed", discovery_route="grant",
          country="GB", geography="north_east", hq_postcode="NE1 4ST"),
        source_key="innovate_uk",
        source_url="https://www.gov.uk/innovate-uk/award-2",
        priority=70,
    )
    payload = build_today(db.conn)
    assert cid in {row["company_id"] for row in payload["companies"]}


def test_ipo_article_is_not_an_early_stage_lead(cfg):
    from radar.extract.heuristic import heuristic_extract
    from tests.factories import score_one

    got = heuristic_extract(
        title="Spine files for IPO on the London Stock Exchange",
        text=(
            "Spine, a renewable energy software company, has filed for an IPO "
            "on the London Stock Exchange after years of venture backing. "
        ) * 3,
    )
    assert got.rejection_reason == "already_large_company"
    assert got.is_usable is False

    late = heuristic_extract(
        title="Unibloom closes Series C to accelerate growth",
        text=(
            "Unibloom, which helps organisations meet sustainability targets, "
            "has closed a Series C round led by existing backers. "
        ) * 3,
    )
    assert late.stage == "series_b_plus"
    scored = score_one(
        C(stage="series_b_plus", canonical_name="Unibloom",
          discovery_route="news"),
        fund="dsw", cfg=cfg,
    )
    assert scored.reject_reason == "max_stage"
