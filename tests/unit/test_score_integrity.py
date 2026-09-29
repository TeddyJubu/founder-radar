"""Scoring integrity — the score area of the 2026-09-30 audit.

* H-06  daily scoring must read the stored press count, like `rescore --all`.
* H-07  a route change must not leave the old vehicle's row behind.
* M-06  historical digests are rendered from snapshots, not from `score`.
* M-07  cards show the sector / stage / region the score was computed from.
* M-08  the tuning sweep recomputes the whole tier decision per threshold.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from radar.pipeline import rescore_all, score_company
from radar.score.derive import Company, Signal
from radar.store.db import now_iso

from tests.factories import approve_cards, C, F, registry_company, store_company

TODAY = date(2026, 8, 8)


# ---------------------------------------------------------------- helpers


def _scores(db) -> dict:
    """Every score row, keyed by identity, without ids or timestamps."""
    out = {}
    for row in db.query("SELECT * FROM score ORDER BY company_id, fund_key, vehicle_key"):
        d = dict(row)
        d.pop("id")
        d.pop("scored_at")
        out[(d["company_id"], d["fund_key"], d["vehicle_key"])] = d
    return out


def _components(db) -> dict:
    out = {}
    for row in db.query(
        """SELECT s.company_id, s.fund_key, s.vehicle_key, sc.key, sc.sub_score,
                  sc.weight, sc.contribution, sc.evidence
             FROM score_component sc JOIN score s ON s.id = sc.score_id"""):
        out[(row["company_id"], row["fund_key"], row["vehicle_key"], row["key"])] = (
            row["sub_score"], row["weight"], row["contribution"], row["evidence"])
    return out


def _daily_then_bulk(db, config, ids):
    """Score `ids` one at a time, then wipe and score them in bulk."""
    for cid in ids:
        score_company(db, cid, config, today=TODAY)
        approve_cards(db)
    daily = (_scores(db), _components(db))
    db.execute("DELETE FROM score")
    rescore_all(db, config, today=TODAY)
    approve_cards(db)
    bulk = (_scores(db), _components(db))
    return daily, bulk


def _metzero(**over) -> Company:
    """A company that shortlists for Northstar — not (yet) a spinout."""
    base = dict(
        id="metzero", canonical_name="METzero Technologies",
        norm_key="metzerotechnologies", country_iso2="GB",
        hq_region="north_east", hq_postcode="NE1 4ST",
        incorporated_on=date(2026, 3, 11), sector="climate_tech", stage="seed",
        founder_signal="research_spinout", traction_signal="clinical_grant_validation",
        total_funding_gbp=450_000, news_mention_count=2, on_vc_portfolio=False,
        discovery_route="spinout", is_university_spinout=False,
        seis_eis_qualifying=True, qualifiers=["spinout", "press", "grant"],
        signals=[Signal(kind="spinout", headline="Northern Accelerator spinout",
                        occurred_on=date(2026, 7, 28))],
    )
    return Company(**{**base, **over})


def _add_source(db, company_id: str) -> None:
    stamp = now_iso()
    db.execute(
        "INSERT OR IGNORE INTO company_source(company_id, source_key, external_id, source_url,"
        " first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (company_id, "news_x", "1", "https://example.com/article", stamp, stamp))


def _become_a_durham_spinout(db, company_id: str) -> None:
    """New evidence moves the winning vehicle *and* lowers the priority: the
    press count and funding rise, so Discovery Edge falls. The old vehicle's
    row therefore outranks the new one by priority — the audit's scenario."""
    db.execute(
        "UPDATE company SET is_university_spinout = 1, spinout_university = 'durham',"
        " total_funding_gbp = 1000000, news_mention_count = 3 WHERE id = ?",
        (company_id,))


def _vehicles_for(db, company_id: str, fund: str = "northstar") -> list[str | None]:
    return [r["vehicle_key"] for r in db.query(
        "SELECT vehicle_key FROM score WHERE company_id = ? AND fund_key = ? "
        "ORDER BY id", (company_id, fund))]


# ============================================================ H-06 press count


def test_daily_scoring_reads_the_stored_press_count(db, config):
    """`company_from_row` dropped `news_mention_count`, so the morning run scored
    every company as if it had no press and `rescore --all` scored it as stored."""
    cid = store_company(db, C(age_months=6, canonical_name="Pressed Co",
                              norm_key="pressedco", news_mention_count=4))

    (daily_scores, daily_parts), (bulk_scores, bulk_parts) = _daily_then_bulk(
        db, config, [cid])

    assert daily_scores == bulk_scores
    assert daily_parts == bulk_parts
    press = [v for k, v in daily_parts.items() if k[3] == "press_coverage"]
    assert press and all(evidence == "4 tracked article(s)"
                         for *_, evidence in press)


