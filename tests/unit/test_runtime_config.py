"""Stage ① actually drives the daily run.

The 9 July promise — fund criteria live in the sheet, not the code — was
kept in `load_config` and then dropped: `founder-radar run` used
`default_config()` and never opened the spreadsheet. These tests pin the
runtime path so a Settings or Sources edit reaches scoring without a deploy.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from radar.config.defaults import DEFAULT_SOURCES, default_config
from radar.config.loader import (
    J25_META,
    J25B_META,
    canonical_fund_key,
    drop_legacy_website_qualifier,
    drop_legacy_admitting_qualifiers,
    funds_are_poisoned,
    load_config,
    load_last_good,
    load_runtime_config,
    parse_fund_criteria,
    save_snapshot,
    with_default_sources,
)
from radar.config.models import SourceConfig
from radar.render.formatting import SETTINGS, SETTINGS_HEADERS
from tests.fakes import FakeSheetGateway
from tests.factories import C, store_company

TODAY = date(2026, 8, 8)


class FakeHttp:
    def get(self, url, **kw):  # noqa: ARG002
        return SimpleNamespace(ok=True, status=200, text="", json=lambda: {})


def test_with_default_sources_appends_missing_keys_and_keeps_disabled():
    existing = [SourceConfig(key="oxford_innovation", track="A", enabled=False)]
    merged = with_default_sources(existing)
    by_key = {s.key: s for s in merged}
    assert by_key["oxford_innovation"].enabled is False
    assert "founders_factory" in by_key
    assert by_key["founders_factory"].enabled is True
    assert {s.key for s in DEFAULT_SOURCES} <= set(by_key)


def test_load_config_merges_new_default_sources_onto_an_existing_tab():
    raw = {
        "Sources": [
            ["Source", "Track", "Enabled"],
            ["oxford_innovation", "A", "TRUE"],
        ],
    }
    cfg, errors = load_config(raw)
    assert not errors
    keys = {s.key for s in cfg.sources}
    assert "oxford_innovation" in keys
    assert "founders_factory" in keys
    assert "ucl_ventures" in keys


def test_legacy_website_qualifier_is_stripped_until_the_lists_tab_is_rewritten(db):
    lists = {"qualifiers": ["share_issue", "website", "grant"]}
    out, stripped = drop_legacy_website_qualifier(lists, db)
    assert stripped is True
    assert "website" not in out["qualifiers"]
    assert "share_issue" in out["qualifiers"]


def test_after_j25_migration_website_can_be_re_enabled_from_the_sheet(db):
    db.set_meta(J25_META, "1")
    lists = {"qualifiers": ["share_issue", "website"]}
    out, stripped = drop_legacy_website_qualifier(lists, db)
    assert stripped is False
    assert "website" in out["qualifiers"]


def test_legacy_repeat_founder_qualifier_is_stripped_until_lists_rewritten(db):
    lists = {"qualifiers": ["share_issue", "repeat_founder", "grant"]}
    out, stripped = drop_legacy_admitting_qualifiers(lists, db)
    assert stripped == ["repeat_founder"]
    assert "repeat_founder" not in out["qualifiers"]
    assert "share_issue" in out["qualifiers"]


def test_after_j25b_migration_repeat_founder_can_be_re_enabled_from_the_sheet(db):
    db.set_meta(J25B_META, "1")
    lists = {"qualifiers": ["share_issue", "repeat_founder"]}
    out, stripped = drop_legacy_admitting_qualifiers(lists, db)
    assert stripped == []
    assert "repeat_founder" in out["qualifiers"]


def test_sheet_fund_display_names_canonicalize_to_score_keys():
    assert canonical_fund_key("DSW Ventures") == "dsw"
    assert canonical_fund_key("dsw ventures") == "dsw"
    assert canonical_fund_key("Outward VC") == "outward"
    assert canonical_fund_key("Northstar Ventures") == "northstar"
    lists = default_config().lists
    funds, _warnings, _cells, layout_error = parse_fund_criteria(
        [
            ["Fund key", "Vehicle key", "Fund", "Vehicle", "Active", "Stage min",
             "Stage max", "Cheque min", "Cheque max", "Geo rule", "Geo values",
             "Max age (yrs)", "Hard rejects", "Sectors +", "Sectors −",
             "One-liner"],
            ["DSW Ventures", "DSW SEIS Fund", "DSW Ventures", "DSW SEIS Fund",
             "TRUE", "idea", "pre_seed", "50000", "250000", "HARD",
             "outside_golden_triangle", "3", "", "deeptech", "", "Regional"],
        ],
        lists=lists,
    )
    assert layout_error is None
    assert [fund.key for fund in funds] == ["dsw"]
    assert funds[0].vehicles[0].vehicle_key == "seis_fund"


def _seeded_fund_criteria_grid():
    """The grid `seed_grids` writes for a blank Fund Criteria tab."""
    from radar.render.formatting import FUND_CRITERIA_HEADERS

    cfg = default_config()
    grid = [list(FUND_CRITERIA_HEADERS)]
    for vehicle in cfg.all_vehicles():
        rejects = " · ".join(
            f"{k}:{v}" for k, v in vehicle.hard_rejects.items()
        ) if isinstance(vehicle.hard_rejects, dict) else str(vehicle.hard_rejects or "")
        grid.append([
            vehicle.fund_key, vehicle.vehicle_key, vehicle.fund_name,
            vehicle.vehicle_name, "TRUE" if vehicle.active else "FALSE",
            vehicle.stage_min or "", vehicle.stage_max or "",
            vehicle.cheque_min or "", vehicle.cheque_max or "",
            vehicle.geo_rule, ", ".join(vehicle.geo_values),
            vehicle.max_age_years or "",
            rejects,
            ", ".join(vehicle.sectors_plus), ", ".join(vehicle.sectors_minus),
            vehicle.one_liner, "",
        ])
    return grid


def test_seeded_fund_criteria_round_trips_and_promotes_last_good(db):
    """Clean defaults still become last-good after a sheet load."""
    grid = _seeded_fund_criteria_grid()
    funds, warnings, _cells, layout_error = parse_fund_criteria(
        grid, lists=default_config().lists,
    )
    assert layout_error is None
    assert not any("TRUE/FALSE" in w for w in warnings.values())
    keys = {
        (fund.key, vehicle.vehicle_key)
        for fund in funds
        for vehicle in fund.vehicles
    }
    assert ("dsw", "seis_fund") in keys
    assert ("outward", "fund_ii") in keys
    assert all(vk != "yes" for _, vk in keys)

    result = load_config({"Fund Criteria": grid}, db=db)
    assert not result.errors
    row = db.one(
        "SELECT config_json FROM config_snapshot WHERE is_last_good = 1"
    )
    assert row is not None
    import json
    payload = json.loads(row["config_json"])
    vehicle_keys = [
        v["vehicle_key"]
        for fund in payload["funds"]
        for v in fund["vehicles"]
    ]
    assert "yes" not in vehicle_keys
    assert "seis_fund" in vehicle_keys


def test_misaligned_active_in_column_b_does_not_poison_last_good(db):
    """Live failure: Active YES in B → vehicle_key='yes', fund names = blurbs."""
    good = default_config()
    save_snapshot(db, good, is_last_good=True)
    prior = db.one(
        "SELECT config_hash FROM config_snapshot WHERE is_last_good = 1"
    )["config_hash"]

    # Fund key | Active | geography note | … — the signature that hit production.
    poisoned = [
        ["Fund key", "Active", "Note", "Vehicle", "Enabled", "Stage min",
         "Stage max", "Cheque min", "Cheque max", "Geo rule", "Geo values",
         "Max age (yrs)", "Hard rejects", "Sectors +", "Sectors −",
         "One-liner"],
        ["outward", "YES", "UK-based founders at entry", "Fund II", "TRUE",
         "pre_seed", "series_a", "", "", "HARD", "uk_wide", "", "", "", "", ""],
        ["dsw", "YES", "Regional UK preferred (not London-centric); UK founders",
         "SEIS", "TRUE", "idea", "pre_seed", "", "", "HARD",
         "outside_golden_triangle", "3", "", "", "", ""],
    ]
    funds, warnings, cells, layout_error = parse_fund_criteria(
        poisoned, lists=default_config().lists,
    )
    assert layout_error is not None
    assert funds == []
    assert all(
        "yes" not in (v.vehicle_key or "").lower()
        for fund in funds
        for v in fund.vehicles
    )
    assert any("TRUE/FALSE" in c.text or "shifted" in (layout_error or "").lower()
               for c in cells) or layout_error

    result = load_config({"Fund Criteria": poisoned}, db=db)
    assert "fund_criteria" in result.errors or result.errors
    assert result.used_last_good is True
    assert all(
        v.vehicle_key != "yes"
        for fund in result.config.funds
        for v in fund.vehicles
    )
    assert any(f.name == "Outward VC" for f in result.config.funds)
    still = db.one(
        "SELECT config_hash FROM config_snapshot WHERE is_last_good = 1"
    )["config_hash"]
    assert still == prior, "poisoned Fund Criteria must not replace last-good"


def test_poisoned_last_good_is_healed_from_code_defaults(db):
    """When sheet AND last-good are poison, defaults become the new last-good."""
    import json

    from radar.config.models import Fund, Vehicle

    poison = default_config().model_copy(deep=True)
    poison.funds = [
        Fund(
            key="outward",
            name="UK-based founders at entry",
            vehicles=[
                Vehicle(
                    fund_key="outward",
                    vehicle_key="yes",
                    fund_name="UK-based founders at entry",
                    vehicle_name="Pre-Series A",
                    active=True,
                ),
            ],
        ),
    ]
    save_snapshot(db, poison, is_last_good=True)
    assert funds_are_poisoned(load_last_good(db).funds)

    poisoned_grid = [
        ["Fund key", "Active", "Note", "Vehicle", "Enabled"],
        ["outward", "YES", "UK-based founders at entry", "Fund II", "TRUE"],
    ]
    result = load_config({"Fund Criteria": poisoned_grid}, db=db)
    assert result.healed_fund_criteria is True
    assert not result.errors
    assert not funds_are_poisoned(result.config.funds)
    assert any(f.name == "Outward VC" for f in result.config.funds)
    row = db.one(
        "SELECT config_json FROM config_snapshot WHERE is_last_good = 1"
    )
    payload = json.loads(row["config_json"])
    keys = [v["vehicle_key"] for f in payload["funds"] for v in f["vehicles"]]
    assert "yes" not in keys
    assert "fund_ii" in keys




def test_run_pipeline_reads_sheet_settings_when_a_gateway_is_present(db):
    """A Settings-tab age cap of 12 months must reject a 24-month company."""
    from radar.pipeline import run_pipeline

    sheet = FakeSheetGateway(tabs=[SETTINGS])
    sheet.grids[SETTINGS] = [
        list(SETTINGS_HEADERS),
        ["max_company_age_months", "12", "int 1–120", "", "Reject anything older"],
    ]
    cid = store_company(db, C(age_months=24))
    run_pipeline(
        db, config=None, gateway=sheet, http=FakeHttp(),
        use_llm=False, now=TODAY,
    )
    row = db.one(
        "SELECT reject_reason, tier FROM score WHERE company_id = ? LIMIT 1",
        (cid,),
    )
    assert row is not None
    assert row["reject_reason"] == "max_company_age_months"
    assert row["tier"] == "reject"


def test_run_pipeline_uses_last_good_when_the_sheet_is_unreachable(db):
    cfg = default_config()
    cfg = cfg.model_copy(
        update={"settings": cfg.settings.model_copy(
            update={"max_company_age_months": 12})},
        deep=True,
    )
    save_snapshot(db, cfg, is_last_good=True)

    loaded, gateway, warnings = load_runtime_config(db, gateway=None)
    assert gateway is None
    assert loaded.settings.max_company_age_months == 12
    assert "founders_factory" in {s.key for s in loaded.sources}

    from radar.pipeline import run_pipeline

    cid = store_company(db, C(age_months=24))
    run_pipeline(
        db, config=None, gateway=None, http=FakeHttp(),
        use_llm=False, now=TODAY,
    )
    row = db.one(
        "SELECT reject_reason FROM score WHERE company_id = ? LIMIT 1",
        (cid,),
    )
    assert row["reject_reason"] == "max_company_age_months"
    assert not any("sheet not" in w for w in warnings)


def test_build_sources_lists_the_named_early_adapters(db):
    from radar.render.sheet import build_sources

    rows = build_sources(db, default_config(), today=TODAY)
    keys = {r.cells["A"] for r in rows}
    assert "founders_factory" in keys
    assert "ucl_ventures" in keys


def test_track_b_docs_do_not_treat_website_as_an_admitting_qualifier():
    """J25 docs drift: README/PRD used to list a live website as Track B admission."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    leaked = []
    for relative in (
        "README.md",
        "docs/prd/README.md",
        "docs/prd/01-product-requirements.md",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        if "a live website, a matching grant" in text:
            leaked.append(relative)
        if "officer history, live website, grant match" in text:
            leaked.append(relative)
        if "press in a tracked source, or a repeat founder" in text:
            leaked.append(relative)
        if "press, repeat founder) gate Track B" in text:
            leaked.append(relative)
    assert not leaked, f"Track B docs still treat website as admitting: {leaked}"


def test_sheet_lists_tab_keeps_sic_map_and_j25_qualifiers():
    """The live rescore crash: Lists-tab columns are strings, but scoring
    needs `sic_sector.exact` and a qualifiers set that does not include
    `website`. Loading an empty-ish Lists tab must not drop those maps.
    """
    from radar.config.loader import load_config
    from radar.render.formatting import LISTS_HEADERS
    from radar.score.derive import derive_sector
    from radar.score.qualify import _admitting_qualifiers, is_qualified
    from tests.factories import registry_company

    raw = {"Lists": [list(LISTS_HEADERS)]}
    cfg, errors = load_config(raw)
    assert not errors
    table = cfg.lists["sic_sector"]
    assert isinstance(table, dict)
    assert "62012" in table["exact"]
    assert derive_sector(["62012"], cfg) == "b2b_saas"

    admitted = _admitting_qualifiers(cfg)
    assert "website" not in admitted
    assert "repeat_founder" not in admitted
    assert "share_issue" in admitted
    assert not is_qualified(
        registry_company(qualifiers=["website"], discovery_route="registry"),
        cfg,
    )


def test_stringified_sic_sector_column_does_not_crash_derive():
    """How the Lists tab used to seed `sic_sector`: iterating the nested
    dict wrote the keys `exact`/`prefix` as the column, and derive_sector
    then called `.get` on a list.
    """
    from radar.config.loader import load_config, parse_lists
    from radar.score.derive import derive_sector

    lists = parse_lists([
        ["stage", "sic_code", "sic_sector"],
        ["seed", "", "exact"],
        ["pre_seed", "", "prefix"],
    ])
    assert isinstance(lists["sic_sector"], dict)
    assert lists["sic_sector"]["exact"]["62012"] == "b2b_saas"
    assert derive_sector(["62012"], type("C", (), {"lists": lists})()) == "b2b_saas"

    raw = {"Lists": [["stage", "sic_code", "sic_sector"], ["seed", "", "exact"]]}
    cfg, errors = load_config(raw)
    assert not errors
    # A company with SIC codes must not raise — that was the VPS traceback.
    assert derive_sector(["72110"], cfg) == "life_sciences"


def test_paired_sic_columns_rebuild_the_sector_map():
    from radar.config.loader import parse_lists

    lists = parse_lists([
        ["sic_code", "sic_sector"],
        ["99999", "fintech"],
        ["77", "climate_tech"],
    ])
    assert lists["sic_sector"]["exact"]["99999"] == "fintech"
    assert lists["sic_sector"]["prefix"]["77"] == "climate_tech"


def test_lists_grid_round_trips_the_sic_map():
    from radar.config.defaults import default_config
    from radar.config.loader import parse_lists, sic_columns_from_map
    from radar.render.sheet import _lists_grid

    cfg = default_config()
    grid = _lists_grid(cfg)
    header = grid[0]
    assert "sic_code" in header and "sic_sector" in header
    parsed = parse_lists(grid)
    codes, _ = sic_columns_from_map(cfg.lists["sic_sector"])
    assert parsed["sic_sector"]["exact"]["62012"] == "b2b_saas"
    assert len(parsed["sic_sector"]["exact"]) == len(cfg.lists["sic_sector"]["exact"])
    assert "62012" in codes
    assert "website" not in parsed["qualifiers"]
    assert "repeat_founder" not in parsed["qualifiers"]
