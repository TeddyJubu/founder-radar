"""Signals — the bridge between an adapter's `kind_hint` and everything
downstream that reads `signal.kind`.

This file exists because that bridge was missing. `_record_item` wrote a row
only for `incorporation`, and `_route_of` matched on `"grant"`/`"cohort"`,
which no adapter has ever emitted. The consequences were quiet and expensive:

* a Track B company could never earn its `grant` qualifier (06-scoring §3), so
  a newly incorporated company that won an Innovate UK award was never scored;
* every grant and cohort source was filed as `discovery_route="news"`, worth
  0.2 on the Discovery Edge route component instead of 0.6;
* `derive_traction_signal` maps `grant_award → clinical_grant_validation`, so
  the company also lost a scored attribute and some coverage with it.

None of it raised. The tests below pin the vocabulary at both ends, because a
string mismatch between two closed vocabularies is exactly the kind of bug
that type checkers and green suites both sail straight past.
"""

from __future__ import annotations

from datetime import date

import pytest

from radar.pipeline import SIGNAL_KINDS, resolve_item
from radar.sources.base import RawItem

# 03-data-model §3. Transcribed from the spec table, deliberately not imported
# from the code — a test that imports the thing it checks agrees with itself.
CANONICAL_KINDS = {
    "incorporation", "share_issue", "grant_award", "spinout",
    "accelerator_cohort", "competition_win", "funding_round",
    "product_launch", "news_mention",
}


def _item(kind_hint, *, url="https://x.test/1", external_id="e1",
          structured=None, title="Acme Robotics raises pre-seed"):
    return RawItem(
        source_key="probe", source_url=url, external_id=external_id,
        published_at=date(2026, 8, 1), title=title,
        structured=structured or {"company_name": "Acme Robotics Ltd"},
        kind_hint=kind_hint,
    )


def test_signal_kinds_match_the_data_model_vocabulary():
    """The closed vocabulary is a contract with 03-data-model §3."""
    assert set(SIGNAL_KINDS) == CANONICAL_KINDS


@pytest.mark.parametrize("kind", sorted(CANONICAL_KINDS))
def test_every_kind_hint_becomes_a_signal_row(db, config, kind):
    """Whatever an adapter emits must survive into the signal table. Only
    `incorporation` used to."""
    resolve_item(db, _item(kind), config)
    kinds = [r["kind"] for r in db.query("SELECT kind FROM signal")]
    assert kinds == [kind]


@pytest.mark.parametrize("kind_hint,expected_route", [
    ("grant_award", "grant"),               # was silently "news"
    ("accelerator_cohort", "accelerator"),  # was silently "news"
    ("spinout", "spinout"),
    ("news_mention", "news"),
    ("funding_round", "news"),
    (None, "news"),
])
def test_discovery_route_matches_the_adapter_vocabulary(db, config, kind_hint,
                                                        expected_route):
    """`discovery_route` is a scored input (Discovery Edge, 20 points), so a
    route that quietly defaults to `news` costs real score."""
    cid = resolve_item(db, _item(kind_hint), config)
    row = db.one("SELECT discovery_route FROM company WHERE id = ?", (cid,))
    assert row["discovery_route"] == expected_route


def test_a_company_number_still_wins_the_route(db, config):
    """Register provenance outranks any kind_hint — Track B is Track B."""
    cid = resolve_item(db, _item("grant_award", structured={
        "company_name": "Acme Robotics Ltd", "company_number": "15000001"}), config)
    assert db.one("SELECT discovery_route FROM company WHERE id = ?",
                  (cid,))["discovery_route"] == "registry"


def test_press_count_counts_articles_and_nothing_else(db, config):
    """06-scoring §6 scores *articles in tracked sources*. A TTO listing and a
    cohort page are directories, not coverage — and they already earn their
    visibility through `discovery_route`."""
    for i, kind in enumerate(["news_mention", "funding_round", "product_launch"]):
        resolve_item(db, _item(kind, url=f"https://x.test/a{i}",
                               external_id=f"a{i}"), config)
    assert db.scalar("SELECT news_mention_count FROM company") == 3

    for i, kind in enumerate(["spinout", "accelerator_cohort", "incorporation"]):
        resolve_item(db, _item(kind, url=f"https://x.test/b{i}",
                               external_id=f"b{i}"), config)
    assert db.scalar("SELECT news_mention_count FROM company") == 3, \
        "directory listings must not inflate press coverage"


def test_press_count_is_never_a_constant(db, config):
    """FR-5.2's own argument: a value every company shares is a constant, not
    a signal. `news_mention_count` was previously always 0, which made 30 of
    the 100 Discovery Edge points a no-op for every company alive."""
    quiet = resolve_item(db, _item("incorporation", url="https://x.test/q",
                                   external_id="q1"), config)
    loud = resolve_item(db, _item("news_mention", url="https://x.test/l",
                                  external_id="l1", title="Beta Industries raises",
                                  structured={"company_name": "Beta Industries Ltd"}),
                        config)
    assert quiet != loud, "the two fixtures must be distinct companies"
    counts = {db.one("SELECT news_mention_count c FROM company WHERE id=?",
                     (cid,))["c"] for cid in (quiet, loud)}
    assert counts == {0, 1}


def test_recording_the_same_signal_twice_is_a_no_op(db, config):
    """Re-runs are idempotent (09-test-plan §6). The count is recomputed
    rather than incremented for exactly this reason."""
    for _ in range(3):
        resolve_item(db, _item("news_mention"), config)
    assert db.scalar("SELECT COUNT(*) FROM signal") == 1
    assert db.scalar("SELECT news_mention_count FROM company") == 1


