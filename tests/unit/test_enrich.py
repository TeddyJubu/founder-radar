"""09-test-plan §2.7/§7 — the enrichment layer.

Three rules are enforced here: personal data is dropped **in the adapter, not
hidden at render**; an SH01 on a young company is a pre-seed round on the
public record (and sets `has_share_issue`); and the enrichment budget counts
requests, not companies, because Companies House bans an application for
repeated breaches rather than throttling it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from radar.enrich import RequestBudget, parse_officers
from radar.enrich.ch_filings import qualifying_share_issues, record_share_issues
from radar.score.derive import Company, derive_geography, derive_stage, geography_from_outcode
from radar.store.db import now_iso

from tests.factories import C, store_company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------- privacy at ingest


def test_ch_officer_ingest_drops_dob_and_address():
    """CH returns partial DOB and correspondence address. Both must be
    dropped in the ADAPTER, not merely hidden at render (03-data-model §2)."""
    raw = load_fixture("ch_officers_with_dob.json")
    founders = parse_officers(raw, source_url="https://find-and-update.company-information.service.gov.uk/company/15021884")
    assert len(founders) >= 1, "fixture must actually contain officers"
    for f in founders:
        assert not hasattr(f, "date_of_birth")
        assert not hasattr(f, "address")
        assert not hasattr(f, "nationality")


# --------------------------------------------------------------- SH01 → stage


def test_sh01_sets_has_share_issue(db):
    """FR-1.6: an SH01 filed within 18 months of incorporation flags the
    company and derives a pre-seed stage (06-scoring §2.3)."""
    raw = load_fixture("ch_filing_history_sh01.json")
    company = Company(
        id="c-sh01",
        canonical_name="METzero Limited",
        norm_key="metzerolimited",
        companies_house_no="15021884",
        country_iso2="GB",
        incorporated_on=date(2026, 6, 14),  # NEWINC in the fixture
        discovery_route="registry",
    )
    cid = store_company(db, company)

    issues = qualifying_share_issues(raw, "2026-06-14")
    assert issues, "fixture must contain a qualifying SH01"
    record_share_issues(db, cid, "15021884", "METzero Limited", issues)

    row = db.one("SELECT * FROM company WHERE id = ?", (cid,))
    assert row["has_share_issue"] == 1
    scored = Company(**{k: row[k] for k in (
        "id", "canonical_name", "norm_key", "companies_house_no", "incorporated_on",
        "hq_region", "country_iso2", "discovery_route", "has_share_issue")})
    assert derive_stage(scored) == "pre_seed"


# ------------------------------------------------- postcode → geography (FR-1.3)


@pytest.mark.parametrize("outcode,expected", [
    ("NE1", "north_east"), ("S75", "yorkshire"), ("EC2A", "london"),
    ("EH1", "uk_regions"),        # Scotland: region is NULL, country wins
    ("CF10", "uk_regions"),       # Wales
    ("OX1", "uk_regions"),        # but fails outside_golden_triangle
])
def test_postcode_to_geography(config, outcode, expected):
    """The offline path reads the seeded outcode map from Config.lists —
    scoring stays a pure function; the live postcodes.io lookup only fills
    the cache."""
    assert geography_from_outcode(outcode, config) == expected


def test_derive_geography_prefers_region_then_country():
    assert derive_geography("London", "England", "EC2A") == "london"
    assert derive_geography("North East", "England", "NE1") == "north_east"
    assert derive_geography(None, "Scotland", "EH1") == "uk_regions"
    assert derive_geography(None, "England", "OX1") == "uk_wide"   # unresolvable
    assert derive_geography("West Midlands", "England", "B1") == "uk_regions"


# -------------------------------------------------------------- the budget


def test_budget_counts_requests_not_companies():
    """300 companies × 2 is off by a factor of three: full enrichment is 4–8
    calls per company (04-sources §3.4a)."""
    b = RequestBudget(limit=4)
    assert b.spend(1)
    assert b.spend(2)
    assert not b.spend(2)          # 3 of 4 spent — this would exceed the cap
    assert b.spend(1)
    assert b.exhausted
    assert b.remaining == 0


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.text = ""

    @property
    def ok(self):
        return 200 <= self.status < 400

    def json(self):
        if self._payload is not None:
            return self._payload
        raise ValueError("no json payload")


class _ProfileHttp:
    def __init__(self, payload, status=200):
        self.requests: list[str] = []
        self.payload = payload
        self.status = status

    def get(self, url, **kw):  # noqa: ARG002
        self.requests.append(url)
        return _Resp(self.payload, self.status)


PROFILE = {
    "company_number": "15021884",
    "date_of_creation": "2025-04-02",
    "sic_codes": ["72110"],
    "registered_office_address": {
        "locality": "Newcastle Upon Tyne",
        "postal_code": "NE1 4ST",
    },
}


def test_hydrate_missing_ages_fills_grant_crn_without_changing_route(db):
    """Telegram search wrote Innovate UK CRNs; Today hid them as unknown age."""
    from radar.enrich import RequestBudget, hydrate_missing_ages

    cid = store_company(db, C(
        canonical_name="Grant Co",
        norm_key="grantco",
        companies_house_no="15021884",
        discovery_route="grant",
        incorporated_on=None,
        stage=None,
    ))
    http = _ProfileHttp(PROFILE)
    result = hydrate_missing_ages(
        db, http, api_key="k", budget=RequestBudget(limit=4),
    )
    row = db.one(
        "SELECT incorporated_on, age_source, discovery_route, hq_postcode "
        "FROM company WHERE id = ?",
        (cid,),
    )
    assert result.ages_hydrated == 1
    assert row["incorporated_on"] == "2025-04-02"
    assert row["age_source"] == "companies_house"
    assert row["discovery_route"] == "grant"
    assert row["hq_postcode"] == "NE1 4ST"
    assert any(url.rstrip("/").endswith("/company/15021884") for url in http.requests)
    signal = db.one(
        "SELECT kind, source_key FROM signal WHERE company_id = ?", (cid,),
    )
    assert signal["kind"] == "verification"
    assert signal["source_key"] == "companies_house"


def test_hydrate_missing_ages_does_not_overwrite_an_existing_date(db):
    from radar.enrich import RequestBudget, hydrate_missing_ages

    cid = store_company(db, C(
        canonical_name="Dated Co",
        norm_key="datedco",
        companies_house_no="15021884",
        discovery_route="grant",
        incorporated_on=date(2024, 1, 1),
    ))
    result = hydrate_missing_ages(
        db, _ProfileHttp(PROFILE), api_key="k", budget=RequestBudget(limit=4),
    )
    assert result.ages_hydrated == 0
    row = db.one("SELECT incorporated_on FROM company WHERE id = ?", (cid,))
    assert row["incorporated_on"] == "2024-01-01"


def test_hydrate_missing_ages_marks_a_404_so_it_does_not_retry(db):
    from radar.enrich import RequestBudget, hydrate_missing_ages, missing_age_queue

    store_company(db, C(
        canonical_name="Gone Co",
        norm_key="goneco",
        companies_house_no="15021884",
        discovery_route="news",
        incorporated_on=None,
    ))
    http = _ProfileHttp(None, status=404)
    hydrate_missing_ages(db, http, api_key="k", budget=RequestBudget(limit=4))
    assert missing_age_queue(db) == []
    second = hydrate_missing_ages(
        db, http, api_key="k", budget=RequestBudget(limit=4),
    )
    assert second.enrich_requests == 0


# ------------------------------- H-05: a later SH01 must still be discovered


def _iso_ago(*, days: int = 0, hours: int = 0) -> str:
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_filing_markers(db, *, days: int = 0, hours: int = 0) -> None:
    """Move every filing-history marker back in time.

    The enrichment code reads the wall clock, so "a week later" is simulated by
    making the stored markers a week older rather than by waiting.
    """
    stamp = _iso_ago(days=days, hours=hours)
    db.execute("UPDATE _meta SET value = ? WHERE key LIKE 'ch_filings_%'", (stamp,))


class _ScriptedCH:
    """A Companies House double whose filing-history answer changes between runs."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.filing_items: list[dict] = []
        self.filing_status = 200

    def get(self, url, **kw):  # noqa: ARG002
        self.requests.append(url)
        if "filing-history" in url:
            if self.filing_status != 200:
                return _Resp(None, self.filing_status)
            return _Resp({"items": list(self.filing_items)})
        if "persons-with-significant-control" in url:
            return _Resp({"items": []})
        if "/appointments" in url:
            return _Resp({"total_results": 1, "items": []})
        if "/officers" in url:
            return _Resp({"items": [{
                "name": "LOVELACE, Ada",
                "officer_role": "director",
                "appointed_on": "2026-01-05",
                "links": {"officer": {"appointments": "/officers/abc123/appointments"}},
            }]})
        return _Resp(PROFILE)

    @property
    def filing_requests(self) -> int:
        return sum("filing-history" in u for u in self.requests)


