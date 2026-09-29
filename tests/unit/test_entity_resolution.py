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
from radar.resolve.merge import (
    duplicate_audit,
    merge_companies,
    unmerge,
    upsert_record,
)
from radar.resolve.review import enqueue_review, resolve_review
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
    # Similar corporate-role names are ambiguous parent/investor relationships,
    # not safe fuzzy merges.
    ("Acme Holdings", "Acme Holding", REVIEW, {}, {}),
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


def test_review_queue_is_idempotent_with_a_deterministic_pair_key(db):
    """Re-finding the same pair must not grow the queue (05-pipeline §4.2), and
    the queue key is a deterministic function of the *unordered* pair: the same
    pair presented either way round resolves to the same key via one `_meta`
    lookup, never a scan of the queue."""
    a = upsert_record(db, Record(name="Acme Robotics"), source_key="t",
                      source_url="https://a", external_id="a")
    b = upsert_record(db, Record(name="Acme Robotic Arms"), source_key="t",
                      source_url="https://b", external_id="b")
    assert a.action == "created"
    assert b.action == "review" and b.review_key is not None

    # Same pair, both orders -> the same key, and the queue still has one entry.
    assert enqueue_review(db, a.company_id, b.company_id, b.match) == b.review_key
    assert enqueue_review(db, b.company_id, a.company_id, b.match) == b.review_key
    assert db.scalar("SELECT COUNT(*) FROM _meta WHERE key LIKE 'review:%'") == 1

    # A different pair gets a different key.
    h = upsert_record(db, Record(name="Acme Holdings"), source_key="t",
                      source_url="https://h", external_id="h")
    g = upsert_record(db, Record(name="Acme Holding"), source_key="t",
                      source_url="https://g", external_id="g")
    assert h.action == "created"
    assert g.action == "review"
    assert g.review_key != b.review_key
    assert db.scalar("SELECT COUNT(*) FROM _meta WHERE key LIKE 'review:%'") == 2

    # Dismissing one pair removes exactly its entry.
    resolve_review(db, b.review_key, merge=False)
    assert db.scalar("SELECT COUNT(*) FROM _meta WHERE key LIKE 'review:%'") == 1


def test_duplicate_audit_returns_zero_rows(db):
    """03-data-model §6 query 9, gated by 10-build-plan Phase 2.

    The query was written and never run, which is the worst state for an audit
    to be in: it looks like a guarantee and checks nothing. Note the second
    half — asserting "no duplicates" against a database that could not contain
    any is a vacuous pass, so this plants one and proves the query sees it
    before proving a real ingest leaves none.
    """
    # A realistic ingest: the same company arriving from three sources under
    # three spellings, plus an unrelated company that must not be swept in.
    for name, source in (("Acme Robotics Ltd", "uktn"),
                         ("Acme Robotics Limited", "businesscloud"),
                         ("ACME ROBOTICS", "companies_house"),
                         ("Beta Analytics", "uktn")):
        upsert_record(db, Record(name=name), source_key=source,
                      source_url=f"https://{source}/x", external_id=name)

    assert duplicate_audit(db) == [], "a normal ingest left duplicate rows behind"

    # Now plant one the resolver could never create, and confirm the query is
    # actually looking: two live companies sharing a norm_key and country.
    live = db.one("SELECT norm_key, country_iso2 FROM company "
                  "WHERE merged_into IS NULL LIMIT 1")
    stamp = now_iso()
    db.execute(
        "INSERT INTO company(id, canonical_name, norm_key, country_iso2, "
        "                    first_seen, last_seen, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("planted", "Planted Duplicate", live["norm_key"], live["country_iso2"],
         stamp, stamp, stamp, stamp),
    )
    planted = duplicate_audit(db)
    assert planted, "query 9 did not notice a duplicate — the audit is blind"
    assert planted[0]["norm_key"] == live["norm_key"]
    assert planted[0]["c"] == 2

    # ...and a merge clears it, which is what the query exists to confirm.
    survivor = db.scalar("SELECT id FROM company WHERE merged_into IS NULL "
                         "AND norm_key = ? AND id != 'planted'", (live["norm_key"],))
    merge_companies(db, survivor, "planted", rule="test", score=99.0)
    assert duplicate_audit(db) == []


