"""The pipeline — seven stages, in order (05-pipeline).

```
① CONFIG → ② FETCH → ③ EXTRACT → ④ RESOLVE → ⑤ ENRICH → ⑥ GATE+SCORE → ⑦ RENDER
```

This is the only module that knows the order. Each stage is a call into its own
package, and **no stage raises into the one above it** — failures are recorded
and the run continues (05-pipeline, failure summary). The CLI (`radar.cli`)
and the tests drive everything through here.

Every parameter the tests need is injectable: `config`, `http`, `gateway`,
`now`, `use_llm`. With nothing injected, `run_pipeline` does the real thing —
reads the sheet, hits the network, renders. With a mock `http` and no gateway,
the same function is the chaos-test harness.

Stage ⑥ is the product. `evaluate()` is a pure function of `(company, config)`:
no AI, no network, byte-identical for identical inputs. It is separated here
so `founder-radar rescore --all` can recompute five thousand scores in under a
second and so the whole scoring model is unit-testable offline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from radar.config.models import Config
from radar.score.criteria import SCORER_VERSION
from radar.store.db import Db, new_id, now_iso

log = logging.getLogger(__name__)


# ------------------------------------------------------------------- results


@dataclass
class RunResult:
    """What one run did. The CLI prints `summary()`; tests assert on fields."""

    mode: str = "daily"
    scope: str | None = None
    status: str = "ok"                       # ok | partial | failed
    items_fetched: int = 0
    items_extracted: int = 0
    companies_new: int = 0
    companies_merged: int = 0
    gated_out: int = 0
    shortlisted: int = 0
    llm_calls: int = 0
    llm_cost_usd: float = 0.0
    error: str | None = None
    run_id: int | None = None
    sources: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scope": self.scope,
            "status": self.status,
            "items_fetched": self.items_fetched,
            "items_extracted": self.items_extracted,
            "companies_new": self.companies_new,
            "companies_merged": self.companies_merged,
            "gated_out": self.gated_out,
            "shortlisted": self.shortlisted,
            "llm_calls": self.llm_calls,
            "llm_cost_usd": round(self.llm_cost_usd, 4),
            "error": self.error,
            "run_id": self.run_id,
            "sources": self.sources,
        }


def _begin_run(db: Db, *, mode: str, scope: str | None) -> int:
    db.execute(
        """INSERT INTO run(started_at, mode, scope, status)
           VALUES (?, ?, ?, 'running')""",
        (now_iso(), mode, scope),
    )
    return int(db.scalar("SELECT last_insert_rowid()"))


def _finish_run(db: Db, run_id: int, result: RunResult) -> None:
    db.execute(
        """UPDATE run SET finished_at = ?, items_fetched = ?, items_extracted = ?,
                 companies_new = ?, companies_merged = ?, gated_out = ?,
                 shortlisted = ?, llm_calls = ?, llm_cost_usd = ?, status = ?,
                 error = ?, warnings = ?
           WHERE id = ?""",
        (now_iso(), result.items_fetched, result.items_extracted,
         result.companies_new, result.companies_merged, result.gated_out,
         result.shortlisted, result.llm_calls, result.llm_cost_usd,
         result.status, result.error,
         "\n".join(result.warnings) if result.warnings else None, run_id),
    )


# ------------------------------------------------------------------ stage ⑥


def funds_in_scope(cfg: Any, fund_key: str | None) -> list[Any]:
    """The funds a run scores against — every fund, or the one `--fund` names.

    Filtering here rather than trimming `cfg.funds` keeps `config_hash`
    identical to a full run, so a scoped run **upserts** the same score rows
    instead of inserting a second generation under a different hash.

    An unknown key raises rather than returning an empty list: a typo must not
    look like a run that scored nothing.
    """
    if fund_key is None:
        return list(cfg.funds)
    scoped = [f for f in cfg.funds if f.key == fund_key]
    if not scoped:
        known = ", ".join(f.key for f in cfg.funds)
        raise ValueError(f"unknown fund {fund_key!r} — known funds: {known}")
    return scoped


def evaluate(company: Any, cfg: Any, *, today: date | None = None,
             config_hash: str | None = None, fund_key: str | None = None) -> list[Any]:
    """Stage ⑥ — gate and score one company against every fund in scope.

    `fund_key` narrows the run to one fund (`--fund`); None scores all four.

    The layout is verbatim 05-pipeline §⑥ and 06-scoring:

    0. Derive the five scored attributes from raw evidence. Without this a
       registry company has nothing to score on.
    1. Universal freshness gates. A NULL input passes and sets a flag.
       A gate failure returns one reject row **per fund**.
    2. Qualification — a Track B company needs a reason to exist.
    3. Per-vehicle hard rules. Failing a vehicle excludes that vehicle only.
    4. Best vehicle wins the fund; Discovery Edge and priority follow.

    Pure: no network, no AI, no database writes. Returns `Score[]`.
    """
    from radar.score.criteria import Score
    from radar.score.derive import Company, derive_attributes
    from radar.score.discovery_edge import discovery_edge
    from radar.score.explain import explain
    from radar.score.fund_fit import fund_fit
    from radar.score.gates import apply_freshness_gates, evaluate_vehicle_gates
    from radar.score.qualify import is_qualified, qualification_reason
    from radar.score.tiering import priority_of, tier_of

    funds = funds_in_scope(cfg, fund_key)
    company = derive_attributes(company, cfg, today=today)

    # `Config.hash()` serialises the whole config and SHA-256s it — roughly a
    # millisecond. Computing it once per Score row would be four to eleven
    # serialisations per company; once per company is still a full second per
    # thousand. The batch callers (`rescore_all`, the daily run) compute it
    # once per run and pass it down; a standalone call falls back to one
    # computation (09-test-plan §8).
    if config_hash is None:
        config_hash = cfg.hash()

    # 1. universal freshness gates — the fix for the client's complaint
    gate = apply_freshness_gates(company, cfg, today=today)
    if not gate.passed:
        return [
            Score(
                company_id=company.id,
                company_name=company.canonical_name,
                fund_key=f.key,
                vehicle_key=None,
                tier="reject",
                reject_reason=gate.reason,
                fund_fit_pct=0.0,
                coverage=0.0,
                discovery_edge=0.0,
                priority=0.0,
                explanation=f"Rejected: {gate.reason} ({gate.detail}).",
                flags=list(gate.flags),
                config_hash=config_hash,
                scorer_version=SCORER_VERSION,
            )
            for f in funds
        ]

    # 2. qualification — Track B needs a reason to exist (06-scoring §3)
    if company.discovery_route == "registry" and not is_qualified(company, cfg):
        log.debug("unqualified registry company %s: %s",
                  company.canonical_name, qualification_reason(company, cfg))
        return []

    results: list[Score] = []
    # Discovery Edge is a property of the company, not of any fund — computing
    # it once per fund meant four identical component lists *and* four
    # identical headline numbers per company, which showed up as a measurable
    # slice of `rescore --all` (09-test-plan §8).
    edge_components = discovery_edge_components(company, cfg, today=today)
    edge = discovery_edge(company, cfg, today=today)
    for fund in funds:
        # Fit depends on the FUND, not the vehicle (fund_fit.py docstring): the
        # matrix and weights are the same for every vehicle under one fund, so
        # the components are computed once per fund and the winning vehicle's
        # key is stamped onto the copy. Eleven vehicles used to compute eleven
        # identical matrices per company — the single biggest slice of the
        # `rescore --all` profile.
        base_fit = fund_fit(company, fund, cfg)
        vehicle_scores: list[Any] = []
        vehicle_flags: list[str] = []
        # inactive vehicles (bbi_coinvest, ne_social) are off by default and
        # must never be scored (07-interfaces tab 4)
        for vehicle in fund.active_vehicles:
            verdict = evaluate_vehicle_gates(company, vehicle, cfg)
            if not verdict.passed:
                continue
            vehicle_flags.extend(verdict.flags)
            fit = base_fit.model_copy(update={"vehicle_key": vehicle.vehicle_key})
            vehicle_scores.append((vehicle, fit, verdict))

        if not vehicle_scores:
            results.append(Score(
                company_id=company.id,
                company_name=company.canonical_name,
                fund_key=fund.key,
                vehicle_key=None,
                tier="reject",
                reject_reason="no_eligible_vehicle",
                fund_fit_pct=0.0,
                coverage=0.0,
                discovery_edge=0.0,
                priority=0.0,
                explanation="Rejected: no eligible vehicle — every hard rule failed.",
                flags=list(gate.flags),
                config_hash=config_hash,
                scorer_version=SCORER_VERSION,
            ))
            continue

        vehicle, best, verdict = max(vehicle_scores, key=lambda t: t[1].pct)
        priority = priority_of(best.pct, edge, cfg)
        flags = sorted(set(gate.flags) | set(vehicle_flags))
        tier, why = tier_of(best, edge, flags, cfg)
        # `explain` already appends `tier_reason`. Appending it again here put
        # the same sentence in twice — once lower-case mid-paragraph and once
        # capitalised at the end — and, worse, the bulk rescore path never did
        # it, so the two paths produced different text for the same company.
        # `test_rescore_bulk_equals_daily` compares `explanation` and missed it
        # only because its fixture has no watchlist rows, where `why` is empty.
        explanation = explain(
            best, edge, company.signals, vehicle, flags,
            tier_reason=why,
            reject_reason=gate.reason,
        )

        results.append(Score(
            company_id=company.id,
            company_name=company.canonical_name,
            fund_key=fund.key,
            vehicle_key=vehicle.vehicle_key,
            vehicle_name=vehicle.vehicle_name,
            fund_fit_pct=best.pct,
            raw_sum=best.raw_sum,
            coverage=best.coverage,
            discovery_edge=edge,
            priority=priority,
            tier=tier,
            reject_reason=gate.reason,
            explanation=explanation,
            flags=flags,
            components=best.components,
            edge_components=edge_components,
            config_hash=config_hash,
            scorer_version=SCORER_VERSION,
        ))

    return results


def discovery_edge_components(company: Any, cfg: Any, *, today: date | None = None):
    from radar.score.discovery_edge import discovery_edge_components as _comps

    return _comps(company, cfg, today=today)


# ------------------------------------------------------- stage ⑥ persistence


def score_company(db: Db, company_id: str, cfg: Any, *, today: date | None = None,
                  config_hash: str | None = None, fund_key: str | None = None) -> int:
    """Evaluate one stored company and upsert its score rows + components.

    Returns how many score rows were written (one per fund in scope).
    Idempotent: the `ux_score` unique index plus its `ON CONFLICT` target
    means a re-score replaces rather than duplicates.

    `fund_key` scopes the write to one fund. Other funds' existing rows are
    left alone — a scoped run refreshes one fund, it does not retire the rest.
    """
    from radar.score.derive import Company
    from radar.score.qualify import is_qualified

    row = db.one("SELECT * FROM company WHERE id = ?", (company_id,))
    if row is None or row["merged_into"] is not None:
        return 0

    company = company_from_row(db, row, cfg)

    # 06-scoring §3: a Track B company with no qualifying signal stays in the
    # candidate pool at `qualified = 0` and is re-checked on every run — a
    # company incorporated today may file an SH01 next month. Never rejected,
    # never shortlisted (qualification is also enforced inside `evaluate`).
    if company.discovery_route == "registry" and not is_qualified(company, cfg):
        db.execute(
            "UPDATE company SET qualified = 0, updated_at = ? WHERE id = ?",
            (now_iso(), company_id),
        )
        # Stale watchlist rows otherwise keep filling Today after the
        # admitting bar tightens (live J25 leak: old `dsw` scores outlived
        # the new `dsw ventures` rejects).
        db.execute("DELETE FROM score WHERE company_id = ?", (company_id,))
        return 0

    scores = evaluate(company, cfg, today=today, config_hash=config_hash,
                      fund_key=fund_key)

    stamp = now_iso()
    if scores:
        db.execute(
            "UPDATE company SET qualified = 1, updated_at = ? WHERE id = ?",
            (stamp, company_id),
        )
    for score in scores:
        # `RETURNING id`, not `last_insert_rowid()`. On the DO UPDATE branch —
        # which is every re-score after the first — no row is inserted, so
        # `last_insert_rowid()` still holds the rowid of the previous INSERT,
        # which by then is a `score_component` rowid. The component rows below
        # were being hung off a `score.id` that does not exist, and the second
        # `rescore --all` died on the foreign key. RETURNING gives the real id
        # on both branches.
        cursor = db.execute(
            """INSERT INTO score
                 (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                  discovery_edge, priority, tier, reject_reason, explanation,
                  flags, config_hash, scorer_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(company_id, fund_key, COALESCE(vehicle_key, ''), config_hash)
               DO UPDATE SET
                 fund_fit_pct = excluded.fund_fit_pct,
                 coverage = excluded.coverage,
                 discovery_edge = excluded.discovery_edge,
                 priority = excluded.priority,
                 tier = excluded.tier,
                 reject_reason = excluded.reject_reason,
                 explanation = excluded.explanation,
                 flags = excluded.flags,
                 scorer_version = excluded.scorer_version,
                 scored_at = excluded.scored_at
               RETURNING id""",
            (company_id, score.fund_key, score.vehicle_key, score.fund_fit_pct,
             score.coverage, score.discovery_edge, score.priority, score.tier,
             score.reject_reason, score.explanation,
             json.dumps(score.flags) if score.flags else None,
             score.config_hash, score.scorer_version, stamp),
        )
        score_id = int(cursor.fetchone()[0])
        # one `executemany` per score row instead of one `execute` per
        # component: five components + five edge components per score row,
        # up to nine rows per company, was thirty-ish round-trips per company
        # and the third-biggest slice of the `rescore --all` profile.
        db.executemany(
            """INSERT OR REPLACE INTO score_component
                 (score_id, key, label, sub_score, weight, contribution, evidence)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (score_id, component.key, component.label, component.sub_score,
                 component.weight, component.contribution, component.evidence)
                for component in [*score.components, *score.edge_components]
            ],
        )
    if scores:
        current_hash = scores[0].config_hash
        if fund_key is None:
            db.execute(
                "DELETE FROM score WHERE company_id = ? AND config_hash != ?",
                (company_id, current_hash),
            )
        else:
            db.execute(
                "DELETE FROM score WHERE company_id = ? AND fund_key = ? "
                "AND config_hash != ?",
                (company_id, fund_key, current_hash),
            )
    return len(scores)


