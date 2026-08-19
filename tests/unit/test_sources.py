"""09-test-plan §4 — source adapter tests. Every adapter gets both of them.

Two canonical tests, parametrised over all eleven Tier 1 adapters:

* `test_adapter_parses_committed_fixture` — `parse()` is the test seam. It is a
  pure function from bytes to `RawItem[]`: no network, no `ctx`, no clock. A
  committed fixture is therefore a complete test of the half that breaks.
* `test_adapter_detects_layout_change` — **the dangerous failure is 200 OK with
  an empty list.** A 404 is loud and stage ② already isolates it; a page that
  still renders, still returns 200, and quietly matches nothing looks exactly
  like a quiet week. Every `_CHANGED` fixture in `tests/fixtures/sources/` is
  that shape — a real payload with the shape moved — and every adapter must
  raise `LayoutChanged` on it rather than returning `[]`.

Plus `test_source_still_reachable_and_parseable`, marked `live`: the Monday
early-warning run against the real sites. It is deselected from the default
suite by `addopts` and must never need the network to *collect*.

Three adapters need a note on the seam they are tested through:

* **conception_x / entrepreneur_first** publish undated portfolio pages, so
  `parse()` deliberately leaves `published_at is None` and `diff()` stamps the
  run date (04-sources §4.3). The seam under test is therefore `parse` + `diff`
  — anything less would assert a contract the source cannot meet.
* **vc_portfolios** is the denylist. Its pages carry no dates at all and never
  will, so it is the one adapter exempt from the `published_at is not None`
  clause ("where the source's shape allows").
* **companies_house** has no `parse()` — its unit of work is the date-windowed
  sweep, so its seam is `fetch()` against a stub HTTP client serving the
  committed `tests/fixtures/api/ch_advanced_search_page.json` (reused, not
  duplicated). Still offline, still bytes-in/items-out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import pytest

from radar.fetch.layout import LayoutChanged
from radar.sources import REGISTRY, TIER_1_SOURCES
from radar.sources import (
    bdaily_regional,
    bethnal_green,
    businesscloud,
    cambridge_enterprise,
    carbon13,
    companies_house,
    conception_x,
    converge,
    edinburgh_innovations,
    entrepreneur_first,
    founders_factory,
    govuk_search,
    northern_accelerator,
    oxford_innovation,
    sheffield,
    startups_magazine,
    techstars_london,
    ucl_ventures,
    uktn,
    vc_portfolios,
    zinc_vc,
)
from radar.sources.base import FetchContext, RawItem

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SOURCE_FIXTURES = FIXTURES / "sources"
API_FIXTURES = FIXTURES / "api"

#: The day the fixtures were captured. Snapshot-diff sources stamp this.
RUN_DATE = date(2026, 8, 8)


def load(name: str) -> str:
    return (SOURCE_FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------- stubs


class StubResponse:
    def __init__(self, text: str = "", payload=None, status: int = 200) -> None:
        self.status = status
        self.text = text
        self._payload = payload
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)


class StubLimiter:
    def __init__(self) -> None:
        self.delays: dict[str, float] = {}

    def set_delay(self, host: str, delay: float) -> None:
        self.delays[host] = delay


class StubHttp:
    """Serves one committed body for every GET. Never touches a socket."""

    def __init__(self, text: str = "", payload=None) -> None:
        self.text = text
        self.payload = payload
        self.limiter = StubLimiter()
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StubResponse(self.text, self.payload)


def _ctx(http: StubHttp, *, db=None) -> FetchContext:
    return FetchContext(http=http, config=None, db=db, now=RUN_DATE)


# -------------------------------------------------------- per-adapter cases


@dataclass(frozen=True)
class Case:
    """One adapter: how to parse it, how to break it, what must be true."""

    parse: Callable[[], list[RawItem]]
    changed: Callable[[], list[RawItem]]
    check: Callable[[list[RawItem]], None]
    dated: bool = True
    min_items: int = 3


def _diffed(adapter, fixture: str) -> list[RawItem]:
    """parse + diff — the real seam for an undated snapshot-diff source."""
    return list(adapter.diff(adapter.parse(load(fixture)), _ctx(StubHttp())))


def _ch_items(fixture_path: Path) -> list[RawItem]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    adapter = companies_house.CompaniesHouseAdapter(
        api_key="test-key", days_back=90, window_days=90)
    return list(adapter.fetch(_ctx(StubHttp(payload=payload))))


def check_northern_accelerator(items):
    assert all(i.kind_hint == "spinout" for i in items)
    spinouts = [i for i in items if i.structured.get("is_university_spinout")]
    assert len(spinouts) >= 3
    assert any(i.structured.get("university_name") == "Durham University" for i in items)
    # The programme announcement is not a spinout, and the adapter must not
    # claim it is: None means unknown and has to stay that way.
    assert any("is_university_spinout" not in i.structured for i in items)


def test_northern_accelerator_parses_the_rss_fallback_fixture():
    """The official feed carries full post bodies when the JSON route is blocked."""
    items = northern_accelerator.ADAPTER.parse_feed(load("northern_accelerator.xml"))
    check_northern_accelerator(items)


@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_northern_accelerator_fetch_falls_back_to_rss_after_a_block(status):
    """A WAF-blocked JSON route must not erase the source's public feed."""
    class JsonBlockedThenFeed:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url == northern_accelerator.ENDPOINT:
                return StubResponse(status=status)
            return StubResponse(load("northern_accelerator.xml"))

    http = JsonBlockedThenFeed()
    items = list(northern_accelerator.ADAPTER.fetch(_ctx(http)))

    assert len(items) == 4
    assert [url for url, _ in http.calls] == [
        northern_accelerator.ENDPOINT, northern_accelerator.FEED,
    ]


