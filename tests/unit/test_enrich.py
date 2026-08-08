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

from tests.factories import store_company

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
