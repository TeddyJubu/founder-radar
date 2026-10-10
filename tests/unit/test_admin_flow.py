"""Control room Flow model: shape, counts and tolerance of a fresh database."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from radar.admin.flow import STAGES, build_flow
from radar.render.sheet import SETTING_SPECS
from radar.store.db import Db

TODAY = date(2026, 10, 10)
STAGE_IDS = ["config", "fetch", "extract", "resolve", "enrich", "score", "today_qa", "render"]


def _seed(path) -> None:
    """Two-run history plus one company per tier, written straight to SQL."""
    db = Db(path)
    db.migrate()
    db.close()

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ts = "2026-10-10T06:00:00Z"
    for cid, name in [("c1", "Alpha"), ("c2", "Beta"), ("c3", "Gamma"), ("c4", "Delta")]:
        conn.execute(
            "INSERT INTO company (id, canonical_name, norm_key, first_seen, last_seen, "
            "created_at, updated_at, merged_into) VALUES (?,?,?,?,?,?,?,?)",
            (cid, name, name.lower(), ts, ts, ts, ts, "c3" if cid == "c4" else None),
        )

    # Run 1 never finished, run 2 finished in 512s, run 3 (latest) in 90s.
    conn.execute(
        "INSERT INTO run (id, started_at, finished_at, mode, status, error) "
        "VALUES (1, '2026-10-08T06:30:00Z', NULL, 'daily', 'failed', 'boom')")
    conn.execute(
        "INSERT INTO run (id, started_at, finished_at, mode, status, items_fetched, "
        "items_extracted, companies_new, companies_merged, gated_out, shortlisted, llm_calls) "
        "VALUES (2, '2026-10-09T06:30:00Z', '2026-10-09T06:38:32Z', 'daily', 'ok', "
        "99, 30, 4, 1, 20, 3, 10)")
    conn.execute(
        "INSERT INTO run (id, started_at, finished_at, mode, status, items_fetched, "
        "items_extracted, companies_new, companies_merged, gated_out, shortlisted, llm_calls) "
        "VALUES (3, '2026-10-10T06:30:00Z', '2026-10-10T06:31:30Z', 'daily', 'partial', "
        "120, 40, 9, 2, 30, 6, 18)")

    conn.executemany(
        "INSERT INTO run_source (run_id, source_key, status, items, duration_ms, error) "
        "VALUES (?,?,?,?,?,?)",
        [
            (3, "uktn", "ok", 12, 900, None),
            (3, "ukri", "failed", 0, 4000, "timeout"),
            (2, "uktn", "ok", 50, 700, None),
        ],
    )
    conn.executemany(
        "INSERT INTO source_health (source_key, observed_on, items, status) VALUES (?,?,?,?)",
        [
            ("uktn", "2026-10-08", 11, "ok"),
            ("uktn", "2026-10-10", 12, "ok"),
            ("ukri", "2026-10-10", 0, "failed"),
            ("uktn", "2026-07-01", 5, "ok"),  # outside the 14-day window
        ],
    )

    # Two config hashes: cfg_new drives the tiers; cfg_old must not leak in.
    scores = [
        ("c1", "northstar", "shortlist", "cfg_new", "2026-10-10T06:20:00Z"),
        ("c1", "dsw", "shortlist", "cfg_new", "2026-10-10T06:20:00Z"),
        ("c2", "outward", "watchlist", "cfg_new", "2026-10-10T06:20:00Z"),
        ("c3", "anticus", "reject", "cfg_new", "2026-10-10T06:20:00Z"),
        ("c2", "anticus", "shortlist", "cfg_old", "2026-10-01T06:20:00Z"),
    ]
    conn.executemany(
        "INSERT INTO score (company_id, fund_key, fund_fit_pct, coverage, discovery_edge, "
        "priority, tier, explanation, config_hash, scorer_version, scored_at) "
        "VALUES (?,?,50,1,50,50,?,'x',?,'v1',?)",
        scores,
    )

    conn.executemany(
        "INSERT INTO today_check (company_id, snapshot_hash, verdict, checker, "
        "prompt_version, checked_at) VALUES (?,?,?,'hermes','p1',?)",
        [
            ("c1", "h1", "pass", "2026-10-10T06:40:00Z"),
            ("c2", "h2", "reject", "2026-10-10T06:41:00Z"),
            ("c3", "h3", "pass", "2026-10-09T06:40:00Z"),  # yesterday, excluded
        ],
    )

    conn.execute(
        "INSERT INTO llm_cache (key, response_json, created_at) VALUES ('k1','{}',?)", (ts,))
    conn.execute(
        "INSERT INTO quarantine (source_key, raw_json, error, created_at) "
        "VALUES ('uktn','{}','bad',?)", (ts,))
    conn.executemany(
        "INSERT INTO config_snapshot (config_hash, config_json, is_last_good, created_at) "
        "VALUES (?,'{}',?,?)",
        [("cfg_old", 0, "2026-10-01T05:00:00Z"), ("cfg_new", 1, "2026-10-10T05:00:00Z")],
    )
    conn.close()


def test_build_flow_shape_and_counts(tmp_path):
    path = tmp_path / "radar.db"
    _seed(path)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row

    flow = build_flow(conn, today=TODAY)

    assert [s["id"] for s in flow["stages"]] == STAGE_IDS
    assert [s["num"] for s in flow["stages"]] == ["①", "②", "③", "④", "⑤", "⑥", "⑥½", "⑦"]
    datetime.fromisoformat(flow["generated_at"])
    # Tiers follow the newest score row's config when none is passed in.
    assert flow["active_config_hash"] == "cfg_new"
    assert flow["tiers"] == {"shortlist": 1, "watchlist": 1, "reject": 1}

    by_id = {s["id"]: s for s in flow["stages"]}
    assert by_id["fetch"]["stats"] == [
        {"label": "Items fetched", "value": 120},
        {"label": "Sources ok", "value": 1},
        {"label": "Sources failed", "value": 1},
    ]
    assert by_id["enrich"]["stats"] == [{"label": "Companies (not merged)", "value": 3}]
    assert by_id["render"]["stats"] == [{"label": "Latest run status", "value": "partial"}]
    assert by_id["extract"]["prompts"] == ["extract.system"]
    assert by_id["today_qa"]["prompts"] == ["today_qa.brief"]
    assert by_id["render"]["prompts"] == ["publish.brief"]

    # Runs: newest first, duration only where both timestamps parse.
    assert [r["id"] for r in flow["runs"]] == [3, 2, 1]
    assert flow["runs"][0]["duration_s"] == 90.0
    assert flow["runs"][1]["duration_s"] == 512.0
    assert flow["runs"][2]["duration_s"] is None
    assert flow["runs"][2]["error"] == "boom"

    assert [s["source_key"] for s in flow["latest_run_sources"]] == ["ukri", "uktn"]

    health = {h["source_key"]: h["days"] for h in flow["source_health"]}
    assert set(health) == {"uktn", "ukri"}
    assert [d["observed_on"] for d in health["uktn"]] == ["2026-10-08", "2026-10-10"]
    assert health["ukri"][0]["status"] == "failed"

    assert flow["today_qa"] == {"pass": 1, "reject": 1, "incomplete": 0}
    assert flow["llm_cache_entries"] == 1
    assert flow["quarantine_count"] == 1
    assert [c["config_hash"] for c in flow["config_snapshots"]] == ["cfg_new", "cfg_old"]
    assert flow["config_snapshots"][0]["is_last_good"] is True
    assert flow["config_snapshots"][1]["is_last_good"] is False
    conn.close()


def test_explicit_config_hash_and_read_only(tmp_path):
    path = tmp_path / "radar.db"
    _seed(path)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row

    before = conn.total_changes
    flow = build_flow(conn, today=TODAY, active_config_hash="cfg_old")
    assert conn.total_changes == before

    assert flow["active_config_hash"] == "cfg_old"
    assert flow["tiers"] == {"shortlist": 1, "watchlist": 0, "reject": 0}
    conn.close()


def test_fresh_database_renders_zeros():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    flow = build_flow(conn, today=TODAY)

    assert [s["id"] for s in flow["stages"]] == STAGE_IDS
    assert flow["runs"] == []
    assert flow["latest_run_sources"] == []
    assert flow["source_health"] == []
    assert flow["config_snapshots"] == []
    assert flow["tiers"] == {"shortlist": 0, "watchlist": 0, "reject": 0}
    assert flow["today_qa"] == {"pass": 0, "reject": 0, "incomplete": 0}
    assert flow["llm_cache_entries"] == 0
    assert flow["quarantine_count"] == 0
    assert flow["active_config_hash"] is None
    by_id = {s["id"]: s for s in flow["stages"]}
    assert by_id["render"]["stats"] == [{"label": "Latest run status", "value": "no runs yet"}]
    numeric = [x["value"] for sid in ("fetch", "extract", "resolve", "enrich", "score", "today_qa")
               for x in by_id[sid]["stats"]]
    assert numeric and all(v == 0 for v in numeric)
    conn.close()


def test_stage_settings_exist_in_setting_specs():
    known = {key for key, _, _ in SETTING_SPECS}
    for stage in STAGES:
        missing = set(stage.settings) - known
        assert not missing, f"{stage.id} lists unknown settings {missing}"
