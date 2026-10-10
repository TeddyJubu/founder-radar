"""The denylist's portfolio pages, as each site presented them on 10 Oct 2026.

Until then only Outward parsed: DSW and Mercia had become logo-only grids,
Anticus moved to a B12 page, Praetura and Par Equity merged into PXN, and
Outward's heading carried an "Exited" badge into the name. The markup below is
the real card shape of each site with invented company names.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from radar.fetch.layout import LayoutChanged
from radar.sources import vc_portfolios as vc
from radar.sources import zinc_vc

SITE = {s.slug: s for s in vc.SITES}

DSW = """<main>{}</main>""".format("".join(
    f'<div class="elementor-element elementor-widget elementor-widget-image"><div class="elementor-widget-container">'
    f'<a href="https://dsw.vc/portfolio/{slug}/"><img src="https://dsw.vc/wp-content/uploads/x-{i}.jpg" '
    f'class="attachment-medium size-medium wp-image-{1000 + i}" alt=""></a></div></div>'
    for i, slug in enumerate(["example-instruments", "one-utility-widget", "loamweave-2",
                              "quantia-formerly-quantum-labs"])))

OUTWARD = """<div class="tile tile--company"><a class="tile__link noline" href="https://outwardvc.com/companies/anorakia/">View Anorakia</a>
<div class="tile__bd bd"><h4 class="heading-med tile__heading hiddenlinkline"><span class="tile__heading font-reg"><span class="linkline">Anorakia</span></span>
<span class="text-sml tile__heading__exited">Exited</span></h4><div class="tile__description">Reimagining widgets</div></div></div>"""

ANTICUS = "<ul>{}</ul>".format("".join(
    f'<li class="items-grid__item items-grid__item--bg-default tag-all " data-title="{name.lower()}">'
    f'<div class="items-grid__item-body"><div class="items-grid__item-image">'
    f'<a aria-label="More about {name}" class="quick-view-1 sb-animate-image" '
    f'href="https://cdn.b12.io/client_media/{i}-jpg-hero_image.jpeg"><figure class="option-image">'
    f'<img alt="{name.lower()}_logo.jpg" src="x.jpg"></figure></a></div></div></li>'
    for i, name in enumerate(["Spoonworks Cereals", "Batch&#x27;d Example"])))

MERCIA = """<div class="search-filter-query"><div class="posts row">
<div class="list column"><a id="example-gears-2" href="https://www.mercia.co.uk/portfolio/example-gears-2/"><div class="outer"><div class="inner"></div></div></a></div>
<div class="list column"><a id="8point8-widgets" href="https://www.mercia.co.uk/portfolio/8point8-widgets/"><div class="outer"><div class="inner"></div></div></a></div>
</div></div>"""

PXN = """<section class="pv-section py-5"><div class="row row-cols-2 row-cols-lg-4">
<div class="col"><a href="#" data-id="1" class="pv-portfolio-link js-overlay-item"><div class="pv-portfolio-link__img-wrap">
<img class="d-block w-100 p-4" src="https://example.test/accesswidget.png" alt="Access Widget"><div class="pv-portfolio-link__img-desc">Read More</div></div></a></div>
<div class="col"><a href="#" data-id="2" class="pv-portfolio-link js-overlay-item"><div class="pv-portfolio-link__img-wrap">
<img class="d-block w-100 p-4" src="https://example.test/aerowidget.png" alt="AeroWidget"><div class="pv-portfolio-link__img-desc">Read More</div></div></a></div>
</div></section>"""


def names(slug, payload):
    return [i.title for i in vc.ADAPTER.parse(payload, site=SITE[slug])]


def test_dsw_logo_grid_reads_names_from_the_card_links():
    assert names("dsw", DSW) == ["Example Instruments", "One Utility Widget", "Loamweave", "Quantia"]
    items = vc.ADAPTER.parse(DSW, site=SITE["dsw"])
    assert items[0].external_id == "dsw:example-instruments"
    assert items[0].structured["norm_key"] == "exampleinstruments"


def test_outward_name_excludes_the_exited_badge():
    assert names("outward", OUTWARD) == ["Anorakia"]


def test_anticus_names_come_from_the_more_about_label_and_ids_never_from_lightbox_images():
    items = vc.ADAPTER.parse(ANTICUS, site=SITE["anticus"])
    assert [i.title for i in items] == ["Spoonworks Cereals", "Batch'd Example"]
    assert items[0].external_id == "anticus:spoonworkscereals"
    assert items[0].source_url == SITE["anticus"].url


def test_mercia_slugs_drop_the_wordpress_duplicate_suffix():
    assert names("mercia", MERCIA) == ["Example Gears", "8point8 Widgets"]


def test_pxn_reads_logo_alt_text_and_ignores_overlay_anchors():
    items = vc.ADAPTER.parse(PXN, site=SITE["pxn"])
    assert [i.title for i in items] == ["Access Widget", "AeroWidget"]
    assert items[0].external_id == "pxn:accesswidget"
    assert items[0].source_url == SITE["pxn"].url


def test_site_fingerprint_ignores_logo_and_image_id_churn():
    vc.ADAPTER.parse(DSW, site=SITE["dsw"])
    first = vc.ADAPTER.last_fingerprint
    vc.ADAPTER.parse(DSW.replace("wp-image-100", "wp-image-900").replace("x-", "y-"), site=SITE["dsw"])
    assert vc.ADAPTER.last_fingerprint == first and first.startswith("vc:")


def test_a_site_selector_giving_way_to_a_generic_one_changes_the_fingerprint():
    vc.ADAPTER.parse(OUTWARD, site=SITE["outward"])
    own = vc.ADAPTER.last_fingerprint
    generic = '<div class="portfolio-item"><h3>Anorakia</h3><a href="/companies/anorakia/">x</a></div>'
    vc.ADAPTER.parse(generic, site=SITE["outward"])
    assert vc.ADAPTER.last_fingerprint != own


def _ctx_with(responses):
    def get(url, **_):
        status, body = responses[url]
        return SimpleNamespace(status=status, ok=200 <= status < 300, text=body)
    return SimpleNamespace(http=SimpleNamespace(get=get), db=None)


def test_unchanged_pages_are_not_a_failure():
    """10 Oct 2026: every page answered 304 or failed, and the source was marked failed."""
    adapter = vc.VcPortfoliosAdapter()
    adapter.sites = (SITE["dsw"], SITE["outward"])
    ctx = _ctx_with({SITE["dsw"].url: (304, ""), SITE["outward"].url: (403, "")})
    assert adapter.fetch(ctx) == []
    assert adapter.last_failures == ["outward: HTTP 403"]


def test_nothing_parsed_and_nothing_unchanged_is_still_a_layout_change():
    adapter = vc.VcPortfoliosAdapter()
    adapter.sites = (SITE["dsw"],)
    ctx = _ctx_with({SITE["dsw"].url: (200, "<main><p>" + "redesigned " * 40 + "</p></main>")})
    with pytest.raises(LayoutChanged):
        adapter.fetch(ctx)


def test_northstar_gap_is_documented_not_silent():
    assert "northstar" not in SITE
    assert "Northstar" in vc.__doc__ or "Northstar" in open(vc.__file__).read()


# ------------------------------------------------------------------ zinc feed


ZINC_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>Zinc</title>
<item><title>Rewiring widgets: Why Zinc invested in Example Therapeutics</title><link>https://www.zinc.vc/example-therapeutics/</link>
<guid isPermaLink="false">https://www.zinc.vc/?p=2709</guid><pubDate>Thu, 08 Oct 2026 09:00:00 +0000</pubDate><description>x</description></item>
<item><title>Investing in Neurowidget to bring precision imaging to surgery</title><link>https://www.zinc.vc/neurowidget/</link>
<guid isPermaLink="false">https://www.zinc.vc/?p=2671</guid><pubDate>Mon, 05 Oct 2026 09:00:00 +0000</pubDate><description>x</description></item>
<item><title>Reflections from the Zinc Summer Gathering</title><link>https://www.zinc.vc/summer/</link>
<guid isPermaLink="false">https://www.zinc.vc/?p=2677</guid><pubDate>Fri, 02 Oct 2026 09:00:00 +0000</pubDate><description>x</description></item>
</channel></rss>"""


def test_zinc_reads_the_public_feed_and_keeps_rest_post_ids():
    items = zinc_vc.ADAPTER.parse(ZINC_FEED)
    assert [i.external_id for i in items] == ["2709", "2671", "2677"]
    named = [i.structured.get("company_name") for i in items]
    assert named == ["Example Therapeutics", "Neurowidget", None]
    assert zinc_vc.ADAPTER.last_fingerprint.startswith("rss:")
    assert zinc_vc.ENDPOINT == "https://www.zinc.vc/feed/"