# ------------------------- H-08: review and QA decisions must survive a merge

DAY = "2026-09-30"
DECISION_TABLES = ("user_field", "daily_review", "today_check")


def _pair(db):
    a = upsert_record(db, Record(name="Acme Robotics"), source_key="t",
                      source_url="https://a", external_id="a")
    b = upsert_record(db, Record(name="Beta Analytics"), source_key="t",
                      source_url="https://b", external_id="b")
    return a.company_id, b.company_id


def _verdict(db, cid, verdict, at, field="verdict"):
    db.execute("INSERT INTO user_field(company_id, field, value, updated_at) "
               "VALUES (?,?,?,?)", (cid, field, verdict, at))


def _review(db, cid, verdict, at, day=DAY):
    db.execute("INSERT INTO daily_review(company_id, review_date, verdict, reviewed_at) "
               "VALUES (?,?,?,?)", (cid, day, verdict, at))


def _qa(db, cid, verdict, at, snapshot="snap"):
    db.execute(
        "INSERT INTO today_check(company_id, snapshot_hash, verdict, reason, summary, "
        "checker, prompt_version, raw_text, checked_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, f"{cid}:{snapshot}", verdict,
         "already_backed" if verdict == "reject" else None, "", "hermes", "t", None, at))


def _decisions(db) -> dict[str, list[dict]]:
    return {
        table: sorted((dict(r) for r in db.query(f"SELECT * FROM {table}")),
                      key=lambda r: repr(sorted(r.items())))
        for table in DECISION_TABLES
    }


def test_merge_moves_todays_review_and_qa_reject_to_the_winner(db):
    """A company rejected today, then merged into another, must stay rejected:
    the winner used to inherit no review marker and no QA veto."""
    from radar.qa.today import is_rejected

    a, b = _pair(db)
    _verdict(db, b, "not for me", "2026-09-30T10:00:00Z")
    _review(db, b, "not for me", "2026-09-30T10:00:00Z")
    _qa(db, b, "reject", "2026-09-30T09:00:00Z")
    assert not is_rejected(db, a)

    merge_companies(db, a, b, rule="test", score=96.0)

    for table in DECISION_TABLES:
        assert db.scalar(f"SELECT COUNT(*) FROM {table} WHERE company_id = ?", (b,)) == 0, \
            f"{table} rows were left on the merged-away company"
    review = db.one("SELECT verdict FROM daily_review WHERE company_id = ? AND review_date = ?",
                    (a, DAY))
    assert review is not None and review["verdict"] == "not for me"
    assert is_rejected(db, a), "the QA reject was lost in the merge"
    assert db.scalar("SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
                     (a,)) == "not for me"


@pytest.mark.parametrize("loser_is_newer", [True, False])
def test_merge_keeps_the_newest_decision_when_both_sides_decided(db, loser_is_newer):
    """Same day, same field: the newest decision wins, whichever side made it."""
    a, b = _pair(db)
    early, late = "2026-09-30T09:00:00Z", "2026-09-30T11:00:00Z"
    winner_at, loser_at = (early, late) if loser_is_newer else (late, early)
    _verdict(db, a, "unsure", winner_at)
    _verdict(db, b, "not for me", loser_at)
    _verdict(db, a, "call back Monday", winner_at, field="notes")
    _verdict(db, b, "duplicate of Acme", loser_at, field="notes")
    _review(db, a, "unsure", winner_at)
    _review(db, b, "not for me", loser_at)

    merge_companies(db, a, b, rule="test", score=96.0)

    want = "not for me" if loser_is_newer else "unsure"
    assert db.scalar("SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
                     (a,)) == want
    assert db.scalar("SELECT value FROM user_field WHERE company_id = ? AND field = 'notes'",
                     (a,)) == ("duplicate of Acme" if loser_is_newer else "call back Monday")
    rows = db.query("SELECT verdict FROM daily_review WHERE company_id = ? AND review_date = ?",
                    (a, DAY))
    assert [r["verdict"] for r in rows] == [want]
    assert db.scalar("SELECT COUNT(*) FROM user_field WHERE company_id = ?", (b,)) == 0