def company_from_row(db: Db, row: Mapping[str, Any], cfg: Any = None):
    """Rebuild a scoring `Company` from a stored row, with founders + signals.

    This is the read path of stage ⑥: the database is the truth, and the
    scoring engine is a pure function of what is stored.
    """
    from radar.score.derive import Company, Founder, Signal

    founders = []
    for f in db.query("SELECT * FROM founder WHERE company_id = ?", (row["id"],)):
        founders.append(dict(f))

    signals = []
    for s in db.query("SELECT * FROM signal WHERE company_id = ?", (row["id"],)):
        signals.append(dict(s))

    def _parse_list(raw: str | None, default=None):
        if not raw:
            return default if default is not None else []
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def _as_date(value):
        if value in (None, ""):
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    founder_rows = [
        Founder(
            name=f["name"],
            role=f["role"],
            profile_url=f["profile_url"],
            is_psc=bool(f["is_psc"]),
            appointed_on=_as_date(f["appointed_on"]),
            prior_appointments=f["prior_appointments"],
        )
        for f in founders
    ]
    signal_rows = [
        Signal(
            kind=s["kind"],
            headline=s["headline"],
            detail=s["detail"],
            occurred_on=_as_date(s["occurred_on"]),
            amount_gbp=s.get("amount_gbp"),
            source_key=s.get("source_key") or "",
            source_url=s.get("source_url") or "",
        )
        for s in signals
    ]

    return Company(
        id=row["id"],
        canonical_name=row["canonical_name"],
        norm_key=row["norm_key"],
        companies_house_no=row["companies_house_no"],
        domain=row["domain"],
        website_url=row["website_url"],
        incorporated_on=_as_date(row["incorporated_on"]),
        age_source=row["age_source"],
        hq_postcode=row["hq_postcode"],
        hq_region=row["hq_region"],
        hq_city=row["hq_city"],
        country_iso2=row["country_iso2"],
        sector=row["sector"],
        stage=row["stage"],
        founder_signal=row["founder_signal"],
        traction_signal=row["traction_signal"],
        total_funding_gbp=row["total_funding_gbp"],
        sic_codes=_parse_list(row["sic_codes"]),
        has_share_issue=bool(row["has_share_issue"]),
        on_vc_portfolio=bool(row["on_vc_portfolio"]),
        discovery_route=row["discovery_route"],
        is_university_spinout=bool(row["is_university_spinout"])
        if row["is_university_spinout"] is not None else None,
        spinout_university=row["spinout_university"],
        last_round_gbp=row["last_round_gbp"],
        prior_total_gbp=row["prior_total_gbp"],
        valuation_gbp=row["valuation_gbp"],
        uk_exec_pct=row["uk_exec_pct"],
        seis_eis_qualifying=bool(row["seis_eis_qualifying"])
        if row["seis_eis_qualifying"] is not None else None,
        qualifiers=_parse_list(row["qualifiers"]),
        extraction_method=row["extraction_method"],
        founders=founder_rows,
        signals=signal_rows,
    )