def test_northern_accelerator_rss_detects_layout_change():
    """A valid but empty fallback feed must fail loudly, not look like a quiet week."""
    with pytest.raises(LayoutChanged) as excinfo:
        northern_accelerator.ADAPTER.parse_feed(load("northern_accelerator_CHANGED.xml"))

    assert excinfo.value.source_key == "northern_accelerator"


def check_cambridge_enterprise(items):
    assert all(i.structured["university_name"] == "University of Cambridge" for i in items)
    # A licensing deal is not a company. No name is invented for it.
    assert all(not i.structured.get("company_name") for i in items)
    assert any(not i.structured.get("is_university_spinout") for i in items)


def check_zinc_vc(items):
    named = {i.structured.get("company_name") for i in items}
    assert {"Palisade Health", "Brightbox Analytics", "Northwind Diagnostics"} <= named
    assert all(i.structured["stage"] == "pre_seed"
               for i in items if i.structured.get("company_name"))
    # A cohort announcement is not an investment: no name, no invented stage.
    assert any(i.kind_hint == "news" and not i.structured.get("company_name") for i in items)


def check_businesscloud(items):
    full = [i for i in items if i.structured["full_text_in_feed"]]
    assert len(full) >= 3                       # content:encoded is why it is Tier 1
    assert all(len(i.body_text or "") >= businesscloud.FULL_TEXT_MIN for i in full)
    # The one excerpt-only item degrades to "fetch the article", not to silence.
    assert any(not i.structured["full_text_in_feed"] for i in items)
    assert any(i.kind_hint == "funding_round" for i in items)


def check_uktn(items):
    assert all(i.structured["needs_article_fetch"] is True for i in items)
    assert all("?" not in i.source_url for i in items)          # robots: /*?
    # The post with no `date` field still gets a date, from the slug, and says so.
    inferred = [i for i in items if i.structured["date_confidence"] == "inferred"]
    assert len(inferred) == 1
    assert inferred[0].published_at == date(2026, 7, 24)


