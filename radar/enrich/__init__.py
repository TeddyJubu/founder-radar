"""Stage ⑤ — ENRICH, plus the Companies House backfill that feeds it.

Everything in this package obeys three rules that the rest of the system
depends on:

* **The budget counts requests, not companies.** Full enrichment is 4–8 calls
  per company, so "300 companies × 2" is off by a factor of three. Companies
  House *bans* an application for repeated breaches rather than throttling it,
  which makes an over-eager first run the most likely way to brick the key
  (04-sources §3.4a).
* **Privacy at ingest.** Officers and PSC records are scrubbed in
  `ch_officers` before they reach any INSERT.
* **Idempotent.** Running the backfill twice creates no duplicate company,
  founder, signal, identifier or observation rows.

The orchestrator wires `backfill(db, http, config, days=90)` into
`radar.pipeline.run_backfill`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Sequence

from radar.enrich import ch_filings, ch_officers, postcode
from radar.enrich.ch_filings import (
    SH01,
    ShareIssue,
    fetch_filing_history,
    find_share_issues,
    has_share_issue,
    qualifying_share_issues,
    record_share_issues,
)
from radar.enrich.ch_officers import (
    FORBIDDEN_FIELDS,
    Founder,
    PscHolder,
    apply_psc,
    fetch_appointments,
    fetch_officers,
    fetch_psc,
    founder_candidates,
    merge_founders,
    only_corporate_secretary,
    parse_appointment_count,
    parse_officers,
    parse_psc,
    store_founders,
)
from radar.enrich.postcode import (
    PostcodeInfo,
    geography_enabled,
    lookup_outcode,
    outcode_of,
    region_to_geography,
    resolve_postcode,
)
from radar.sources.base import FetchContext, SourceError
from radar.sources.companies_house import (
    CH_API_BASE,
    CH_HOST,
    CH_PROFILE_URL,
    SIC_DENYLIST,
    SIC_TIERS,
    CompaniesHouseAdapter,
    api_key_from_env,
    is_denylisted_only,
    is_placeholder_name,
    norm_key,
    normalise_ch_number,
)
from radar.store.db import new_id, now_iso

log = logging.getLogger(__name__)

SOURCE_KEY = "companies_house"
SOURCE_TYPE = "registry"

#: `_meta` marker so a re-run does not re-spend pass-1 requests on a company we
#: already checked. Pass 1 can complete while pass 2 never starts, and
#: `enriched_at` alone cannot express that.
FILINGS_CHECKED_PREFIX = "ch_filings_checked:"


# ------------------------------------------------------------------ budget


@dataclass
class RequestBudget:
    """`max_enrichment_requests_per_run`, decremented on every call.

    When it runs out enrichment stops **cleanly** and the remaining companies
    stay queued with `enriched_at IS NULL` for tomorrow (05-pipeline ⑤).
    """

    limit: int
    spent: int = 0

    @property
    def remaining(self) -> int:
        return max(self.limit - self.spent, 0)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def can_spend(self, n: int = 1) -> bool:
        return self.spent + n <= self.limit

    def spend(self, n: int = 1) -> bool:
        if not self.can_spend(n):
            return False
        self.spent += n
        return True


# ------------------------------------------------------------------ results


@dataclass
class ScreenResult:
    """The verdict of the free noise filter for one swept company."""

    keep: bool
    reason: str = "ok"
    geography: str | None = None
    info: PostcodeInfo | None = None
    placeholder: bool = False


@dataclass
class BackfillResult:
    """Everything the digest and the tests want to know about one backfill."""

    days: int = 0
    windows: int = 0
    pages: int = 0
    truncated_pages: int = 0
    fetched: int = 0
    dropped_denylist: int = 0
    dropped_region: int = 0
    dropped_formation_agent: int = 0
    dropped_corporate_only: int = 0
    companies_new: int = 0
    companies_seen: int = 0
    signals_new: int = 0
    founders: int = 0
    share_issues: int = 0
    enriched: int = 0
    queued: int = 0
    budget_limit: int = 0
    budget_spent: int = 0
    sweep_requests: int = 0
    enrich_requests: int = 0
    postcode_requests: int = 0

    @property
    def ch_requests(self) -> int:
        return self.sweep_requests + self.enrich_requests


# ------------------------------------------------------- the free noise filter


def screen_item(
    db: Any,
    http: Any,
    item: Any,
    *,
    regions_enabled: Sequence[str],
    postcodes_base: str = postcode.POSTCODES_IO_BASE,
) -> ScreenResult:
    """04-sources §3.4 steps 1–4. No Companies House requests are spent here.

    Step 1 (denylisted SIC only) already ran inside the adapter, so a
    denylist-only company never reaches this function at all — which is the
    point: it costs nothing.
    """
    structured = item.structured or {}

    # 1. belt and braces — the adapter drops these; assert it stayed true.
    if is_denylisted_only(structured.get("sic_codes") or []):
        return ScreenResult(False, "sic_denylist")

    # 2. postcode outcode → region. Cached forever, so ~free after run one.
    pc = structured.get("postal_code")
    info = resolve_postcode(db, http, pc, base_url=postcodes_base) if pc else None
    geography = info.geography if info else None
    if not geography_enabled(geography, regions_enabled):
        return ScreenResult(False, "region_not_enabled", geography, info)

    # 3. formation-agent registered office. ~50 postcodes, thousands of shells.
    if pc and _is_formation_agent(db, pc):
        return ScreenResult(False, "formation_agent_address", geography, info)

    # 4. placeholder name. KEEP the company number — these rename into real
    #    companies — but never let the name act as identity.
    placeholder = bool(structured.get("placeholder_name")) or is_placeholder_name(item.title)
    return ScreenResult(True, "ok", geography, info, placeholder=placeholder)


def _is_formation_agent(db: Any, raw_postcode: str) -> bool:
    compact = "".join(str(raw_postcode).upper().split())
    row = db.one(
        "SELECT 1 FROM formation_agent_address "
        "WHERE REPLACE(UPPER(postcode), ' ', '') = ?",
        (compact,),
    )
    return row is not None


# --------------------------------------------------------------- persistence


def _observe_once(
    db: Any,
    company_id: str,
    field_name: str,
    value: Any,
    *,
    source_url: str,
    confidence: float = 1.0,
) -> None:
    """Append an observation unless the identical fact is already recorded.

    Observations are append-only by design, but re-running a backfill must not
    grow the table without adding information.
    """
    payload = json.dumps(value)
    exists = db.one(
        """SELECT 1 FROM observation
           WHERE company_id = ? AND field = ? AND source_key = ? AND value_json = ?""",
        (company_id, field_name, SOURCE_KEY, payload),
    )
    if exists:
        return
    db.add_observation(
        company_id, field_name, value,
        source_key=SOURCE_KEY, source_type=SOURCE_TYPE,
        source_url=source_url, confidence=confidence, extractor_ver="ch-1",
    )


def upsert_company(db: Any, item: Any, screen: ScreenResult) -> tuple[str, bool]:
    """Insert or refresh one registry company. Idempotent on the CH number."""
    s = item.structured or {}
    number = normalise_ch_number(s.get("company_number"))
    name = s.get("company_name") or item.title
    stamp = now_iso()
    key = s.get("norm_key") or norm_key(name)
    sic_json = json.dumps(s.get("sic_codes") or [])
    region = screen.geography
    postal = s.get("postal_code")
    city = s.get("locality")
    created = s.get("date_of_creation")

    row = db.one(
        "SELECT id FROM company WHERE companies_house_no = ? AND merged_into IS NULL",
        (number,),
    )
    if row is None:
        company_id = new_id()
        db.execute(
            """INSERT INTO company
                 (id, canonical_name, norm_key, companies_house_no, incorporated_on,
                  age_source, date_confidence, hq_postcode, hq_region, hq_city,
                  country_iso2, sic_codes, discovery_route, extraction_method,
                  first_seen, last_seen, created_at, updated_at)
               VALUES (?,?,?,?,?,'companies_house','exact',?,?,?,'GB',?,'registry',
                       'structured',?,?,?,?)""",
            (company_id, name, key, number, created, postal, region, city,
             sic_json, stamp, stamp, stamp, stamp),
        )
        created_new = True
    else:
        company_id = row["id"]
        created_new = False
        db.execute(
            """UPDATE company SET
                 canonical_name = ?, norm_key = ?, incorporated_on = ?,
                 age_source = 'companies_house', date_confidence = 'exact',
                 hq_postcode = ?, hq_region = COALESCE(?, hq_region), hq_city = ?,
                 country_iso2 = 'GB', sic_codes = ?,
                 discovery_route = COALESCE(discovery_route, 'registry'),
                 last_seen = ?, updated_at = ?
               WHERE id = ?""",
            (name, key, created, postal, region, city, sic_json, stamp, stamp, company_id),
        )

    db.execute(
        """INSERT OR IGNORE INTO identifier(company_id, kind, value, source_key, first_seen)
           VALUES (?,?,?,?,?)""",
        (company_id, "ch", number, SOURCE_KEY, stamp),
    )
    # 04-sources §3.4 #4 — a placeholder name is never a merge key. Keep it as an
    # alias so a later mention under the old name still resolves after the rename.
    db.execute(
        """INSERT OR IGNORE INTO identifier(company_id, kind, value, source_key, first_seen)
           VALUES (?,?,?,?,?)""",
        (company_id, "alias" if screen.placeholder else "norm_key", key, SOURCE_KEY, stamp),
    )

    db.execute(
        """INSERT INTO company_source(company_id, source_key, external_id, source_url,
                                      first_seen, last_seen)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(company_id, source_key, external_id)
           DO UPDATE SET last_seen = excluded.last_seen""",
        (company_id, SOURCE_KEY, number, item.source_url, stamp, stamp),
    )

    _observe_once(db, company_id, "incorporated_on", created, source_url=item.source_url)
    _observe_once(db, company_id, "sic_codes", s.get("sic_codes") or [], source_url=item.source_url)
    if region:
        _observe_once(db, company_id, "hq_region", region, source_url=item.source_url)
    if postal:
        _observe_once(db, company_id, "hq_postcode", postal, source_url=item.source_url)

    return company_id, created_new


def record_incorporation_signal(db: Any, company_id: str, item: Any) -> bool:
    """One `incorporation` signal per company. `INSERT OR IGNORE` makes re-runs free."""
    s = item.structured or {}
    created = s.get("date_of_creation")
    name = s.get("company_name") or item.title
    cur = db.execute(
        """INSERT OR IGNORE INTO signal
             (company_id, kind, occurred_on, headline, detail, source_key,
              source_url, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            company_id, "incorporation", created,
            f"{name} incorporated at Companies House on {created}",
            ", ".join(s.get("sic_codes") or []) or None,
            SOURCE_KEY, item.source_url, now_iso(),
        ),
    )
    return bool(cur.rowcount)


