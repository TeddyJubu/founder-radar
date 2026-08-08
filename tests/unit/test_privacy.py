"""Erasure has to survive the next morning's run, or it isn't erasure."""

from __future__ import annotations

import pytest

from radar.privacy import (
    forget_person,
    insert_founder,
    is_suppressed,
    norm_person,
    purge_stale_founders,
)
from radar.store.db import now_iso


@pytest.fixture
def company(db):
    ts = now_iso()
    db.execute(
        """INSERT INTO company(id, canonical_name, norm_key, first_seen, last_seen,
                               created_at, updated_at)
           VALUES ('c1','Acme Robotics','acmerobotics',?,?,?,?)""",
        (ts, ts, ts, ts),
    )
    return "c1"


def _ingest(db, company_id, name="Jane Smith"):
    """Stand-in for the adapter path — the same call every adapter must use."""
    return insert_founder(
        db, company_id, name=name, source_url="https://example.com/article",
        role="Co-founder",
    )


def test_forget_removes_and_suppresses(db, company):
    _ingest(db, company)
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name='Jane Smith'") == 1

    receipt = forget_person(db, "Jane Smith")
    assert receipt["founders_deleted"] == 1
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name='Jane Smith'") == 0

    # Re-ingesting the same article must not resurrect her.
    assert _ingest(db, company) is False
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name='Jane Smith'") == 0


def test_suppression_matches_across_casing_and_punctuation(db, company):
    forget_person(db, "Jane Smith")
    assert is_suppressed(db, "jane  smith")
    assert is_suppressed(db, "JANE SMITH")
    assert is_suppressed(db, "Jane-Smith")
    assert not is_suppressed(db, "Jane Smythe")


def test_norm_person_is_stable():
    assert norm_person("  Dr. Ada  Lovelace ") == "dr ada lovelace"


def test_insert_founder_has_no_way_to_pass_personal_data():
    """The privacy guarantee is structural: the function has no parameter for a
    date of birth or an address, so an adapter author cannot forget to drop it."""
    import inspect

    params = set(inspect.signature(insert_founder).parameters)
    forbidden = {"date_of_birth", "dob", "dob_month", "dob_year", "address",
                 "postcode", "email", "phone", "nationality", "country_of_residence"}
    assert not (params & forbidden)


def test_purge_stale_founders_drops_long_rejected(db, company):
    _ingest(db, company)
    db.execute(
        """INSERT INTO score(company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
                             discovery_edge, priority, tier, explanation, config_hash,
                             scorer_version, scored_at)
           VALUES ('c1','northstar',NULL,10,0.9,10,10,'reject','too old','h1','1',
                   date('now','-18 month'))"""
    )
    assert purge_stale_founders(db, months=12) == 1
    assert db.scalar("SELECT COUNT(*) FROM founder") == 0