def rescore_all(db: Db, cfg: Any, *, today: date | None = None) -> dict[str, Any]:
    """Recompute every score in the database. No network, no AI.

    This is what makes weight changes interactive: `founder-radar rescore
    --all` recomputes the whole score table in well under a second
    (05-pipeline ⑥, NFR-1).

    It is a **bulk** pass, not a loop over `score_company`: three reads (all
    companies, all founders, all signals), the same scoring arithmetic as the
    daily path but on plain dicts with no pydantic model construction, and
    three writes (qualified flags, score rows, component rows) inside one
    transaction. The numbers come from the same `attribute_raw` / `_edge_parts`
    / `derive_updates` cores the pydantic path uses, so the two cannot drift
    (09-test-plan §8, `test_rescore_bulk_equals_daily`).
    """
    from radar.score.criteria import attributes_for

    today = today or date.today()
    attributes = tuple(attributes_for(cfg))
    config_hash = cfg.hash()
    stamp = now_iso()

    # --- three reads: everything stage ⑥ needs, none of it per-company
    companies = db.query(
        "SELECT * FROM company WHERE merged_into IS NULL ORDER BY id")
    founders_by: dict[str, list[dict]] = {}
    for f in db.query("SELECT * FROM founder ORDER BY company_id"):
        founders_by.setdefault(f["company_id"], []).append(dict(f))
    signals_by: dict[str, list[dict]] = {}
    for s in db.query("SELECT * FROM signal ORDER BY company_id"):
        signals_by.setdefault(s["company_id"], []).append(dict(s))

    score_rows: list[tuple] = []
    component_rows: list[tuple] = []      # (company_id, fund_key, vehicle, key, label, sub, weight, contribution, evidence)
    qualified_updates: list[tuple] = []   # (qualified, updated_at, company_id)
    shortlisted = 0

    for row in companies:
        company = _dict_company(row, founders_by.get(row["id"], []),
                                signals_by.get(row["id"], []))
        rows_out = _bulk_score_rows(company, cfg, attributes, today=today,
                                    config_hash=config_hash, scored_at=stamp)
        if rows_out is None:
            # unqualified registry company — stays in the pool, never scored
            qualified_updates.append((0, stamp, row["id"]))
            continue
        qualified_updates.append((1, stamp, row["id"]))
        for score_row, components in rows_out:
            score_rows.append(score_row)
            shortlisted += 1 if score_row[7] == "shortlist" else 0
            for comp in components:
                component_rows.append((row["id"],) + comp)

    with db.tx():
        db.executemany(
            "UPDATE company SET qualified = ?, updated_at = ? WHERE id = ?",
            qualified_updates,
        )
        if score_rows:
            db.executemany(
                """INSERT INTO score
                     (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                      discovery_edge, priority, tier, reject_reason, explanation,
                      flags, config_hash, scorer_version, scored_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(company_id, fund_key, COALESCE(vehicle_key, ''), config_hash)
                   DO UPDATE SET
                     fund_fit_pct = excluded.fund_fit_pct,
                     coverage = excluded.coverage,
                     discovery_edge = excluded.discovery_edge,
                     priority = excluded.priority,
                     tier = excluded.tier,
                     reject_reason = excluded.reject_reason,
                     explanation = excluded.explanation,
                     flags = excluded.flags,
                     scorer_version = excluded.scorer_version,
                     scored_at = excluded.scored_at""",
                score_rows,
            )
            # one read back the score ids we just upserted, then components in
            # one executemany — never per-row RETURNING in a loop
            ids: dict[tuple[str, str, str | None], int] = {}
            for s in db.query(
                "SELECT id, company_id, fund_key, vehicle_key FROM score "
                "WHERE config_hash = ?", (config_hash,)
            ):
                ids[(s["company_id"], s["fund_key"], s["vehicle_key"])] = s["id"]
            db.executemany(
                """INSERT OR REPLACE INTO score_component
                     (score_id, key, label, sub_score, weight, contribution, evidence)
                   VALUES (?,?,?,?,?,?,?)""",
                [(ids[(cid, fund, vehicle)], key, label, sub, weight, contribution, evidence)
                 for cid, fund, vehicle, key, label, sub, weight, contribution, evidence
                 in component_rows],
            )
        unqualified_ids = [cid for qualified, _, cid in qualified_updates if not qualified]
        db.execute("DELETE FROM score WHERE config_hash != ?", (config_hash,))
        if unqualified_ids:
            db.executemany(
                "DELETE FROM score WHERE company_id = ?",
                [(cid,) for cid in unqualified_ids],
            )

    return {"scored": len(companies), "shortlisted": shortlisted,
            "config_hash": config_hash}


