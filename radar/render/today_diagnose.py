"""Explain an empty (or thin) Today list without opening the web UI.

Used by `founder-radar why-today` and `doctor` when the client says there is
nothing to review. Counts only — no company names — so the answer stays safe
to paste into Telegram.
"""

from __future__ import annotations

from typing import Any


def diagnose_today(db: Any) -> dict[str, Any]:
    """Return funnel + config health for the live last-good generation."""
    from radar.config.loader import (
        funds_are_poisoned,
        load_last_good,
        parse_snapshot,
    )
    from radar.config.defaults import default_config

    last_good = load_last_good(db)
    poisoned = bool(last_good and funds_are_poisoned(last_good.funds))
    config_hash = None
    if last_good is not None:
        config_hash = last_good.hash()
    else:
        row = db.one(
            "SELECT config_hash, config_json FROM config_snapshot "
            "WHERE is_last_good = 1 ORDER BY created_at DESC LIMIT 1"
        )
        if row:
            config_hash = row["config_hash"]
            parsed = parse_snapshot(row["config_json"]) if row["config_json"] else None
            if parsed is not None:
                poisoned = funds_are_poisoned(parsed.funds)

    if config_hash:
        tiers = {
            r["tier"]: int(r["n"])
            for r in db.query(
                "SELECT tier, COUNT(*) AS n FROM score "
                "WHERE config_hash = ? GROUP BY tier",
                (config_hash,),
            )
        }
        scored_for_hash = int(db.scalar(
            "SELECT COUNT(*) FROM score WHERE config_hash = ?", (config_hash,)
        ) or 0)
    else:
        tiers = {
            r["tier"]: int(r["n"])
            for r in db.query("SELECT tier, COUNT(*) AS n FROM score GROUP BY tier")
        }
        scored_for_hash = int(db.scalar("SELECT COUNT(*) FROM score") or 0)

    other_hash_scores = 0
    if config_hash:
        other_hash_scores = int(db.scalar(
            "SELECT COUNT(*) FROM score WHERE config_hash != ?",
            (config_hash,),
        ) or 0)

    hermes_rejected = 0
    try:
        hermes_rejected = int(db.scalar(
            """SELECT COUNT(*) FROM today_check
                WHERE verdict = 'reject'
                  AND id IN (
                        SELECT MAX(id) FROM today_check GROUP BY company_id
                  )"""
        ) or 0)
    except Exception:  # noqa: BLE001 — pre-migration DBs still diagnose
        hermes_rejected = 0

    last_run = db.one(
        "SELECT started_at, finished_at, status, items_fetched, gated_out, "
        "shortlisted, error FROM run ORDER BY id DESC LIMIT 1"
    )

    reviewable = int(tiers.get("shortlist", 0)) + int(tiers.get("watchlist", 0))

    likely_causes: list[str] = []
    if poisoned:
        likely_causes.append(
            "Fund Criteria last-good is poisoned (vehicle_key looks like "
            "Active YES/TRUE) — run `founder-radar repair-fund-criteria` "
            "then `founder-radar rescore --all`"
        )
    if config_hash and scored_for_hash == 0 and other_hash_scores > 0:
        likely_causes.append(
            "Scores exist only under an older config_hash — Today filters to "
            "last-good and sees zero cards. Run `founder-radar rescore --all`"
        )
    if last_run is not None and (last_run["shortlisted"] or 0) == 0:
        if (last_run["items_fetched"] or 0) == 0:
            likely_causes.append(
                "Last run fetched 0 items — sources may be failing or quiet"
            )
        else:
            likely_causes.append(
                "Last run shortlisted 0 after gates — quiet day or thresholds "
                "too strict (see Settings shortlist_fit / shortlist_edge)"
            )
    if hermes_rejected and reviewable == 0:
        likely_causes.append(
            f"Hermes Today QA has {hermes_rejected} stored rejects — check "
            "`today_check` reasons"
        )
    if not likely_causes and reviewable == 0:
        likely_causes.append(
            "No shortlist/watchlist rows for the active config — empty Today "
            "is expected until the next successful score write"
        )
    if not likely_causes and reviewable > 0:
        likely_causes.append(
            f"Today should show up to {reviewable} reviewable companies "
            "(shortlist + watchlist). If the web UI is empty, cards may already "
            "be reviewed today, lack an http(s) source URL, or fail a Today "
            "hide rule — open /api/today eligibility_diagnostics"
        )

    vehicle_keys: list[str] = []
    funds = (last_good or default_config()).funds
    for fund in funds:
        for vehicle in fund.vehicles:
            vehicle_keys.append(f"{fund.key}/{vehicle.vehicle_key}")

    return {
        "poisoned_fund_criteria": poisoned,
        "config_hash": config_hash,
        "tiers": {
            "shortlist": int(tiers.get("shortlist", 0)),
            "watchlist": int(tiers.get("watchlist", 0)),
            "reject": int(tiers.get("reject", 0)),
        },
        "scored_for_active_hash": scored_for_hash,
        "scores_on_other_hashes": other_hash_scores,
        "hermes_rejects_latest": hermes_rejected,
        "reviewable": reviewable,
        "vehicle_keys": vehicle_keys,
        "last_run": dict(last_run) if last_run else None,
        "likely_causes": likely_causes,
    }


def format_today_diagnosis(report: dict[str, Any]) -> str:
    """Human-readable block for the CLI / Telegram paste."""
    lines = ["📡 Founder Radar — why is Today empty?", ""]
    tiers = report["tiers"]
    lines.append(
        f"Active scores  shortlist {tiers['shortlist']} · "
        f"watchlist {tiers['watchlist']} · reject {tiers['reject']}"
    )
    if report.get("config_hash"):
        lines.append(f"config_hash   {report['config_hash'][:12]}…")
    lines.append(
        f"scored rows   {report['scored_for_active_hash']} on active hash · "
        f"{report['scores_on_other_hashes']} on older hashes"
    )
    lines.append(
        f"Fund Criteria {'POISONED' if report['poisoned_fund_criteria'] else 'ok'} · "
        f"Hermes rejects (latest) {report['hermes_rejects_latest']}"
    )
    last = report.get("last_run")
    if last:
        lines.append(
            f"Last run      {last.get('status')} · "
            f"fetched {last.get('items_fetched') or 0} · "
            f"gated {last.get('gated_out') or 0} · "
            f"shortlisted {last.get('shortlisted') or 0}"
        )
        if last.get("error"):
            lines.append(f"              ⚠️ {last['error']}")
    else:
        lines.append("Last run      none recorded")
    lines.append("")
    lines.append("Likely cause:")
    for cause in report.get("likely_causes") or ["unknown"]:
        lines.append(f"• {cause}")
    keys = report.get("vehicle_keys") or []
    if keys and not report["poisoned_fund_criteria"]:
        lines.append("")
        lines.append("Vehicles: " + ", ".join(keys[:12]))
    return "\n".join(lines)
