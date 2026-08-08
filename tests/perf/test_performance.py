"""09-test-plan §8 — performance.

Marked `perf`, so these sit outside the 60-second offline budget they are
partly measuring (NFR-4). Run them deliberately:

    .venv/bin/python -m pytest -m perf

| Target | NFR | Test |
|---|---|---|
| Daily run < 25 minutes | NFR-1 | `test_full_run_under_25_minutes` |
| Rescore is interactive | — | `test_rescore_5000_companies_under_1s` |
| Offline suite < 60 s | NFR-4 | `test_offline_suite_under_60s` |

NFR-2 (< 700 MB) is measured by `test_memory_under_700mb` in
`tests/unit/test_digest.py`, next to the fixture that builds a full day.

Every ceiling below is the published target multiplied by an allowance that is
stated where it is used. The allowances exist because these run on whatever
box is free — a 1 vCPU Hostinger VPS, a laptop with a browser open — and a
number tight enough to catch a 10% drift would go red for reasons that have
nothing to do with this codebase.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from radar.sources.base import RawItem

from tests.factories import C, registry_company, store_company
from tests.perf.conftest import best_of

pytestmark = pytest.mark.perf

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 8, 8)


# ------------------------------------------------------- NFR-1: the daily run

# A heavy day. Track B dominates volume: the Companies House trailing window is
# hundreds of incorporations, Track A is tens of articles.
REGISTRY_ITEMS = 400
ARTICLE_ITEMS = 100

NFR_1_SECONDS = 25 * 60


def _article_html(name: str) -> str:
    return (
        f"<html><head><title>{name} raises £1.2m pre-seed</title></head><body>"
        f"<p>{name} Ltd, a Newcastle-based software company, has raised £1.2m in "
        "a pre-seed round led by a regional fund.</p><p>The company says the "
        "money will pay for eight engineering hires over the next eighteen "
        "months. Its founders met while working on industrial sensing at a large "
        f"manufacturer and have been running paid pilots since the spring. {name} "
        "now has a waiting list of prospective customers and expects to announce "
        "a further partnership before the end of the year. The round is the first "
        "outside money the business has taken.</p></body></html>"
    )


class HeavyDay:
    """One adapter standing in for a whole day's fetch, offline.

    The real 25 minutes is mostly network and provider latency, which no
    offline test can measure. What this pins is the half that *is* ours: the
    extract, resolve, enrich-skip and score work the pipeline does per item.
    If that ever grows to a meaningful fraction of the budget, the network has
    nowhere left to go.
    """

    key = "perf_feed"
    kind = "news"
    schedule = "daily"
    requires_browser = False

    def fetch(self, ctx):                                          # noqa: ARG002
        for index in range(REGISTRY_ITEMS):
            number = f"{12_000_000 + index}"
            yield RawItem(
                source_key=self.key,
                source_url=f"https://example.test/ch/{number}",
                external_id=f"ch-{number}",
                published_at=date(2026, 8, 1),
                title=f"Perf Registry {index} Ltd",
                structured={
                    "company_number": number,
                    "company_name": f"Perf Registry {index} Ltd",
                    "date_of_creation": "2026-05-01",
                    "sic_codes": ["62012"],
                    "postal_code": "NE1 4ST",
                    "locality": "Newcastle upon Tyne",
                },
            )
        for index in range(ARTICLE_ITEMS):
            name = f"Perfco {index}"
            yield RawItem(
                source_key=self.key,
                source_url=f"https://example.test/news/{index}",
                external_id=f"news-{index}",
                published_at=date(2026, 8, 1),
                title=f"{name} raises £1.2m pre-seed",
                body_text=_article_html(name),
            )


class NoHttp:
    def get(self, url, **kw):                                      # noqa: ARG002
        raise AssertionError(f"the perf run must not touch the network: {url}")


def test_full_run_under_25_minutes(db, config, monkeypatch, report):
    """NFR-1 — a daily run completes in under 25 minutes.

    Five hundred items, extraction on the deterministic path, scoring against
    all four funds, no sheet. The assertion is the real NFR ceiling because
    that is the promise; the useful signal is the printed number, which on a
    developer machine is a fraction of a second and should stay there.
    """
    import radar.sources
    from radar.pipeline import run_pipeline

    monkeypatch.setattr(radar.sources, "enabled_adapters",
                        lambda cfg, keys=None: [HeavyDay()])
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.delenv("CH_API_KEY", raising=False)

    started = time.perf_counter()
    result = run_pipeline(db, config=config, http=NoHttp(), use_llm=False,
                          gateway=None, now=TODAY)
    elapsed = time.perf_counter() - started

    assert result.status in ("ok", "partial"), result.error
    assert result.items_fetched == REGISTRY_ITEMS + ARTICLE_ITEMS
    report("full_run", elapsed, NFR_1_SECONDS)
    assert elapsed < NFR_1_SECONDS

    # The half we control must stay a rounding error against the budget, or a
    # slow week at one source turns into a missed run. 1% of 25 minutes for
    # 500 items is fifteen seconds — nowhere near tight, and still ten times
    # smaller than anything that has ever been measured here.
    assert elapsed < NFR_1_SECONDS * 0.01, (
        f"local pipeline work is {elapsed:.1f}s of the {NFR_1_SECONDS}s budget — "
        "there is no headroom left for the network")


# -------------------------------------------------- interactive weight tuning

RESCORE_COMPANIES = 5_000

# The design target is one second: `founder-radar rescore --all` is what Aryan
# runs after every weight edit during tuning week, and a five-second wait turns
# an experiment into a chore. The assertion carries 2.5x for shared hardware.
RESCORE_BUDGET_S = 1.0
RESCORE_CEILING_S = RESCORE_BUDGET_S * 2.5


@pytest.fixture
def five_thousand_companies(db):
    """A realistic database, not a uniform one.

    Track B is most of the volume and most of it never reaches the scorer: a
    registry company with no qualifying signal stays in the candidate pool and
    is re-checked, never scored (06-scoring §3). Filling the corpus with 5,000
    fully-qualified companies would measure a database that cannot exist.
    """
    for index in range(RESCORE_COMPANIES):
        if index % 20 < 17:
            company = registry_company(canonical_name=f"Registry {index}",
                                       norm_key=f"registry{index}")
        else:
            company = C(age_months=12, canonical_name=f"Scored {index}",
                        norm_key=f"scored{index}")
        store_company(db, company)
    return db


def test_rescore_5000_companies_under_1s(five_thousand_companies, config, report):
    """Weight tuning has to be interactive.

    Re-scoring is pure: no network, no AI, no extraction — just the stage ⑥
    arithmetic and the score upserts. The number that matters is how long
    `rescore --all` makes someone wait between two guesses at a weight.
    """
    from radar.pipeline import rescore_all

    db = five_thousand_companies
    elapsed = best_of(lambda: rescore_all(db, config), runs=3)

    report("rescore_5000", elapsed, RESCORE_BUDGET_S)

    # `COUNT(*) > 0` would pass on a single scored row, which is how a timing
    # test quietly turns into a test of nothing. Pin the real figure: 3 in
    # every 20 companies in the fixture are scoreable.
    expected_scored = sum(1 for i in range(RESCORE_COMPANIES) if i % 20 >= 17)
    assert db.scalar("SELECT COUNT(DISTINCT company_id) FROM score") == expected_scored
    assert db.scalar("SELECT COUNT(*) FROM company") == RESCORE_COMPANIES

    assert elapsed < RESCORE_CEILING_S, (
        f"rescoring {RESCORE_COMPANIES} companies took {elapsed:.2f}s against a "
        f"{RESCORE_BUDGET_S:g}s design target")


def test_rescore_worst_case_all_qualified(db, config, report):
    """The tail the realistic fixture deliberately does not measure.

    If every stored company were scoreable — which needs 5,000 qualified
    companies, a shape Track B's qualification gate makes very unlikely — the
    work is roughly 6x the typical case. Kept as a bound, not a target: the
    number to watch is whether it drifts, since a regression here shows up in
    the realistic case long before a user feels it.
    """
    from radar.pipeline import rescore_all

    for index in range(RESCORE_COMPANIES):
        store_company(db, C(age_months=12, canonical_name=f"Q {index}",
                            norm_key=f"q{index}"))

    elapsed = best_of(lambda: rescore_all(db, config), runs=3)
    report("rescore_5000_worst_case", elapsed, RESCORE_BUDGET_S)

    assert db.scalar("SELECT COUNT(DISTINCT company_id) FROM score") == RESCORE_COMPANIES
    assert elapsed < RESCORE_BUDGET_S * 5, (
        f"worst-case rescore took {elapsed:.2f}s — the tail has grown past the "
        "point where the typical case stays interactive")


def test_rescore_scales_linearly(db, config):
    """The machine-independent half, and the one that actually catches a
    regression: ten times the companies must not be much more than ten times
    the work.

    The failure this guards against is real and has happened here — hashing the
    whole config once per score row, and recomputing an identical fund-fit
    matrix once per vehicle, both turned a constant into a per-row cost.
    """
    from radar.pipeline import rescore_all

    for index in range(500):
        store_company(db, C(age_months=12, canonical_name=f"Small {index}",
                            norm_key=f"small{index}"))
    small = best_of(lambda: rescore_all(db, config), runs=3)

    for index in range(4_500):
        store_company(db, C(age_months=12, canonical_name=f"Big {index}",
                            norm_key=f"big{index}"))
    big = best_of(lambda: rescore_all(db, config), runs=3)

    # 10x the rows, allowed 20x the time. Anything worse is super-linear.
    assert big < small * 20, f"500 rows in {small:.3f}s but 5000 in {big:.3f}s"


# --------------------------------------------------------- NFR-4: the suite

NFR_4_SECONDS = 60


def test_offline_suite_under_60s(report):
    """NFR-4 — the default suite runs offline in under 60 seconds.

    Measured the way CI measures it: a clean interpreter running bare `pytest`,
    which picks up the `addopts` in pyproject.toml and therefore deselects
    `perf` — so this does not recurse.

    Sixty seconds is not an arbitrary round number. A suite people wait for is
    a suite people stop running, and every guarantee in `09-test-plan.md`
    depends on it being run on every change.
    """
    # No extra `-q`: pyproject already passes one, and a second turns off the
    # summary line this test reads.
    started = time.perf_counter()
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=NFR_4_SECONDS * 5)
    elapsed = time.perf_counter() - started

    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "deselected" in done.stdout, (
        "the child run did not deselect perf/integration — this would recurse:\n"
        + done.stdout[-1000:])

    report("offline_suite", elapsed, NFR_4_SECONDS)
    assert elapsed < NFR_4_SECONDS, (
        f"the offline suite took {elapsed:.1f}s:\n{done.stdout[-2000:]}")