@pytest.mark.parametrize("loser_reject_is_newer,rejected", [(True, True), (False, False)])
def test_merge_latest_qa_check_decides_across_both_histories(db, loser_reject_is_newer, rejected):
    from radar.qa.today import is_rejected

    a, b = _pair(db)
    if loser_reject_is_newer:
        _qa(db, a, "pass", "2026-09-30T08:00:00Z")
        _qa(db, b, "reject", "2026-09-30T09:00:00Z")
    else:
        _qa(db, b, "reject", "2026-09-30T08:00:00Z")
        _qa(db, a, "pass", "2026-09-30T09:00:00Z")

    merge_companies(db, a, b, rule="test", score=96.0)

    assert is_rejected(db, a) is rejected
    assert db.scalar("SELECT COUNT(*) FROM today_check WHERE company_id = ?", (a,)) == 2


def test_merge_compares_mixed_timestamp_formats_as_instants(db, monkeypatch):
    """The web UI stamps local wall-clock time, the sheet sync stamps UTC with a
    `Z`. Compared as strings, 10:10 (BST) beats 09:30Z although it is 09:10Z."""
    import time

    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    try:
        a, b = _pair(db)
        _verdict(db, a, "worth contacting", "2026-09-30T09:30:00Z")   # later instant
        _verdict(db, b, "not for me", "2026-09-30T10:10:00")           # 09:10Z
        merge_companies(db, a, b, rule="test", score=96.0)
        assert db.scalar(
            "SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
            (a,)) == "worth contacting"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_unmerge_restores_reviews_qa_checks_and_verdicts_exactly(db):
    a, b = _pair(db)
    # collisions where each side is newer, plus rows that simply move
    _verdict(db, a, "unsure", "2026-09-30T09:00:00Z")
    _verdict(db, b, "not for me", "2026-09-30T11:00:00Z")
    _verdict(db, a, "winner note", "2026-09-30T12:00:00Z", field="notes")
    _verdict(db, b, "loser note", "2026-09-30T08:00:00Z", field="notes")
    _verdict(db, b, "yes", "2026-09-30T08:00:00Z", field="contacted")
    _review(db, a, "unsure", "2026-09-30T09:00:00Z")
    _review(db, b, "not for me", "2026-09-30T11:00:00Z")
    _review(db, b, "unsure", "2026-09-29T11:00:00Z", day="2026-09-29")
    _qa(db, a, "pass", "2026-09-30T08:00:00Z", snapshot="one")
    _qa(db, b, "reject", "2026-09-30T09:00:00Z", snapshot="two")
    _qa(db, b, "pass", "2026-09-29T09:00:00Z", snapshot="three")
    before = _decisions(db)

    event = merge_companies(db, a, b, rule="test", score=96.0)
    merged = _decisions(db)
    assert merged != before
    assert all(row["company_id"] == a
               for table in DECISION_TABLES for row in merged[table]), \
        "some decision rows were left on the loser"

    unmerge(db, event)

    assert _decisions(db) == before


def test_merge_and_unmerge_preserve_score_snapshots(db):
    from tests.factories import C, store_company
    winner = store_company(db, C(canonical_name="Winner", norm_key="winner"))
    loser = store_company(db, C(canonical_name="Loser", norm_key="loser"))
    db.execute("CREATE TABLE IF NOT EXISTS score_snapshot(company_id TEXT, fund_key TEXT, "
               "snapshot_date TEXT, config_hash TEXT, fund_fit_pct REAL, coverage REAL, "
               "discovery_edge REAL, priority REAL, tier TEXT, scored_at TEXT, "
               "PRIMARY KEY(company_id, fund_key, snapshot_date))")
    db.execute("INSERT INTO score_snapshot(company_id, fund_key, snapshot_date, config_hash, "
               "fund_fit_pct, coverage, discovery_edge, priority, tier, scored_at) "
               "VALUES (?, 'northstar', '2026-08-03', 'h', 80, 1, 80, 80, 'shortlist', "
               "'2026-08-03T01:00:00Z')", (loser,))
    before = [tuple(row) for row in db.query("SELECT * FROM score_snapshot")]
    event = merge_companies(db, winner, loser, rule="test")
    assert db.scalar("SELECT company_id FROM score_snapshot") == winner
    unmerge(db, event)
    assert [tuple(row) for row in db.query("SELECT * FROM score_snapshot")] == before
