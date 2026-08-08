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

from dataclasses import dataclass
from typing import Iterable, Sequence

from radar.fetch.layout import LayoutChanged
from radar.sources._common import (
    absolute_url,
    attr_of,
    guard_nonempty,
    html_doc,
    node_fingerprint,
    norm_key,
    select_any,
    slug_of,
    text_of,
)
from radar.sources.base import FetchContext, RawItem
from radar.store.db import now_iso

CARD_SELECTORS = (
    ".portfolio-item",
    ".portfolio__item",
    ".w-dyn-item",
    ".company-card",
    "li.company",
    ".portfolio-grid > a",
)
NAME_SELECTORS = (".portfolio-item__title", ".company-name", "h3", "h4", "h2",
                  ".title", "img[alt]")


@dataclass(frozen=True)
class VcSite:
    slug: str
    name: str
    url: str
    #: Optional site-specific override when the generic list is not enough.
    card_selector: str | None = None


#: The four named in 04-sources, plus the wider UK set the ledger implies.
#: Adding one is a single line — that is the promise this file has to keep.
SITES: tuple[VcSite, ...] = (
    VcSite("dsw", "DSW Ventures", "https://dsw.vc/portfolio/"),
    VcSite("northstar", "Northstar Ventures",
           "https://www.northstarventures.co.uk/portfolio/"),
    VcSite("outward", "Outward VC", "https://outwardvc.com/portfolio/"),
    VcSite("anticus", "Anticus Partners", "https://anticuspartners.com/portfolio/"),
    VcSite("mercia", "Mercia Ventures", "https://www.mercia.co.uk/portfolio/"),
    VcSite("maven", "Maven Capital Partners", "https://www.mavencp.com/portfolio/"),
    VcSite("praetura", "Praetura Ventures", "https://praetura.co.uk/portfolio/"),
    VcSite("par_equity", "Par Equity", "https://www.parequity.com/portfolio"),
    VcSite("ada_ventures", "Ada Ventures", "https://www.adaventures.com/portfolio"),
    VcSite("fuel_ventures", "Fuel Ventures", "https://fuel.ventures/portfolio/"),
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
        for site in self.sites:
            try:
                resp = ctx.http.get(site.url)
                if resp.status == 304:
                    continue
                if not resp.ok:
                    failures.append(f"{site.slug}: HTTP {resp.status}")
                    continue
                items.extend(self.parse(resp.text, site=site))
            except Exception as exc:                     # noqa: BLE001
                # One VC's site being down is not this source failing.
                failures.append(f"{site.slug}: {exc}")
        self.last_failures = failures
        if not items:
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
        self.last_fingerprint = node_fingerprint(cards)

        out: list[RawItem] = []
        for card in cards:
            name = _name_of(card)
            if not name:
                continue
            href = attr_of(card, None, "href") or attr_of(card, "a[href]", "href")
            out.append(RawItem(
                source_key=self.key,
                source_url=absolute_url(site.url, href) or site.url,
                external_id=f"{site.slug}:{slug_of(href or '') or norm_key(name)}",
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


def apply_denylist(db, items: Sequence[RawItem]) -> dict:
    """Set `company.on_vc_portfolio` and write the negative signal.

    Runs after resolve, on names we already hold. Deliberately does **not**
    create companies: a portfolio page is a reason to *demote* a company we
    found elsewhere, never a reason to add one — adding them is precisely the
    version-1 behaviour the client rejected.
    """
    matched: list[str] = []
    keys = {}
    for item in items:
        structured = item.structured or {}
        key = structured.get("norm_key") or norm_key(item.title)
        if key:
            keys.setdefault(key, item)

    if not keys:
        return {"listings": 0, "companies_flagged": 0, "matched": []}

    placeholders = ",".join("?" for _ in keys)
    rows = db.query(
        f"""SELECT id, canonical_name, norm_key FROM company
            WHERE merged_into IS NULL AND norm_key IN ({placeholders})""",
        list(keys),
    )
    for row in rows:
        item = keys[row["norm_key"]]
        structured = item.structured or {}
        db.execute(
            "UPDATE company SET on_vc_portfolio = 1, updated_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        db.execute(
            """INSERT OR IGNORE INTO signal
               (company_id, kind, occurred_on, headline, detail, source_key,
                source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (row["id"], "vc_portfolio_listing", None,
             f"Listed on {structured.get('vc_name', 'a VC')} portfolio",
             structured.get("vc_slug"), "vc_portfolios", item.source_url, now_iso()),
        )
        matched.append(row["canonical_name"])

    return {
        "listings": len(items),
        "companies_flagged": len(matched),
        "matched": sorted(matched),
    }


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
