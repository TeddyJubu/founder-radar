"""09-test-plan §2.2 — the scoring engine: evidence-aware percentage,
the unknown-never-zero invariant, reproducibility and the worked example."""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

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
    s = score_all(METZERO_FIXTURE, cfg, today=date(2026, 8, 12))
    assert s["northstar"].vehicle_key    == "spinout_inspire"
    assert s["northstar"].fund_fit_pct   == pytest.approx(92.2, abs=0.1)
    assert s["northstar"].coverage       == 1.00
    assert s["northstar"].discovery_edge == pytest.approx(47.5, abs=0.1)
    assert s["northstar"].priority       == pytest.approx(74.3, abs=0.1)
    assert s["northstar"].tier           == "watchlist"      # edge 47.5 < 55
    assert "already on their radar"      in s["northstar"].explanation
    assert s["anticus"].reject_reason    == "no_eligible_vehicle"
    assert s["outward"].fund_fit_pct     == pytest.approx(15.0, abs=0.1)
    assert s["outward"].tier             == "reject"
    assert s["dsw"].vehicle_key          == "eis_service"    # seis_fund fails on stage
    assert s["dsw"].fund_fit_pct         == pytest.approx(54.7, abs=0.1)
    assert s["dsw"].tier                 == "watchlist"


# ------------------------------------------------------------ the invariants


def test_unknown_criteria_stay_in_the_fit_denominator(cfg):
    """Unknown criteria lower confidence, rather than disappearing from fit."""
    company = C(sector="climate_tech", stage=None, geography=None,
                founder_signal=None, traction_signal=None)
    before = fund_fit(company, cfg.fund("northstar"), cfg)
    cfg2 = cfg.model_copy(deep=True)
    cfg2.lists["scored_attributes"] = list(SCORED_ATTRIBUTES) + ["bonus_attr"]
    after = fund_fit(company, cfg.fund("northstar"), cfg2)
    assert before.pct == pytest.approx(25.0)
    assert after.pct == pytest.approx(23.5, abs=0.1)
    assert after.pct < before.pct


def test_unknown_never_becomes_zero(cfg):
    """The single most important invariant in the scoring code."""
    c = C(sector=None)
    comp = next(x for x in fund_fit(c, cfg.fund("dsw"), cfg).components if x.key == "sector")
    assert comp.sub_score is None          # not 0.0
    assert comp.evidence == "unknown"


def test_sparse_evidence_cannot_look_like_a_perfect_match(cfg):
    """The headline fit includes unknown criteria in its denominator.

    This prevents a company with only two confirmed criteria from displaying
    100 Match, while coverage still says exactly how much evidence exists.
    NOTE geography is present — a NULL region would trip min_uk_presence
    and make this a reject, testing the wrong thing."""
    c = C(sector="climate_tech", geography="north_east",
          stage=None, founder_signal=None, traction_signal=None)
    s = score_one(c, fund="northstar", cfg=cfg)
    assert s.fund_fit_pct == 50.0
    assert s.coverage < 0.5
    assert s.tier == "watchlist"           # NOT shortlist


def test_coverage_counts_known_attributes_not_weighted_share(cfg):
    """Coverage answers how much evidence exists, independently of weighting.

    Fit now uses all criteria as its denominator, while the coverage count
    remains a plain count of confirmed attributes.
    """
    c = C(sector="climate_tech", geography="north_east",
          stage=None, founder_signal=None, traction_signal=None)
    fit = fund_fit(c, "northstar", cfg)

    known = sum(1 for a in SCORED_ATTRIBUTES if getattr(c, a, None) is not None)
    assert known == 2
    assert fit.coverage == 0.40, "coverage is a count of known attributes: 2 of 5"

    weighted = fit.max_achievable / fit.max_all
    assert weighted == 0.50, "the §6 formula on this company, for the record"
    assert fit.pct == 50.0, "two fully matched criteria are half of the full model"
    assert fit.coverage != weighted, "coverage remains a count, not a weighted share"


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


# ------------------------------------------------- the tuning sweep (§8)