def test_a_registry_company_admitted_by_press_is_scored_daily(db, config):
    """Press is an admitting qualifier for a register find. With the count read
    as zero the daily run declared it unqualified and deleted its scores, while
    `rescore --all` (which reads the column) scored it."""
    cid = store_company(db, registry_company(
        age_months=6, canonical_name="Press Only Ltd", norm_key="pressonlyltd",
        news_mention_count=2))

    assert score_company(db, cid, config, today=TODAY) > 0
    approve_cards(db)

    (daily_scores, _), (bulk_scores, _) = _daily_then_bulk(db, config, [cid])
    assert daily_scores and daily_scores == bulk_scores


def test_a_null_press_count_scores_the_same_on_both_paths(db, config):
    """NULL is "unknown", not zero — the bulk path already keeps it that way."""
    cid = store_company(db, C(age_months=6, canonical_name="Unknown Press Co",
                              norm_key="unknownpressco"))
    db.execute("UPDATE company SET news_mention_count = NULL WHERE id = ?", (cid,))

    (daily_scores, daily_parts), (bulk_scores, bulk_parts) = _daily_then_bulk(
        db, config, [cid])

    assert daily_scores == bulk_scores
    assert daily_parts == bulk_parts
    assert any(v[3] == "press count unknown" for v in daily_parts.values())


# ======================================================= H-07 obsolete vehicle


def _scored_then_rerouted(db, config, *, bulk: bool = False) -> str:
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    assert _vehicles_for(db, cid) == ["ne_innovation_fund"]
    _become_a_durham_spinout(db, cid)
    if bulk:
        rescore_all(db, config, today=TODAY)
        approve_cards(db)
    else:
        score_company(db, cid, config, today=TODAY)
        approve_cards(db)
    return cid


def test_daily_rescore_removes_the_obsolete_vehicle_row(db, config):
    cid = _scored_then_rerouted(db, config)

    assert _vehicles_for(db, cid) == ["spinout_inspire"]
    assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?", (cid,)) == 4


def test_bulk_rescore_removes_the_obsolete_vehicle_row(db, config):
    cid = _scored_then_rerouted(db, config, bulk=True)

    assert _vehicles_for(db, cid) == ["spinout_inspire"]
    assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?", (cid,)) == 4


def test_removing_an_obsolete_vehicle_row_removes_its_components(db, config):
    _scored_then_rerouted(db, config)

    orphans = db.scalar(
        "SELECT COUNT(*) FROM score_component "
        "WHERE score_id NOT IN (SELECT id FROM score)")
    assert orphans == 0


def test_a_fund_scoped_run_only_cleans_its_own_fund(db, config):
    """`--fund` refreshes one fund and must leave the others alone."""
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    _become_a_durham_spinout(db, cid)
    dsw_before = _vehicles_for(db, cid, "dsw")

    score_company(db, cid, config, today=TODAY, fund_key="northstar")
    approve_cards(db)

    assert _vehicles_for(db, cid, "northstar") == ["spinout_inspire"]
    assert _vehicles_for(db, cid, "dsw") == dsw_before


@pytest.mark.parametrize("bulk", [False, True])
def test_every_reader_shows_the_current_vehicle(db, config, bulk):
    """Sheet, digest, Kept and Today QA each take the highest-priority row. With
    the old vehicle's row still stored (priority 91 against 87) every one of
    them reported the route that had just been retired."""
    from prototype.server import build_kept, set_verdict
    from radar.qa.today import load_today_cards
    from radar.render.digest import _shortlist
    from radar.render.sheet import build_companies

    cid = _scored_then_rerouted(db, config, bulk=bulk)
    set_verdict(db.conn, cid, "worth contacting")
    day = date.fromisoformat(db.scalar("SELECT date('now')"))
    current = "spinout_inspire"
    current_name = next(v.vehicle_name for v in config.fund("northstar").vehicles
                        if v.vehicle_key == current)

    sheet = build_companies(db, config, {}, today=TODAY)
    assert [row.cells["R"] for row in sheet] == [current]

    digest = _shortlist(db, day, day)
    assert [e["vehicle_key"] for e in digest] == [current]

    kept = build_kept(db.conn)["worth contacting"]
    assert [k["vehicle"] for k in kept] == [current_name]

    # A Kept company leaves the QA queue; explicit lookup still verifies its route.
    cards = load_today_cards(db, config, company_id=cid)
    assert [c.vehicle_key for c in cards] == [current]


