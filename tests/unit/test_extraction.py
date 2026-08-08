"""09-test-plan §3 — golden extraction tests.

Every fixture in `tests/fixtures/articles/` is run through the **real**
extraction path (`extract_html` → prefilter → llm cache → grounding) against
the ReplayLLM, which serves `tests/fixtures/llm_cache` and hard-fails on a
miss. No provider, no network: the socket is blocked for the whole session in
`conftest.py`.

The fixtures were authored and self-verified by
`tests/fixtures/build_extraction_fixtures.py` — the same file that computes
each cache key with the production code path, so a key can never drift from
what `extract()` asks for.

Rules from 05-pipeline §3.2, all asserted here:

* The "is this even about one startup?" decision is a schema field, and the
  round-up fixtures (6-8, 18-20) assert it directly.
* Every surviving evidence quote must be verbatim in the source
  (`test_no_hallucinations` — rate exactly 0).
* A provider failure degrades to the deterministic heuristic reader, never to
  a crash (`test_heuristic_fallback_when_llm_unavailable`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.extract import ExtractContext, extract_html
from radar.extract.llm import DEFAULT_MODEL, ProviderDown
from radar.extract.prefilter import parse_title
from radar.resolve.normalise import norm_key

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ARTICLES = FIXTURES / "articles"


def _all_slugs() -> list[str]:
    return sorted(p.stem for p in ARTICLES.glob("*.html"))


def _html(slug: str) -> str:
    return (ARTICLES / f"{slug}.html").read_text(encoding="utf-8")


def _expected(slug: str) -> dict:
    return json.loads((ARTICLES / f"{slug}.expected.json").read_text(encoding="utf-8"))


ALL_ARTICLE_FIXTURES = _all_slugs()

URLS: dict[str, str] = {
    slug: f"https://example.news/{slug}"
    for slug in ALL_ARTICLE_FIXTURES
}


def _extract(slug: str, llm) -> object:
    html = _html(slug)
    ctx = ExtractContext(llm=llm, db=None, model_id=DEFAULT_MODEL)
    return extract_html(
        url=URLS[slug],
        title=parse_title(html),
        html=html,
        ctx=ctx,
        source_key="fixture",
    )


# ---------------------------------------------------------------- the gate


@pytest.mark.parametrize("slug", ALL_ARTICLE_FIXTURES)
def test_extraction_matches_expected(slug, offline_llm):
    """Every fixture: the single-company gate, name, sector and amount."""
    got = _extract(slug, offline_llm)
    expected = _expected(slug)

    assert got.is_about_single_company == expected["is_about_single_company"]
    if got.is_about_single_company:
        # name equality is asserted after normalisation — never byte-exact
        # on free text (plan §3, "never assert exact string equality")
        assert norm_key(got.company_name) == norm_key(expected["company_name"])
        assert got.sector == expected["sector"]
        if expected.get("amount") is None:
            # unknown is not zero: an amount the article never states must come
            # back None, never 0.0 (the invariant this whole system holds to)
            assert got.amount_raised_gbp is None
        else:
            assert got.amount_raised_gbp == pytest.approx(expected["amount"], rel=0.01)
    else:
        assert got.rejection_reason == expected.get("rejection_reason")


def test_roundup_gate_is_decided_not_dropped(offline_llm):
    """The round-up fixtures reach the model and come back `False`, rather
    than being silently dropped by a prefilter accident. The gate is a schema
    field the model decides (05-pipeline §3.2)."""
    for slug in ("roundup_weekly_digest", "listicle_top_startups",
                 "accelerator_cohort_1", "accelerator_cohort_2"):
        got = _extract(slug, offline_llm)
        assert got.is_about_single_company is False
        assert got.rejection_reason == "roundup"


# ------------------------------------------------------------- hallucinations


def test_no_hallucinations(offline_llm):
    """Every evidence quote must appear verbatim in the source. Rate exactly 0
    (05-pipeline §3.2 — this is the promise grounding makes true)."""
    from radar.extract.grounding import hallucination_rate
    from radar.extract.prefilter import extract_text

    for slug in ALL_ARTICLE_FIXTURES:
        html = _html(slug)
        got = _extract(slug, offline_llm)
        assert hallucination_rate(got, extract_text(html)) == 0.0, slug
        # spot-check the two headline quotes survive for the clean fixtures
        if slug.startswith("clean_funding_announcement"):
            assert got.evidence_quote_company is not None
            assert got.evidence_quote_amount is not None


def test_invented_company_is_dropped_by_grounding(offline_llm):
    """Fixture 22: the model invented a company name the article never names.
    Grounding must drop the claim, not the record."""
    got = _extract("invented_company_grounding", offline_llm)
    assert got.is_about_single_company is True      # the article IS one company
    assert got.company_name is None                 # ...but the name is gone
    assert "company_name" in got.dropped_fields
    assert got.needs_review is True


# ------------------------------------------------------------- fallback path


class RaisingLLM:
    """An LLMClient whose provider is down. `complete` raises `ProviderDown`,
    which `extract` is the only code allowed to swallow."""

    model_id = DEFAULT_MODEL

    def complete(self, *, key, system, user):
        raise ProviderDown("simulated provider outage")


def test_heuristic_fallback_when_llm_unavailable():
    """Provider down → deterministic heuristic reader, run still completes."""
    ctx = ExtractContext(llm=RaisingLLM(), db=None, model_id=DEFAULT_MODEL)
    html = _html("clean_funding_announcement_1")
    got = extract_html(
        url=URLS["clean_funding_announcement_1"],
        title=parse_title(html),
        html=html,
        ctx=ctx,
    )
    assert got.extraction_method == "heuristic"
    assert got.needs_review is True
    assert got.company_name is not None             # the heuristic still finds it
    assert got.is_about_single_company is True


def test_no_llm_flag_uses_heuristic_without_a_provider():
    """`use_llm=False` — the `--no-llm` half of the same switch — produces a
    complete heuristic record with zero AI cost."""
    ctx = ExtractContext(llm=None, db=None, use_llm=False, model_id=DEFAULT_MODEL)
    html = _html("clean_funding_announcement_2")
    got = extract_html(
        url=URLS["clean_funding_announcement_2"],
        title=parse_title(html),
        html=html,
        ctx=ctx,
    )
    assert got.extraction_method == "heuristic"
    assert got.needs_review is True


def test_grant_is_not_confused_with_equity(offline_llm):
    """Fixture 11-12: Innovate UK grants go to `grant_amount_gbp`, never to
    `amount_raised_gbp` (the prompt's rule 4, pinned here)."""
    got = _extract("innovate_uk_grant_1", offline_llm)
    assert got.grant_amount_gbp == 250_000
    assert got.amount_raised_gbp is None


# --------------------------------------------------- the cache is the ledger


def test_refresh_llm_is_not_required_and_a_miss_never_reaches_the_network(
    tmp_path, monkeypatch
):
    """The committed cache must be sufficient. With `REFRESH_LLM` unset, a miss
    raises `CacheMiss` — it does not fall back to the heuristic (which would
    hide a stale fixture) and it does not build a provider (which would be a
    network call in CI). `REFRESH_LLM=1` is the only path that may record."""
    from radar.extract.llm import CacheMiss, ReplayLLM

    monkeypatch.delenv("REFRESH_LLM", raising=False)

    def _no_provider():
        raise AssertionError("a cache miss must never construct a provider")

    llm = ReplayLLM(tmp_path, provider_factory=_no_provider)   # an empty cache
    assert llm.refresh is False

    with pytest.raises(CacheMiss):
        _extract("clean_funding_announcement_1", llm)


def test_cost_ledger_records_token_counts(db, offline_llm):
    """FR-9.2 / 03-data-model §6 query 10: every call lands in `llm_cache`
    with real `tokens_in`, `tokens_out` and `cost_usd`, so monthly spend is a
    query rather than a guess."""
    html = _html("clean_funding_announcement_3")
    ctx = ExtractContext(llm=offline_llm, db=db, model_id=DEFAULT_MODEL)
    got = extract_html(
        url=URLS["clean_funding_announcement_3"],
        title=parse_title(html),
        html=html,
        ctx=ctx,
    )
    assert got.extraction_method == "llm"

    row = db.one("SELECT * FROM llm_cache ORDER BY rowid DESC LIMIT 1")
    assert row is not None, "the call was not written to the ledger"
    assert row["tokens_in"] > 0
    assert row["tokens_out"] > 0
    assert row["cost_usd"] > 0

    from radar.extract.llm import LlmCache

    spend = LlmCache(db).monthly_spend()
    assert spend and spend[0]["calls"] == 1
    assert spend[0]["cost_usd"] > 0


def test_usd_amount_is_flagged_not_silently_converted(offline_llm):
    """Fixture 21: a USD amount stays in `amount_original`/`amount_currency`
    and the record is flagged for review — never silently assumed GBP."""
    got = _extract("usd_amount_round", offline_llm)
    assert got.amount_original == 4_000_000
    assert got.amount_currency == "USD"
    assert got.amount_raised_gbp is None
    assert got.needs_review is True
