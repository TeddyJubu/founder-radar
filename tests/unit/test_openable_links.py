"""Evidence surfaces open a page, not an Innovate UK workbook download."""
from __future__ import annotations

import json
from datetime import date

from prototype.server import build_kept, build_kept_table, build_today, render_kept_table
from radar.config.defaults import default_config
from radar.links import (
    is_workbook_url,
    openable_url,
    prefer_evidence_sources,
    primary_source,
)
from radar.qa.today import TodayCard, load_today_cards
from radar.render.sheet import _gather, build_companies
from radar.sources.innovate_uk import PUBLICATION
from tests.fakes import seed_companies

WORKBOOK = (
    "https://www.ukri.org/wp-content/uploads/2026/09/"
    "IUK-funded-projects-2016-present.xlsx"
)
BOOK_WITH_REF = f"{WORKBOOK}#project=10012345&participant=Ada"
GTR = "https://gtr.ukri.org/projects?ref=10012345"
ARTICLE = "https://www.uktn.com/news/example-startup-raises"
CH = "https://find-and-update.company-information.service.gov.uk/company/01823344"


def test_openable_url_maps_workbook_to_gtr_or_publication():
    assert openable_url(BOOK_WITH_REF) == GTR
    assert openable_url(WORKBOOK, "10012345:01823344") == GTR
    assert openable_url(WORKBOOK) == PUBLICATION
    assert openable_url(ARTICLE) == ARTICLE
    assert openable_url("") == ""
    assert is_workbook_url(WORKBOOK) is True
    assert is_workbook_url(ARTICLE) is False


def test_prefer_evidence_sources_puts_pages_before_workbook_and_ch():
    rows = [
        {"source_key": "companies_house", "source_url": CH},
        {"source_key": "innovate_uk", "source_url": BOOK_WITH_REF,
         "external_id": "10012345:01823344"},
        {"source_key": "uktn", "source_url": ARTICLE},
    ]
    ordered = prefer_evidence_sources(rows)
    assert [r["source_key"] for r in ordered] == [
        "uktn", "innovate_uk", "companies_house",
    ]
    assert primary_source(rows)["source_url"] == ARTICLE


def test_sheet_companies_emit_openable_sources_and_evidence(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    db.execute("DELETE FROM company_source WHERE company_id=?", (cid,))
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "companies_house", "01823344", CH, "2026-09-01", "2026-09-01"),
    )
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "innovate_uk", "10012345:01823344", BOOK_WITH_REF,
         "2026-09-02", "2026-09-02"),
    )
    db.execute(
        "INSERT INTO signal(company_id, kind, headline, source_url, source_key, "
        "occurred_on, first_seen) VALUES (?,?,?,?,?,?,?)",
        (cid, "grant_award", "Pilot grant", BOOK_WITH_REF, "innovate_uk",
         "2026-08-01", "2026-09-02"),
    )

    views = _gather(db)
    assert views[cid].sources[0] == GTR
    assert all(not is_workbook_url(u) for u in views[cid].sources)
    assert views[cid].evidence[0][1] == GTR

    rows = build_companies(db, default_config(), {}, today=date(2026, 10, 1))
    company_row = next(r for r in rows if r.key == cid)
    assert GTR in company_row.cells["AE"]
    assert ".xlsx" not in company_row.cells["Y"]
    assert ".xlsx" not in company_row.cells["AE"]


def test_kept_html_opens_gtr_not_workbook(db):
    cid = seed_companies(db, count=1, shortlist=1)[0]
    db.execute("DELETE FROM company_source WHERE company_id=?", (cid,))
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "innovate_uk", "10012345:01823344", BOOK_WITH_REF,
         "2026-09-02", "2026-09-02"),
    )
    db.execute(
        "INSERT INTO user_field(company_id, field, value, updated_at) "
        "VALUES (?,?,?,?)",
        (cid, "verdict", "worth contacting", "2026-10-01T12:00:00Z"),
    )

    kept = build_kept(db.conn)["worth contacting"]
    assert kept and kept[0]["source"] == GTR

    table = build_kept_table(db.conn)
    html = render_kept_table(db.conn)
    assert GTR in html
    assert ".xlsx" not in html
    assert table[0]["sources"][0]["source_url"] == BOOK_WITH_REF


def test_today_api_and_qa_blob_prefer_openable(db):
    from tests.factories import approve_cards

    cid = seed_companies(db, count=1, shortlist=1)[0]
    name = db.scalar("SELECT canonical_name FROM company WHERE id=?", (cid,))
    db.execute("DELETE FROM company_source WHERE company_id=?", (cid,))
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "innovate_uk", "10012345:01823344", BOOK_WITH_REF,
         "2026-09-02", "2026-09-02"),
    )
    db.execute(
        "INSERT INTO company_source(company_id, source_key, external_id, "
        "source_url, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
        (cid, "uktn", "story-1", ARTICLE, "2026-09-03", "2026-09-03"),
    )
    # Source edits change the QA snapshot; re-approve so Today includes the card.
    approve_cards(db, [cid])

    payload = build_today(db.conn, limit=5)
    card = next(c for c in payload["companies"] if c["company_id"] == cid)
    assert card["source_key"] == "uktn"
    assert card["open_url"] == ARTICLE
    assert all(
        ".xlsx" not in str(s.get("open_url", "")).lower()
        for s in card["sources"]
    )

    db.execute(
        "DELETE FROM company_source WHERE company_id=? AND source_key='uktn'",
        (cid,),
    )
    qa = load_today_cards(db, default_config(), company_id=cid)[0]
    assert qa.source_url == GTR
    data = json.loads(TodayCard(
        company_id=cid,
        name=name,
        source_url=GTR,
        source_key="innovate_uk",
        source_evidence=({
            "receipt_kind": "stored_signal_and_citation_lookup",
            "citation_url": BOOK_WITH_REF,
            "source_url": BOOK_WITH_REF,
            "source_workbook_url": WORKBOOK,
        },),
    ).blob())
    assert data["source_url"] == GTR
    assert data["source_evidence"][0]["citation_url"] == GTR
    assert data["source_evidence"][0]["source_url"] == GTR
    assert data["source_evidence"][0]["source_workbook_url"] == WORKBOOK