# ========================================================== M-06 snapshots


class _Clock:
    """Stamps for the scoring passes, so a test can score on chosen days."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def at(self, stamp: str) -> None:
        self.monkeypatch.setattr("radar.pipeline.now_iso", lambda: stamp)


MON, TUE, WED = "2026-08-03T06:00:00Z", "2026-08-04T06:00:00Z", "2026-08-05T06:00:00Z"


def _digest(db, day: str, period: str = "today") -> str:
    from radar.render.digest import render_digest

    return render_digest(db, period=period, on_date=day)


def test_score_snapshot_only_keeps_what_the_digests_read(db, config, monkeypatch):
    """Growth bound: shortlist rows only, one per company × fund × day."""
    _Clock(monkeypatch).at(MON)
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    score_company(db, cid, config, today=TODAY)          # a second pass, same day
    approve_cards(db)

    rows = db.query("SELECT * FROM score_snapshot")
    assert [(r["company_id"], r["fund_key"], r["tier"], r["snapshot_date"])
            for r in rows] == [(cid, "northstar", "shortlist", "2026-08-03")]
    assert json.loads(rows[0]["components"])            # the ledger's inputs


def test_a_later_rescore_does_not_change_an_earlier_days_digest(db, config, monkeypatch):
    """The audit's scenario: shortlisted Monday, rescored Tuesday. Monday's
    digest filtered on `score.scored_at`, which Tuesday's pass had overwritten."""
    clock = _Clock(monkeypatch)
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    clock.at(MON)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    monday_before = _digest(db, "2026-08-03")
    assert "METzero Technologies" in monday_before

    _become_a_durham_spinout(db, cid)
    db.execute("UPDATE company SET sector = 'life_sciences' WHERE id = ?", (cid,))
    clock.at(TUE)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)

    assert _digest(db, "2026-08-03") == monday_before      # byte for byte
    tuesday = _digest(db, "2026-08-04")
    assert "METzero Technologies" in tuesday
    # Monday's route and ledger are Monday's, not the rescore's.
    assert "NE Innovation Fund" in monday_before
    assert "Spinout Inspire Fund" not in monday_before
    assert "Spinout Inspire Fund" in tuesday
    assert "Climate Tech" in monday_before and "Life Sciences" not in monday_before


def test_the_weekly_digest_keeps_a_company_that_was_later_dropped(db, config, monkeypatch):
    clock = _Clock(monkeypatch)
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    clock.at(MON)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    # Wednesday: too old for the freshness gate, so every fund rejects it.
    db.execute("UPDATE company SET incorporated_on = '2020-01-01' WHERE id = ?", (cid,))
    clock.at(WED)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    assert db.scalar("SELECT COUNT(*) FROM score WHERE tier = 'shortlist'") == 0

    assert "METzero Technologies" in _digest(db, "2026-08-09", period="week")
    assert "METzero Technologies" in _digest(db, "2026-08-03")
    assert "METzero Technologies" not in _digest(db, "2026-08-05")


def test_a_same_day_rescore_replaces_that_days_snapshot(db, config, monkeypatch):
    """Today's digest is today's final state: a retune that drops a company from
    the shortlist must drop it from today's digest too."""
    clock = _Clock(monkeypatch)
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    clock.at(MON)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    assert "METzero Technologies" in _digest(db, "2026-08-03")

    db.execute("UPDATE company SET incorporated_on = '2020-01-01' WHERE id = ?", (cid,))
    clock.at("2026-08-03T15:00:00Z")
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)

    assert db.scalar("SELECT COUNT(*) FROM score_snapshot") == 0
    assert "METzero Technologies" not in _digest(db, "2026-08-03")


def test_bulk_rescore_writes_the_same_snapshots_as_the_daily_path(db, config, monkeypatch):
    _Clock(monkeypatch).at(MON)
    ids = [store_company(db, _metzero()),
           store_company(db, _metzero(id="second", canonical_name="Second Co",
                                      norm_key="secondco", news_mention_count=5))]
    for fixture_cid in ids:
        _add_source(db, fixture_cid)

    def snapshot():
        return [tuple(r) for r in db.query(
            "SELECT company_id, fund_key, snapshot_date, vehicle_key, config_hash,"
            " fund_fit_pct, coverage, discovery_edge, priority, tier, components"
            " FROM score_snapshot ORDER BY company_id, fund_key")]

    for cid in ids:
        score_company(db, cid, config, today=TODAY)
        approve_cards(db)
    daily = snapshot()
    db.execute("DELETE FROM score_snapshot")
    rescore_all(db, config, today=TODAY)
    approve_cards(db)

    assert daily and snapshot() == daily


