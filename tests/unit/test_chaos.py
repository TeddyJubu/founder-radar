"""09-test-plan §6 — chaos: proving nothing stops the run.

Every row of the failure table in 02-architecture §7 is a test. Four of them
already live in `test_pipeline.py` (one source down, Hermes down, re-run
idempotence, run-log row) and one in `test_companies_house.py` (the rate
limit). The three here are the rest of the *survivable* rows:

| 02-architecture §7 row | test |
|---|---|
| AI provider down or rate-limited | `test_llm_down_still_completes` |
| AI returns invalid JSON | `test_invalid_llm_json_is_quarantined_not_dropped` |
| Google Sheets API down | `test_sheets_down_keeps_data` |

Failures are injected through the code's own seams — an `LLMClient` that
raises, a `SheetGateway` that 503s — never by unplugging the network. The
suite blocks every non-loopback socket anyway, so a chaos test that "worked"
by breaking the network would be testing the test harness.
"""

from __future__ import annotations

from datetime import date

import pytest

from radar.sources.base import RawItem

TODAY = date(2026, 8, 8)


# ------------------------------------------------------------------ articles


def article_html(name: str, city: str = "Newcastle") -> str:
    """A minimal but realistic funding story.

    Synthetic rather than a committed fixture on purpose: the point of these
    tests is the *pipeline's* behaviour when the reader fails, so the article
    has to be one the deterministic heuristic extractor can definitely read.
    Only one of the 25 committed article fixtures clears that bar, which would
    have made a three-company assertion accidentally a one-company assertion.
    """
    body = (
        f"<p>{name} Ltd, a {city}-based software company, has raised £1.2m in a "
        "pre-seed round led by a regional fund.</p>"
        "<p>The company was founded in 2025 and says the money will pay for eight "
        "engineering hires over the next eighteen months. Its founders met while "
        "working on industrial sensing at a large manufacturer and have been "
        f"running paid pilots with two utilities since the spring. {name} says it "
        "now has a waiting list of prospective customers and expects to announce a "
        "further partnership before the end of the year. The round is the first "
        "outside money the business has taken.</p>"
    )
    return (f"<html><head><title>{name} raises £1.2m pre-seed</title></head>"
            f"<body>{body}</body></html>")


ARTICLE_COMPANIES = ("Palisade Health", "Loamweave", "Corvid Optics")


class ArticleSource:
    """One Track A adapter serving `ARTICLE_COMPANIES` as prose items."""

    key = "chaos_news"
    kind = "news"
    schedule = "daily"
    requires_browser = False

    def fetch(self, ctx):                                        # noqa: ARG002
        for name in ARTICLE_COMPANIES:
            slug = name.lower().replace(" ", "-")
            yield RawItem(
                source_key=self.key,
                source_url=f"https://example.test/{slug}",
                external_id=slug,
                published_at=date(2026, 8, 1),
                title=f"{name} raises £1.2m pre-seed",
                body_text=article_html(name),
            )


@pytest.fixture
def article_feed(monkeypatch):
    """Replace the registry's verdict on which adapters run.

    `fetch_all` resolves `enabled_adapters` from the module globals, so this is
    the same seam a real `Sources` tab edit uses — no adapter is monkeypatched
    into lying, and the per-source isolation wrapper still runs unchanged.
    """
    import radar.sources

    monkeypatch.setattr(radar.sources, "enabled_adapters",
                        lambda cfg, keys=None: [ArticleSource()])
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.delenv("CH_API_KEY", raising=False)
    return ArticleSource()


class NoHttp:
    """The run must not reach for the network at all on this path."""

    def get(self, url, **kw):                                    # noqa: ARG002
        raise AssertionError(f"unexpected HTTP GET: {url}")


# ------------------------------------------------- AI provider down (§7 row 3)


def test_llm_down_still_completes(db, config, article_feed):
    """"AI provider down or rate-limited → heuristic extractor → records
    flagged `needs_review`", and the run finishes.

    The digest line Aryan sees is "6 companies, 2 need review — AI unavailable",
    which is only true if the run completed at all.
    """
    from radar.extract.llm import ProviderDown, StubLLM
    from radar.pipeline import run_pipeline

    result = run_pipeline(db, config=config, http=NoHttp(), use_llm=True,
                          llm=StubLLM(ProviderDown("provider unreachable")),
                          gateway=None, now=TODAY)

    assert result.status in ("ok", "partial"), result.error
    assert result.error is None
    assert result.items_fetched == len(ARTICLE_COMPANIES)

    companies = db.query("SELECT * FROM company WHERE merged_into IS NULL")
    assert companies, "the run produced nothing — the fallback did not fire"
    for row in companies:
        assert row["needs_review"] == 1, f"{row['canonical_name']} is not flagged"

    # And the run is honest about it in the run log rather than only in memory.
    assert db.scalar("SELECT status FROM run ORDER BY id DESC LIMIT 1") in ("ok", "partial")


def test_llm_down_costs_nothing(db, config, article_feed):
    """The failed calls must not be billed. A provider outage that showed up as
    spend would make the cost ledger useless exactly when it is being read."""
    from radar.extract.llm import ProviderDown, StubLLM
    from radar.pipeline import run_pipeline

    run_pipeline(db, config=config, http=NoHttp(), use_llm=True,
                 llm=StubLLM(ProviderDown("429")), gateway=None, now=TODAY)
    assert db.scalar("SELECT COUNT(*) FROM llm_cache") == 0


# ------------------------------------------------ invalid model JSON (§7 row 4)


