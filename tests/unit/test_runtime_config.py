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
    drop_legacy_website_qualifier,
    load_config,
    load_runtime_config,
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
    assert not leaked, f"Track B docs still treat website as admitting: {leaked}"