def _dict_company(row: Mapping[str, Any], founders: list[dict],
                  signals: list[dict]) -> dict[str, Any]:
    """A stored row as the plain dict the bulk scorer reads. Field names match
    the `company` table; `_get` reads dicts and models alike.

    `sic_codes` and `qualifiers` are stored as JSON strings; `company_from_row`
    parses them back into lists for the pydantic path, and this must do the
    same or `derive_sector` iterates the characters of the JSON string and
    derives `other` for every company (the drift
    `test_rescore_bulk_equals_daily` exists to catch).
    """
    out = dict(row)
    for key in ("sic_codes", "qualifiers"):
        raw = out.get(key)
        if not raw:
            out[key] = []
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        out[key] = parsed if isinstance(parsed, list) else []
    out["founders"] = founders
    out["signals"] = signals
    return out


def _bulk_score_rows(company: dict, cfg: Any, attributes: tuple[str, ...], *,
                     today: date, config_hash: str, scored_at: str
                     ) -> list[tuple[tuple, list[tuple]]] | None:
    """One company through stage ⑥ on plain dicts — the bulk twin of
    `evaluate`. Returns `[(score_row_tuple, component_tuples), ...]` or None
    for an unqualified registry company (never scored, 06-scoring §3).
    """
    from types import SimpleNamespace

    from radar.score.derive import derive_updates
    from radar.score.discovery_edge import _edge_parts
    from radar.score.explain import explain
    from radar.score.gates import apply_freshness_gates, evaluate_vehicle_gates
    from radar.score.qualify import is_qualified
    from radar.score.tiering import priority_of, tier_of

    updates, _ = derive_updates(company, cfg, today=today)
    company = {**company, **updates}
    # `geography` is a `Company` *property* aliasing `hq_region`; a plain dict
    # has no property, so mirror the alias or every scored attribute reads
    # geography as unknown and the bulk path drifts from the daily path.
    company["geography"] = company.get("hq_region")

    gate = apply_freshness_gates(company, cfg, today=today)
    if not gate.passed:
        return [(_score_row(
            company["id"], f.key, None, 0.0, 0.0, 0.0, 0.0, "reject",
            gate.reason, f"Rejected: {gate.reason} ({gate.detail}).",
            list(gate.flags), config_hash, scored_at), [])
            for f in cfg.funds]

    if company.get("discovery_route") == "registry" and not is_qualified(company, cfg):
        return None

    edge, edge_parts = _edge_parts(company, cfg, today=today)
    edge_components = [
        (key, label, sub, weight, _contribution(sub, weight), evidence)
        for key, label, sub, weight, evidence in edge_parts
    ]

    rows_out: list[tuple[tuple, list[tuple]]] = []
    for fund in cfg.funds:
        best_vehicle, best = None, None
        vehicle_flags: list[str] = []
        # fit depends on the fund, not the vehicle (fund_fit.py docstring) —
        # compute it once and let every eligible vehicle ride the same numbers
        fit = _fit_numbers(company, fund.key, cfg, attributes)
        for vehicle in fund.active_vehicles:
            verdict = evaluate_vehicle_gates(company, vehicle, cfg)
            if not verdict.passed:
                continue
            vehicle_flags.extend(verdict.flags)
            if best is None or fit["pct"] > best["pct"]:
                best_vehicle, best = vehicle, fit

        if best is None:
            # mirror `evaluate`: a reject row carries no discovery edge
            rows_out.append((_score_row(
                company["id"], fund.key, None, 0.0, 0.0, 0.0, 0.0, "reject",
                "no_eligible_vehicle",
                "Rejected: no eligible vehicle — every hard rule failed.",
                list(gate.flags), config_hash, scored_at), []))
            continue

        priority = priority_of(best["pct"], edge, cfg)
        flags = sorted(set(gate.flags) | set(vehicle_flags))
        fit_ns = SimpleNamespace(pct=best["pct"], coverage=best["coverage"],
                                 components=[SimpleNamespace(
                                     label=label, evidence=evidence,
                                     sub_score=sub, weight=weight)
                                     for _, label, sub, weight, _, evidence
                                     in best["components"]])
        tier, why = tier_of(fit_ns, edge, flags, cfg)
        # `explain` owns the tier reason — see the note on the daily path.
        # Both paths carried this duplicate append, which is why the two agreed
        # with each other and were wrong together.
        explanation = explain(fit_ns, edge, company.get("signals", []),
                              best_vehicle, flags, tier_reason=why,
                              reject_reason=gate.reason)

        vehicle_key = best_vehicle.vehicle_key
        rows_out.append((_score_row(
            company["id"], fund.key, vehicle_key, best["pct"],
            best["coverage"], edge, priority, tier, gate.reason, explanation,
            flags, config_hash, scored_at),
            [(fund.key, vehicle_key, key, label, sub, weight, contribution, evidence)
             for key, label, sub, weight, contribution, evidence
             in [*best["components"], *edge_components]]))
    return rows_out


