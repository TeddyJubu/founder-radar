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


# --------------------- H-09: erasure must reach every stored reference, too

import json
import re

NAME_RE = re.compile(r"jane[\W_]+smith|smith[\W_]+jane", re.IGNORECASE)


def _residue(db, pattern=NAME_RE) -> list[str]:
    """Every `table.column` whose stored text still mentions the person.

    Scans every column of every table (JSON documents are decoded first, so a
    `\\u00eb` escape cannot hide a name). The suppression key is the one thing
    that must keep the folded name: it is what stops re-ingestion.
    """
    tables = [r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    found: list[str] = []
    for table in tables:
        if table == "suppression":
            continue
        for row in db.query(f"SELECT * FROM {table}"):
            for col in row.keys():
                value = row[col]
                if not isinstance(value, str):
                    continue
                texts = [value]
                if value[:1] in "{[\"":
                    try:
                        texts.append(json.dumps(json.loads(value), ensure_ascii=False))
                    except ValueError:
                        pass
                if any(pattern.search(t) for t in texts):
                    found.append(f"{table}.{col}")
    return sorted(set(found))


def _seed_person_everywhere(db):
    """One company that mentions Jane Smith in every place the pipeline writes text,
    plus a bystander who must not be touched."""
    from radar.resolve.match import Record
    from radar.resolve.merge import merge_companies, upsert_record

    stamp = now_iso()
    a = upsert_record(db, Record(name="Acme Robotics"), source_key="t",
                      source_url="https://example.com/acme", external_id="a").company_id
    b = upsert_record(db, Record(name="Beta Analytics"), source_key="t",
                      source_url="https://example.com/beta", external_id="b").company_id

    for cid in (a, b):
        insert_founder(db, cid, name="Jane Smith", source_url="https://example.com/x",
                       role="director")
    insert_founder(db, a, name="Bob Jones", source_url="https://example.com/x")

    db.execute("UPDATE company SET one_liner = ? WHERE id = ?",
               ("Founded by Jane Smith, builds warehouse robots", a))
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, detail, source_key, source_url,
                              first_seen) VALUES (?,?,?,?,?,?,?)""",
        (a, "news_mention", "Jane Smith launches Acme Robotics",
         "An interview with Jane Smith about the round", "t",
         "https://example.com/news/1", stamp))
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?)""",
        (a, "grant_award", "Acme wins Innovate UK grant", "t",
         "https://example.com/news/2", stamp))
    for field, value in (("founders", ["Jane Smith", "Bob Jones"]),
                         ("one_liner", {"text": "Jane Smith's robotics start-up"}),
                         ("sector", "robotics")):
        db.add_observation(a, field, value, source_key="t", source_type="news")
    db.execute("INSERT INTO user_field(company_id, field, value, updated_at) VALUES (?,?,?,?)",
               (a, "notes", "Ask jane smith about the pilot", stamp))
    db.execute("INSERT INTO user_field(company_id, field, value, updated_at) VALUES (?,?,?,?)",
               (a, "verdict", "unsure", stamp))
    db.execute(
        """INSERT INTO today_check(company_id, snapshot_hash, verdict, reason, summary,
                                   checker, prompt_version, raw_text, checked_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (a, "h1", "pass", None, "Headline names Jane Smith", "hermes", "t",
         "PASS - Jane Smith is the founder", stamp))
    for key, payload in (("k1", {"founders": ["Jane Smith"], "company_name": "Acme"}),
                         ("k2", {"founders": ["Somebody Else"], "company_name": "Beta"})):
        db.execute("INSERT INTO llm_cache(key, response_json, created_at) VALUES (?,?,?)",
                   (key, json.dumps(payload), stamp))
    db.execute("INSERT INTO quarantine(source_key, source_url, raw_json, error, created_at) "
               "VALUES (?,?,?,?,?)",
               ("t", "https://example.com/q", json.dumps({"title": "Jane Smith raises"}),
                "bad row for Jane Smith", stamp))
    db.execute("INSERT INTO sheet_row_state(company_id, tab, col, last_value) VALUES (?,?,?,?)",
               (a, "Companies", "F", "Jane Smith; Bob Jones"))
    db.execute("INSERT INTO sheet_row_state(company_id, tab, col, last_value) VALUES (?,?,?,?)",
               (a, "Companies", "B", "Acme Robotics"))

    # A merge that had to drop a duplicate founder row: the evidence keeps that row
    # so that `unmerge` can restore it, name included.
    merge_id = merge_companies(db, a, b, rule="test", score=96.0)
    return a, b, merge_id


def test_forget_leaves_no_stored_text_that_names_the_person(db):
    """H-09: after `forget "Jane Smith"` no table's text may still say who she is
    (the suppression key excepted). The old code deleted founders first and then
    used them to find observations, so that delete matched nothing, and it never
    looked at signal headlines, one-liners, QA text, caches or merge evidence."""
    a, b, merge_id = _seed_person_everywhere(db)
    assert _residue(db), "the seed data must actually mention her"

    receipt = forget_person(db, "Jane Smith")

    assert _residue(db) == []
    assert set(receipt) == {"name", "norm_name", "founders_deleted",
                            "companies_affected", "suppressed"}
    assert receipt["founders_deleted"] == 1            # b's row went in the merge
    assert a in receipt["companies_affected"]
    assert receipt["suppressed"] is True
    assert is_suppressed(db, "Jane Smith")


def test_forget_redacts_the_card_text_but_keeps_the_evidence(db):
    """The Today card shows `signal.headline` and `company.one_liner`; both keep
    their row (so the card still explains itself) with the name taken out."""
    a, b, merge_id = _seed_person_everywhere(db)
    forget_person(db, "Jane Smith")

    headlines = {r["kind"]: r["headline"] for r in db.query(
        "SELECT kind, headline FROM signal WHERE company_id = ?", (a,))}
    assert "Jane" not in headlines["news_mention"]
    assert "launches Acme Robotics" in headlines["news_mention"]
    assert headlines["grant_award"] == "Acme wins Innovate UK grant"
    assert "Jane" not in db.scalar("SELECT one_liner FROM company WHERE id = ?", (a,))
    assert "warehouse robots" in db.scalar("SELECT one_liner FROM company WHERE id = ?", (a,))

    # bystanders are untouched
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name = 'Bob Jones'") == 1
    assert db.scalar("SELECT COUNT(*) FROM llm_cache WHERE key = 'k2'") == 1
    assert db.scalar("SELECT COUNT(*) FROM llm_cache WHERE key = 'k1'") == 0
    assert db.scalar("SELECT value FROM user_field WHERE company_id = ? AND field = 'verdict'",
                     (a,)) == "unsure"
    assert db.scalar("SELECT COUNT(*) FROM observation WHERE company_id = ? AND field = 'sector'",
                     (a,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM sheet_row_state WHERE col = 'B'") == 1


def test_unmerge_after_forget_cannot_bring_the_person_back(db):
    """Merge evidence stores the founder row it dropped. Restoring it after an
    erasure would re-create the record."""
    from radar.resolve.merge import unmerge

    a, b, merge_id = _seed_person_everywhere(db)
    forget_person(db, "Jane Smith")
    unmerge(db, merge_id)

    assert db.scalar("SELECT COUNT(*) FROM founder WHERE norm_name LIKE '%smith%'") == 0
    assert _residue(db) == []


def test_forget_matches_the_registry_spelling_and_accents(db, company):
    """Companies House stores an accent-folded `norm_name` and `Surname, Forename`
    order in its raw feed, so the person must be findable under those forms."""
    from radar.enrich.ch_officers import Founder, norm_person as ch_norm, store_founders

    person = "Zoë Smith"
    store_founders(db, company, [Founder(name=person, norm_name=ch_norm(person),
                                         role="director", source_url="https://ch/x")],
                   source_url="https://ch/x")
    db.add_observation(company, "officers", [{"name": "SMITH, Zoë"}],
                       source_key="companies_house", source_type="registry")
    db.execute("INSERT INTO llm_cache(key, response_json, created_at) VALUES (?,?,?)",
               ("kz", json.dumps({"founders": [person]}), now_iso()))     # ë-escaped
    assert db.scalar("SELECT COUNT(*) FROM founder") == 1

    receipt = forget_person(db, person)

    assert receipt["founders_deleted"] == 1
    assert db.scalar("SELECT COUNT(*) FROM founder") == 0
    assert db.scalar("SELECT COUNT(*) FROM llm_cache") == 0
    assert db.scalar("SELECT COUNT(*) FROM observation WHERE field = 'officers'") == 0


def test_registry_ingest_honours_the_suppression_list(db, company):
    """`store_founders` is how founders really arrive (Companies House
    enrichment). It never asked the suppression list, so the next enrichment
    put an erased person straight back."""
    from radar.enrich.ch_officers import Founder, norm_person as ch_norm, store_founders

    forget_person(db, "Jane Smith")
    written = store_founders(
        db, company,
        [Founder(name="Jane Smith", norm_name=ch_norm("Jane Smith"), role="director",
                 source_url="https://ch/x"),
         Founder(name="Bob Jones", norm_name=ch_norm("Bob Jones"), role="director",
                 source_url="https://ch/x")],
        source_url="https://ch/x")

    assert written == 1
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name = 'Jane Smith'") == 0
    assert db.scalar("SELECT COUNT(*) FROM founder WHERE name = 'Bob Jones'") == 1


def test_forgetting_someone_with_no_founder_row_still_scrubs_headlines(db, company):
    """She may only ever have been named in an article, never as an officer."""
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?)""",
        (company, "news_mention", "Jane Smith raises pre-seed", "t",
         "https://example.com/n", now_iso()))
    receipt = forget_person(db, "Jane Smith")
    assert receipt["founders_deleted"] == 0
    assert _residue(db) == []
    assert company in receipt["companies_affected"]