def test_invalid_llm_json_is_quarantined_not_dropped(db, config):
    """"AI returns invalid JSON → one retry with the validation error appended,
    then quarantine the record."

    Quarantined, not dropped: a record that vanishes silently is a record
    nobody ever finds out was wrong.
    """
    from radar.extract import ExtractContext, extract_html
    from radar.extract.llm import StubLLM

    llm = StubLLM("{ not json")
    ctx = ExtractContext(llm=llm, db=db, use_llm=True)
    record = extract_html(
        url="https://example.test/palisade-health",
        title="Palisade Health raises £1.2m pre-seed",
        html=article_html("Palisade Health"),
        ctx=ctx,
        source_key="chaos_news",
    )

    assert len(llm.calls) == 2, "the retry with the validation error did not happen"

    rows = db.query("SELECT * FROM quarantine")
    assert len(rows) == 1
    assert rows[0]["source_key"] == "chaos_news"
    assert rows[0]["source_url"] == "https://example.test/palisade-health"
    assert "not json" in rows[0]["raw_json"], "the raw payload was not kept"
    assert rows[0]["error"], "the reason it was quarantined was not kept"

    # The run keeps moving on the deterministic fallback rather than stopping.
    assert record.quarantined is True
    assert record.extraction_method == "heuristic"
    assert record.needs_review is True
    assert record.company_name == "Palisade Health"


def test_valid_llm_json_is_not_quarantined(db, config):
    """The guard on the guard: a good response must leave the table empty, or
    `test_invalid_llm_json_is_quarantined_not_dropped` proves nothing."""
    from radar.extract import ExtractContext, extract_html
    from radar.extract.llm import StubLLM

    payload = {
        "is_about_single_company": True,
        "company_name": "Palisade Health",
        "extraction_confidence": 0.9,
    }
    ctx = ExtractContext(llm=StubLLM(payload), db=db, use_llm=True)
    record = extract_html(url="https://example.test/palisade-health",
                          title="Palisade Health raises £1.2m pre-seed",
                          html=article_html("Palisade Health"), ctx=ctx,
                          source_key="chaos_news")
    assert db.scalar("SELECT COUNT(*) FROM quarantine") == 0
    assert record.extraction_method == "llm"


# ------------------------------------------------- Google Sheets down (§7 row 6)


class SheetsApiError(RuntimeError):
    """Stands in for `gspread.exceptions.APIError`.

    Constructing the real one needs a live transport response object, and the
    renderer treats every gateway exception identically — it has to, because
    a 503, a socket timeout and an expired credential all mean "the sheet did
    not get written".
    """

    def __init__(self, status: int = 503) -> None:
        super().__init__(f"Google Sheets API returned {status}")
        self.status = status


class FakeSheet:
    """An in-memory `SheetGateway`. Five verbs, all counted."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.tabs: dict[str, int] = {}
        self.value_writes = 0
        self.format_writes = 0

    def _guard(self) -> None:
        if self.down:
            raise SheetsApiError(503)

    def sheets(self) -> dict[str, int]:
        self._guard()
        return dict(self.tabs)

    def add_tabs(self, titles):
        self._guard()
        added = {title: 1000 + index for index, title in enumerate(titles)}
        self.tabs.update(added)
        return added

    def batch_get(self, ranges):
        self._guard()
        return {rng: [] for rng in ranges}

    def batch_set(self, data, value_input_option):                # noqa: ARG002
        self._guard()
        self.value_writes += 1

    def batch_requests(self, requests):                           # noqa: ARG002
        self._guard()
        self.format_writes += 1


def test_sheets_down_keeps_data(db, config, article_feed):
    """"Google Sheets API down → rows stay in SQLite with `synced = 0`, next
    run upserts them." A late sheet, never lost data.

    SQLite is the truth and the sheet is the view (02-architecture §1), so a
    Google outage may cost a day of visibility and nothing else.
    """
    from radar.pipeline import run_pipeline
    from radar.render.sheet import sync_sheet

    down = FakeSheet(down=True)
    result = run_pipeline(db, config=config, http=NoHttp(), use_llm=False,
                          gateway=down, now=TODAY)

    unsynced = db.scalar("SELECT COUNT(*) FROM company WHERE synced = 0")
    assert unsynced > 0, "nothing was kept for the next run to upsert"
    assert down.value_writes == 0

    # The run itself survived: a dead sheet is one stage failing, not the run.
    # Marking it `failed` would also read to the heartbeat as "no successful
    # run in 26 hours" and raise a second, wrong alarm on top of the outage.
    assert result.status == "partial"
    assert any("sheet" in w for w in result.warnings)

    # ...and the next successful render clears the backlog.
    working = FakeSheet()
    sync_sheet(db, gateway=working, today=TODAY)
    assert working.value_writes > 0
    assert db.scalar("SELECT COUNT(*) FROM company WHERE synced = 0") == 0
    assert db.scalar("SELECT COUNT(*) FROM company WHERE synced = 1") == unsynced


def test_sheets_down_does_not_lose_the_companies_themselves(db, config, article_feed):
    """The stronger form: the same companies are still there afterwards, with
    their scores, so the next run upserts rather than re-discovers."""
    from radar.pipeline import run_pipeline

    run_pipeline(db, config=config, http=NoHttp(), use_llm=False,
                 gateway=FakeSheet(down=True), now=TODAY)

    names = {r["canonical_name"] for r in db.query("SELECT canonical_name FROM company")}
    assert names, "the outage cost us the companies"
    assert db.scalar("SELECT COUNT(*) FROM score") > 0
