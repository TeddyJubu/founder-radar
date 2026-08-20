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
    load_config,
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
    funds, _warnings, _cells = parse_fund_criteria(
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
    assert [fund.key for fund in funds] == ["dsw"]
    assert funds[0].vehicles[0].vehicle_key == "seis_fund"


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
