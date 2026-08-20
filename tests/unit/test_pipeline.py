"""09-test-plan §6/§7 — chaos: proving nothing stops the run.

These encode the failure table from 02-architecture §7. The rules:

* **A broken source never stops the run.** `fetch_all` wraps each adapter
  individually, so one `ConnectionError` costs one red row on the Sources tab.
* **Every run writes a run-log row**, so a deployment that "looks fine" but
  silently never runs is visible (FR-9.2).
* **Re-running is idempotent** — no duplicate companies, no duplicate signals.
* **The digest goes out when Hermes is down** — the direct Bot API is the
  unconditional fallback, because a broken gateway must not silence the run.
"""

from __future__ import annotations

from datetime import date

import pytest

from radar.resolve.match import Record
from radar.resolve.merge import upsert_record


class FakeResponse:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload
        self.status = status
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status < 400

    def json(self):
        if self._payload is not None:
            return self._payload
        raise ValueError("no json payload")


class FakeHttp:
    """One `get()` for everything: 200 with an empty body, so every adapter
    sees a quiet source rather than a crash. An empty dict, not an empty
    list: Companies House reads `data.get("items")` off it."""

    def get(self, url, **kw):  # noqa: ARG002
        return FakeResponse(payload={})


def test_one_source_failure_does_not_stop_run(db, config, monkeypatch):
    """Kill one Tier 1 source; the other ten stay green and the run completes."""
    import radar.sources.uktn as uktn

    monkeypatch.setenv("CH_API_KEY", "test-key")   # a real deployment has one

    def boom(self, ctx):  # noqa: ARG002 - the adapter protocol
        raise ConnectionError("simulated outage")

    monkeypatch.setattr(type(uktn.ADAPTER), "fetch", boom)

    from radar.pipeline import run_pipeline

    result = run_pipeline(db, config=config, http=FakeHttp(), use_llm=False,
                          gateway=None, now=date(2026, 8, 8))
    assert result.status == "partial"
    uktn_row = next(s for s in result.sources if s["key"] == "uktn")
    assert uktn_row["status"] == "failed"
    assert "ConnectionError" in (uktn_row["error"] or "")
    # every OTHER source is still its own verdict — the failure never spreads
    other_failures = [s["key"] for s in result.sources
                      if s["status"] == "failed" and s["key"] != "uktn"]
    assert other_failures == []
    assert result.items_fetched >= 0                 # the run completed


def test_degraded_source_is_a_run_log_warning_not_a_failure(db, config, monkeypatch):
    """A 403 from a Tier 1 source is a run-log warning, not a failed run.

    The site is up, the crawler is not welcome — degraded, so the run still
    counts as successful (a block is not an outage and is usually fixable by
    allowlisting), but the warning is on the run row where the heartbeat and
    a human reading `status` can see it.
    """
    import radar.sources.uktn as uktn
    from radar.sources.base import SourceBlocked

    def blocked(self, ctx):  # noqa: ARG002 - the adapter protocol
        raise SourceBlocked(
            "uktn",
            "HTTP 403 from https://www.uktech.news/wp-json/wp/v2/posts/latest "
            "— the site is refusing us (possible anti-bot block)",
        )

    monkeypatch.setattr(type(uktn.ADAPTER), "fetch", blocked)

    from radar.pipeline import run_pipeline

    result = run_pipeline(db, config=config, http=FakeHttp(), use_llm=False,
                          gateway=None, now=date(2026, 8, 8))
    # The run-wide status is `partial` here only because the offline chaos
    # harness's empty responses trip `LayoutChanged` on other adapters — the
    # point is that uktn's block is its own `degraded` verdict, not `failed`.
    uktn_row = next(s for s in result.sources if s["key"] == "uktn")
    assert uktn_row["status"] == "degraded"
    assert any("uktn" in w and "degraded" in w for w in result.warnings)

    row = db.one("SELECT * FROM run ORDER BY id DESC LIMIT 1")
    assert "uktn" in (row["warnings"] or "")
    assert "degraded" in (row["warnings"] or "")

    src = db.one(
        "SELECT status FROM run_source WHERE source_key = 'uktn' "
        "ORDER BY run_id DESC LIMIT 1")
    assert src["status"] == "degraded"


