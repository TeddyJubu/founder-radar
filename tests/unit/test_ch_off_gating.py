"""Companies House discovery off — no registry Today cards; no CH attach."""

from __future__ import annotations

from datetime import date

from radar.config.defaults import default_config
from radar.config.models import SourceConfig
from radar.enrich import RequestBudget
from radar.enrich.website_identity import verify_missing_crns
from radar.selection import deterministic_block_reason, source_enabled
from radar.store.db import now_iso
from tests.factories import approve_cards, registry_company, store_company
from tests.unit.test_website_identity import HTTP, company, page, profile


def _config(*, companies_house: bool):
    cfg = default_config()
    sources = [
        s.model_copy(update={"enabled": companies_house}) if s.key == "companies_house" else s
        for s in cfg.sources
    ]
    if not any(s.key == "companies_house" for s in sources):
        sources.append(SourceConfig(key="companies_house", track="B", enabled=companies_house))
    return cfg.model_copy(update={"sources": sources})


def _registry_row(db, company, *, source_key="companies_house", priority=80):
    cid = store_company(db, company)
    stamp = now_iso()
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, source_key, f"ext-{cid}",
         "https://find-and-update.company-information.service.gov.uk/company/15021885",
         stamp, stamp),
    )
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, "outward", None, 80.0, 0.8, 70.0, priority, "watchlist", None,
         "Queued for review.", None, "testhash", "1", stamp),
    )
    return cid


def test_source_enabled_reads_companies_house_flag():
    assert source_enabled(_config(companies_house=False), "companies_house") is False
    assert source_enabled(_config(companies_house=True), "companies_house") is True
    assert source_enabled(default_config(), "companies_house") is False


def test_registry_route_blocked_when_companies_house_disabled(db):
    cid = _registry_row(
        db,
        registry_company(
            canonical_name="Registry Off Ltd",
            norm_key="registryoff",
            age_months=6,
            has_share_issue=True,
        ),
    )
    row = {
        "company_id": cid,
        "discovery_route": "registry",
        "incorporated_on": db.one(
            "SELECT incorporated_on FROM company WHERE id = ?", (cid,),
        )["incorporated_on"],
        "stage": "pre_seed",
        "country_iso2": "GB",
        "on_vc_portfolio": 0,
        "total_funding_gbp": None,
        "vehicle_key": None,
    }
    reason = deterministic_block_reason(
        db, row, _config(companies_house=False), today=date.today(),
    )
    assert reason == "companies_house_disabled"


def test_empty_discovery_route_blocked_when_companies_house_disabled(db):
    cid = _registry_row(
        db,
        registry_company(
            canonical_name="Blank Route Ltd",
            norm_key="blankroute",
            age_months=6,
            has_share_issue=True,
            discovery_route="",
        ),
    )
    row = {
        "company_id": cid,
        "discovery_route": "",
        "incorporated_on": db.one(
            "SELECT incorporated_on FROM company WHERE id = ?", (cid,),
        )["incorporated_on"],
        "stage": "pre_seed",
        "country_iso2": "GB",
        "on_vc_portfolio": 0,
        "total_funding_gbp": None,
        "vehicle_key": None,
    }
    assert deterministic_block_reason(
        db, row, _config(companies_house=False), today=date.today(),
    ) == "companies_house_disabled"


def test_registry_venture_path_unchanged_when_companies_house_enabled(db, companies_house_on):
    from prototype.server import build_today

    cid = _registry_row(
        db,
        registry_company(
            canonical_name="SH01 Still On Ltd",
            norm_key="sh01stillon",
            age_months=6,
            has_share_issue=True,
        ),
    )
    approve_cards(db, [cid])
    payload = build_today(db.conn)
    assert cid in {row["company_id"] for row in payload["companies"]}


def test_today_hides_registry_when_companies_house_off(db):
    from prototype.server import build_today

    cid = _registry_row(
        db,
        registry_company(
            canonical_name="Hidden Registry Ltd",
            norm_key="hiddenregistry",
            age_months=6,
            has_share_issue=True,
        ),
    )
    # Force a QA pass so the CH-off gate is the first block reason, not QA.
    from radar.qa.today import TodayCheckResult, load_today_cards, record_check
    for card in load_today_cards(db, _config(companies_house=True), limit=100):
        if card.company_id == cid:
            record_check(db, card, TodayCheckResult(
                verdict="pass", checker="rules", summary="test"))
    payload = build_today(db.conn)
    assert cid not in {row["company_id"] for row in payload["companies"]}
    reasons = {row["key"]: row["count"]
               for row in payload["eligibility_diagnostics"]["reasons"]}
    assert reasons.get("companies_house_disabled") == 1


def test_verify_does_not_attach_ch_discovery_source_when_off(db):
    cid = company(db, country="GB")
    before = {
        (r["company_id"], r["source_key"], r["external_id"])
        for r in db.query("SELECT company_id, source_key, external_id FROM company_source")
    }
    result = verify_missing_crns(
        db, HTTP([page(), profile()]), api_key="test-key",
        budget=RequestBudget(20), attach_ch_source=False,
    )
    assert result["verified"] == 1
    row = db.one("SELECT companies_house_no, incorporated_on FROM company WHERE id=?", (cid,))
    assert row["companies_house_no"] == "12345678"
    assert row["incorporated_on"] == "2025-01-01"
    after = {
        (r["company_id"], r["source_key"], r["external_id"])
        for r in db.query("SELECT company_id, source_key, external_id FROM company_source")
    }
    assert after == before
    assert not db.one(
        "SELECT 1 FROM company_source WHERE company_id=? AND source_key=?",
        (cid, "companies_house"),
    )
    assert db.one(
        "SELECT 1 FROM observation WHERE company_id=? AND field=?",
        (cid, "verified_company_registration"),
    )


def test_verify_attaches_ch_discovery_source_when_on(db):
    cid = company(db, country="GB")
    result = verify_missing_crns(
        db, HTTP([page(), profile()]), api_key="test-key",
        budget=RequestBudget(20), attach_ch_source=True,
    )
    assert result["verified"] == 1
    linked = db.one(
        "SELECT source_key, external_id FROM company_source "
        "WHERE company_id=? AND source_key=?",
        (cid, "companies_house"),
    )
    assert linked is not None
    assert linked["external_id"] == "12345678"