def _fit_numbers(company: dict, fund_key: str, cfg: Any,
                 attributes: tuple[str, ...]) -> dict:
    """Adapt the shared Fund Fit calculation to the bulk row shape."""
    from radar.score.fund_fit import calculate_fund_fit

    calculation = calculate_fund_fit(company, fund_key, cfg, attributes)
    return {
        "pct": calculation.pct,
        "coverage": calculation.coverage,
        "raw_sum": calculation.raw_sum,
        "components": [
            (component.key, component.label, component.sub_score,
             component.weight, component.contribution, component.evidence)
            for component in calculation.components
        ],
    }


def _contribution(sub_score: float | None, weight: float) -> float | None:
    """`weight × sub_score`, or None — mirrors `ComponentScore.contribution`."""
    return weight * sub_score if sub_score is not None else None


def _score_row(company_id: str, fund_key: str, vehicle_key: str | None,
               pct: float, coverage: float, edge: float, priority: float,
               tier: str, reject_reason: str | None, explanation: str,
               flags: list[str], config_hash: str, scored_at: str) -> tuple:
    return (company_id, fund_key, vehicle_key, pct, coverage, edge, priority,
            tier, reject_reason, explanation,
            json.dumps(flags) if flags else None,
            config_hash, SCORER_VERSION, scored_at)


# ------------------------------------------------------------ stage ② fetch


def fetch_stage(db: Db, cfg: Any, http: Any, *, source_key: str | None = None,
                run_id: int | None = None, now: date | None = None,
                since: date | None = None) -> tuple[list[Any], Any]:
    """Run every enabled adapter in isolation. One failure never stops a run.

    Returns `(items, FetchResult)` so the run can record per-source health and
    report `partial` when any source failed (02-architecture §7, chaos tests).

    `since` is the `--since` floor: adapters drop anything published before it,
    and Companies House narrows its incorporation window to match.
    """
    from radar.sources import fetch_all
    from radar.sources.base import FetchContext

    ctx = FetchContext(http=http, config=cfg, db=db, now=now or date.today(),
                       since=since)
    result = fetch_all(ctx, keys=[source_key] if source_key else None,
                       db=db, run_id=run_id, observed_on=now or date.today())
    for source in result.sources:
        if source.status not in ("ok", "skipped", "disabled"):
            log.warning("source %s %s: %s", source.key, source.status, source.error)
    return result.items, result


# ------------------------------------------------------------------ stage ④


def resolve_item(db: Db, item: Any, cfg: Any, *, seen_at: str | None = None) -> str | None:
    """Fold one raw mention into the company graph (stage ④).

    Handles both worlds:
    * structured items (Companies House) — fields already known, no AI;
    * prose items — `extraction` carries the structured record.

    Returns the canonical `company_id`, or None when the item is a reject.
    """
    from radar.resolve.merge import add_identifier, attach, create_company, upsert_record
    from radar.resolve.match import Record

    structured = getattr(item, "structured", None) or {}
    extraction = getattr(item, "extraction", None)

    name = (structured.get("company_name")
            or (getattr(extraction, "company_name", None) if extraction else None)
            or "")
    if not name:
        # ponytail: an article headline is not a company. This used to fall
        # back to `item.title`, which created a company called "Manchester
        # fintech raises £2m Seed to expand across Europe" for every prose item
        # the reader could not name — and that row then scored, shortlisted and
        # landed in the sheet, so the client was reading articles and opening
        # each one to find the company. Only two things may name a company: a
        # structured source that states it, or the extractor reading it out of
        # the prose. Everything else is a source, not a subject. The article is
        # still recorded — as `company_source.source_url` on whatever company
        # it does resolve to.
        log.debug("no company named in %s (%s) — article kept as a source only",
                  getattr(item, "source_url", "?"), getattr(item, "source_key", "?"))
        return None

    record = Record(
        name=name,
        ch_number=structured.get("company_number"),
        domain=structured.get("domain") or getattr(item, "domain", None),
        country_iso2=(structured.get("country_iso2")
                      or structured.get("hq_country_iso2")),
        first_seen=getattr(item, "published_at", None) and str(item.published_at) or None,
    )

    fields: dict[str, Any] = {}
    if extraction is not None:
        fields.update(_fields_from_extraction(extraction, cfg))
        if extraction.is_about_single_company is False:
            return None

    fields.update(_fields_from_structured(structured, existing=fields))
    fields["discovery_route"] = _route_of(item, cfg)

    resolution = upsert_record(
        db, record,
        source_key=item.source_key,
        source_url=item.source_url,
        external_id=item.external_id,
        seen_at=seen_at or now_iso(),
        fields=fields,
    )

    _record_signal(db, resolution.company_id, item, name, fields)
    return resolution.company_id