def test_every_run_writes_a_run_log_row(db, config):
    """FR-9.2 — the row every deployment gets checked against."""
    from radar.pipeline import run_pipeline

    run_pipeline(db, config=config, http=FakeHttp(), use_llm=False, gateway=None)
    row = db.one("SELECT * FROM run ORDER BY id DESC LIMIT 1")
    for field in ("items_fetched", "companies_new", "gated_out", "shortlisted",
                  "llm_calls", "llm_cost_usd", "status", "finished_at"):
        assert row[field] is not None, field
    assert row["status"] in ("ok", "partial", "failed")


def test_extraction_method_reaches_the_company_row(db, config):
    """The sheet's confidence column reads `company.extraction_method`, so a
    prose extraction must persist it — `_fields_from_extraction` used to drop
    it, leaving every Track A company NULL (heuristic 0.3 confidence hidden).
    """
    from datetime import date as _date

    from radar.extract.schema import Extraction
    from radar.pipeline import resolve_item
    from radar.sources.base import RawItem

    record = Extraction.model_validate({
        "is_about_single_company": True,
        "company_name": "Acme Robotics Ltd",
        "company_website": "https://acme.example",
        "one_line_description": "Warehouse robots.",
        "sector": "industrial",
        "stage": "seed",
        "hq_city": "Sheffield",
        "hq_country_iso2": "GB",
        "founded_year": 2021,
        "founders": [],
        "amount_raised_gbp": None,
        "amount_original": None,
        "amount_currency": None,
        "grant_amount_gbp": None,
        "extraction_confidence": 0.9,
        "extraction_method": "llm",
        "needs_review": False,
    })
    item = RawItem(
        source_key="probe", source_url="https://x.test/1", external_id="e1",
        published_at=_date(2026, 8, 1), title="Acme Robotics raises pre-seed",
        structured={},
    )
    object.__setattr__(item, "extraction", record)

    cid = resolve_item(db, item, config)
    row = db.one("SELECT extraction_method, one_liner FROM company WHERE id = ?", (cid,))
    assert row["extraction_method"] == "llm"
    # The client, 11 Aug: "Ideally I'd like the output to be the company
    # itself, with the article just used as the source." `one_liner` is the
    # only field that says what a company *does*; the model already fills it
    # and `_fields_from_extraction` used to drop it, so every surface fell back
    # to the article headline.
    assert row["one_liner"] == "Warehouse robots."


def test_structured_founded_year_reaches_the_age_gate(db, config):
    """Structured adapters must not turn an old company into age-unknown."""
    from datetime import date as _date

    from radar.pipeline import resolve_item, score_company
    from radar.sources.base import RawItem

    item = RawItem(
        source_key="entrepreneur_first",
        source_url="https://joinef.com/portfolio/old-venture",
        external_id="old-venture",
        published_at=_date(2026, 8, 1),
        title="Old Venture",
        structured={
            "company_name": "Old Venture Ltd",
            "founded_year": 2016,
            "hq_country_iso2": "GB",
            "hq_region": "uk_regions",
            "company_website": "https://old-venture.example",
            "one_line_description": "Builds useful things.",
            "stage": "pre_seed",
        },
        kind_hint="accelerator_cohort",
    )

    cid = resolve_item(db, item, config)
    row = db.one(
        "SELECT incorporated_on, age_source, country_iso2, hq_region, website_url, "
        "one_liner, stage FROM company WHERE id = ?",
        (cid,),
    )
    assert row["incorporated_on"] == "2016-07-01"
    assert row["age_source"] == "source_stated"
    assert row["country_iso2"] == "GB"
    assert row["hq_region"] == "uk_regions"
    assert row["website_url"] == "https://old-venture.example"
    assert row["one_liner"] == "Builds useful things."
    assert row["stage"] == "pre_seed"

    score_company(db, cid, config, today=_date(2026, 8, 8))
    assert {
        row["reject_reason"]
        for row in db.query("SELECT reject_reason FROM score WHERE company_id = ?", (cid,))
    } == {"max_company_age_months"}