def test_forget_does_not_touch_a_longer_name_that_merely_starts_the_same(db, company):
    db.execute(
        """INSERT INTO signal(company_id, kind, headline, source_key, source_url, first_seen)
           VALUES (?,?,?,?,?,?)""",
        (company, "news_mention", "Jane Smithson joins Acme", "t",
         "https://example.com/n", now_iso()))
    forget_person(db, "Jane Smith")
    assert db.scalar("SELECT headline FROM signal WHERE company_id = ?", (company,)) \
        == "Jane Smithson joins Acme"


def test_ch_norm_person_and_the_privacy_fold_agree_on_registry_names():
    """`forget` looks founders up by both folds; keep the copy of the registry's
    accent fold in `radar.privacy` honest."""
    from radar.enrich.ch_officers import norm_person as ch_norm
    from radar.privacy import norm_person_folded

    for name in ("Zoë Smith", "SMITH, Zoë", "Jane O'Brien", "  Renée  Dupont-Martin "):
        assert norm_person_folded(name) == ch_norm(name)


def test_suppression_matches_accented_and_plain_registry_spellings(db):
    from radar.privacy import suppress, is_suppressed
    suppress(db, "Zoë Smith")
    assert is_suppressed(db, "Zoe Smith")
    assert is_suppressed(db, "ZOË SMITH")