def check_govuk_search(items):
    assert all(i.kind_hint == "grant_award" for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    assert any("Innovate UK" in i.structured["organisations"] for i in items)


def check_oxford_innovation(items):
    # The whole reason this source is Tier 1: a literal incorporation date per
    # company, so stage ③ never has to ask a model anything.
    assert all(i.structured["incorporated_on"] for i in items)
    assert all(i.structured["extraction_method"] == "structured" for i in items)
    assert all(i.structured["is_university_spinout"] is True for i in items)
    assert all(i.structured["date_confidence"] == "stated" for i in items)
    # The link a human opens is the portfolio entry, not the company's own site.
    assert all(i.source_url.startswith("https://innovation.ox.ac.uk/") for i in items)
    assert any(i.structured["department"] == "Department of Physics" for i in items)


def check_conception_x(items):
    assert all(i.published_at == RUN_DATE for i in items)       # stamped by diff()
    assert all(i.structured["date_confidence"] == "inferred" for i in items)
    assert all(i.structured["bootstrap"] is True for i in items)  # first ever run
    assert all(i.structured["stage"] == "pre_seed" for i in items)
    assert {i.structured["cohort"] for i in items} <= {"CX23", "CX24", "CX25"}


def check_entrepreneur_first(items):
    assert all(i.published_at == RUN_DATE for i in items)
    assert all(i.structured["stage"] == "pre_seed" for i in items)
    assert all(i.structured["age_source"] == "unknown" for i in items)
    assert any(i.structured["founded_year"] == 2026 for i in items)


def check_startups_magazine(items):
    """wp-json over RSS: the feed carries summaries, the API carries articles."""
    assert all(i.structured["full_text_in_feed"] is True for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    assert all(len(i.body_text or "") > 120 for i in items)     # real article text
    assert any(i.kind_hint == "funding_round" for i in items)
    # A sector round-up is not a funding announcement about one company.
    assert any(i.kind_hint == "news_mention" for i in items)


def check_bdaily_regional(items):
    """Excerpt-only by design, and scoped to one region.

    `full_text_in_feed` is False on every item — that is a statement about the
    source, not a measurement, and it is what makes stage ③ pay an article
    fetch here but not for BusinessCloud.
    """
    assert all(i.structured["full_text_in_feed"] is False for i in items)
    assert all(i.structured["hq_region"] == "north_east" for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    assert any(i.kind_hint == "funding_round" for i in items)
    # An arts appointment is not a funding round; the adapter must not guess.
    assert any(i.kind_hint == "news_mention" for i in items)


def check_edinburgh_innovations(items):
    """The date is parsed out of display text, so it is worth asserting."""
    assert all(i.structured["university_name"] == "University of Edinburgh"
               for i in items)
    assert any(i.published_at == date(2026, 7, 20) for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    # Scotland is neither North East nor Yorkshire — these route to DSW and
    # Outward, never to Northstar's or Anticus's regional vehicles.
    assert all(i.structured["hq_region"] == "uk_regions" for i in items)
    assert any("Investment" in i.structured["tags"] for i in items)


def check_ucl_ventures(items):
    """The only Tier 2 HTML source with a machine-readable date."""
    assert all(i.structured["hq_city"] == "London" for i in items)
    assert any(i.published_at == date(2026, 7, 27) for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    # The kicker decides whether this is a company story. Events are kept and
    # tagged, never dropped in the adapter — the prefilter owns that call.
    spinouts = [i for i in items if i.structured.get("is_university_spinout")]
    assert len(spinouts) == 2
    assert any("Events & programmes" in i.structured["tags"] for i in items)


def check_bethnal_green(items):
    """Undated portfolio: freshness is what changed, never a founding claim."""
    assert all(i.published_at == RUN_DATE for i in items)       # stamped by diff()
    assert all(i.structured["bootstrap"] is True for i in items)
    assert all(i.structured["date_confidence"] == "inferred" for i in items)
    assert all(i.structured["age_source"] == "unknown" for i in items)
    assert all(i.structured["stage"] == "pre_seed" for i in items)
    # The provenance link is the portfolio page, not the venture's own site:
    # a third of those are still plain http (see `oxford_innovation`).
    assert all(i.source_url == "https://bethnalgreenventures.com/portfolio"
               for i in items)
    assert all(i.structured["company_website"] for i in items)
    # An exited venture is flagged, not dropped — the gates own that decision.
    assert any(i.structured.get("exited") for i in items)
    assert any("Healthy Lives" in i.structured["themes"] for i in items)


def check_carbon13(items):
    """A venture builder, so a post with no funding language is a cohort
    announcement rather than a bare mention — that default is the difference
    between this and the reference RSS adapter."""
    assert all(i.structured["accelerator_name"] == "Carbon13" for i in items)
    assert all(i.structured["sector"] == "climate_tech" for i in items)
    assert all(i.structured["stage"] == "pre_seed" for i in items)
    assert any(i.kind_hint == "accelerator_cohort" for i in items)
    assert any(i.kind_hint == "funding_round" for i in items)


def check_converge(items):
    """04-sources calls this RSS at /updates/; that URL is an HTML page. The
    site is WordPress and wp-json carries the full article, so that is what the
    adapter reads — the ledger row means "there is a feed", not "call this"."""
    assert all(i.structured["accelerator_name"] == "Converge" for i in items)
    assert all(i.structured["full_text_in_feed"] is True for i in items)
    # Scotland is `uk_regions`: outside every North East and Yorkshire mandate.
    assert all(i.structured["hq_region"] == "uk_regions" for i in items)
    assert any(i.kind_hint == "accelerator_cohort" for i in items)


def check_sheffield(items):
    """Yorkshire is a hard mandate for both Anticus vehicles, so the region
    hint here is doing routing work rather than decorating a row."""
    assert all(i.structured["hq_region"] == "yorkshire" for i in items)
    assert all(i.structured["university_name"] == "University of Sheffield"
               for i in items)
    assert any(i.published_at == date(2026, 6, 1) for i in items)
    # The card is the anchor: every item must carry a real provenance URL.
    assert all(i.source_url.startswith("https://www.sheffield.ac.uk/commercialisation/")
               for i in items)
    assert any(i.structured.get("is_university_spinout") for i in items)


def check_founders_factory(items):
    """Relative timestamps only, so the date is unknown and says so.

    Guessing the crawl day would invent a fact the freshness gate would then
    trust. Unknown passes and flags, which is the whole `None` is not `0` rule
    applied to a date.
    """
    assert all(i.published_at is None for i in items)
    assert all(i.structured["date_confidence"] == "unknown" for i in items)
    assert all(i.structured["age_source"] == "unknown" for i in items)
    assert any(i.kind_hint == "funding_round" for i in items)
    # The first card inlines a <style> block; CSS must not reach stage ③.
    assert all("box-sizing" not in (i.body_text or "") for i in items)
    assert all(not (i.body_text or "").startswith(".css-") for i in items)


def check_techstars_london(items):
    """The newsroom is global, and the adapter must not pretend otherwise.

    04-sources calls this "Techstars London", but a live read returns Boston
    and MENAT. Asserting a London geography here would put a false fact into
    the record that the geography gate would then trust, so the adapter emits
    no region at all and the gate throws the rest away.
    """
    assert all("hq_region" not in i.structured for i in items)
    assert all("hq_city" not in i.structured for i in items)
    assert all(i.structured["accelerator_name"] == "Techstars" for i in items)
    assert any(i.kind_hint == "accelerator_cohort" for i in items)
    # The fixture deliberately carries non-UK items — that is the real feed.
    assert any("Boston" in i.title or "MENAT" in i.title for i in items)


def check_vc_portfolios(items):
    assert all(i.structured["on_vc_portfolio"] is True for i in items)
    assert all(i.kind_hint == "vc_portfolio_listing" for i in items)
    # Matching is by norm_key, never raw name: "Acme Robotics Ltd" is
    # "Acme Robotics" from Companies House.
    assert "acmerobotics" in {i.structured["norm_key"] for i in items}
    assert all(i.external_id.startswith("dsw:") for i in items)


def check_companies_house(items):
    assert all(i.kind_hint == "incorporation" for i in items)
    # Company numbers are strings forever: zero padding and the SC prefix are
    # real data, and int() destroys both.
    ids = {i.external_id for i in items}
    assert "00445790" in ids and "SC812345" in ids
    assert all(i.published_at.isoformat() == i.structured["date_of_creation"] for i in items)


CASES: dict[str, Case] = {
    "northern_accelerator": Case(
        parse=lambda: northern_accelerator.ADAPTER.parse(load("northern_accelerator.json")),
        changed=lambda: northern_accelerator.ADAPTER.parse(
            load("northern_accelerator_CHANGED.json")),
        check=check_northern_accelerator,
    ),
    "cambridge_enterprise": Case(
        parse=lambda: cambridge_enterprise.ADAPTER.parse(load("cambridge_enterprise.json")),
        changed=lambda: cambridge_enterprise.ADAPTER.parse(
            load("cambridge_enterprise_CHANGED.json")),
        check=check_cambridge_enterprise,
    ),
    "zinc_vc": Case(
        parse=lambda: zinc_vc.ADAPTER.parse(load("zinc_vc.json")),
        changed=lambda: zinc_vc.ADAPTER.parse(load("zinc_vc_CHANGED.json")),
        check=check_zinc_vc,
    ),
    "businesscloud": Case(
        parse=lambda: businesscloud.ADAPTER.parse(load("businesscloud.xml")),
        changed=lambda: businesscloud.ADAPTER.parse(load("businesscloud_CHANGED.xml")),
        check=check_businesscloud,
    ),
    "uktn": Case(
        parse=lambda: uktn.ADAPTER.parse(load("uktn.json")),
        changed=lambda: uktn.ADAPTER.parse(load("uktn_CHANGED.json")),
        check=check_uktn,
    ),
    "govuk_search": Case(
        parse=lambda: govuk_search.ADAPTER.parse(load("govuk_search.json")),
        changed=lambda: govuk_search.ADAPTER.parse(load("govuk_search_CHANGED.json")),
        check=check_govuk_search,
    ),
    "oxford_innovation": Case(
        parse=lambda: oxford_innovation.ADAPTER.parse(load("oxford_innovation.html")),
        changed=lambda: oxford_innovation.ADAPTER.parse(load("oxford_innovation_CHANGED.html")),
        check=check_oxford_innovation,
    ),
    "conception_x": Case(
        parse=lambda: _diffed(conception_x.ADAPTER, "conception_x.html"),
        changed=lambda: conception_x.ADAPTER.parse(load("conception_x_CHANGED.html")),
        check=check_conception_x,
    ),
    "entrepreneur_first": Case(
        parse=lambda: _diffed(entrepreneur_first.ADAPTER, "entrepreneur_first.html"),
        changed=lambda: entrepreneur_first.ADAPTER.parse(
            load("entrepreneur_first_CHANGED.html")),
        check=check_entrepreneur_first,
    ),
    "vc_portfolios": Case(
        parse=lambda: vc_portfolios.ADAPTER.parse(load("vc_portfolios.html")),
        changed=lambda: vc_portfolios.ADAPTER.parse(load("vc_portfolios_CHANGED.html")),
        check=check_vc_portfolios,
        dated=False,            # a portfolio page states no dates and never will
    ),
    "companies_house": Case(
        parse=lambda: _ch_items(API_FIXTURES / "ch_advanced_search_page.json"),
        changed=lambda: _ch_items(SOURCE_FIXTURES / "companies_house_CHANGED.json"),
        check=check_companies_house,
    ),

    # ---- 04-sources Tier 2 --------------------------------------------------
    "startups_magazine": Case(
        parse=lambda: startups_magazine.ADAPTER.parse(load("startups_magazine.json")),
        changed=lambda: startups_magazine.ADAPTER.parse(
            load("startups_magazine_CHANGED.json")),
        check=check_startups_magazine,
    ),
    "bdaily_regional": Case(
        parse=lambda: bdaily_regional.ADAPTER.parse(load("bdaily_regional.xml")),
        changed=lambda: bdaily_regional.ADAPTER.parse(
            load("bdaily_regional_CHANGED.xml")),
        check=check_bdaily_regional,
    ),
    "edinburgh_innovations": Case(
        parse=lambda: edinburgh_innovations.ADAPTER.parse(
            load("edinburgh_innovations.html")),
        changed=lambda: edinburgh_innovations.ADAPTER.parse(
            load("edinburgh_innovations_CHANGED.html")),
        check=check_edinburgh_innovations,
    ),
    "ucl_ventures": Case(
        parse=lambda: ucl_ventures.ADAPTER.parse(load("ucl_ventures.html")),
        changed=lambda: ucl_ventures.ADAPTER.parse(load("ucl_ventures_CHANGED.html")),
        check=check_ucl_ventures,
    ),
    "bethnal_green": Case(
        parse=lambda: _diffed(bethnal_green.ADAPTER, "bethnal_green.html"),
        changed=lambda: bethnal_green.ADAPTER.parse(load("bethnal_green_CHANGED.html")),
        check=check_bethnal_green,
    ),
    "carbon13": Case(
        parse=lambda: carbon13.ADAPTER.parse(load("carbon13.xml")),
        changed=lambda: carbon13.ADAPTER.parse(load("carbon13_CHANGED.xml")),
        check=check_carbon13,
    ),
    "converge": Case(
        parse=lambda: converge.ADAPTER.parse(load("converge.json")),
        changed=lambda: converge.ADAPTER.parse(load("converge_CHANGED.json")),
        check=check_converge,
    ),
    "sheffield": Case(
        parse=lambda: sheffield.ADAPTER.parse(load("sheffield.html")),
        changed=lambda: sheffield.ADAPTER.parse(load("sheffield_CHANGED.html")),
        check=check_sheffield,
    ),
    "founders_factory": Case(
        parse=lambda: founders_factory.ADAPTER.parse(load("founders_factory.html")),
        changed=lambda: founders_factory.ADAPTER.parse(
            load("founders_factory_CHANGED.html")),
        check=check_founders_factory,
        dated=False,        # relative timestamps only — see the adapter docstring
    ),
    "techstars_london": Case(
        parse=lambda: techstars_london.ADAPTER.parse(load("techstars_london.html")),
        changed=lambda: techstars_london.ADAPTER.parse(
            load("techstars_london_CHANGED.html")),
        check=check_techstars_london,
        dated=False,        # the newsroom listing states no date
    ),
}


# ------------------------------------------------------------- the two tests


@pytest.mark.parametrize("key", list(CASES))
def test_adapter_parses_committed_fixture(key):
    """09-test-plan §4, applied to every adapter in the registry."""
    case = CASES[key]
    items = case.parse()

    assert len(items) >= case.min_items
    if case.dated:
        assert all(i.published_at is not None for i in items)
    assert all(i.source_url.startswith("https://") for i in items)
    assert len({i.external_id for i in items}) == len(items)     # ids are unique
    assert all(i.source_key == key for i in items)
    assert all(i.title and i.title.strip() for i in items)
    assert all(i.external_id and str(i.external_id).strip() for i in items)

    case.check(items)


@pytest.mark.parametrize("key", list(CASES))
def test_adapter_detects_layout_change(key):
    """The dangerous failure is 200 OK with an empty list — it looks like a
    quiet week rather than a bug. Each `_CHANGED` fixture is a *successful*
    response whose shape moved, so returning `[]` here would be silent data
    loss; only an exception is acceptable."""
    with pytest.raises(LayoutChanged) as excinfo:
        CASES[key].changed()
    assert excinfo.value.source_key == key
    assert excinfo.value.detail


# ------------------------------------------------------ the Monday live check


@pytest.mark.live
@pytest.mark.parametrize("key", TIER_1_SOURCES)
def test_source_still_reachable_and_parseable(key):
    """Not in CI: websites change, and a red CI nobody trusts is worse than no
    CI. Run it Mondays — it is the early warning for the failure that silently
    degrades quality (09-test-plan §4)."""
    from radar.config.defaults import default_config
    from radar.fetch.http import HttpClient
    from radar.sources.companies_house import api_key_from_env

    if key == "companies_house" and not api_key_from_env():
        pytest.skip("companies_house needs CH_API_KEY")

    adapter = REGISTRY[key]
    with HttpClient() as http:
        items = list(adapter.fetch(FetchContext(
            http=http, config=default_config(), now=date.today())))

    assert len(items) > 0, f"{key} returned nothing — check for a layout change"


# --------------------------------------------------- adapter-specific detail


def test_uktn_never_builds_a_query_string():
    """UKTN's robots.txt disallows `/*?`. The adapter refuses rather than
    stripping — a silent strip lets a future edit reintroduce the violation
    and never fail a test."""
    with pytest.raises(uktn.QueryStringForbidden):
        uktn._assert_no_query("https://www.uktech.news/wp-json/wp/v2/posts/latest?per_page=50")
    assert uktn.INDEX == uktn._assert_no_query(uktn.INDEX)
    assert "?" not in uktn.INDEX


def test_uktn_reads_the_article_body_and_notices_when_it_moves():
    """`latest` carries no body, so the text costs one fetch per article. An
    article whose content wrapper was renamed must fail loudly, not return an
    empty body that reads as a thin article."""
    text = uktn.ADAPTER.parse_article(load("uktn_article.html"))
    assert "geospatial foundation models" in text
    assert "dataLayer" not in text               # scripts stripped, not inlined

    with pytest.raises(LayoutChanged):
        uktn.ADAPTER.parse_article(load("uktn_article_CHANGED.html"))


def test_entrepreneur_first_fetch_filters_to_recent_london():
    """04-sources §2 row 10: a London filter and a founded-year filter. The
    fixture carries a Bangalore venture and a 2016 alumnus; neither is in scope,
    and neither may be dropped by the *parser* — the gates own that decision."""
    parsed = entrepreneur_first.ADAPTER.parse(load("entrepreneur_first.html"))
    assert len(parsed) == 5

    http = StubHttp(load("entrepreneur_first.html"))
    got = list(entrepreneur_first.ADAPTER.fetch(_ctx(http)))

    names = {i.structured["company_name"] for i in got}
    assert names == {"Meridian Compute", "Cordage Labs", "Stanchion"}
    assert http.limiter.delays["www.joinef.com"] == 10.0        # Crawl-delay: 10


def test_conception_x_returns_only_new_ventures_after_the_first_run(db):
    """An undated page's only freshness signal is what changed. The bootstrap
    run returns everything and says so; the second run returns nothing, which
    is correct — and is exactly why `guard_nonempty` has to fire on the parse,
    not on the diff."""
    page = load("conception_x.html")
    first = conception_x.ADAPTER.diff(conception_x.ADAPTER.parse(page),
                                      _ctx(StubHttp(), db=db))
    assert len(first) == 4
    assert all(i.structured["bootstrap"] is True for i in first)

    second = conception_x.ADAPTER.diff(conception_x.ADAPTER.parse(page),
                                       _ctx(StubHttp(), db=db))
    assert second == []

    # The new venture has to arrive in the *live* markup. Appending a
    # `.venture-card` would prove nothing: `select_any` stops at
    # `.portfolio-collection-item`, so the legacy card would never be read.
    grown = page.replace(
        "<!--APPEND-VENTURE-HERE-->",
        '<div role="listitem" class="portfolio-collection-item w-dyn-item">'
        '<div class="portfolio-def-info"><div class="portfolio-title-wrap">'
        '<div class="para-xxl-24">Wearside Optics</div></div>'
        '<div class="portfolio-def-cohort">'
        '<div fs-list-field="cohort" class="label-s-14">CX26</div></div>'
        "</div></div>")
    assert grown != page, "fixture lost its append marker"
    third = conception_x.ADAPTER.diff(conception_x.ADAPTER.parse(grown),
                                      _ctx(StubHttp(), db=db))
    assert [i.title for i in third] == ["Wearside Optics"]
    assert third[0].structured["bootstrap"] is False
    assert third[0].published_at == RUN_DATE