def test_a_signal_lands_on_a_company_first_seen_elsewhere(db, config):
    """The signal write is not gated on `created`. A company already in the
    database earns new signals as they arrive — which is the whole mechanism
    behind `test_unqualified_company_is_rechecked_not_rejected`."""
    structured = {"company_name": "Acme Robotics Ltd", "company_number": "15000001"}
    first = resolve_item(db, _item("incorporation", structured=structured), config)
    later = resolve_item(db, _item("grant_award", url="https://gtr.test/a1",
                                   external_id="gtr-a1", structured=structured),
                         config)
    assert later == first, "the grant must attach to the existing company"
    assert {r["kind"] for r in db.query("SELECT kind FROM signal")} == {
        "incorporation", "grant_award"}


def test_registry_company_earns_the_grant_qualifier(db, config):
    """The load-bearing case, end to end.

    A bare register sweep is correctly unqualified and stays in the candidate
    pool. When the award lands, `grant` appears and the company becomes
    scoreable. Before the fix this transition was unreachable, so no company
    was ever qualified by a grant.
    """
    from radar.pipeline import company_from_row
    from radar.score.qualify import derive_qualifiers, is_qualified

    structured = {"company_name": "Acme Robotics Ltd", "company_number": "15000001",
                  "date_of_creation": "2026-05-01", "sic_codes": ["62012"],
                  "postal_code": "NE1 4ST"}
    resolve_item(db, _item("incorporation", structured=structured), config)

    company = company_from_row(db, db.query("SELECT * FROM company")[0], config)
    assert derive_qualifiers(company) == []
    assert is_qualified(company, config) is False

    resolve_item(db, _item("grant_award", url="https://gtr.test/a1",
                           external_id="gtr-a1",
                           structured={**structured, "grant_amount_gbp": 250_000}),
                 config)

    company = company_from_row(db, db.query("SELECT * FROM company")[0], config)
    assert "grant" in derive_qualifiers(company)
    assert is_qualified(company, config) is True


def test_grant_amount_is_recorded_on_the_signal(db, config):
    """The award value is evidence; the sheet's "why" sentence can cite it."""
    resolve_item(db, _item("grant_award", structured={
        "company_name": "Acme Robotics Ltd", "grant_amount_gbp": 250_000}), config)
    assert db.scalar("SELECT amount_gbp FROM signal") == 250_000


def test_grant_signal_derives_the_traction_attribute(db, config):
    """`derive_traction_signal` reads signal kinds. With no row written, a
    grant-winning company silently lost one of its five scored attributes."""
    from radar.score.derive import Company, Signal, derive_attributes

    base = dict(id="01", canonical_name="Acme Robotics", norm_key="acmerobotics",
                country_iso2="GB", incorporated_on="2026-05-01",
                sic_codes=["62012"], hq_postcode="NE1 4ST",
                discovery_route="registry")
    grant = Signal(kind="grant_award", occurred_on="2026-07-01",
                   headline="award", source_key="ukri_gtr")

    assert derive_attributes(Company(**base, signals=[]), config).traction_signal is None
    assert derive_attributes(Company(**base, signals=[grant]),
                             config).traction_signal == "clinical_grant_validation"


# ----------------------------------------------------------- source tracks


def test_every_source_declares_the_track_the_ledger_gives_it():
    """04-sources §2. The `Track` column is how Aryan reads where his edge
    comes from, and it is rendered straight onto the Sources tab.

    Companies House defaulted to "A" because it never declared a track, which
    labelled the register — the entire basis of version 2 — as just another
    signal-first source. Transcribed from the ledger, not imported.
    """
    from radar.sources import REGISTRY

    ledger = {
        "companies_house": "B",       # rows 1-3, registry-first
        "oxford_innovation": "A",
        "northern_accelerator": "A",
        "cambridge_enterprise": "A",
        "conception_x": "A",
        "entrepreneur_first": "A",
        "zinc_vc": "—",               # inverted — investment posts are denylist
        "govuk_search": "A",
        "ukri_gtr": "A",
        "innovate_uk": "A",
        "businesscloud": "A",
        "uktn": "A",
        "vc_portfolios": "—",         # row 14, the inverted source
        # Tier 2 — "add after Tier 1 is proven". Every one is signal-first.
        "startups_magazine": "A",
        "bdaily_regional": "A",
        "edinburgh_innovations": "A",
        "ucl_ventures": "A",
        "bethnal_green": "A",
        "carbon13": "A",
        "converge": "A",
        "sheffield": "A",
        "founders_factory": "A",
        "techstars_london": "A",
    }
    assert set(REGISTRY) == set(ledger), "the registry and the ledger disagree"
    for key, expected in ledger.items():
        assert getattr(REGISTRY[key], "track", "A") == expected, key


def test_tier_2_sources_are_not_promoted_to_tier_1():
    """`TIER_1_SOURCES` gates the Monday live check and the Tier 1 fixture
    sweep. It used to be `tuple(SOURCE_MODULES)`, so adding a Tier 2 entry
    would have silently promoted it — and a source that 04-sources says is
    optional would start failing the checks reserved for the fourteen that
    have to work."""
    from radar.sources import ALL_SOURCES, TIER_1_SOURCES, TIER_2_SOURCES

    assert set(TIER_1_SOURCES) & set(TIER_2_SOURCES) == set()
    assert set(ALL_SOURCES) == set(TIER_1_SOURCES) | set(TIER_2_SOURCES)
    assert "companies_house" in TIER_1_SOURCES
    assert "bethnal_green" not in TIER_1_SOURCES
