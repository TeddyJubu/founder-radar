"""Test factories — the `C()` / `S()` / `F()` helpers of 09-test-plan.

`C()` builds a `Company`, never a score; `S()` builds a `Score`, never a
company. The two are deliberately separate so a test cannot accidentally assert
on the wrong shape.

`C()` starts from a minimally-valid company whose universal gates all pass, so
a test can isolate **one** gate at a time. Anything not passed in is `None` —
except the fields needed to get past the freshness gates, which default to
valid values.

`months_ago` uses `int()` (floor) on the day count so the boundary cases in
`test_freshness_gates` land exactly where the spec puts them: 36 months of days
is 1095.75 days, and flooring to 1095 keeps the age **at** the limit while
rounding to 1096 would have pushed it a day over.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from radar.score.derive import DAYS_PER_MONTH, Company, Founder, Signal
from radar.store.db import new_id, now_iso


# ------------------------------------------------------------------- helpers


def months_ago(n: int | float | None, today: date | None = None) -> date | None:
    """`date` that is `n` months before `today` (or None for unknown age)."""
    if n is None:
        return None
    today = today or date.today()
    return today - timedelta(days=int(n * DAYS_PER_MONTH))


def F(**kw) -> Founder:
    """A minimally-valid `Founder` (the scoring model, not the CH dataclass)."""
    base = dict(name="Ada Lovelace", role="Director", prior_appointments=0)
    return Founder(**{**base, **kw})


def S(**kw):
    """A bare `Score`, for tests that only check routing/tier arithmetic."""
    from radar.score.criteria import Score

    base = dict(
        company_id="c1",
        company_name="Test Co",
        fund_key="northstar",
        fund_fit_pct=70.0,
        coverage=1.0,
        discovery_edge=60.0,
        priority=66.0,
        tier="shortlist",
    )
    return Score(**{**base, **kw})


def C(**kw) -> Company:
    """A minimally-valid `Company`. See the module docstring."""
    base: dict[str, Any] = dict(
        id=new_id(),
        canonical_name="Test Co",
        norm_key="testco",
        country_iso2="GB",
        hq_region="north_east",
        on_vc_portfolio=False,
        stage="pre_seed",
        sector="b2b_saas",
        founder_signal="technical_founder",
        traction_signal="pilot_customers",
        total_funding_gbp=None,
        discovery_route="news",
        qualifiers=["press"],
    )
    if "age_months" in kw:
        base["incorporated_on"] = months_ago(kw.pop("age_months"))
    if "funding" in kw:
        base["total_funding_gbp"] = kw.pop("funding")
    if "geography" in kw:
        base["hq_region"] = kw.pop("geography")
    if "country" in kw:
        base["country_iso2"] = kw.pop("country")
    return Company(**{**base, **kw})


def registry_company(**kw) -> Company:
    """A company built ONLY from register-derived facts (06-scoring §2).

    Defaults keep the freshness gates passing but carry **no** qualifying
    signal — Track B companies must earn their way into scoring (§3).
    """
    base: dict[str, Any] = dict(
        id=new_id(),
        canonical_name="Registry Co",
        norm_key="registryco",
        country_iso2="GB",
        hq_region=None,
        on_vc_portfolio=False,
        stage=None,
        sector=None,
        founder_signal=None,
        traction_signal=None,
        total_funding_gbp=None,
        discovery_route="registry",
        qualifiers=[],
        incorporated_on=months_ago(5),
        hq_postcode="NE1 4ST",
        sic_codes=["72110"],
        has_share_issue=False,
        founders=[Founder(name="Ada Lovelace", prior_appointments=0)],
    )
    if "age_months" in kw:
        base["incorporated_on"] = months_ago(kw.pop("age_months"))
    if "funding" in kw:
        base["total_funding_gbp"] = kw.pop("funding")
    return Company(**{**base, **kw})


# ------------------------------------------------------------------- scoring


def score_all(company, cfg=None, *, today=None) -> dict[str, Any]:
    """`{fund_key: Score}` from the pure stage-⑥ evaluator (never a DB)."""
    from radar.config.defaults import default_config
    from radar.pipeline import evaluate

    cfg = cfg or default_config()
    return {s.fund_key: s for s in evaluate(company, cfg, today=today)}


def score_one(company, fund: str, cfg=None, *, today=None) -> Any:
    """The single-fund `Score`, for tests that only care about one fund."""
    return score_all(company, cfg, today=today)[fund]


# -------------------------------------------------------------------- store


def store_company(db, company: Company) -> str:
    """Persist a scoring `Company` as a real row (idempotent on its id).

    Written column-by-column against the live schema so it cannot drift from
    `company_from_row`. Founders and signals ride along as child rows.
    """
    cols = [r["name"] for r in db.execute("PRAGMA table_info(company)")]
    stamp = now_iso()
    field_map: dict[str, Any] = dict(
        id=company.id,
        canonical_name=company.canonical_name,
        norm_key=company.norm_key,
        companies_house_no=company.companies_house_no,
        domain=company.domain,
        website_url=company.website_url,
        incorporated_on=str(company.incorporated_on) if company.incorporated_on else None,
        age_source=company.age_source,
        hq_postcode=company.hq_postcode,
        hq_region=company.hq_region,
        hq_city=company.hq_city,
        country_iso2=company.country_iso2,
        sector=company.sector,
        stage=company.stage,
        founder_signal=company.founder_signal,
        traction_signal=company.traction_signal,
        total_funding_gbp=company.total_funding_gbp,
        one_liner=company.one_liner,
        sic_codes=json.dumps(company.sic_codes) if company.sic_codes else None,
        has_share_issue=1 if company.has_share_issue else 0,
        on_vc_portfolio=1 if company.on_vc_portfolio else 0,
        discovery_route=company.discovery_route,
        is_university_spinout=(1 if company.is_university_spinout else 0)
        if company.is_university_spinout is not None else None,
        spinout_university=company.spinout_university,
        last_round_gbp=company.last_round_gbp,
        prior_total_gbp=company.prior_total_gbp,
        valuation_gbp=company.valuation_gbp,
        uk_exec_pct=company.uk_exec_pct,
        seis_eis_qualifying=(1 if company.seis_eis_qualifying else 0)
        if company.seis_eis_qualifying is not None else None,
        qualifiers=json.dumps(company.qualifiers) if company.qualifiers else None,
        extraction_method=company.extraction_method,
        news_mention_count=company.news_mention_count,
        first_seen=stamp,
        last_seen=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    row = {c: field_map.get(c) for c in cols}
    db.execute(
        f"INSERT INTO company({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [row[c] for c in cols],
    )

    for f in company.founders:
        db.execute(
            """INSERT INTO founder(company_id, name, norm_name, role, is_psc,
                                   prior_appointments, source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (company.id, f.name, f.name.lower().replace(" ", ""), f.role,
             1 if f.is_psc else 0, f.prior_appointments, "", stamp),
        )
    for s in company.signals:
        db.execute(
            """INSERT INTO signal(company_id, kind, headline, detail, occurred_on,
                                  amount_gbp, source_key, source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (company.id, s.kind, s.headline, s.detail,
             str(s.occurred_on) if s.occurred_on else None,
             s.amount_gbp, s.source_key, s.source_url, stamp),
        )
    return company.id