def _seed_scored(db, count: int, *, fit_from: float = 90.0, tier: str = "watchlist"):
    """`count` companies with descending fit, so any threshold splits them."""
    from radar.store.db import now_iso
    from tests.factories import store_company

    stamp = now_iso()
    ids = []
    for index in range(count):
        company = C(canonical_name=f"Co {index:03d}", norm_key=f"co{index:03d}",
                    age_months=6)
        store_company(db, company)
        db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company.id, "northstar", None, fit_from - index, 0.9,
             70.0, fit_from - index, tier, None, "x", None,
             "h", "1", stamp))
        ids.append(company.id)
    return ids


def _label(db, company_id: str, verdict: str) -> None:
    from radar.store.db import now_iso

    db.execute("INSERT INTO user_field(company_id, field, value, updated_at) "
               "VALUES (?,?,?,?)", (company_id, "verdict", verdict, now_iso()))


def test_unjudged_companies_are_not_false_positives(db):
    """An unlabelled company is unknown, not wrong (06-scoring §8).

    Counting every unjudged company in the shortlist as a false positive drove
    precision toward zero and the recommendation toward "shortlist nothing" —
    the `None` is not `0` rule, in set arithmetic where the CI grep can't see
    it. With ~50 labels against thousands of companies that error *is* the
    whole answer, so it has to be pinned here and not only through the sheet.
    """
    from radar.score.tune import sweep

    ids = _seed_scored(db, 30)              # fit 90 down to 61
    _label(db, ids[0], "worth contacting")  # fit 90 — above every threshold
    _label(db, ids[1], "worth contacting")  # fit 89
    _label(db, ids[2], "not for me")        # fit 88

    # An explicit grid keeps this test about the precision arithmetic; the
    # derived thresholds are exercised in test_sweep_uses_change_points.
    row = next(r for r in sweep(db, fit_grid=[55])["sweep"] if r["threshold"] == 55)

    # All 30 clear a threshold of 55, but only three are judged: two good,
    # one bad. Precision is 2/3 over the judged set — not 2/30.
    assert row["would_shortlist"] == 30
    assert row["precision"] == round(2 / 3, 2)
    assert row["recall"] == 1.0


def test_sweep_uses_change_points_not_a_fixed_grid(db):
    """No two rows may give the same answer.

    The fixed grid (55, 60, ... 85) spent rows on thresholds that were
    pairwise identical — on the live database 60 and 65 both shortlisted 787
    companies — while missing the cliff at 91 entirely. A row that cannot
    change the outcome is a decision dressed up as a choice.
    """
    from radar.score.tune import sweep

    _seed_scored(db, 30)                                    # fit 90 down to 61
    # Gated companies below the eligible range. Their fit still has a floor,
    # but no threshold crossing it changes the shortlist — on live data these
    # produced three consecutive rows all reading 792.
    _seed_scored(db, 8, fit_from=50.0, tier="reject")

    result = sweep(db)

    counts = [r["would_shortlist"] for r in result["sweep"]]
    assert len(set(counts)) == len(counts), f"duplicate shortlists: {counts}"
    assert min(r["threshold"] for r in result["sweep"]) >= 61, (
        "a gated company's fit became a phantom threshold")
    assert result["thresholds"] == "change points"
    assert result["change_points_omitted"] == 0


def test_change_points_cover_every_distinct_shortlist(db):
    """Completeness: the change points are the whole answer, not a sample.

    Sweeping 0-100 by hand can produce no shortlist that the change points
    miss. The one exception is the empty shortlist above the highest score,
    which is never a threshold worth recommending.
    """
    from radar.score.tune import _score_rows, change_points

    _seed_scored(db, 30)
    rows = _score_rows(db)
    fits = [float(r["fund_fit_pct"]) for r in rows]

    def shortlist(threshold: int) -> frozenset:
        return frozenset(i for i, v in enumerate(fits) if v >= threshold)

    exhaustive = {shortlist(t) for t in range(0, 101)}
    covered = {shortlist(t) for t in change_points(rows)}

    assert exhaustive - covered <= {frozenset()}