def test_an_article_headline_never_becomes_a_company(db, config):
    """A prose item the reader could not name is a source, not a subject.

    `resolve_item` used to fall back to `item.title`, so an article the
    extractor rejected — a round-up, a market report, or anything it simply
    could not read a name out of — was inserted as a company called
    "Six Manchester startups to watch in 2026". Those rows scored, shortlisted
    and reached the sheet, which is what the client meant on 11 Aug by "I'm
    currently seeing articles rather than the actual companies themselves".
    """
    from datetime import date as _date

    from radar.pipeline import resolve_item
    from radar.sources.base import RawItem

    item = RawItem(
        source_key="uktn", source_url="https://uktn.test/roundup",
        external_id="e9", published_at=_date(2026, 8, 1),
        title="Six Manchester startups to watch in 2026",
        structured={},
    )
    # No `extraction` attribute: `extract_stage` only attaches one when the
    # record `is_usable`, i.e. when a company was actually named.
    assert resolve_item(db, item, config) is None
    assert db.scalar("SELECT COUNT(*) FROM company") == 0


def test_rerun_is_idempotent(db):
    """The same mention arriving twice creates one company, not two."""
    record = Record(name="Acme Robotics", ch_number="00445790")
    first = upsert_record(db, record, source_key="test", source_url="https://a",
                          external_id="e1")
    second = upsert_record(db, record, source_key="test", source_url="https://a",
                           external_id="e1")
    assert first.action == "created"
    assert second.company_id == first.company_id
    assert second.action == "matched"
    assert db.scalar("SELECT COUNT(*) FROM company") == 1


def test_digest_delivered_when_hermes_is_down(monkeypatch):
    """The fallback is unconditional: a broken Hermes gateway must not silence
    the digest (02-architecture §7)."""
    from radar.notify.telegram import DeliveryError, send_digest

    calls: list[tuple[str, str]] = []

    def hermes_down(text):
        calls.append(("hermes", text))
        return False

    def bot_ok(text):
        calls.append(("bot", text))
        return True

    assert send_digest("test", hermes=hermes_down, bot_api=bot_ok) is True
    assert calls[0][0] == "hermes"
    assert calls[1][0] == "bot"


def test_digest_raises_only_when_both_transports_fail(monkeypatch):
    from radar.notify.telegram import DeliveryError, send_digest

    def always_fail(text):
        return False

    with pytest.raises(DeliveryError):
        send_digest("test", hermes=always_fail, bot_api=always_fail,
                    raise_on_failure=True)


def test_telegram_allowlist_rejects_unknown_user(monkeypatch):
    """FR-8.4 — the allow-list is a closed door on our side of the boundary,
    and an empty list admits nobody, not everybody."""
    from radar.notify.telegram import handle

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")
    reply = handle(999_999, "/run")
    assert reply.status == "denied"
    assert reply.ran_pipeline is False

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
    reply = handle(111, "/run")
    assert reply.status == "denied"      # misconfigured = locked, never open


def test_every_telegram_command_maps_to_a_cli_command():
    """FR-9.6 — nothing is trapped in the chat layer: every command the
    Hermes skill teaches maps to a real `founder-radar` CLI command."""
    from pathlib import Path

    from radar.cli import cli
    from radar.notify.telegram import skill_commands

    cli_commands = set(cli.commands)
    skill_path = Path("hermes/skills/founder-radar/SKILL.md")
    assert skill_path.is_file()
    for argv in skill_commands(str(skill_path)):
        assert argv[0] in cli_commands, f"{argv[0]} is not a real CLI command"


