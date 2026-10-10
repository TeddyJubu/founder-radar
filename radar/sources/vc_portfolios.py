"""VC portfolio pages, read **inverted** — the denylist.

Every other adapter in this package is looking for companies to surface. This
one is looking for companies to *demote*. A company on dsw.vc, Northstar,
Outward or Anticus has already been found by a fund; 06-scoring §1 turns that
into the `already_on_vc_portfolio` gate, and 03-data-model calls the resulting
signal `vc_portfolio_listing` and marks it explicitly negative.

That inversion is the whole client complaint, restated: version 1 read these
pages as a source of leads, which is a record of companies that *already
raised*. Version 2 reads them to know what not to bother Aryan with.

Three design notes:

* **Selectors are generic, not per-site.** Twenty-odd VC sites on five
  different site builders is twenty-odd maintenance liabilities; a small
  candidate list plus "the card's heading is the company name" survives a
  rebrand, and `LayoutChanged` catches the case where it does not.
* **One site failing must not lose the other nineteen.** Each site is fetched
  inside its own try/except, mirroring the per-source isolation one level up.
* **Matching is by `norm_key`, never by raw name.** "Acme Robotics Ltd" on a
  portfolio page and "Acme Robotics" from Companies House are one company.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable

from radar.fetch.layout import LayoutChanged, check_fingerprint
from radar.sources._common import (
    absolute_url,
    attr_of,
    guard_nonempty,
    html_doc,
    norm_key,
    select_any,
    selector_fingerprint,
    slug_of,
    text_of,
)
from radar.sources.base import FetchContext, RawItem

CARD_SELECTORS = (
    ".tile--company",              # Outward VC — same template as joinef.com
    ".portfolio-item",
    ".portfolio__item",
    ".w-dyn-item",
    ".company-card",
    "li.company",
    ".portfolio-grid > a",
)
NAME_SELECTORS = (".portfolio-item__title", ".company-name", ".tile__name",
                  "h3", "h4", "h2",
                  ".title", "img[alt]")


@dataclass(frozen=True)
class VcSite:
    slug: str
    name: str
    url: str
    #: Optional site-specific override when the generic list is not enough.
    card_selector: str | None = None
    #: CSS inside a card that holds only the name (Outward's heading also carries
    #: an "Exited" badge, which made "Anorak" read as "Anorak Exited").
    name_selector: str | None = None
    #: Where the name lives when the card has no text of its own:
    #: "slug" — the card link's last path segment (logo-only grids);
    #: "aria_more_about" — `aria-label="More about X"` (B12 site builder);
    #: "img_alt" — the logo's alt text. Applies only when `card_selector` matched.
    name_from: str | None = None


#: The four funds named in 04-sources, plus the wider UK set the ledger implies.
#: Adding one is a single line — that is the promise this file has to keep.
#:
#: Checked live on 10 Oct 2026; until then only Outward parsed (35 names a day
#: from ten sites). Sites with no list this adapter may read are left out, each
#: for a stated reason, rather than failing every week:
#:
#: * **Northstar Ventures** — the portfolio grid is filled in by JavaScript, and
#:   the two machine-readable copies (WordPress REST, sitemap) both send
#:   `X-Robots-Tag: noindex`, which 04-sources §5 tells us to honour. Northstar
#:   is one of the client's four funds, so this gap is reported, not hidden.
#: * **Maven Capital Partners**, **Fuel Ventures** — JavaScript-rendered lists.
#: * **Ada Ventures** — no portfolio page; investments appear only as articles.
#: * **Praetura Ventures** and **Par Equity** — merged as PXN Ventures, whose one
#:   portfolio page lists both books.
#: * **Zinc** — JS-rendered portfolio; covered by the `zinc_vc` feed instead.
SITES: tuple[VcSite, ...] = (
    # Logo-only Elementor grid; each card links to /portfolio/<company>/.
    VcSite("dsw", "DSW Ventures", "https://dsw.vc/portfolio/",
           card_selector='.elementor-widget-image a[href*="/portfolio/"]', name_from="slug"),
    # /portfolio/ now redirects here.
    VcSite("outward", "Outward VC", "https://outwardvc.com/companies/",
           card_selector=".tile--company", name_selector=".tile__heading .linkline"),
    # B12 site; logo cards whose link reads "More about <company>".
    VcSite("anticus", "Anticus Partners", "https://anticuspartners.com/Portfolio",
           card_selector="li.items-grid__item", name_from="aria_more_about"),
    # First page of a filtered grid (32 of several hundred); logo-only cards.
    VcSite("mercia", "Mercia Ventures", "https://www.mercia.co.uk/about-us/portfolio/",
           card_selector='.posts .list a[href*="/portfolio/"]', name_from="slug"),
    VcSite("pxn", "PXN Ventures (Par Equity, Praetura Ventures)",
           "https://www.pxnventures.co.uk/portfolio/",
           card_selector="a.pv-portfolio-link", name_from="img_alt"),
)


class VcPortfoliosAdapter:
    key = "vc_portfolios"
    kind = "portfolio"
    schedule = "weekly"
    requires_browser = False
    # Neither track (04-sources §2, row 14): this is the inverted source. It
    # discovers nothing — it marks what the funds have already seen.
    track = "—"
    endpoint = SITES[0].url
    homepage = "https://dsw.vc"
    sites = SITES

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        items: list[RawItem] = []
        failures: list[str] = []
        unchanged: list[str] = []
        for site in self.sites:
            try:
                resp = ctx.http.get(site.url)
                if resp.status == 304:
                    # Unchanged since the last read: its names are already on
                    # the denylist. Not a failure, and not "nothing parsed".
                    unchanged.append(site.slug)
                    continue
                if not resp.ok:
                    failures.append(f"{site.slug}: HTTP {resp.status}")
                    continue
                parsed = self.parse(resp.text, site=site)
                if ctx.db is not None:
                    check_fingerprint(ctx.db, f"{self.key}:{site.slug}", self.last_fingerprint)
                items.extend(parsed)
            except Exception as exc:                     # noqa: BLE001
                # One VC's site being down is not this source failing.
                failures.append(f"{site.slug}: {exc}")
        # Each site has its own structure; never compare whichever site
        # happened to be fetched last with a different site's fingerprint.
        self.last_fingerprint = None
        self.last_failures = failures
        if not items and not unchanged:
            raise LayoutChanged(
                self.key,
                f"no portfolio company parsed from any of {len(self.sites)} sites: "
                + "; ".join(failures[:5]),
            )
        return items

    # ------------------------------------------------------------------ parse

    def parse(self, payload: str | bytes, *, site: VcSite | None = None) -> list[RawItem]:
        site = site or SITES[0]
        doc = html_doc(payload, self.key)
        selectors = (site.card_selector,) + CARD_SELECTORS if site.card_selector \
            else CARD_SELECTORS
        selector, cards = select_any(doc, selectors)
        guard_nonempty(
            self.key, cards,
            detail=f"{site.slug}: no portfolio card matched any of {selectors}",
            document=payload if isinstance(payload, str) else payload.decode("utf-8", "replace"),
        )
        self.last_selector = selector
        own = bool(site.card_selector) and selector == site.card_selector
        # Which reading worked, not what the cards contain: logo filenames and
        # WordPress image ids churn weekly and must not read as a redesign. A
        # site's own selector giving way to a generic one is the change to catch.
        self.last_fingerprint = "vc:" + selector_fingerprint(
            [selector, site.name_selector or site.name_from or "generic"] if own else [selector])

        out: list[RawItem] = []
        seen: set[str] = set()
        for card in cards:
            name = _site_name(card, site) if own else _name_of(card)
            if not name:
                continue
            href = attr_of(card, None, "href") or attr_of(card, "a[href]", "href")
            if href and (href.strip().startswith("#") or _IMAGE_LINK.search(href)):
                # "#" overlays (PXN) and lightbox image links (Anticus) are not
                # the company's page; an id built from them would churn.
                href = None
            external_id = f"{site.slug}:{slug_of(href or '') or norm_key(name)}"
            if external_id in seen:
                continue
            seen.add(external_id)
            out.append(RawItem(
                source_key=self.key,
                source_url=absolute_url(site.url, href) or site.url,
                external_id=external_id,
                published_at=None,          # portfolio pages are undated
                title=name,
                body_text=None,
                structured={
                    "company_name": name,
                    "on_vc_portfolio": True,
                    "vc_slug": site.slug,
                    "vc_name": site.name,
                    "norm_key": norm_key(name),
                    "date_confidence": "inferred",
                },
                kind_hint="vc_portfolio_listing",
            ))
        return out


# ---------------------------------------------------------------- the denylist


# Kept as a re-export so existing `from radar.sources.vc_portfolios import
# apply_denylist` call sites (tests, older docs) keep working. New code should
# import from `radar.sources.denylist` so shared modules stay source-agnostic.
from radar.sources.denylist import apply_denylist  # noqa: E402


_WP_DUPLICATE = re.compile(r"-\d+$")
_FORMER_NAME = re.compile(r"-(?:formerly|previously|now)-.*$")
_MORE_ABOUT = re.compile(r"^\s*more about\s+(.+?)\s*$", re.I)
_IMAGE_LINK = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|avif)(?:[?#].*)?$", re.I)


def _name_from_slug(href: str | None) -> str:
    """`/portfolio/one-utility-bill/` -> "One Utility Bill"; `2pd-2` -> "2pd".

    Matching is by `norm_key`, which ignores case and spacing, so a slug-derived
    name meets the registry name. WordPress's `-2` duplicate suffix and a
    trailing "-formerly-…" are dropped; generic path words are refused.
    """
    slug = _FORMER_NAME.sub("", _WP_DUPLICATE.sub("", slug_of(href or "").lower()))
    words = [w for w in slug.split("-") if w]
    if not words or slug in {"portfolio", "companies", "page", "investments"}:
        return ""
    return " ".join(w if any(c.isdigit() for c in w) else w.capitalize() for w in words)


def _site_name(card, site: VcSite) -> str:
    """The name, read the way this site is known to present it."""
    if site.name_selector:
        value = text_of(card, site.name_selector)
    elif site.name_from == "slug":
        value = _name_from_slug(attr_of(card, None, "href") or attr_of(card, "a[href]", "href"))
    elif site.name_from == "aria_more_about":
        label = attr_of(card, None, "aria-label") or attr_of(card, "[aria-label]", "aria-label") or ""
        match = _MORE_ABOUT.match(label)
        value = match.group(1) if match else ""
    elif site.name_from == "img_alt":
        value = (attr_of(card, None, "alt") or attr_of(card, "img[alt]", "alt") or "").strip()
    else:
        value = _name_of(card)
    value = " ".join(html.unescape(value or "").split())
    return value if value and len(value) < 80 else ""


def _name_of(card) -> str:
    for selector in NAME_SELECTORS:
        if selector == "img[alt]":
            alt = attr_of(card, "img[alt]", "alt")
            if alt and len(alt) < 60:
                return alt.strip()
            continue
        value = text_of(card, selector)
        if value and len(value) < 80:
            return value
    value = text_of(card)
    return value if value and len(value) < 80 else ""


ADAPTER = VcPortfoliosAdapter()