def test_sweep_reports_the_change_points_it_had_to_drop(db):
    """No silent caps — a truncated table must not read as a complete one."""
    from radar.score.tune import MAX_SWEEP_ROWS, sweep

    _seed_scored(db, 60)                       # 60 distinct floors
    result = sweep(db)

    assert len(result["sweep"]) <= MAX_SWEEP_ROWS
    assert result["change_points_omitted"] > 0
    # The extremes survive sampling: the widest and narrowest shortlists are
    # the two a person most wants to see.
    thresholds = [r["threshold"] for r in result["sweep"]]
    assert thresholds[0] == 31 and thresholds[-1] == 90


def test_sweep_precision_is_none_without_any_verdicts(db):
    """No labels means nothing to be precise against — not precision zero."""
    from radar.score.tune import sweep

    _seed_scored(db, 10)
    result = sweep(db)

    assert all(r["precision"] is None and r["recall"] is None and r["f1"] is None
               for r in result["sweep"])
    assert result["best"] is None
    assert "No verdicts yet" in result["recommendation"]


# ------------------------------------------------- the explanation sentence


def _fit_stub(**over):
    from radar.score.criteria import ComponentScore as Comp

    base = dict(pct=100.0, coverage=0.2, components=[
        Comp(key="founder_signal", label="Founder signal", sub_score=1.0,
             weight=4, evidence="research/spinout"),
        Comp(key="sector", label="Sector", sub_score=None, weight=4, evidence="unknown"),
    ])
    base.update(over)
    return SimpleNamespace(**base)


def test_explanation_never_renders_a_none_date():
    """`Found via Acme Robotics (None)` told the reader the system was broken
    when the truth was only that a signal carried no date."""
    from radar.score.explain import explain

    undated = SimpleNamespace(headline="Allos AI", occurred_on=None)
    text = explain(_fit_stub(), 67.0, [undated])
    assert "(None)" not in text
    assert "Found via Allos AI." in text

    dated = SimpleNamespace(headline="SH01 filed", occurred_on="2026-07-30")
    assert "SH01 filed (2026-07-30)" in explain(_fit_stub(), 67.0, [dated])


def test_each_caveat_is_stated_exactly_once():
    """It used to read: `age unknown — verify before sending. ⚠ age unknown.
    ⚠ gate unverified. ⚠ uk unverified. Age unknown — verify before sending.`
    One unknown, said three times, looks like three problems."""
    from radar.score.explain import explain

    text = explain(_fit_stub(), 67.0, [],
                   flags=["age_unknown", "gate_unverified", "uk_unverified"],
                   tier_reason="age unknown — verify before sending")

    assert text.lower().count("age unknown") == 1, text
    # The caveats the sentence has not already made appear once, together.
    assert text.count("⚠") == 1, text
    assert "gate unverified, uk unverified" in text


def test_a_flag_the_sentence_never_mentions_is_still_shown():
    """Suppression must not swallow a caveat nobody else stated."""
    from radar.score.explain import explain

    text = explain(_fit_stub(), 67.0, [], flags=["uk_unverified"])
    assert "⚠ uk unverified" in text


def test_the_tier_reason_is_appended_once_by_the_caller_chain(db, cfg):
    """`explain` owns the tier reason. The daily path used to append it a
    second time while the bulk rescore path did not, so the two produced
    different text for the same company — a divergence
    `test_rescore_bulk_equals_daily` compares but could not see, because its
    fixture has no watchlist rows."""
    from radar.pipeline import score_company
    from tests.factories import store_company

    company = C(sector="climate_tech", geography="north_east",
                stage=None, founder_signal=None, traction_signal=None)
    cid = store_company(db, company)
    score_company(db, cid, cfg, today=date(2026, 8, 8))

    for row in db.query("SELECT tier, explanation FROM score WHERE company_id = ?", (cid,)):
        text = (row["explanation"] or "").lower()
        for phrase in ("below fit threshold", "verify before sending",
                       "eligibility unconfirmed"):
            assert text.count(phrase) <= 1, f"{phrase!r} repeated in: {text}"
