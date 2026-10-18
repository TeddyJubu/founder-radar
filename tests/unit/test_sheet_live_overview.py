"""The workbook must agree with the dashboard (Oct 2026: v1's Overview sat at
"93 leads, Outward VC 68%" while the dashboard showed mostly DSW)."""
from prototype.server import build_today as dashboard
from radar.config.defaults import default_config
from radar.config.loader import parse_sources
from radar.render.sheet import (
    OVERVIEW_ROWS, build_overview_grid, build_sources, build_today, today_cards,
)
from tests.fakes import seed_companies


def test_overview_counts_are_the_dashboards(db):
    seed_companies(db, count=6, shortlist=2)
    shown = dashboard(db.conn, limit=100)["companies"]
    grid = build_overview_grid(db, default_config())
    assert len(grid) == OVERVIEW_ROWS                    # blanks overwrite old v1 cells
    assert grid[4][0] == str(len(shown))
    fund_rows = {row[0]: row[1] for row in grid[10:14]}
    assert sum(int(v) for v in fund_rows.values()) == len(shown)


def test_the_sheet_today_tab_lists_the_dashboard_companies(db):
    seed_companies(db, count=6, shortlist=2)
    cfg = default_config()
    rows = build_today(db, cfg, {}, today=__import__("datetime").date.today())
    sheet_ids = {r.key.split(":")[0] for r in rows if not r.key.startswith("__")}
    expected = {c.company_id for c in today_cards(db, cfg)[:cfg.settings.daily_digest_max]}
    assert sheet_ids == expected


def test_feed_url_and_funds_round_trip_through_the_sources_tab(db):
    from radar.config.models import SourceConfig
    cfg = default_config().model_copy(update={"sources": [
        SourceConfig(key="feed_sifted", url="https://sifted.eu/feed", funds=["outward"])]})
    row = build_sources(db, cfg, today=__import__("datetime").date.today())[0]
    assert row.cells["I"] == "https://sifted.eu/feed" and row.cells["J"] == "outward"
    grid = [["Source"], [row.cells[c] for c in "ABCDEFGHIJ"]]
    parsed = parse_sources(grid)[0]
    assert parsed.url == "https://sifted.eu/feed" and parsed.funds == ["outward"]