def _sh01_item(days_ago: int = 2) -> dict:
    from datetime import timedelta

    return {
        "type": "SH01",
        "category": "capital",
        "date": (date.today() - timedelta(days=days_ago)).isoformat(),
        "transaction_id": "MzQ1Njc4OTBhZGlxemtjeA",
        "description": "capital-allotment-shares",
    }


def _registry_row(db, *, number="15021884", age_months=5, name="Late Filer Ltd"):
    from tests.factories import registry_company

    company = registry_company(
        canonical_name=name, norm_key=name.lower().replace(" ", ""),
        companies_house_no=number, age_months=age_months,
    )
    return store_company(db, company)


def _run_enrichment(db, http, limit: int = 60):
    from radar.enrich import enrich_companies

    return enrich_companies(db, http, api_key="k", budget=RequestBudget(limit=limit))


def test_sh01_filed_after_the_first_pass_is_discovered_on_a_later_run(db):
    """H-05: day one there is no SH01, so the company enriches and leaves the
    queue. A week later it files one. The second run must ask again, record
    the signal, and let the qualification gate admit the company. The older
    qualification test inserts the signal by hand and never exercised this."""
    from radar.config.defaults import default_config
    from radar.pipeline import score_company

    cfg = default_config()
    cid = _registry_row(db)
    ch = _ScriptedCH()

    day_one = _run_enrichment(db, ch)
    assert day_one.enriched == 1
    assert ch.filing_requests == 1
    assert db.scalar("SELECT has_share_issue FROM company WHERE id = ?", (cid,)) == 0
    assert score_company(db, cid, cfg) == 0          # no qualifier yet

    _age_filing_markers(db, days=8)
    ch.filing_items = [_sh01_item()]
    later = _run_enrichment(db, ch)

    assert ch.filing_requests == 2, "the young company was never asked again"
    assert later.share_issues == 1
    assert db.scalar("SELECT has_share_issue FROM company WHERE id = ?", (cid,)) == 1
    assert db.scalar(
        "SELECT COUNT(*) FROM signal WHERE company_id = ? AND kind = 'share_issue'",
        (cid,)) == 1
    assert score_company(db, cid, cfg) > 0           # now admitted to scoring
    assert db.scalar("SELECT qualified FROM company WHERE id = ?", (cid,)) == 1


