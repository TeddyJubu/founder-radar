"""09-test-plan §4 — the two Phase 8 grant adapters.

`10-build-plan.md` Phase 8 items 3 and 4: UKRI Gateway to Research and the
Innovate UK funded-projects XLSX. Both are **quality** signals rather than
freshness signals (Phase 5 note), so what they have to get right is not "is
this new" but "does this award reach `06-scoring §3` as the `grant`
qualifier" — which is what `test_grant_award_is_the_grant_qualifier` pins.

Both required tests per adapter, per §4:

* `..._parses_committed_fixture` — `parse()` is a pure function from committed
  bytes to `RawItem[]`. No socket, no `ctx`, no clock.
* `..._detects_layout_change` — the dangerous failure is 200 OK with an empty
  list, because it reads as a quiet week. `ukri_gtr_CHANGED.json` is a valid
  GtR envelope with `results: []`; `innovate_uk_CHANGED.xlsx` is a workbook
  that still opens, with the columns renamed. Neither may return `[]`.

Both fixtures are trimmed captures of the real responses, taken 8 August 2026:
33,170 Innovate-UK-funded projects on `gtr.ukri.org/api/search/project`, and
51,899 rows of `IUK-060726-FundedProjectsFromFinancialYear2016to2017toPresent
.xlsx`. `personRoles` — the named principal investigators — is dropped from the
GtR fixture exactly as the adapter drops it, so no personal data is committed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from radar.fetch.layout import LayoutChanged
from radar.sources import REGISTRY, innovate_uk, ukri_gtr
from radar.sources.base import FetchContext

SOURCE_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"

#: The day both fixtures were captured.
RUN_DATE = date(2026, 8, 8)

#: An API adapter is tested through committed JSON, a FILE adapter through a
#: committed workbook. Same seam, different bytes.
_EXT = {"ukri_gtr": ".json", "innovate_uk": ".xlsx"}


def load_bytes(name: str) -> bytes:
    return (SOURCE_FIXTURES / name).read_bytes()


class _StubResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status = status
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class _StubHttp:
    """Serves one committed body for every GET. Never touches a socket."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _StubResponse(self.text)


def _ctx(http) -> FetchContext:
    return FetchContext(http=http, config=None, db=None, now=RUN_DATE)


def _assert_items_well_formed(items) -> None:
    """The plan's shared shape checks."""
    assert len(items) >= 3
    assert all(i.published_at is not None for i in items)
    assert all(i.source_url.startswith("https://") for i in items)
    assert all(i.title for i in items)
    assert len({i.external_id for i in items}) == len(items)    # ids are unique
    assert all(i.kind_hint == "grant_award" for i in items)
    assert all(i.structured["qualifiers"] == ["grant"] for i in items)


# ------------------------------------------------- UKRI Gateway to Research


def test_ukri_gtr_parses_committed_fixture():
    items = ukri_gtr.ADAPTER.parse(load_bytes("ukri_gtr.json"))
    _assert_items_well_formed(items)

    names = {i.structured["company_name"] for i in items}
    assert {"NATURAL NEGATIVE LTD", "ARDA BIOMATERIALS LTD", "VacTimmune"} <= names
    # A Knowledge Transfer Partnership is led by the university, so its lead
    # organisation is not a company and must not become one. Dropped in the
    # adapter, not at render time.
    assert not any("UNIVERSITY" in n.upper() for n in names)
    assert all(i.structured["funder"] == "Innovate UK" for i in items)
    assert all(i.structured["date_confidence"] == "exact" for i in items)
    assert all(i.structured["grant_amount_gbp"] > 0 for i in items)
    # `fund.start` is epoch milliseconds, not a date string.
    assert any(i.published_at == date(2026, 6, 30) for i in items)
    assert all(i.source_url.startswith("https://gtr.ukri.org/projects?ref=") for i in items)


def test_ukri_gtr_detects_layout_change():
    """A valid envelope with an empty `results` reads as a quiet week."""
    with pytest.raises(LayoutChanged):
        ukri_gtr.ADAPTER.parse(load_bytes("ukri_gtr_CHANGED.json"))


def test_ukri_gtr_asks_for_innovate_uk_awards_newest_first():
    """The facet is GtR's own base64 id, and the sort is what makes page 1 the
    newest awards rather than the most relevant ones."""
    import base64

    assert base64.b64decode(ukri_gtr.INNOVATE_UK_FACET) == b"funder|Innovate UK|string"

    http = _StubHttp(load_bytes("ukri_gtr.json").decode())
    items = list(ukri_gtr.ADAPTER.fetch(_ctx(http)))
    assert len(items) == 6                       # deduplicated across both pages
    params = http.calls[0][1]["params"]
    assert params["selectedFacets"] == ukri_gtr.INNOVATE_UK_FACET
    assert params["selectedSortableField"] == "pro.sd"
    assert params["selectedSortOrder"] == "DESC"


# ---------------------------------------------------- Innovate UK XLSX file


