"""Stable, soft diversity ordering. Never changes scores, matches or membership."""
from __future__ import annotations

from collections import Counter
from typing import Any


def source_family(key: str | None, route: str | None) -> str:
    key = (key or "").lower()
    if key in {"innovate_uk", "ukri", "ukri_gtr", "gateway_to_research", "gtr"}:
        return "public_research_grants"
    if key == "companies_house":
        return "registry"
    # Different publications are one discovery family, not artificial variety.
    if route == "news":
        return "press"
    return route or key or "unknown"


def diversify(rows: list[Any], *, metadata: dict[str, dict]) -> list[Any]:
    """Reorder within each track using at most twelve priority points of pressure.

    A repeat costs 2 points for the actual winning viable fund, 1.5 for source
    family and 1 for sector (each saturates after three repeats). No quota,
    invented match, random choice, exclusion, or persistent score adjustment.
    Input order breaks ties. Unknowns never earn a diversity advantage.
    """
    result = []
    for track in (0, 1):
        pool = [(i, row) for i, row in enumerate(rows)
                if metadata[row["company_id"]]["track"] == track]
        funds, sources, sectors = Counter(), Counter(), Counter()
        while pool:
            def value(item):
                index, row = item
                m = metadata[row["company_id"]]
                penalty = min(12, 2 * (3 if m["fund"] == "unknown" else min(funds[m["fund"]], 3))
                              + 1.5 * (3 if m["source"] == "unknown" else min(sources[m["source"]], 3))
                              + (3 if m["sector"] == "unknown" else min(sectors[m["sector"]], 3)))
                return (-(float(row["priority"] or 0) - penalty), index)
            picked = min(pool, key=value)
            pool.remove(picked)
            row = picked[1]
            m = metadata[row["company_id"]]
            funds[m["fund"]] += 1
            sources[m["source"]] += 1
            sectors[m["sector"]] += 1
            result.append(row)
    return result


def rank_today_rows(db: Any, rows: list[Any], *, config_hash: str | None = None) -> list[Any]:
    """Shared ordering for the QA queue and approved Today cards.

    Fund diversity uses the actual winning score only if that fund has a latest
    non-rejected reviewable match. Rejected/unscored funds cannot supply variety.
    """
    if not rows:
        return []
    def query(sql, params=()):
        return list(db.query(sql, params)) if hasattr(db, "query") else list(db.execute(sql, params))
    sql = """SELECT company_id, fund_key, tier, reject_reason FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY company_id, fund_key
          ORDER BY scored_at DESC, id DESC) rank FROM score s"""
    if config_hash:
        sql += " WHERE config_hash = ?"
    sql += ") WHERE rank = 1"
    viable = {(r["company_id"], r["fund_key"]) for r in query(sql, (config_hash,) if config_hash else ())
              if r["tier"] in {"shortlist", "watchlist"} and not r["reject_reason"]}
    source = {}
    for r in query("SELECT company_id, source_key, source_url FROM company_source ORDER BY last_seen DESC, source_key"):
        if not str(r["source_url"] or "").startswith(("http://", "https://")):
            continue
        cid = r["company_id"]
        if cid not in source or source[cid] == "companies_house":
            source[cid] = r["source_key"]
    metadata = {}
    for row in rows:
        cid, fund, route = row["company_id"], row["fund_key"], row["discovery_route"]
        metadata[cid] = {
            "track": 0 if route in {"news", "grant", "spinout", "accelerator"} else 1,
            "fund": fund if (cid, fund) in viable else "unknown",
            "source": source_family(source.get(cid), route),
            "sector": row["sector"] or "unknown",
        }
    return diversify(rows, metadata=metadata)