def test_a_failed_filing_fetch_is_a_retryable_failure_not_a_check(db):
    """H-05: a 503 used to be stamped "checked" and never retried."""
    from radar.enrich import FILINGS_CHECKED_PREFIX, FILINGS_RETRY_PREFIX

    cid = _registry_row(db)
    ch = _ScriptedCH()
    ch.filing_status = 503

    first = _run_enrichment(db, ch)
    assert first.enriched == 1, "officers/PSC hydration does not depend on filings"
    assert db.get_meta(FILINGS_CHECKED_PREFIX + cid) is None
    assert db.get_meta(FILINGS_RETRY_PREFIX + cid) is not None
    assert ch.filing_requests == 1

    # Not hammered: an immediate second run leaves the failed company alone.
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1

    # Once the retry interval has passed it is asked again, and this time the
    # register answers.
    _age_filing_markers(db, hours=48)
    ch.filing_status = 200
    ch.filing_items = [_sh01_item()]
    _run_enrichment(db, ch)

    assert ch.filing_requests == 2
    assert db.get_meta(FILINGS_CHECKED_PREFIX + cid) is not None
    assert db.get_meta(FILINGS_RETRY_PREFIX + cid) is None
    assert db.scalar("SELECT has_share_issue FROM company WHERE id = ?", (cid,)) == 1