def test_innovate_uk_parses_committed_fixture():
    items = innovate_uk.ADAPTER.parse(load_bytes("innovate_uk.xlsx"))
    _assert_items_well_formed(items)

    names = {i.structured["company_name"] for i in items}
    assert {"NATURAL NEGATIVE LTD", "ARDA BIOMATERIALS LTD"} <= names
    # Academic, Large and withdrawn participants are all in the fixture and
    # none of them is an early-stage company we could route to a fund.
    assert not any("Queen's University" in n for n in names)
    assert not any("BOEING" in n for n in names)
    assert len([i for i in items if i.structured["grant_reference"] == "10199999"]) == 0

    natural = next(i for i in items if i.structured["company_name"] == "NATURAL NEGATIVE LTD")
    assert natural.structured["crn"] == "15492053"          # Companies House number
    assert natural.structured["grant_amount_gbp"] == 249900.0
    assert natural.structured["postal_code"] == "BN2 4GL"
    assert natural.published_at == date(2026, 7, 1)         # Excel serial 46204

    # `_route_of` reads `company_number` as "this came off the register", which
    # would put a Track A source behind the Track B qualification gate.
    assert all("company_number" not in i.structured for i in items)


def test_innovate_uk_detects_layout_change():
    """The workbook still opens and still has 51,899-shaped rows — the columns
    moved. Returning `[]` here would look like a month with no awards."""
    with pytest.raises(LayoutChanged):
        innovate_uk.ADAPTER.parse(load_bytes("innovate_uk_CHANGED.xlsx"))

    # And the coarser failure: something that is not a workbook at all.
    with pytest.raises(LayoutChanged):
        innovate_uk.ADAPTER.parse(b"<html>maintenance</html>")


def test_innovate_uk_filters_by_date_without_a_clock():
    """`parse` takes `since` rather than reading the clock, so the committed
    fixture does not rot: the 2019 award is in it deliberately."""
    blob = load_bytes("innovate_uk.xlsx")
    assert len(innovate_uk.ADAPTER.parse(blob, since=date(2026, 1, 1))) == 6
    assert len(innovate_uk.ADAPTER.parse(blob)) == 7
    assert any(i.published_at == date(2019, 1, 1)
               for i in innovate_uk.ADAPTER.parse(blob))


def test_innovate_uk_discovers_the_dated_download_link():
    """The filename carries the release date, so a pinned URL 404s within a
    month. It is read off the publication page, or the run fails loudly."""
    page = ('<a class="govuk-link" '
            'href="https://www.ukri.org/wp-content/uploads/2022/03/'
            'IUK-060726-FundedProjectsFromFinancialYear2016to2017toPresent.xlsx">'
            'Innovate UK funded projects</a>')
    assert innovate_uk.ADAPTER.discover(page).endswith("2016to2017toPresent.xlsx")

    with pytest.raises(LayoutChanged):
        innovate_uk.ADAPTER.discover("<p>This publication has been withdrawn.</p>")


def test_innovate_uk_discovers_the_current_2016_to_present_filename():
    """UKRI renamed the workbook from the old camel-case slug to a hyphenated one."""
    page = (
        '<a href="https://www.ukri.org/wp-content/uploads/2026/08/'
        'IUK-20260804-Innovate-UK-funded-projects-between-2004-and-financial-year-2015-16.xlsx">'
        'older workbook</a>'
        '<a href="https://www.ukri.org/wp-content/uploads/2026/08/'
        'IUK-20260804-Innovate-UK-funded-projects-from-financial-year-2016-17-to-present.xlsx">'
        'current workbook</a>'
    )
    assert innovate_uk.ADAPTER.discover(page).endswith(
        "Innovate-UK-funded-projects-from-financial-year-2016-17-to-present.xlsx"
    )


# ------------------------------------------------------- where it has to land


@pytest.mark.parametrize("adapter", [ukri_gtr.ADAPTER, innovate_uk.ADAPTER])
def test_grant_award_is_the_grant_qualifier(adapter):
    """The contract between these adapters and 06-scoring §3.

    `kind_hint` is the `signal.kind` vocabulary (03-data-model §3, which names
    these two sources against `grant_award`), and `qualify._SIGNAL_QUALIFIERS`
    maps that kind to the `grant` qualifier. Get the string wrong and the
    award silently buys the company nothing.
    """
    from radar.score.derive import Signal
    from radar.score.qualify import derive_qualifiers

    from tests.factories import registry_company

    company = registry_company(qualifiers=[])
    assert derive_qualifiers(company) == []                 # nothing yet

    emitted = adapter.parse(load_bytes(f"{adapter.key}{_EXT[adapter.key]}"))[0]
    company.signals = [Signal(kind=emitted.kind_hint, headline="Innovate UK award")]
    assert "grant" in derive_qualifiers(company)
    assert adapter.kind == "grant"


def test_both_sources_are_registered():
    """NFR-5: one new file, one registry line, and `sources --list` shows it."""
    for key in ("ukri_gtr", "innovate_uk"):
        adapter = REGISTRY[key]
        assert adapter.key == key
        assert adapter.track == "A"
        assert adapter.requires_browser is False
        assert adapter.endpoint.startswith("https://")


# ------------------------------------------------------------- weekly live run


@pytest.mark.live
@pytest.mark.parametrize("key", ["ukri_gtr", "innovate_uk"])
def test_source_still_reachable_and_parseable(key):
    """Monday's early-warning run. Deselected by `addopts`; must never need the
    network to *collect*."""
    from radar.fetch.http import HttpClient

    adapter = REGISTRY[key]
    items = list(adapter.fetch(FetchContext(
        http=HttpClient(), config=None, db=None, now=date.today())))
    assert items, f"{key} returned nothing — check for a layout change"
    assert all(i.structured.get("company_name") for i in items)
