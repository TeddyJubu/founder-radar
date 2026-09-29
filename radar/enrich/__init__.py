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

import calendar
import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from radar.enrich import ch_filings, ch_officers, postcode
from radar.enrich.ch_filings import (
    SH01,
    SHARE_ISSUE_WINDOW_MONTHS,
    ShareIssue,
    fetch_filing_history,
    fetch_filing_history_checked,
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
    fetch_company_profile,
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

#: `_meta` marker: when the register last ANSWERED a filing-history request for
#: this company (a 200, or a 404). A failed request never writes it. Pass 1 can
#: complete while pass 2 never starts, and `enriched_at` alone cannot express
#: that. The value is a timestamp because a young company is polled again once
#: `FILINGS_RECHECK_DAYS` have passed (H-05): an SH01 filed next month must still
#: be found even though the company already left the enrichment queue.
FILINGS_CHECKED_PREFIX = "ch_filings_checked:"
#: `_meta` marker: when a filing-history request last FAILED (5xx, 429,
#: transport error, unparseable body). A retryable failure, kept apart from a
#: successful check so a bad day at Companies House is retried rather than
#: recorded as "no SH01". Cleared by the next successful check.
FILINGS_RETRY_PREFIX = "ch_filings_retry:"
#: A young company with no SH01 yet is polled again after this many days. The
#: age bound is `SHARE_ISSUE_WINDOW_MONTHS`: an SH01 beyond it does not count.
FILINGS_RECHECK_DAYS = 7
#: A failed request is not retried before this many hours have passed.
FILINGS_RETRY_HOURS = 12
#: This many failures in a row end filing-history polling for the run.
FILINGS_MAX_CONSECUTIVE_FAILURES = 5
#: Pass 3 can also be deferred when the request budget ends after officers/PSC.
#: Keep that state separate from `enriched_at`, so repeat-founder evidence is
#: not silently lost when a company was only partially hydrated.
APPOINTMENTS_COMPLETE_PREFIX = "ch_appointments_complete:"
#: Pass 0: company profile → `incorporated_on`. Separate from `enriched_at`
#: because officer hydration used to mark rows complete without ever fetching
#: the date, which is how Innovate UK cards vanished from Today.
PROFILE_CHECKED_PREFIX = "ch_profile_checked:"


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
    filing_rechecks: int = 0
    filing_failures: int = 0
    ages_hydrated: int = 0
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


def _utc_stamp(moment: datetime) -> str:
    """Same shape as `now_iso()`, so stored markers compare as strings."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _months_before(today: date, months: int) -> date:
    index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def _filings_checked(db: Any, company_id: str) -> bool:
    return db.get_meta(FILINGS_CHECKED_PREFIX + company_id) is not None


def _filings_retry_due(db: Any, company_id: str) -> bool:
    """False while a failed request is still inside its retry interval."""
    failed_at = db.get_meta(FILINGS_RETRY_PREFIX + company_id)
    if failed_at is None:
        return True
    cutoff = _utc_stamp(datetime.now(timezone.utc) - timedelta(hours=FILINGS_RETRY_HOURS))
    return failed_at <= cutoff


def _mark_filings_checked(db: Any, company_id: str) -> None:
    """The register answered. Also clears any earlier retryable failure."""
    db.set_meta(FILINGS_CHECKED_PREFIX + company_id, now_iso())
    db.execute("DELETE FROM _meta WHERE key = ?", (FILINGS_RETRY_PREFIX + company_id,))


def _mark_filings_failed(db: Any, company_id: str) -> None:
    """The register did not answer. Not a check: the company stays due."""
    db.set_meta(FILINGS_RETRY_PREFIX + company_id, now_iso())


def _mark_appointments_complete(db: Any, company_id: str) -> None:
    db.set_meta(APPOINTMENTS_COMPLETE_PREFIX + company_id, now_iso())


def _profile_checked(db: Any, company_id: str) -> bool:
    return db.get_meta(PROFILE_CHECKED_PREFIX + company_id) is not None


def _mark_profile_checked(db: Any, company_id: str) -> None:
    db.set_meta(PROFILE_CHECKED_PREFIX + company_id, now_iso())


def missing_age_queue(db: Any, limit: int | None = None) -> list[dict]:
    """Companies that have a CRN but no incorporation date.

    Innovate UK (and other Track A sources) store the number as identity.
    Enrichment used to skip the profile lookup, so Today treated them as
    maturity_unknown and the Telegram search looked like it never landed.
    Reviewable scores go first so a budget-capped run unblocks the dashboard.
    """
    sql = """
        SELECT c.id, c.companies_house_no, c.canonical_name, c.incorporated_on
        FROM company c
        WHERE c.companies_house_no IS NOT NULL
          AND TRIM(c.companies_house_no) != ''
          AND c.incorporated_on IS NULL
          AND c.merged_into IS NULL
          AND NOT EXISTS (
                SELECT 1 FROM _meta m WHERE m.key = ? || c.id
          )
        ORDER BY EXISTS(
                   SELECT 1 FROM score s
                    WHERE s.company_id = c.id
                      AND s.tier IN ('shortlist', 'watchlist')
                 ) DESC,
                 c.last_seen DESC,
                 c.id
    """
    params: tuple[Any, ...] = (PROFILE_CHECKED_PREFIX,)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.query(sql, params)]


def apply_company_profile(
    db: Any,
    company_id: str,
    profile: Mapping[str, Any],
    *,
    source_url: str,
) -> bool:
    """Write register facts onto an existing company. Never changes route.

    Returns True when `incorporated_on` was filled in this call.
    """
    row = db.one(
        "SELECT canonical_name, incorporated_on, hq_postcode, hq_city, sic_codes "
        "FROM company WHERE id = ?",
        (company_id,),
    )
    if row is None:
        return False

    created = str(profile.get("date_of_creation") or "").strip()[:10] or None
    if created and len(created) < 10:
        created = None
    address = profile.get("registered_office_address") or {}
    postal = (address.get("postal_code") or "").strip() or None
    city = (address.get("locality") or "").strip() or None
    sic = profile.get("sic_codes") or []
    sic_json = json.dumps(sic) if sic else None

    assignments: list[str] = []
    params: list[Any] = []
    wrote_age = False
    if created and not row["incorporated_on"]:
        assignments.extend([
            "incorporated_on = ?",
            "age_source = 'companies_house'",
            "date_confidence = 'exact'",
        ])
        params.append(created)
        wrote_age = True
        _observe_once(
            db, company_id, "incorporated_on", created, source_url=source_url,
        )
        db.execute(
            """INSERT OR IGNORE INTO signal
                 (company_id, kind, occurred_on, headline, detail, source_key,
                  source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                company_id, "verification", created,
                f"{row['canonical_name']} verified on Companies House, "
                f"incorporated {created}",
                None, SOURCE_KEY, source_url, now_iso(),
            ),
        )
    if postal and not row["hq_postcode"]:
        assignments.append("hq_postcode = ?")
        params.append(postal)
    if city and not row["hq_city"]:
        assignments.append("hq_city = ?")
        params.append(city)
    if sic_json and not row["sic_codes"]:
        assignments.append("sic_codes = ?")
        params.append(sic_json)

    stamp = now_iso()
    assignments.append("updated_at = ?")
    params.append(stamp)
    params.append(company_id)
    db.execute(
        f"UPDATE company SET {', '.join(assignments)} WHERE id = ?",
        params,
    )
    return wrote_age