# ------------------------------------------------------- bulk rescore parity


def test_rescore_bulk_equals_daily(db, config):
    """The interactive `rescore --all` path writes the same rows the daily
    path does, for every shape of company.

    `rescore_all` is a deliberately separate implementation — bulk reads,
    plain-dict arithmetic, executemany writes — because the pydantic daily
    path cannot hit the NFR-1 one-second target on five thousand companies
    (09-test-plan §8). A second scorer is only acceptable if it cannot drift;
    this test is the tripwire. It stores a deliberately varied corpus —
    qualified and unqualified registry finds, spinouts, gate rejects,
    companies with founders and signals — then runs both paths and compares
    every score row and component byte for byte.
    """
    from radar.pipeline import rescore_all, score_company
    from radar.score.derive import Signal

    from tests.factories import C, F, months_ago, registry_company, store_company

    today = date(2026, 8, 8)

    # A corpus with every branch the scorer can take: gate rejects, spinouts,
    # a non-GB company, an unqualified registry find, a heavily-signalled news
    # find, and a plain eligible company.
    companies = [
        C(age_months=12, canonical_name="Eligible Co", norm_key="eligibleco"),
        # gate reject — too old
        C(age_months=60, canonical_name="Old Co", norm_key="oldco"),
        # gate reject — already seen on a VC portfolio page
        C(age_months=12, canonical_name="Portfolio Co", norm_key="portfolioco",
          on_vc_portfolio=True),
        # spinout with a hard-reject input satisfied
        C(age_months=8, canonical_name="Spinout Co", norm_key="spinoutco",
          is_university_spinout=True, spinout_university="Durham University",
          founders=[F(prior_appointments=1)],
          signals=[Signal(kind="spinout", headline="Durham spinout",
                          occurred_on=str(months_ago(2)))]),
        # non-GB — the only hard no on UK presence
        C(age_months=12, canonical_name="US Co", norm_key="usco", country="US"),
        # unqualified registry find — stays in the pool, never scored
        registry_company(age_months=4, canonical_name="Registry Quiet Co",
                         norm_key="registryquietco"),
        # qualified registry find — a share issue earns its way in
        registry_company(age_months=8, canonical_name="Registry SH01 Co",
                         norm_key="registrysh01co", has_share_issue=True),
    ]
    ids = [store_company(db, c) for c in companies]

    def read_rows():
        scores = {}
        for row in db.query("SELECT * FROM score ORDER BY company_id, fund_key"):
            d = dict(row)
            d.pop("id")
            d.pop("scored_at")
            scores[(d["company_id"], d["fund_key"], d["vehicle_key"])] = d
        components = {}
        for row in db.query("SELECT * FROM score_component ORDER BY score_id, key"):
            d = dict(row)
            d.pop("score_id")
            components.setdefault((row["score_id"], d["key"]), d)
        return scores, components

    def by_identity():
        """Components keyed by (company, fund, vehicle, key) — score ids differ
        between runs, so identity is the only stable comparison."""
        out = {}
        for row in db.query(
            """SELECT sc.key, sc.label, sc.sub_score, sc.weight, sc.contribution,
                      sc.evidence, s.company_id, s.fund_key, s.vehicle_key
                 FROM score_component sc JOIN score s ON s.id = sc.score_id
                ORDER BY s.company_id, s.fund_key, sc.key"""):
            key = (row["company_id"], row["fund_key"], row["vehicle_key"], row["key"])
            out[key] = dict(row)
            out[key].pop("company_id"); out[key].pop("fund_key"); out[key].pop("vehicle_key")
        return out

    # ---- daily path first: one company at a time, pydantic models everywhere
    for cid in ids:
        score_company(db, cid, config, today=today)
    daily_scores, _ = read_rows()
    daily_identity = by_identity()
    daily_count = db.scalar("SELECT COUNT(*) FROM score")

    # ---- bulk path on the same database
    db.execute("DELETE FROM score")
    db.execute("DELETE FROM score_component")
    result = rescore_all(db, config, today=today)
    bulk_scores, _ = read_rows()
    bulk_identity = by_identity()

    assert result["scored"] == len(ids)
    assert db.scalar("SELECT COUNT(*) FROM score") == daily_count
    assert bulk_scores.keys() == daily_scores.keys()
    for key in daily_scores:
        daily, bulk = daily_scores[key], bulk_scores[key]
        for field in daily:
            assert bulk[field] == daily[field], (
                f"{key} {field}: bulk={bulk[field]!r} daily={daily[field]!r}")

    assert bulk_identity.keys() == daily_identity.keys()
    for key in daily_identity:
        assert bulk_identity[key] == daily_identity[key], key


