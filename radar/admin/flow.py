"""Read-only model behind the Control room Flow tab (`GET /api/admin/flow`).

It answers one question for the client: what did each stage of the daily run
do, and how did the latest run go? Every number is read straight from the
tables the pipeline already writes, so the page and the CLI cannot disagree.

Nothing here writes. A fresh database has no tables yet, so every query treats
`sqlite3.OperationalError` as "no rows": the page renders zeros instead of
erroring before the first run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from radar.config.models import TIERS

SOURCE_HEALTH_DAYS = 14
RUNS_LIMIT = 14
SNAPSHOTS_LIMIT = 10

_RUN_COLUMNS = (
    "id", "started_at", "finished_at", "mode", "status", "items_fetched",
    "items_extracted", "companies_new", "companies_merged", "gated_out",
    "shortlisted", "llm_calls", "error",
)


@dataclass(frozen=True)
class Stage:
    """One box on the Flow tab. Order of STAGES is the order of the daily run."""

    id: str
    num: str
    name: str
    summary: str
    ai: bool
    network: bool
    deterministic: bool
    settings: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()


# Mirrors the "The daily run" table in README.md. `deterministic` for Extract and
# Today QA is the README's "Cached -> yes": the same input replays the same answer.
STAGES: tuple[Stage, ...] = (
    Stage("config", "①", "Config", "Read and validate the Google Sheet",
          ai=False, network=True, deterministic=True,
          settings=("llm_enabled",)),
    Stage("fetch", "②", "Fetch", "14 sources, each isolated and polite",
          ai=False, network=True, deterministic=False,
          settings=("regions_enabled", "ch_backfill_days", "ch_daily_window_days")),
    Stage("extract", "③", "Extract", "Article prose into a structured record",
          ai=True, network=True, deterministic=True,
          settings=("llm_enabled", "llm_model"), prompts=("extract.system",)),
    Stage("resolve", "④", "Resolve", "Normalise, match, merge, keep provenance",
          ai=False, network=False, deterministic=True),
    Stage("enrich", "⑤", "Enrich", "Officers, filings, postcode to region",
          ai=False, network=True, deterministic=True,
          settings=("max_enrichment_requests_per_run",)),
    Stage("score", "⑥", "Gate + score", "Derive, gate, fund fit, discovery edge",
          ai=False, network=False, deterministic=True,
          settings=("max_company_age_months", "max_total_funding_gbp", "max_stage",
                    "shortlist_fit", "shortlist_edge", "min_coverage",
                    "watchlist_fit", "min_qualifiers", "weight_fit", "weight_edge")),
    Stage("today_qa", "⑥½", "Today QA", "Hermes veto on the morning list",
          ai=True, network=True, deterministic=True,
          prompts=("today_qa.brief",)),
    Stage("render", "⑦", "Render", "Sheet (minimal diff), then Telegram digest",
          ai=False, network=True, deterministic=True,
          settings=("daily_digest_max",), prompts=("publish.brief",)),
)


def build_flow(conn: sqlite3.Connection, *, today: date | None = None,
               active_config_hash: str | None = None) -> dict:
    """Snapshot of the daily run for the Flow tab. Pure reads."""
    today = today or date.today()

    latest = _latest_run(conn)
    latest_sources = _run_sources(conn, latest["id"]) if latest else []
    # Tiers follow the config the user is looking at; without one, the newest
    # score row says which config produced the current cards.
    tier_hash = active_config_hash or _latest_score_hash(conn)
    tiers = _tier_counts(conn, tier_hash)
    qa = _today_qa_counts(conn, today)
    stats = _stage_stats(conn, latest, latest_sources, tiers, qa)

    stages = [
        {
            "id": s.id,
            "num": s.num,
            "name": s.name,
            "summary": s.summary,
            "ai": s.ai,
            "network": s.network,
            "deterministic": s.deterministic,
            "settings": list(s.settings),
            "prompts": list(s.prompts),
            "stats": stats[s.id],
        }
        for s in STAGES
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "active_config_hash": tier_hash,
        "stages": stages,
        "runs": _runs(conn),
        "latest_run_sources": latest_sources,
        "source_health": _source_health(conn, today),
        "tiers": tiers,
        "today_qa": qa,
        "llm_cache_entries": _scalar(conn, "SELECT COUNT(*) FROM llm_cache"),
        "quarantine_count": _scalar(conn, "SELECT COUNT(*) FROM quarantine"),
        "config_snapshots": _config_snapshots(conn),
    }


# ------------------------------------------------------------------ queries


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Missing table or column: a fresh or partly migrated database.
        return []


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    rows = _rows(conn, sql, params)
    if not rows or rows[0][0] is None:
        return 0
    return int(rows[0][0])


def _latest_run(conn: sqlite3.Connection) -> dict | None:
    cols = ", ".join(_RUN_COLUMNS)
    rows = _rows(conn, f"SELECT {cols} FROM run ORDER BY id DESC LIMIT 1")
    if not rows:
        return None
    return _run_dict(rows[0])


def _run_dict(row: sqlite3.Row) -> dict:
    out = {k: row[k] for k in _RUN_COLUMNS}
    out["duration_s"] = _duration_s(row["started_at"], row["finished_at"])
    return out


def _runs(conn: sqlite3.Connection) -> list[dict]:
    cols = ", ".join(_RUN_COLUMNS)
    rows = _rows(conn, f"SELECT {cols} FROM run ORDER BY id DESC LIMIT ?", (RUNS_LIMIT,))
    return [_run_dict(r) for r in rows]


def _duration_s(started_at: str | None, finished_at: str | None) -> float | None:
    """Seconds between two ISO timestamps; None if unfinished or unparseable."""
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at)
        return round((end - start).total_seconds(), 1)
    except (TypeError, ValueError):
        # Mixed aware/naive timestamps raise TypeError on subtraction.
        return None


def _run_sources(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = _rows(
        conn,
        "SELECT source_key, status, items, duration_ms, error FROM run_source "
        "WHERE run_id = ? ORDER BY source_key",
        (run_id,),
    )
    return [dict(r) for r in rows]


def _source_health(conn: sqlite3.Connection, today: date) -> list[dict]:
    start = today - timedelta(days=SOURCE_HEALTH_DAYS - 1)
    rows = _rows(
        conn,
        "SELECT source_key, observed_on, items, status FROM source_health "
        "WHERE observed_on BETWEEN ? AND ? ORDER BY source_key, observed_on",
        (start.isoformat(), today.isoformat()),
    )
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source_key"], []).append({
            "observed_on": r["observed_on"],
            "items": r["items"],
            "status": r["status"],
        })
    return [{"source_key": k, "days": days} for k, days in by_source.items()]


def _latest_score_hash(conn: sqlite3.Connection) -> str | None:
    rows = _rows(conn, "SELECT config_hash FROM score ORDER BY scored_at DESC LIMIT 1")
    return rows[0]["config_hash"] if rows else None


def _tier_counts(conn: sqlite3.Connection, config_hash: str | None) -> dict[str, int]:
    # The three named tiers always appear, so the page never has to guess a zero.
    counts = {tier: 0 for tier in TIERS}
    if config_hash is None:
        return counts
    rows = _rows(
        conn,
        "SELECT tier, COUNT(DISTINCT company_id) AS n FROM score "
        "WHERE config_hash = ? GROUP BY tier",
        (config_hash,),
    )
    for r in rows:
        counts[r["tier"]] = int(r["n"])
    return counts


def _today_qa_counts(conn: sqlite3.Connection, today: date) -> dict[str, int]:
    counts = {"pass": 0, "reject": 0, "incomplete": 0}
    rows = _rows(
        conn,
        "SELECT verdict, COUNT(*) AS n FROM today_check "
        "WHERE date(checked_at) = ? GROUP BY verdict",
        (today.isoformat(),),
    )
    for r in rows:
        counts[r["verdict"]] = int(r["n"])
    return counts


def _config_snapshots(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(
        conn,
        "SELECT config_hash, created_at, is_last_good FROM config_snapshot "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (SNAPSHOTS_LIMIT,),
    )
    return [
        {
            "config_hash": r["config_hash"],
            "created_at": r["created_at"],
            "is_last_good": bool(r["is_last_good"]),
        }
        for r in rows
    ]


# ------------------------------------------------------------------- stats


def _stage_stats(conn: sqlite3.Connection, latest: dict | None,
                 latest_sources: list[dict], tiers: dict[str, int],
                 qa: dict[str, int]) -> dict[str, list[dict]]:
    """Per-stage stat cards. Run-scoped numbers come from the latest run."""

    def run(key: str) -> int:
        return int(latest[key] or 0) if latest else 0

    def stat(label: str, value: Any) -> dict:
        return {"label": label, "value": value}

    ok_sources = sum(1 for s in latest_sources if s["status"] == "ok")
    failed_sources = sum(1 for s in latest_sources if s["status"] == "failed")

    return {
        "config": [
            stat("Config snapshots", _scalar(conn, "SELECT COUNT(*) FROM config_snapshot")),
        ],
        "fetch": [
            stat("Items fetched", run("items_fetched")),
            stat("Sources ok", ok_sources),
            stat("Sources failed", failed_sources),
        ],
        "extract": [
            stat("Items extracted", run("items_extracted")),
            stat("AI calls", run("llm_calls")),
            stat("Saved AI answers", _scalar(conn, "SELECT COUNT(*) FROM llm_cache")),
            stat("Quarantined", _scalar(conn, "SELECT COUNT(*) FROM quarantine")),
        ],
        "resolve": [
            stat("New companies", run("companies_new")),
            stat("Merged", run("companies_merged")),
        ],
        "enrich": [
            stat("Companies (not merged)", _scalar(
                conn, "SELECT COUNT(*) FROM company WHERE merged_into IS NULL")),
        ],
        "score": [
            stat("Gated out", run("gated_out")),
            stat("Shortlisted", run("shortlisted")),
            stat("Shortlist tier", tiers["shortlist"]),
            stat("Watchlist tier", tiers["watchlist"]),
            stat("Reject tier", tiers["reject"]),
        ],
        "today_qa": [
            stat("Passed today", qa["pass"]),
            stat("Rejected today", qa["reject"]),
            stat("Incomplete today", qa["incomplete"]),
        ],
        "render": [
            stat("Latest run status", latest["status"] if latest else "no runs yet"),
        ],
    }