def test_a_day_before_snapshots_existed_still_renders_from_score(db, config, monkeypatch):
    """Legacy databases hold `score` rows and no snapshots. They keep rendering."""
    cid = store_company(db, _metzero())
    _add_source(db, cid)
    _Clock(monkeypatch).at(MON)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    db.execute("DELETE FROM score_snapshot")

    assert "METzero Technologies" in _digest(db, "2026-08-03")


def test_an_unqualified_company_leaves_no_snapshot(db, config, monkeypatch):
    """`score_company` deletes the scores of a registry company that stopped
    qualifying; today's snapshot of it must go with them."""
    cid = store_company(db, registry_company(
        age_months=6, canonical_name="Fading Ltd", norm_key="fadingltd",
        has_share_issue=True))
    _Clock(monkeypatch).at(MON)
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    db.execute(
        "INSERT INTO score_snapshot(company_id, fund_key, snapshot_date, config_hash,"
        " fund_fit_pct, coverage, discovery_edge, priority, tier, scored_at)"
        " VALUES (?, 'northstar', '2026-08-03', 'h', 80, 1, 80, 80, 'shortlist', ?)",
        (cid, MON))
    db.execute("UPDATE company SET has_share_issue = 0, qualifiers = NULL WHERE id = ?",
               (cid,))

    assert score_company(db, cid, config, today=TODAY) == 0
    approve_cards(db)
    assert db.scalar("SELECT COUNT(*) FROM score_snapshot") == 0


# ================================================== M-07 derived facts on cards


def _derived_company(**over) -> Company:
    """A register find: SIC code, postcode and an SH01, but no stored sector,
    stage or region. Scoring derives all three."""
    base = dict(
        age_months=8, canonical_name="Derived Co", norm_key="derivedco",
        sector=None, stage=None, hq_region=None, founder_signal=None,
        traction_signal=None, hq_postcode="NE1 4ST", sic_codes=["72110"],
        has_share_issue=True, is_university_spinout=False, news_mention_count=1,
        discovery_route="news", founders=[F(prior_appointments=0, is_psc=True)],
    )
    return C(**{**base, **over})


def test_derived_facts_are_what_scoring_derived(db, config):
    from radar.score.derive import derived_facts

    cid = store_company(db, _derived_company())
    row = db.one("SELECT * FROM company WHERE id = ?", (cid,))

    assert derived_facts(row, config, today=TODAY) == {
        "hq_region": "north_east", "sector": "life_sciences", "stage": "pre_seed"}
    # ...and they are the values the score's own components report.
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)
    evidence = {r["key"]: r["evidence"] for r in db.query(
        "SELECT sc.key, sc.evidence FROM score_component sc JOIN score s"
        " ON s.id = sc.score_id WHERE s.company_id = ? AND s.fund_key = 'northstar'",
        (cid,))}
    assert (evidence["sector"], evidence["stage"], evidence["geography"]) == (
        "Life Sciences", "Pre-seed", "North East")


def test_stated_facts_are_never_replaced_by_derived_ones(db, config):
    from radar.score.derive import derived_facts

    cid = store_company(db, _derived_company(sector="fintech", stage="seed",
                                             hq_region="london"))
    row = db.one("SELECT * FROM company WHERE id = ?", (cid,))

    assert derived_facts(row, config, today=TODAY) == {}


def test_the_sheet_shows_the_facts_the_score_used(db, config):
    from radar.render.sheet import build_companies

    cid = store_company(db, _derived_company())
    score_company(db, cid, config, today=TODAY)
    approve_cards(db)

    (row,) = build_companies(db, config, {}, today=TODAY)

    assert (row.cells["G"], row.cells["H"], row.cells["I"]) == (
        "north_east", "life_sciences", "pre_seed")
    # The stored columns stay as sources reported them: derived is not stated.
    stored = db.one("SELECT sector, stage, hq_region FROM company WHERE id = ?", (cid,))
    assert tuple(stored) == (None, None, None)