@pytest.mark.parametrize("policy", ["pessimistic", "assume"])
def test_rescore_bulk_matches_daily_with_unknown_policies(db, config, policy):
    """Both persistence paths use the same coverage semantics for unknowns."""
    from radar.pipeline import rescore_all, score_company
    from tests.factories import C, store_company

    config.weights.unknown_policy["founder_signal"] = policy
    if policy == "assume":
        config.lists["assume_values"] = {"founder_signal": "technical_founder"}
    company = C(canonical_name="Pessimistic Co", norm_key="pessimisticco",
                founder_signal=None)
    cid = store_company(db, company)

    def snapshot():
        scores = [
            tuple(row[key] for key in
                  ("fund_key", "fund_fit_pct", "coverage", "priority",
                   "tier", "explanation"))
            for row in db.query(
                "SELECT fund_key, fund_fit_pct, coverage, priority, tier, explanation "
                "FROM score WHERE company_id = ? ORDER BY fund_key", (cid,))
        ]
        components = [
            tuple(row[key] for key in
                  ("fund_key", "key", "sub_score", "weight", "contribution",
                   "evidence"))
            for row in db.query(
                """SELECT s.fund_key, sc.key, sc.sub_score, sc.weight,
                          sc.contribution, sc.evidence
                     FROM score_component sc
                     JOIN score s ON s.id = sc.score_id
                    WHERE s.company_id = ?
                    ORDER BY s.fund_key, sc.key""", (cid,))
        ]
        return scores, components

    score_company(db, cid, config, today=date(2026, 8, 8))
    daily = snapshot()
    db.execute("DELETE FROM score_component")
    db.execute("DELETE FROM score")

    rescore_all(db, config, today=date(2026, 8, 8))
    bulk = snapshot()

    assert bulk == daily


def test_unqualified_registry_company_drops_stale_scores(db, config):
    from radar.pipeline import score_company
    from radar.store.db import now_iso
    from tests.factories import registry_company, store_company

    cid = store_company(db, registry_company(
        canonical_name="Stale Ltd", norm_key="staleltd",
        qualifiers=["repeat_founder"],
    ))
    stamp = now_iso()
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, "dsw", None, 80.0, 0.8, 70.0, 90.0, "watchlist", None,
         "Stale watchlist.", None, "oldhash", "1", stamp),
    )
    assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?", (cid,)) == 1
    assert score_company(db, cid, config) == 0
    assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?", (cid,)) == 0


def test_rescore_all_drops_scores_from_older_config_hashes(db, config):
    from radar.pipeline import rescore_all, score_company
    from tests.factories import C, store_company

    cid = store_company(db, C(age_months=6, canonical_name="Hash Co",
                             norm_key="hashco"))
    score_company(db, cid, config, today=date(2026, 8, 8))
    current = config.hash()
    db.execute("UPDATE score SET config_hash = 'oldhash' WHERE company_id = ?",
               (cid,))
    result = rescore_all(db, config, today=date(2026, 8, 8))
    hashes = {
        row["config_hash"]
        for row in db.query("SELECT DISTINCT config_hash FROM score")
    }
    assert hashes == {current}
    assert result["config_hash"] == current