def hydrate_missing_ages(
    db: Any,
    http: Any,
    *,
    api_key: str,
    budget: RequestBudget,
    base_url: str = CH_API_BASE,
    result: BackfillResult | None = None,
    max_companies: int | None = None,
) -> BackfillResult:
    """Pass 0: fill `incorporated_on` from the Companies House profile."""
    result = result or BackfillResult()
    queue = missing_age_queue(db, max_companies)
    for row in queue:
        if not budget.spend(1):
            break
        result.enrich_requests += 1
        number = normalise_ch_number(row["companies_house_no"]) or row["companies_house_no"]
        raw = fetch_company_profile(
            http, number, api_key=api_key, base_url=base_url,
        )
        _mark_profile_checked(db, row["id"])
        if not raw:
            continue
        source_url = CH_PROFILE_URL.format(number)
        if apply_company_profile(db, row["id"], raw, source_url=source_url):
            result.ages_hydrated += 1
    return result


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
        WHERE c.companies_house_no IS NOT NULL
          AND c.merged_into IS NULL
          AND (
                c.enriched_at IS NULL
                OR (c.officer_count > 0 AND NOT EXISTS (
                    SELECT 1 FROM _meta m
                    WHERE m.key = ? || c.id
                ))
              )
        ORDER BY has_signal DESC, c.incorporated_on DESC, c.id
    """
    # The marker prefix is a bound value rather than interpolated company data.
    params: tuple[Any, ...] = (APPOINTMENTS_COMPLETE_PREFIX,)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.query(sql, params)]


def filing_recheck_queue(db: Any, limit: int | None = None) -> list[dict]:
    """Companies whose filing history is due for another look (H-05).

    Three groups, all with no SH01 recorded yet and all bounded by the age
    window (an SH01 more than `SHARE_ISSUE_WINDOW_MONTHS` after incorporation
    does not count, so polling past it buys nothing):

    * a young company last confirmed more than `FILINGS_RECHECK_DAYS` ago;
    * a company whose last request failed, once `FILINGS_RETRY_HOURS` passed;
    * an enriched company with no marker at all (nothing ever recorded).

    A company with an unknown incorporation date is only retried until one
    request succeeds; it cannot be re-polled on a schedule it has no age for.
    Never-confirmed rows come first, then the longest-unchecked, so a small
    budget still rotates through everyone. Companies still waiting in
    `enrichment_queue` are handled there.
    """
    moment = datetime.now(timezone.utc)
    age_cutoff = _months_before(moment.date(), SHARE_ISSUE_WINDOW_MONTHS).isoformat()
    recheck_cutoff = _utc_stamp(moment - timedelta(days=FILINGS_RECHECK_DAYS))
    retry_cutoff = _utc_stamp(moment - timedelta(hours=FILINGS_RETRY_HOURS))
    sql = """
        SELECT c.id, c.companies_house_no, c.canonical_name, c.incorporated_on
        FROM company c
        LEFT JOIN _meta ok  ON ok.key  = ? || c.id
        LEFT JOIN _meta bad ON bad.key = ? || c.id
        WHERE c.companies_house_no IS NOT NULL
          AND TRIM(c.companies_house_no) != ''
          AND c.merged_into IS NULL
          AND COALESCE(c.has_share_issue, 0) = 0
          AND (
                (ok.value IS NULL
                 AND (c.incorporated_on IS NULL OR c.incorporated_on >= ?)
                 AND ((bad.value IS NOT NULL AND bad.value <= ?)
                      OR (bad.value IS NULL AND c.enriched_at IS NOT NULL)))
                OR
                (ok.value IS NOT NULL AND ok.value <= ?
                 AND c.incorporated_on IS NOT NULL AND c.incorporated_on >= ?
                 AND (bad.value IS NULL OR bad.value <= ?))
              )
        ORDER BY ok.value IS NOT NULL, COALESCE(ok.value, bad.value),
                 c.incorporated_on DESC, c.id
    """
    params: tuple[Any, ...] = (
        FILINGS_CHECKED_PREFIX, FILINGS_RETRY_PREFIX,
        age_cutoff, retry_cutoff,
        recheck_cutoff, age_cutoff, retry_cutoff,
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.query(sql, params)]


def _check_filings(db: Any, http: Any, row: Mapping[str, Any], *, api_key: str,
                   base_url: str, result: BackfillResult):
    """One filing-history request for one company; records what it learned.

    A successful answer is stored as a check (with any SH01 signal); a failed
    request is stored as a retryable failure and never as a check.
    """
    result.enrich_requests += 1
    fetch = fetch_filing_history_checked(
        http, row["companies_house_no"], api_key=api_key, base_url=base_url,
    )
    if not fetch.ok:
        _mark_filings_failed(db, row["id"])
        result.filing_failures += 1
        return fetch
    _mark_filings_checked(db, row["id"])
    issues = (qualifying_share_issues(fetch.payload, row["incorporated_on"])
              if fetch.payload else [])
    if issues:
        record_share_issues(db, row["id"], row["companies_house_no"],
                            row["canonical_name"], issues)
        result.share_issues += 1
    return fetch


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

    Pass 1 and pass 2 each get at most roughly one third of a run's request
    budget when work is waiting. The final third is reserved for officer
    appointments, which is the evidence that can prove a repeat founder. A
    large backlog must make progress through all three passes or registry
    companies never earn a qualifier and never reach scoring.
    """
    result = result or BackfillResult()

    # ---- pass 0: profile → incorporated_on (1 request each)
    # Must run before filings/officers. A 500-request budget that only looks
    # at SH01 leaves grant companies undated, and Today stays empty while
    # Telegram reports "132 new companies".
    if budget.remaining:
        officer_backlog = enrichment_queue(db, 1)
        age_cap = budget.remaining
        if officer_backlog and age_cap > 1:
            age_cap = max(1, (age_cap * 2) // 3)
        sliced = RequestBudget(age_cap)
        hydrate_missing_ages(
            db, http, api_key=api_key, budget=sliced,
            base_url=base_url, result=result, max_companies=max_companies,
        )
        budget.spent += sliced.spent

    queue = enrichment_queue(db, max_companies)

    # ---- pass 1: filing history → SH01 (1 request each)
    pass1_ok: list[dict] = []
    # A bounded pass-1 cohort makes the queue converge even when new registry
    # rows arrive every day. Already-checked rows cost no requests and are
    # always carried into pass 2 immediately.
    pass1_limit = max(1, budget.limit // 3)
    # A quarter of pass 1 is kept for re-polling young companies that already
    # left the queue, so a steady stream of new incorporations cannot starve a
    # later SH01 (H-05). Whatever the first checks leave unused goes to them.
    recheck_reserve = pass1_limit // 4 if filing_recheck_queue(db, 1) else 0
    first_check_limit = pass1_limit - recheck_reserve
    pass1_spent = 0
    failure_streak = 0
    filings_halted = False
    for row in queue:
        if (_filings_checked(db, row["id"]) or filings_halted
                or not _filings_retry_due(db, row["id"])):
            # Checked already, or failed too recently to ask again, or the
            # register is refusing us this run. Hydration does not depend on
            # filings; a company still owed a check is picked up by the re-poll.
            pass1_ok.append(row)
            continue
        if pass1_spent >= first_check_limit:
            break
        if not budget.spend(1):
            break
        pass1_spent += 1
        fetch = _check_filings(db, http, row, api_key=api_key,
                               base_url=base_url, result=result)
        failure_streak = 0 if fetch.ok else failure_streak + 1
        filings_halted = (fetch.rate_limited
                          or failure_streak >= FILINGS_MAX_CONSECUTIVE_FAILURES)
        pass1_ok.append(row)

    # ---- pass 1b: re-poll filing history for young companies with no SH01 yet
    # (a later SH01 is the whole point), inside the same pass-1 share.
    if not filings_halted:
        for row in filing_recheck_queue(db, max(pass1_limit - pass1_spent, 0)):
            if pass1_spent >= pass1_limit or not budget.spend(1):
                break
            pass1_spent += 1
            result.filing_rechecks += 1
            fetch = _check_filings(db, http, row, api_key=api_key,
                                   base_url=base_url, result=result)
            failure_streak = 0 if fetch.ok else failure_streak + 1
            if fetch.rate_limited or failure_streak >= FILINGS_MAX_CONSECUTIVE_FAILURES:
                break

    # ---- pass 2: officers + PSC (2 requests each)
    hydrated: list[tuple[dict, list[Founder]]] = []
    pass2_limit = max(2, budget.limit // 3)
    pass2_spent = 0
    for row in pass1_ok:
        if pass2_spent + 2 > pass2_limit or not budget.can_spend(2):
            break
        budget.spend(2)
        pass2_spent += 2
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
            _mark_appointments_complete(db, row["id"])
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
        appointments_complete = True
        for f in founders:
            if not f.officer_id:
                enriched.append(f)
                continue
            if not budget.spend(1):
                appointments_complete = False
                enriched.append(f)
                continue
            if f.officer_id:
                result.enrich_requests += 1
                raw = fetch_appointments(http, f.officer_id, api_key=api_key,
                                         base_url=base_url)
                if raw is not None:
                    f = replace(f, prior_appointments=parse_appointment_count(raw))
            enriched.append(f)

        result.founders += store_founders(db, row["id"], enriched, source_url=source_url)
        stamp = now_iso()
        if appointments_complete:
            db.execute(
                "UPDATE company SET enriched_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, row["id"]),
            )
            _mark_appointments_complete(db, row["id"])
            result.enriched += 1
        else:
            # Officers/PSC may have been stored, but the row is not complete
            # until every officer appointment request has been attempted.
            db.execute(
                "UPDATE company SET enriched_at = NULL, updated_at = ? WHERE id = ?",
                (stamp, row["id"]),
            )

    result.budget_limit = budget.limit
    result.budget_spent = budget.spent
    result.queued = _queue_size(db)
    return result


def _queue_size(db: Any) -> int:
    return int(db.scalar(
        """SELECT COUNT(*) FROM company
           WHERE companies_house_no IS NOT NULL
             AND merged_into IS NULL
             AND (
                   enriched_at IS NULL
                   OR (officer_count > 0 AND NOT EXISTS (
                       SELECT 1 FROM _meta m
                       WHERE m.key = ? || company.id
                   ))
                 )""",
        (APPOINTMENTS_COMPLETE_PREFIX,),
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
    "filing_recheck_queue",
    "fetch_filing_history_checked",
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