def _value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def deterministic_block_reason(db, row, config, *, today, config_hash=None):
    """The shared Today gates before QA; never loads cards or reads QA state."""
    from radar.qa.today import _query, has_registry_venture_signal
    company_id = row["company_id"]
    from radar.config.models import STAGES, canon_enum
    from radar.score.gates import apply_freshness_gates, evaluate_vehicle_gates

    freshness = apply_freshness_gates(row, config, today=today)
    if not freshness.passed:
        return freshness.reason or "freshness_gate"
    # Lasting verdicts must not reappear the *next* morning. Same-calendar-day
    # verdicts stay on daily_review so Review Again (which only clears that
    # table) can restore the queue without erasing Kept / not for me.
    # Compare the ISO date prefix — morning sheet sync used to refresh
    # updated_at and break date()-based checks.
    try:
        day = today.isoformat() if hasattr(today, "isoformat") else str(today)
        decided = _query(db,
            "SELECT 1 FROM user_field WHERE company_id = ? AND field = 'verdict' "
            "AND TRIM(COALESCE(value, '')) != '' "
            "AND substr(updated_at, 1, 10) < ? LIMIT 1",
            (company_id, day),
        )
        decided = decided[0] if decided else None
        if decided:
            return "already_decided"
    except Exception:
        pass
    flags = list(freshness.flags)
    age_unknown = (not _value(row, "incorporated_on")) or "age_unknown" in flags
    stage = canon_enum(_value(row, "stage"), STAGES)

    if (row["discovery_route"] or "registry") in {"registry", ""}:
        if age_unknown:
            return "age_unknown"
        leftover = [flag for flag in flags if flag != "age_unknown"]
        if leftover:
            return leftover[0]
        if not has_registry_venture_signal(db, company_id):
            return "registry_without_venture_signal"
    else:
        leftover = [flag for flag in flags if flag != "age_unknown"]
        if leftover:
            return leftover[0]
        if age_unknown and (stage is None or STAGES.index(stage) > STAGES.index("seed")):
            return "maturity_unknown"

    vehicle_key = _value(row, "vehicle_key")
    if not vehicle_key:
        sql = "SELECT vehicle_key FROM score WHERE company_id = ? AND tier IN ('shortlist','watchlist')"
        params = [company_id]
        if config_hash:
            sql += " AND config_hash = ?"
            params.append(config_hash)
        sql += " ORDER BY priority DESC, scored_at DESC, id DESC LIMIT 1"
        winner = _query(db, sql, tuple(params))
        vehicle_key = winner[0]["vehicle_key"] if winner else None
    vehicle = next((v for f in getattr(config, "funds", ()) for v in f.vehicles
                    if v.vehicle_key == vehicle_key), None)
    if vehicle is not None and vehicle.geo_rule == "HARD" and vehicle.geo_values:
        verdict = evaluate_vehicle_gates(row, vehicle, config)
        if not verdict.passed and (verdict.reason or "").startswith("geography"):
            return "geography_mismatch"
        if "geography" in (verdict.unverified_rules or ()):
            return "geography_unverified"

    return None


def recommend_today_rows(db: Any, rows: list[Any], config: Any, *, config_hash=None) -> list[dict]:
    """Suggest a real alternative for London; DSW EIS eligibility stays intact.

    Only a non-rejected reviewable route with at least half its weighted evidence
    present can be suggested. A London company without one remains research,
    with a warning rather than a claim of a strong regional match.
    """
    from radar.qa.today import _query
    from radar.score.derive import derived_facts
    from radar.score.gates import evaluate_vehicle_gates
    vehicles = {v.vehicle_key: v for f in getattr(config, "funds", ()) for v in f.vehicles}
    result = []
    for original in rows:
        row = dict(original)
        row["recommendation_reason"] = None
        row["recommendation_warning"] = None
        sql = """SELECT * FROM (SELECT s.*, ROW_NUMBER() OVER (
            PARTITION BY fund_key ORDER BY scored_at DESC, id DESC) rank
            FROM score s WHERE company_id = ?"""
        params = [row['company_id']]
        if config_hash:
            sql += " AND config_hash = ?"
            params.append(config_hash)
        sql += ") WHERE rank = 1 ORDER BY priority DESC, scored_at DESC, id DESC"
        scores = [score for score in _query(db, sql, tuple(params))
                  if score['tier'] in {'shortlist', 'watchlist'} and not score['reject_reason']]
        if not scores:
            continue
        score_keys = ('fund_key', 'vehicle_key', 'tier', 'fund_fit_pct', 'discovery_edge',
                      'coverage', 'priority', 'explanation', 'flags')
        for key in score_keys:
            row[key] = scores[0][key]
        row['sid'] = scores[0]['id']
        company = _query(db, "SELECT * FROM company WHERE id = ?", (row['company_id'],))
        facts = derived_facts(company[0], config) if company else {}
        if row['fund_key'] != 'dsw' or facts.get('hq_region') != 'london':
            result.append(row)
            continue
        alternative = None
        for score in scores:
            if score['fund_key'] == 'dsw':
                continue
            if score['tier'] not in {'shortlist', 'watchlist'} or score['reject_reason'] or (score['coverage'] or 0) < 0.5:
                continue
            vehicle = vehicles.get(score['vehicle_key'])
            if vehicle is None or not vehicle.active:
                continue
            verdict = evaluate_vehicle_gates(company[0], vehicle, config)
            if not verdict.passed or (vehicle.geo_rule == 'HARD' and 'geography' in verdict.unverified_rules):
                continue
            alternative = score
            break
        if alternative is not None:
            for key in ('fund_key', 'vehicle_key', 'tier', 'fund_fit_pct', 'discovery_edge',
                        'coverage', 'priority', 'explanation', 'flags'):
                row[key] = alternative[key]
            row['sid'] = alternative['id']
            row['recommendation_reason'] = (
                'Suggested fund: this company is in London. DSW EIS remains eligible, '
                'but Today prefers a suitable alternative to DSW’s regional focus. '
                'The scores below have not changed.')
        else:
            row['recommendation_warning'] = (
                'Research only: this company is in London. DSW EIS remains eligible, '
                'but it is outside DSW’s regional focus. No adequately evidenced '
                'alternative fund match is available.')
        result.append(row)
    return result