# The closed `signal.kind` vocabulary of 03-data-model §3. Every adapter
# already emits one of these in `kind_hint`; this module is the only place it
# becomes a row.
SIGNAL_KINDS = frozenset({
    "incorporation", "share_issue", "grant_award", "spinout",
    "accelerator_cohort", "competition_win", "funding_round",
    "product_launch", "news_mention",
})

# `kind_hint` → `discovery_route`, the column vocabulary in schema.sql. Only
# the kinds that name a route appear; everything else is news.
_ROUTE_BY_KIND = {
    "spinout": "spinout",
    "grant_award": "grant",
    "accelerator_cohort": "accelerator",
}


def _record_signal(db: Any, company_id: str, item: Any, name: str,
                   fields: Mapping[str, Any]) -> None:
    """Turn an adapter's `kind_hint` into a dated `signal` row.

    Deliberately not gated on `resolution.action == "created"`. A registry
    company earns its `grant` qualifier (06-scoring §3) on the day the award
    lands, which is almost never the day it was first seen — and a company
    first met in the news gets its `incorporation` signal only when the
    register later matches it. Gating on creation loses both. The table's
    UNIQUE (company_id, kind, source_url) makes the repeat a no-op, which is
    what `test_rerun_is_idempotent` holds us to.
    """
    kind = getattr(item, "kind_hint", None)
    if kind not in SIGNAL_KINDS:
        return

    if kind == "incorporation":
        occurred_on = fields.get("incorporated_on")
        headline = (f"{name} incorporated on {occurred_on}" if occurred_on
                    else f"{name} incorporated")
    else:
        published = getattr(item, "published_at", None)
        occurred_on = str(published) if published else None
        headline = getattr(item, "title", None) or f"{name}: {kind.replace('_', ' ')}"

    structured = getattr(item, "structured", None) or {}
    db.execute(
        """INSERT OR IGNORE INTO signal
             (company_id, kind, occurred_on, headline, amount_gbp,
              source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        (company_id, kind, occurred_on, headline,
         structured.get("grant_amount_gbp"),
         item.source_key, item.source_url, now_iso()),
    )

    if kind in PRESS_KINDS:
        _refresh_press_count(db, company_id)


# What "press coverage in tracked sources" counts (06-scoring §6). Articles
# only. A TTO spinout page and an accelerator cohort page are directory
# listings, not coverage, and they already earn their visibility back through
# `discovery_route` — counting them here would penalise precisely the sources
# this product exists to favour.
PRESS_KINDS = frozenset({
    "news_mention", "funding_round", "product_launch", "competition_win",
})


def _refresh_press_count(db: Any, company_id: str) -> None:
    """Recomputed from the signal table, never incremented.

    The insert above is `OR IGNORE`, so a re-run is a no-op and an increment
    would drift on the first duplicate. Counting the rows is idempotent, which
    is what `test_rerun_is_idempotent` asserts, and it stays right after a
    merge moves signals onto the surviving company.
    """
    placeholders = ",".join("?" * len(PRESS_KINDS))
    kinds = tuple(sorted(PRESS_KINDS))
    db.execute(
        f"""UPDATE company SET news_mention_count =
              (SELECT COUNT(*) FROM signal
                WHERE company_id = ? AND kind IN ({placeholders}))
            WHERE id = ?""",
        (company_id, *kinds, company_id),
    )


def _route_of(item: Any, cfg: Any) -> str | None:
    structured = getattr(item, "structured", None) or {}
    if structured.get("company_number"):
        return "registry"
    return _ROUTE_BY_KIND.get(getattr(item, "kind_hint", None), "news")


def _fields_from_structured(
    structured: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map facts from a source that already parsed its own page.

    Some directory adapters expose a founding year rather than a full date.
    The database deliberately has no ``founded_year`` column: the canonical
    age field is ``incorporated_on``. Keep the same mid-year convention used by
    prose extraction, otherwise an old structured record becomes age-unknown
    and can leak into the review queue.

    A real ``date_of_creation`` always wins. ``existing`` protects a more
    specific date already supplied by extraction when an item happens to carry
    both structured and prose evidence.
    """
    fields: dict[str, Any] = {}
    if structured.get("company_number"):
        fields["companies_house_no"] = structured["company_number"]
    if structured.get("date_of_creation"):
        fields["incorporated_on"] = structured["date_of_creation"]
    elif structured.get("founded_year") and not (existing or {}).get("incorporated_on"):
        fields["incorporated_on"] = f"{int(structured['founded_year']):04d}-07-01"
        fields["age_source"] = "source_stated"
        fields["date_confidence"] = "stated"
    if structured.get("sic_codes"):
        fields["sic_codes"] = json.dumps(structured["sic_codes"])
    if structured.get("company_website"):
        fields["website_url"] = structured["company_website"]
    if structured.get("one_line_description"):
        fields["one_liner"] = structured["one_line_description"]
    if structured.get("sector"):
        fields["sector"] = structured["sector"]
    if structured.get("stage"):
        fields["stage"] = structured["stage"]
    if structured.get("hq_region"):
        fields["hq_region"] = structured["hq_region"]
    if structured.get("hq_city"):
        fields["hq_city"] = structured["hq_city"]
    if structured.get("postal_code"):
        fields["hq_postcode"] = structured["postal_code"]
    if structured.get("locality"):
        fields["hq_city"] = structured["locality"]
    if structured.get("country_iso2") or structured.get("hq_country_iso2"):
        fields["country_iso2"] = (structured.get("country_iso2")
                                   or structured.get("hq_country_iso2"))
    if structured.get("is_university_spinout") is not None:
        fields["is_university_spinout"] = int(bool(structured["is_university_spinout"]))
    if structured.get("university_name"):
        fields["spinout_university"] = structured["university_name"]
    return fields


def _fields_from_extraction(extraction: Any, cfg: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if extraction.company_name:
        fields["canonical_name"] = extraction.company_name
    if extraction.company_website:
        fields["website_url"] = extraction.company_website
    if extraction.one_line_description:
        # `company.one_liner` has existed in schema.sql since the first commit
        # and nothing ever wrote it, so every surface that wanted to say what a
        # company *does* had nothing but the article headline to fall back on.
        # The model is already asked for this field and we already pay for it.
        fields["one_liner"] = extraction.one_line_description
    if extraction.sector:
        fields["sector"] = extraction.sector
    if extraction.stage:
        fields["stage"] = extraction.stage
    if extraction.hq_city:
        fields["hq_city"] = extraction.hq_city
    if extraction.hq_country_iso2:
        fields["country_iso2"] = extraction.hq_country_iso2
    if extraction.founded_year:
        # There is no `founded_year` column — 03-data-model makes
        # `incorporated_on` THE age field and `age_source` the record of how
        # sure we are of it. Writing the raw year killed the whole run with
        # "table company has no column named founded_year" the moment an
        # article said when a company was founded, which is most of them.
        # Mid-year is the convention `radar.score.derive.age_months` already
        # uses for a stated year, and `_fields_from_structured` runs after this
        # so a real Companies House `date_of_creation` still wins.
        fields["incorporated_on"] = f"{int(extraction.founded_year):04d}-07-01"
        fields["age_source"] = "source_stated"
        fields["date_confidence"] = "stated"
    if extraction.amount_raised_gbp is not None:
        fields["total_funding_gbp"] = extraction.amount_raised_gbp
    if extraction.is_university_spinout is not None:
        fields["is_university_spinout"] = int(bool(extraction.is_university_spinout))
    if extraction.university_name:
        fields["spinout_university"] = extraction.university_name
    if extraction.grant_amount_gbp:
        fields["total_funding_gbp"] = extraction.grant_amount_gbp
    if extraction.extraction_method:
        # The Extraction record already carries "llm" vs "heuristic" (set in
        # extract._finish_llm/_finish_heuristic). Without this the company
        # column stayed NULL for every prose-sourced company, which broke the
        # Sources/evidence display and the sheet's confidence column.
        fields["extraction_method"] = extraction.extraction_method
    return fields


# ------------------------------------------------------------- stage ③/⑤ glue


def extract_stage(items: Iterable[Any], cfg: Any, *, use_llm: bool,
                  db: Db | None = None, llm: Any = None) -> list[Any]:
    """Run the reader over every prose item, boxed (05-pipeline ③).

    Returns the `RawItem`s with an `extraction` attribute set. Structured
    items (Companies House) skip the reader entirely.
    """
    from radar.extract import ExtractContext, extract, extract_all

    ctx = ExtractContext.from_settings(cfg.settings, use_llm=use_llm, db=db)
    ctx.llm = llm
    out: list[Any] = []
    for item in items:
        structured = getattr(item, "structured", None) or {}
        if structured.get("company_number"):
            out.append(item)
            continue
        record = extract(item, ctx)
        if record.is_usable:
            # `RawItem` is a frozen dataclass, so a plain attribute assignment
            # raised `FrozenInstanceError` and took the whole extract stage —
            # and with it every Track A company — down with it. Nothing caught
            # it because the offline chaos harness fetched zero prose items.
            # `object.__setattr__` is how a frozen dataclass is annotated
            # after the fact; the declared fields stay immutable.
            object.__setattr__(item, "extraction", record)
        out.append(item)
    return out


def enrich_stage(db: Db, cfg: Any, http: Any, *, api_key: str | None = None,
                 days: int | None = None) -> dict[str, Any]:
    """Stage ⑤ — the Companies House enrichment queue (passes 1→3).

    The budget counts requests, not companies, and the run stops cleanly when
    it runs out; the remainder stay queued for the next run (05-pipeline ⑤).
    """
    from radar.enrich import enrich_companies
    from radar.enrich.ch_officers import CH_API_BASE
    from radar.sources.companies_house import api_key_from_env

    key = api_key or api_key_from_env()
    budget_limit = int(getattr(cfg.settings, "max_enrichment_requests_per_run", 500) or 0)
    if not key:
        return {"enriched": 0, "queued": 0, "budget_limit": budget_limit, "skipped": "no api key"}
    from radar.enrich import RequestBudget

    result = enrich_companies(
        db, http, api_key=key, budget=RequestBudget(budget_limit),
        base_url=CH_API_BASE,
    )
    return {
        "enriched": result.enriched,
        "queued": result.queued,
        "budget_limit": result.budget_limit,
        "budget_spent": result.budget_spent,
        "share_issues": result.share_issues,
    }


# -------------------------------------------------------------------- the run


def run_pipeline(
    db: Db,
    *,
    fund_key: str | None = None,
    source_key: str | None = None,
    since: date | None = None,
    dry_run: bool = False,
    use_llm: bool = True,
    config: Config | None = None,
    http: Any = None,
    gateway: Any = None,
    llm: Any = None,
    now: date | None = None,
    mode: str = "daily",
) -> RunResult:
    """The daily run: fetch → extract → resolve → enrich → score → render.

    Every stage is individually wrapped so no single failure ends the run.
    `dry_run` skips the sheet write and the run-log row. `gateway=None` skips
    the sheet entirely (tests, `--dry-run`); `http=None` builds a real client.
    """
    from radar.config.loader import load_runtime_config

    config_warnings: list[str] = []
    if config is None:
        cfg, opened, config_warnings = load_runtime_config(db, gateway=gateway)
        if gateway is None:
            gateway = opened
    else:
        cfg = config
    # Validate `--fund` before the crawl, not after: a typo should cost nothing.
    funds_in_scope(cfg, fund_key)
    run_id = None if dry_run else _begin_run(db, mode=mode, scope=f"fund={fund_key}" if fund_key else None)
    result = RunResult(mode=mode, scope=f"fund={fund_key}" if fund_key else None)
    result.warnings.extend(config_warnings)

    try:
        # ② fetch
        client = http or _make_http()
        items, fetch = fetch_stage(db, cfg, client, source_key=source_key,
                                   run_id=run_id, now=now, since=since)
        result.items_fetched = len(items)
        result.sources = [vars(s) for s in fetch.sources]
        if fetch.status == "partial":
            result.status = "partial"
        # A degraded source is not an outage — the site answered but refused
        # the crawler (401/403/429/451). It stays a `degraded` row on the
        # Sources tab and a warning on the run row, so two consecutive blocks
        # can trip the heartbeat without every one of those days looking like
        # a failed run (sources/base.SourceBlocked).
        for source in fetch.sources:
            if source.status == "degraded":
                result.warnings.append(
                    f"{source.key} degraded: {source.error or 'site is refusing us'}")

        # ③ extract + ④ resolve
        for item in extract_stage(items, cfg, use_llm=use_llm, db=db, llm=llm):
            extraction = getattr(item, "extraction", None)
            if extraction is not None:
                result.items_extracted += 1
            cid = resolve_item(db, item, cfg)
            if cid is None:
                continue
            if getattr(extraction, "needs_review", False):
                db.execute("UPDATE company SET needs_review = 1 WHERE id = ?", (cid,))

        # ⑤ enrich (only in a real run — the backfill owns its own)
        if not dry_run:
            enrich_stage(db, cfg, client)

        # ⑥ gate + score every company. The qualification gate inside
        # `evaluate` keeps unqualified registry companies out of scoring, and
        # re-checks them on every run — they are never rejected (06-scoring §3).
        candidates = db.query(
            "SELECT id FROM company WHERE merged_into IS NULL"
        )
        # A scoped run refreshes one fund, so its counters must read that fund
        # only — another fund's row is left over from an earlier run, not a
        # result of this one.
        scope_sql = " AND fund_key = ?" if fund_key else ""
        scope_params: tuple = (fund_key,) if fund_key else ()

        for row in candidates:
            count = score_company(db, row["id"], cfg, today=now or date.today(),
                                  fund_key=fund_key)
            if count == 0:
                continue
            tier = db.scalar(
                "SELECT tier FROM score WHERE company_id = ? AND tier = 'shortlist'"
                + scope_sql + " LIMIT 1",
                (row["id"], *scope_params),
            )
            if tier == "shortlist":
                result.shortlisted += 1

        gated = db.scalar(
            """SELECT COUNT(DISTINCT company_id) FROM score
               WHERE tier = 'reject' AND reject_reason IS NOT NULL"""
            + scope_sql, scope_params
        ) or 0
        result.gated_out = int(gated)
        result.companies_new = int(db.scalar(
            "SELECT COUNT(*) FROM company WHERE merged_into IS NULL AND "
            "strftime('%Y-%m-%d', created_at) = strftime('%Y-%m-%d', 'now')") or 0)

        if not dry_run and gateway is not None:
            from radar.render.sheet import sync_sheet

            # 02-architecture §7: a Sheets outage is "a late sheet, no lost
            # data" — the rows stay in SQLite with `synced = 0` and tomorrow's
            # run upserts them. Letting it escape to the handler below marked
            # the whole run `failed`, which is what the heartbeat reads as "no
            # successful run" — a Google outage would have produced a false
            # staleness alert on top of the late sheet. It is one stage, so it
            # fails like one stage.
            try:
                sync_sheet(db, gateway=gateway, today=now or date.today())
            except Exception as exc:             # noqa: BLE001 — one stage, not the run
                result.status = "partial"
                result.warnings.append(f"sheet not written: {type(exc).__name__}: {exc}")
                log.warning("sheet sync failed (%s) — rows stay unsynced", type(exc).__name__)
    except Exception as exc:                     # noqa: BLE001 — the run never dies
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("run failed")
    else:
        # a failed source means the run completed but partially
        result.status = "partial" if result.status == "partial" else "ok"

    if not dry_run and run_id is not None:
        _finish_run(db, run_id, result)
    return result


def run_backfill(db: Db, *, days: int = 90, config: Config | None = None,
                 http: Any = None, now: date | None = None,
                 api_key: str | None = None) -> dict[str, Any]:
    """First-run Companies House sweep + enrichment (Phase 3)."""
    from radar.config.loader import load_runtime_config
    from radar.enrich import backfill

    if config is None:
        cfg, _, _ = load_runtime_config(db)
    else:
        cfg = config
    client = http or _make_http()
    result = backfill(db, client, cfg, days=days, api_key=api_key, now=now)
    return {
        "days": result.days,
        "fetched": result.fetched,
        "companies_new": result.companies_new,
        "companies_seen": result.companies_seen,
        "founders": result.founders,
        "share_issues": result.share_issues,
        "enriched": result.enriched,
        "queued": result.queued,
        "ch_requests": result.ch_requests,
        "postcode_requests": result.postcode_requests,
        "budget_spent": result.budget_spent,
        "truncated_pages": result.truncated_pages,
    }


def run_rescore(db: Db, *, all_companies: bool = False, config: Config | None = None,
                today: date | None = None) -> dict[str, Any]:
    """Recompute scores. `--all` rescans the whole table; default is today's."""
    from radar.config.loader import load_runtime_config

    if config is None:
        cfg, _, _ = load_runtime_config(db)
    else:
        cfg = config
    if all_companies:
        return rescore_all(db, cfg, today=today)
    rows = db.query(
        """SELECT DISTINCT company_id FROM score
           WHERE date(scored_at) = date('now')"""
    )
    scored = shortlisted = 0
    for row in rows:
        count = score_company(db, row["company_id"], cfg, today=today)
        scored += count
        tier = db.scalar(
            "SELECT tier FROM score WHERE company_id = ? AND tier = 'shortlist' LIMIT 1",
            (row["company_id"],),
        )
        if tier == "shortlist":
            shortlisted += 1
    return {"scored": len(rows), "shortlisted": shortlisted, "config_hash": cfg.hash()}


def _make_http() -> Any:
    from radar.fetch.http import HttpClient

    return HttpClient()


__all__ = [
    "RunResult",
    "evaluate",
    "score_company",
    "company_from_row",
    "rescore_all",
    "run_pipeline",
    "run_backfill",
    "run_rescore",
]
