"""09-test-plan §2.5 — qualification (06-scoring §3).

~60,000 companies incorporate in the UK every month. Most are dormant,
holding vehicles or one-person consultancies, so a registry-sourced company
enters scoring only once it has at least one qualifying signal. Companies with
none are **not rejected** — they stay in the candidate pool at `qualified = 0`
and are re-checked on every run, because a company incorporated today may file
an SH01 next month.
"""

from __future__ import annotations

import pytest

from radar.config.defaults import default_config
from radar.pipeline import score_company
from radar.score.qualify import QUALIFIER_KINDS
from radar.store.db import now_iso

from tests.factories import registry_company, score_all, store_company


@pytest.fixture
def cfg():
    return default_config()


def test_bare_registry_company_is_not_scored(db, cfg):
    """A SIC code alone is not a reason to spend Aryan's morning."""
    c = registry_company(qualifiers=[], discovery_route="registry")
    assert score_all(c, cfg) == {}
    # and the persistence path writes nothing and marks it unqualified
    cid = store_company(db, c)
    assert score_company(db, cid, cfg) == 0
    assert db.scalar("SELECT qualified FROM company WHERE id = ?", (cid,)) == 0


@pytest.mark.parametrize("q", ["share_issue", "grant", "spinout", "press",
                               "repeat_founder", "website"])
def test_any_single_qualifier_admits_to_scoring(db, cfg, q):
    c = registry_company(qualifiers=[q], discovery_route="registry")
    assert score_all(c, cfg) != []
    cid = store_company(db, c)
    assert score_company(db, cid, cfg) > 0


def test_unqualified_company_is_rechecked_not_rejected(db, cfg):
    """A company incorporated today may file an SH01 next month."""
    c = registry_company(qualifiers=[])
    cid = store_company(db, c)
    assert score_company(db, cid, cfg) == 0
    assert db.scalar("SELECT qualified FROM company WHERE id = ?", (cid,)) == 0

    # next month it files an SH01 — the signal appears on the register
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?)""",
        (cid, "share_issue", "SH01 share allotment filed", "companies_house",
         "https://find-and-update.company-information.service.gov.uk/company/15021884",
         now_iso()),
    )
    assert score_company(db, cid, cfg) > 0
    assert db.scalar("SELECT qualified FROM company WHERE id = ?", (cid,)) == 1
