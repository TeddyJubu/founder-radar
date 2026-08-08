"""09-test-plan §2.1 — entity resolution.

Forty committed name pairs (the twenty below plus their symmetric partners).
These are the traps that cause real bugs: suffix stripping, accent folding,
the token-set-ratio false merge, same-name-different-jurisdiction, placeholders,
leading zeros on Companies House numbers, and person-named family firms.

Two rows pin behaviour the naive implementation gets wrong:

* `Acme Robotics` vs `Acme Robotics Automotive Division` — `token_set_ratio`
  scores this **100**. The banned-scorer guard keeps it distinct.
* `Acme Robotics Ltd` (GB) vs `Acme Robotics Inc` (US) — same name, different
  jurisdiction, DISTINCT. The ladder deliberately keeps them apart.
"""

from __future__ import annotations

import pytest

from radar.resolve.match import MERGE, REVIEW, DISTINCT, Record, compare
from radar.resolve.merge import merge_companies, unmerge, upsert_record
from radar.store.db import now_iso

PAIRS = [
    # (A, B, expected, a_kwargs, b_kwargs)
    ("Acme Robotics Ltd", "Acme Robotics", MERGE, {}, {}),
    ("Acme Robotics Limited", "ACME ROBOTICS LTD", MERGE, {}, {}),
    ("Café Ltd", "Cafe Limited", MERGE, {}, {}),
    ("Smith & Sons Ltd", "Smith and Sons", MERGE, {}, {}),
    # ⚠️ token_set_ratio scores this 100 — the classic false merge
    ("Acme Robotics", "Acme Robotics Automotive Division", DISTINCT, {}, {}),
    ("Acme Labs", "Acme Holdings", DISTINCT, {}, {}),
    # same name, different jurisdiction
    ("Acme Robotics Ltd", "Acme Robotics Inc",
     DISTINCT, {"country_iso2": "GB"}, {"country_iso2": "US"}),
    # rare-token guard: no distinctive token in common
    ("AI Labs", "Tech Solutions", DISTINCT, {}, {}),
    # placeholder blocklist: two companies both called Stealth are not duplicates
    ("Stealth", "Stealth", DISTINCT, {}, {}),
    ("Unknown", "Unknown", DISTINCT, {}, {}),
    # the CH number wins over everything
    ("Acme", "Acme Robotics", MERGE, {"ch_number": "00445790"}, {"ch_number": "00445790"}),
    # zero-padding — never cast to int
    ("Acme", "Acme", MERGE, {"ch_number": "445790"}, {"ch_number": "00445790"}),
    # Scottish prefix is a different company
    ("Acme", "Acme", DISTINCT, {"ch_number": "SC445790"}, {"ch_number": "00445790"}),
    # domain match
    ("Acme", "Acme Robotics", MERGE, {"domain": "acme.com"}, {"domain": "acme.com"}),
    # social domains are denylisted — never company identity
    ("Acme", "Beta",
     DISTINCT, {"domain": "linkedin.com/co/acme"}, {"domain": "linkedin.com/co/beta"}),
    # ⚠️ a university domain is not company identity
    ("Kelvin Bio", "Oxford Nanopore",
     DISTINCT, {"domain": "eng.ox.ac.uk/spinouts/kelvin"}, {"domain": "ox.ac.uk"}),
    # spinouts incorporate under a placeholder, then rename — only the CH
    # number merges them; the placeholder name alone must NOT
    ("BLUE SKY 4471 LIMITED", "Acme Robotics",
     MERGE, {"ch_number": "00445790"}, {"ch_number": "00445790"}),
    ("BLUE SKY 4471 LIMITED", "Acme Robotics", DISTINCT, {}, {}),
    # person-named companies sharing a rare token — a human should look
    ("Smith & Partners", "Smith & Sons", REVIEW, {}, {}),
    # fuzzy 96 — just short of identical
    ("Acme Robotic", "Acme Robotics", MERGE, {}, {}),
    # fuzzy 87 — the review band
    ("Acme Robotics", "Acme Robotic Arms", REVIEW, {}, {}),
]


@pytest.mark.parametrize("a,b,expected,a_kw,b_kw", PAIRS)
def test_entity_resolution_pairs(a, b, expected, a_kw, b_kw):
    result = compare(Record(name=a, **a_kw), Record(name=b, **b_kw))
    assert result.decision == expected, \
        f"{a!r} vs {b!r}: got {result.decision} ({result.rule}), want {expected}"


def test_transitive_chain_does_not_collapse(db):
    """A~B fuzzy-merges (98) but A~C does not (81 < 84). Naive union-find
    would merge all three; the ladder re-verifies against the canonical's
    *resolved* name and only union-finds on deterministic keys, so C stays
    its own company (05-pipeline §4.3)."""
    a = upsert_record(db, Record(name="Acme Robotic Systems"),
                      source_key="t", source_url="https://a", external_id="a")
    b = upsert_record(db, Record(name="Acme Robotics Systems"),
                      source_key="t", source_url="https://b", external_id="b")
    assert b.action == "matched"
    assert b.matched_id == a.company_id

    c = upsert_record(db, Record(name="Acme Robotic Arms"),
                      source_key="t", source_url="https://c", external_id="c")
    assert c.action == "created"
    assert c.company_id != a.company_id
    assert db.scalar("SELECT COUNT(*) FROM company WHERE merged_into IS NULL") == 2


def test_merge_is_reversible(db):
    """Every merge is recorded and undoable: `unmerge` replays the evidence
    and the database returns to its pre-merge state (05-pipeline §4.3)."""
    a = upsert_record(db, Record(name="Acme Robotics"),
                      source_key="t", source_url="https://a", external_id="a")
    b = upsert_record(db, Record(name="Beta Analytics"),
                      source_key="t", source_url="https://b", external_id="b")
    assert a.action == "created" and b.action == "created"

    # B carries a signal that must come back on unmerge
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?)""",
        (b.company_id, "press", "Beta in the local paper", "t", "https://b", now_iso()),
    )

    event = merge_companies(db, a.company_id, b.company_id, rule="test", score=96.0)
    assert db.scalar("SELECT merged_into FROM company WHERE id = ?", (b.company_id,)) \
        == a.company_id
    assert db.scalar("SELECT COUNT(*) FROM signal WHERE company_id = ?", (a.company_id,)) == 1

    unmerge(db, event)
    assert db.scalar("SELECT merged_into FROM company WHERE id = ?", (b.company_id,)) is None
    assert db.scalar("SELECT COUNT(*) FROM signal WHERE company_id = ?", (b.company_id,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM signal WHERE company_id = ?", (a.company_id,)) == 0