def test_an_unparseable_filing_response_is_not_recorded_as_a_check(db):
    from radar.enrich import FILINGS_CHECKED_PREFIX

    class Garbled(_ScriptedCH):
        def get(self, url, **kw):
            if "filing-history" in url:
                self.requests.append(url)
                return _Resp(None, 200)          # 200 whose body is not JSON
            return super().get(url, **kw)

    cid = _registry_row(db)
    _run_enrichment(db, Garbled())
    assert db.get_meta(FILINGS_CHECKED_PREFIX + cid) is None


def test_a_transport_error_on_filings_does_not_abort_enrichment(db):
    from radar.enrich import FILINGS_CHECKED_PREFIX, FILINGS_RETRY_PREFIX

    class Down(_ScriptedCH):
        def get(self, url, **kw):
            if "filing-history" in url:
                self.requests.append(url)
                raise ConnectionError("boom")
            return super().get(url, **kw)

    cid = _registry_row(db)
    result = _run_enrichment(db, Down())
    assert result.enriched == 1
    assert db.get_meta(FILINGS_CHECKED_PREFIX + cid) is None
    assert db.get_meta(FILINGS_RETRY_PREFIX + cid) is not None


def test_filing_recheck_waits_for_the_interval(db):
    cid = _registry_row(db)
    ch = _ScriptedCH()
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1

    _run_enrichment(db, ch)                                   # same day
    assert ch.filing_requests == 1
    _age_filing_markers(db, days=3)                           # inside the interval
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1
    _age_filing_markers(db, days=8)                           # due
    _run_enrichment(db, ch)
    assert ch.filing_requests == 2
    assert db.get_meta("ch_filings_checked:" + cid) is not None


def test_filing_recheck_stops_once_the_company_is_outside_the_age_window(db):
    """An SH01 only counts within 18 months of incorporation, so polling a
    30-month-old company forever would spend budget for nothing."""
    _registry_row(db, age_months=30, name="Old Timer Ltd")
    ch = _ScriptedCH()
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1                            # the first look
    _age_filing_markers(db, days=30)
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1


def test_filing_recheck_stops_once_an_sh01_has_been_found(db):
    cid = _registry_row(db)
    ch = _ScriptedCH()
    ch.filing_items = [_sh01_item()]
    _run_enrichment(db, ch)
    assert db.scalar("SELECT has_share_issue FROM company WHERE id = ?", (cid,)) == 1
    _age_filing_markers(db, days=30)
    _run_enrichment(db, ch)
    assert ch.filing_requests == 1


def test_filing_recheck_respects_the_request_budget(db):
    """The re-poll rides inside the existing budget and pass-1 share; it does
    not raise either."""
    from radar.enrich import enrichment_queue

    stamp = now_iso()
    for index in range(30):
        cid = _registry_row(db, number=f"{20_000_000 + index}",
                            name=f"Waiting Co {index} Ltd")
        db.execute("UPDATE company SET enriched_at = ?, officer_count = 1 WHERE id = ?",
                   (stamp, cid))
        db.set_meta("ch_appointments_complete:" + cid, stamp)
        db.set_meta("ch_filings_checked:" + cid, _iso_ago(days=10))
    assert enrichment_queue(db) == []                         # nothing else is waiting

    ch = _ScriptedCH()
    result = _run_enrichment(db, ch, limit=12)

    assert len(ch.requests) <= 12
    assert result.budget_spent <= 12
    assert 0 < ch.filing_requests <= 4, "pass 1 keeps its one-third share"