# ------------------------------------------------------------------ enrich


def _filings_checked(db: Any, company_id: str) -> bool:
    return db.get_meta(FILINGS_CHECKED_PREFIX + company_id) is not None


def _mark_filings_checked(db: Any, company_id: str) -> None:
    db.set_meta(FILINGS_CHECKED_PREFIX + company_id, now_iso())


def enrichment_queue(db: Any, limit: int | None = None) -> list[dict]:
    """Companies waiting for enrichment, ordered by expected value.

    Companies with an existing signal (spinout, grant, press) come first, then
    the newest incorporations (04-sources §3.4a).
    """
    sql = """
        SELECT c.id, c.companies_house_no, c.canonical_name, c.incorporated_on,
               EXISTS(SELECT 1 FROM signal s
                      WHERE s.company_id = c.id AND s.kind <> 'incorporation') AS has_signal
        FROM company c
        WHERE c.enriched_at IS NULL
          AND c.companies_house_no IS NOT NULL
          AND c.merged_into IS NULL
        ORDER BY has_signal DESC, c.incorporated_on DESC, c.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.query(sql)]


def enrich_companies(
    db: Any,
    http: Any,
    *,
    api_key: str,
    budget: RequestBudget,
    base_url: str = CH_API_BASE,
    result: BackfillResult | None = None,
    max_companies: int | None = None,
) -> BackfillResult:
    """Passes 1→3 over the queue, stopping the moment the budget is gone.

    Pass 1 runs for as many companies as the budget allows **before** pass 2
    starts for any — a cheap SH01 check across 200 companies is worth more than
    full hydration of 40.
    """
    result = result or BackfillResult()
    queue = enrichment_queue(db, max_companies)

    # ---- pass 1: filing history → SH01 (1 request each)
    pass1_ok: list[dict] = []
    for row in queue:
        if _filings_checked(db, row["id"]):
            pass1_ok.append(row)
            continue
        if not budget.spend(1):
            break
        result.enrich_requests += 1
        raw = fetch_filing_history(http, row["companies_house_no"],
                                   api_key=api_key, base_url=base_url)
        _mark_filings_checked(db, row["id"])
        issues = qualifying_share_issues(raw, row["incorporated_on"]) if raw else []
        if issues:
            record_share_issues(db, row["id"], row["companies_house_no"],
                                row["canonical_name"], issues)
            result.share_issues += 1
        pass1_ok.append(row)

    # ---- pass 2: officers + PSC (2 requests each)
    hydrated: list[tuple[dict, list[Founder]]] = []
    for row in pass1_ok:
        if not budget.can_spend(2):
            break
        budget.spend(2)
        result.enrich_requests += 2
        number = row["companies_house_no"]
        officers_raw = fetch_officers(http, number, api_key=api_key, base_url=base_url)
        psc_raw = fetch_psc(http, number, api_key=api_key, base_url=base_url)

        source_url = CH_PROFILE_URL.format(number)
        officers = parse_officers(officers_raw or {}, source_url=source_url)
        pscs = parse_psc(psc_raw or {})

        if only_corporate_secretary(officers):
            # 04-sources §3.4 #6 — this check needs pass-2 data, so it cannot run
            # any earlier. Mark enriched so we never pay for it twice.
            stamp = now_iso()
            db.execute(
                "UPDATE company SET enriched_at = ?, officer_count = 0, updated_at = ? "
                "WHERE id = ?",
                (stamp, stamp, row["id"]),
            )
            result.dropped_corporate_only += 1
            continue

        founders = merge_founders(officers, pscs, source_url=source_url)
        db.execute(
            "UPDATE company SET officer_count = ?, updated_at = ? WHERE id = ?",
            (len([o for o in officers if not o.resigned]), now_iso(), row["id"]),
        )
        hydrated.append((row, founders))

    # ---- pass 3: prior appointments, 1 request per founder (repeat-founder signal)
    for row, founders in hydrated:
        source_url = CH_PROFILE_URL.format(row["companies_house_no"])
        enriched: list[Founder] = []
        for f in founders:
            if f.officer_id and budget.spend(1):
                result.enrich_requests += 1
                raw = fetch_appointments(http, f.officer_id, api_key=api_key,
                                         base_url=base_url)
                if raw is not None:
                    f = replace(f, prior_appointments=parse_appointment_count(raw))
            enriched.append(f)

        result.founders += store_founders(db, row["id"], enriched, source_url=source_url)
        stamp = now_iso()
        db.execute(
            "UPDATE company SET enriched_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, row["id"]),
        )
        result.enriched += 1

    result.budget_limit = budget.limit
    result.budget_spent = budget.spent
    result.queued = _queue_size(db)
    return result


def _queue_size(db: Any) -> int:
    return int(db.scalar(
        """SELECT COUNT(*) FROM company
           WHERE enriched_at IS NULL AND companies_house_no IS NOT NULL
             AND merged_into IS NULL"""
    ) or 0)


# ----------------------------------------------------------------- backfill


def backfill(
    db: Any,
    http: Any,
    config: Any,
    days: int = 90,
    *,
    api_key: str | None = None,
    now: date | None = None,
    window_days: int | None = None,
    adapter: CompaniesHouseAdapter | None = None,
    base_url: str = CH_API_BASE,
    postcodes_base: str = postcode.POSTCODES_IO_BASE,
    enrich: bool = True,
) -> BackfillResult:
    """Sweep Companies House for `days` of incorporations, then enrich.

    Safe to run repeatedly: every write is an upsert keyed on the Companies
    House number, so a second run creates no duplicates.
    """
    settings = getattr(config, "settings", None)
    api_key = api_key or _settings_key(settings) or api_key_from_env()
    if not api_key:
        raise SourceError(SOURCE_KEY, "no API key — set CH_API_KEY")

    regions = list(getattr(settings, "regions_enabled", None) or ["uk_wide"])
    budget_limit = int(getattr(settings, "max_enrichment_requests_per_run", 500) or 0)
    window = int(window_days or 7)

    adapter = adapter or CompaniesHouseAdapter(
        api_key=api_key, days_back=days, window_days=window, base_url=base_url
    )
    ctx = FetchContext(http=http, config=config, db=db, now=now or date.today(),
                       extra={"days_back": days})

    result = BackfillResult(days=days, budget_limit=budget_limit)
    before = getattr(http, "request_count", 0)

    for item in adapter.fetch(ctx):
        result.fetched += 1
        screen = screen_item(db, http, item, regions_enabled=regions,
                             postcodes_base=postcodes_base)
        if not screen.keep:
            if screen.reason == "region_not_enabled":
                result.dropped_region += 1
            elif screen.reason == "formation_agent_address":
                result.dropped_formation_agent += 1
            else:
                result.dropped_denylist += 1
            continue

        company_id, is_new = upsert_company(db, item, screen)
        result.companies_seen += 1
        result.companies_new += int(is_new)
        result.signals_new += int(record_incorporation_signal(db, company_id, item))

    stats = adapter.stats
    result.windows = stats.get("windows", 0)
    result.pages = stats.get("pages", 0)
    result.truncated_pages = stats.get("truncated_pages", 0)
    result.dropped_denylist += stats.get("dropped_denylist", 0)

    total_spent = getattr(http, "request_count", 0) - before
    result.sweep_requests = result.pages
    result.postcode_requests = max(total_spent - result.pages, 0)

    if enrich:
        enrich_companies(db, http, api_key=api_key,
                         budget=RequestBudget(budget_limit), base_url=base_url,
                         result=result)
    else:
        result.budget_limit = budget_limit
        result.queued = _queue_size(db)
    return result


def _settings_key(settings: Any) -> str | None:
    for name in ("ch_api_key", "companies_house_api_key"):
        value = getattr(settings, name, None)
        if value:
            return str(value)
    return None


__all__ = [
    "BackfillResult",
    "FORBIDDEN_FIELDS",
    "Founder",
    "PostcodeInfo",
    "PscHolder",
    "RequestBudget",
    "SH01",
    "ScreenResult",
    "ShareIssue",
    "apply_psc",
    "backfill",
    "ch_filings",
    "ch_officers",
    "enrich_companies",
    "enrichment_queue",
    "fetch_appointments",
    "fetch_filing_history",
    "fetch_officers",
    "fetch_psc",
    "find_share_issues",
    "founder_candidates",
    "geography_enabled",
    "has_share_issue",
    "lookup_outcode",
    "merge_founders",
    "only_corporate_secretary",
    "outcode_of",
    "parse_appointment_count",
    "parse_officers",
    "parse_psc",
    "postcode",
    "qualifying_share_issues",
    "record_incorporation_signal",
    "record_share_issues",
    "region_to_geography",
    "resolve_postcode",
    "screen_item",
    "store_founders",
    "upsert_company",
]