def test_the_today_card_shows_the_facts_the_score_used(db, config):
    from prototype.server import build_today

    # Region is stored so the HARD North East vehicles can verify it; sector and
    # stage are left for scoring to derive.
    cid = store_company(db, _derived_company(hq_region="north_east"))
    _add_source(db, cid)
    score_company(db, cid, config, today=date.today())
    approve_cards(db)

    cards = build_today(db.conn)["companies"]

    assert [c["company_id"] for c in cards] == [cid]
    assert (cards[0]["sector"], cards[0]["stage"]) == ("life_sciences", "pre_seed")


# ============================================================ M-08 tuning sweep


def _scored(db, fit, *, edge=70.0, coverage=0.9, flags=None, reason=None,
            tier="watchlist", fund="northstar", company_id=None, name="Co"):
    """One stored score row. Vary one field at a time; the rest can shortlist."""
    if company_id is None:
        company_id = store_company(db, C(
            canonical_name=name, norm_key=name.lower().replace(" ", ""),
            age_months=6))
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, fund, None, fit, coverage, edge, 0.6 * fit + 0.4 * edge,
         tier, reason, "x", json.dumps(flags) if flags else None,
         "h", "1", now_iso()))
    return company_id


def _would_shortlist(db, threshold: int, **kw) -> int:
    from radar.score.tune import sweep

    result = sweep(db, fit_grid=[threshold], **kw)
    return next(r for r in result["sweep"] if r["threshold"] == threshold)["would_shortlist"]


def test_sweep_reapplies_the_edge_floor(db):
    _scored(db, 90, edge=30.0, name="Famous Co")        # edge floor is 55
    _scored(db, 90, edge=70.0, name="Obscure Co")

    assert _would_shortlist(db, 60) == 1


def test_sweep_reapplies_the_coverage_floor(db):
    _scored(db, 90, coverage=0.3, name="Thin Co")       # coverage floor is 0.5
    _scored(db, 90, coverage=0.9, name="Known Co")

    assert _would_shortlist(db, 60) == 1


def test_sweep_never_shortlists_a_flagged_company(db):
    _scored(db, 95, flags=["gate_unverified"], name="Flagged Co")

    assert _would_shortlist(db, 10) == 0


def test_sweep_never_shortlists_a_gate_reject(db):
    _scored(db, 95, tier="reject", reason="max_company_age_months", name="Old Co")

    assert _would_shortlist(db, 10) == 0


def test_sweep_counts_an_existing_reject_once_the_threshold_falls_below_it(db):
    """A "below fit threshold" reject (no gate fired) is a tier decision, so a
    lower threshold can change it. The old sweep skipped every `reject`."""
    _scored(db, 40, tier="reject", name="Low Fit Co")

    assert _would_shortlist(db, 60) == 0
    assert _would_shortlist(db, 35) == 1


def test_sweep_counts_a_company_through_any_of_its_fund_rows(db):
    """The best-priority row fails the coverage floor; another fund's row is a
    real shortlist. The company is shortlisted, as the pipeline would say."""
    cid = _scored(db, 80, coverage=0.3, fund="northstar", name="Two Funds Co")
    _scored(db, 72, coverage=0.8, fund="dsw", company_id=cid)

    assert _would_shortlist(db, 70) == 1
    assert _would_shortlist(db, 75) == 0


def test_sweep_honours_a_fund_scope(db):
    cid = _scored(db, 80, coverage=0.3, fund="northstar", name="Two Funds Co")
    _scored(db, 72, coverage=0.8, fund="dsw", company_id=cid)

    assert _would_shortlist(db, 70, fund_key="northstar") == 0
    assert _would_shortlist(db, 70, fund_key="dsw") == 1


def test_sweep_uses_the_live_settings_not_the_defaults(db, config):
    from radar.config.loader import save_snapshot

    strict = config.model_copy(deep=True)
    strict.settings.shortlist_edge = 80
    save_snapshot(db, strict)
    _scored(db, 90, edge=70.0, name="Mid Edge Co")      # clears 55, not 80

    assert _would_shortlist(db, 60) == 0


def test_change_points_skip_rows_that_can_never_shortlist(db):
    """A row that no threshold can shortlist is not a change point."""
    from radar.score.tune import _score_rows, change_points

    _scored(db, 88, edge=70.0, name="Real Co")
    _scored(db, 95, edge=20.0, name="Low Edge Co")
    _scored(db, 93, flags=["age_unknown"], name="Flagged Co")

    assert change_points(_score_rows(db)) == [88]
