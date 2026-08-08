"""Build the 25 golden article fixtures of 09-test-plan §3.

Each fixture is authored here as structured data: paragraphs, a hand-written
"model response" payload, and the assertions that must hold. The builder then:

1. Renders `articles/<slug>.html` from the paragraphs (so every evidence quote
   is verbatim by construction — grounding cannot drop it);
2. Computes the llm-cache key with the *real* code path
   (`prefilter.extract_text` → `cache_key`), so the key can never drift from
   what `extract()` asks for at test time;
3. Writes `llm_cache/<key>.json` with the hand-authored payload;
4. Writes `articles/<slug>.expected.json`;
5. Runs `extract()` end-to-end with the ReplayLLM over the just-written cache
   and asserts the record matches the expected values.

Run it with the project venv, from the repo root:

    .venv/bin/python tests/fixtures/build_extraction_fixtures.py

The committed outputs are the fixtures. Re-run this to regenerate; the
self-verification step makes a mis-generated fixture a build error, not a
surprise at test time. This is the `rekey_llm_cache.py` pattern from the
`prefilter.extract_text` docstring — no provider call anywhere.

Why payloads are hand-authored rather than recorded: recording needs an API
key and a live provider. The plan's golden tests pin the *behaviour*, not the
vendor's current whims, so the cached response is written as the ground truth
a reviewed recording would become — and the diff of these files is exactly
what the plan says to review when the prompt changes.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.extract.llm import DEFAULT_MODEL, PROMPT_VERSION, cache_key  # noqa: E402
from radar.extract.prefilter import prefilter  # noqa: E402

ARTICLES = ROOT / "tests" / "fixtures" / "articles"
LLM_CACHE = ROOT / "tests" / "fixtures" / "llm_cache"


# ------------------------------------------------------------------ helpers


def _html(slug: str, title: str, paragraphs: list[str], *,
          og_title: str | None = None) -> str:
    """A minimal, realistic news page. `og:title` feeds the heuristic reader."""
    og = og_title or title
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<!DOCTYPE html>\n<html><head>\n"
        f'<meta property="og:title" content="{og}">\n'
        f"<title>{title}</title>\n"
        "</head><body>\n"
        f"<h1>{title}</h1>\n{body}\n"
        "</body></html>\n"
    )


@dataclass
class Fixture:
    """One golden fixture: prose, hand-authored payload, expected assertions."""

    slug: str
    title: str
    url: str
    paragraphs: list[str]
    payload: dict
    expected: dict
    og_title: str | None = None

    def write(self) -> str | None:
        """Render the article and its cache entry. Returns the cache key, or
        `None` when the prefilter rejects the article before the model."""
        html = _html(self.slug, self.title, self.paragraphs, og_title=self.og_title)
        (ARTICLES / f"{self.slug}.html").write_text(html, encoding="utf-8")
        (ARTICLES / f"{self.slug}.expected.json").write_text(
            json.dumps(self.expected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        pre = prefilter(self.url, self.title, html)
        if not pre.ok:
            # Prefilter-rejected fixtures never reach the model: no cache entry.
            print(f"  {self.slug}: prefilter {pre.reason} (no cache entry)")
            return None
        key = cache_key(pre.text, DEFAULT_MODEL)
        entry = {
            "payload": self.payload,
            "model_id": DEFAULT_MODEL,
            "tokens_in": int(len(pre.text) / 4) + 300,
            "tokens_out": 350,
            "cost_usd": 0.001,
        }
        (LLM_CACHE / f"{key}.json").write_text(
            json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return key

    # ------------------------------------------------------------ verification

    def verify(self) -> list[str]:
        """Run the real extraction path and confirm every expected assertion."""
        from radar.extract import ExtractContext, extract_html
        from radar.extract.llm import ReplayLLM

        html = (ARTICLES / f"{self.slug}.html").read_text(encoding="utf-8")
        llm = ReplayLLM(LLM_CACHE)
        ctx = ExtractContext(llm=llm, db=None, model_id=DEFAULT_MODEL)
        got = extract_html(url=self.url, title=self.title, html=html, ctx=ctx)

        errors: list[str] = []
        exp = self.expected

        def check(cond: bool, msg: str) -> None:
            if not cond:
                errors.append(f"    ✗ {msg} (got {got.model_dump()})")

        check(got.is_about_single_company == exp["is_about_single_company"],
              f"is_about_single_company == {exp['is_about_single_company']}")
        if got.is_about_single_company:
            check((got.company_name or "").lower().replace(" ", "")
                  == (exp.get("company_name") or "").lower().replace(" ", ""),
                  f"company_name == {exp.get('company_name')!r}")
            check(got.sector == exp.get("sector"),
                  f"sector == {exp.get('sector')!r}")
            # `None` and `0` are different facts, so the unknown case is
            # asserted too — not skipped.
            check(got.amount_raised_gbp == exp.get("amount"),
                  f"amount_raised_gbp == {exp.get('amount')}")
            check(got.stage == exp.get("stage"),
                  f"stage == {exp.get('stage')!r}")
            check(bool(got.needs_review) == bool(exp.get("needs_review", False)),
                  f"needs_review == {exp.get('needs_review', False)}")
            if exp.get("amount_currency") is not None:
                check(got.amount_currency == exp["amount_currency"],
                      f"amount_currency == {exp['amount_currency']}")
            if exp.get("amount_original") is not None:
                check(got.amount_original == exp["amount_original"],
                      f"amount_original == {exp['amount_original']}")
            if exp.get("grant_amount_gbp") is not None:
                check(got.grant_amount_gbp == exp["grant_amount_gbp"],
                      f"grant_amount_gbp == {exp['grant_amount_gbp']}")
            if exp.get("is_university_spinout") is not None:
                check(got.is_university_spinout == exp["is_university_spinout"],
                      f"is_university_spinout == {exp['is_university_spinout']}")
            if exp.get("university_name") is not None:
                check(got.university_name == exp["university_name"],
                      f"university_name == {exp['university_name']}")
            got_founders = sorted(f.name for f in got.founders)
            check(got_founders == sorted(exp.get("founders", [])),
                  f"founders == {exp.get('founders', [])}")
            # the hallucination gate, asserted per fixture: every surviving
            # evidence quote must be verbatim in the source (rate exactly 0)
            from radar.extract.grounding import hallucination_rate
            from radar.extract.prefilter import extract_text
            check(hallucination_rate(got, extract_text(html)) == 0.0,
                  "hallucination rate == 0")
        else:
            check(got.rejection_reason == exp.get("rejection_reason"),
                  f"rejection_reason == {exp.get('rejection_reason')!r}")
        return errors


# ================================================================== the 25


FIXTURES: list[Fixture] = [
    # ---- 1-5: clean single-company funding announcements -------------------
    Fixture(
        slug="clean_funding_announcement_1",
        title="Lumina Bio raises £2.4m seed round to expand clinical trials",
        url="https://www.northerntechnology.co.uk/lumina-bio-seed-round",
        paragraphs=[
            "Lumina Bio, a Newcastle-based healthcare startup, has raised £2.4 million "
            "in a seed round led by a syndicate of regional investors.",
            "The company develops remote monitoring software for clinical trials and "
            "says the funding will be used to expand its engineering team in the North East.",
            "Founded by Dr Jane Smith, a former NHS consultant, the startup was spun out "
            "of a university research group in 2023 and has already signed two NHS trusts.",
            "The seed round brings Lumina Bio's total funding to £2.4 million, according "
            "to documents filed with Companies House.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Lumina Bio",
            "one_line_description": "Remote monitoring software for clinical trials",
            "sector": "healthcare",
            "stage": "seed",
            "hq_city": "Newcastle",
            "hq_country_iso2": "GB",
            "founded_year": 2023,
            "founders": [
                {"name": "Dr Jane Smith", "role": "Founder",
                 "evidence_quote": "Founded by Dr Jane Smith, a former NHS consultant"}
            ],
            "amount_raised_gbp": 2400000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Lumina Bio, a Newcastle-based healthcare startup, has raised £2.4 million",
            "evidence_quote_amount": "has raised £2.4 million in a seed round",
            "evidence_quote_stage": "in a seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Lumina Bio",
            "sector": "healthcare",
            "amount": 2400000,
            "stage": "seed",
            "founders": ["Dr Jane Smith"],
            "needs_review": False,
        },
        og_title="Lumina Bio raises £2.4m seed round to expand clinical trials",
    ),
    Fixture(
        slug="clean_funding_announcement_2",
        title="Leeds fintech Paylate secures £500,000 pre-seed from angel network",
        url="https://www.yorkshirebusinesspost.co.uk/paylate-pre-seed",
        paragraphs=[
            "Paylate, a Leeds-based fintech startup, has secured £500,000 in a pre-seed "
            "round from a Yorkshire angel network.",
            "The company builds instalment-payment tooling for small retailers and will "
            "use the money to hire its first product engineers.",
            "Founded in 2024, Paylate already processes payments for 40 independent shops "
            "across West Yorkshire and is applying for FCA authorisation.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Paylate",
            "one_line_description": "Instalment-payment tooling for small retailers",
            "sector": "fintech",
            "stage": "pre_seed",
            "hq_city": "Leeds",
            "hq_country_iso2": "GB",
            "founded_year": 2024,
            "amount_raised_gbp": 500000,
            "extraction_confidence": 0.92,
            "evidence_quote_company": "Paylate, a Leeds-based fintech startup, has secured £500,000",
            "evidence_quote_amount": "has secured £500,000 in a pre-seed round",
            "evidence_quote_stage": "in a pre-seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Paylate",
            "sector": "fintech",
            "amount": 500000,
            "stage": "pre_seed",
            "needs_review": False,
        },
        og_title="Leeds fintech Paylate secures £500,000 pre-seed from angel network",
    ),
    Fixture(
        slug="clean_funding_announcement_3",
        title="Sheffield SaaS firm Loamweave raises £1.1m to automate farm compliance",
        url="https://www.sheffieldtech.co.uk/loamweave-1-1m",
        paragraphs=[
            "Loamweave, a Sheffield-based vertical SaaS company, has raised £1.1 million "
            "in a seed round led by a Northern venture fund.",
            "Its platform automates farm compliance paperwork, and the startup says it "
            "has cut inspection preparation time by half for its 120 agricultural customers.",
            "The round, announced on Tuesday, values the company at £8 million and will "
            "fund a second product line for livestock assurance.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Loamweave",
            "one_line_description": "Farm compliance automation platform",
            "sector": "vertical_saas",
            "stage": "seed",
            "hq_city": "Sheffield",
            "hq_country_iso2": "GB",
            "amount_raised_gbp": 1100000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Loamweave, a Sheffield-based vertical SaaS company, has raised £1.1 million",
            "evidence_quote_amount": "has raised £1.1 million in a seed round",
            "evidence_quote_stage": "in a seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Loamweave",
            "sector": "vertical_saas",
            "amount": 1100000,
            "stage": "seed",
            "needs_review": False,
        },
        og_title="Sheffield SaaS firm Loamweave raises £1.1m to automate farm compliance",
    ),
    Fixture(
        slug="clean_funding_announcement_4",
        title="Edinburgh AI startup Quicksilver closes £3.2m Series A for geospatial models",
        url="https://www.scotlandinno.co.uk/quicksilver-series-a",
        paragraphs=[
            "Quicksilver, an Edinburgh AI company, has closed a £3.2 million Series A "
            "round to commercialise its geospatial data models.",
            "The startup's software predicts flood risk from satellite imagery and is "
            "already used by two UK water companies.",
            "Quicksilver was founded in 2022 by a team from the University of Edinburgh "
            "and employs 18 people, most of them in Scotland.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Quicksilver",
            "one_line_description": "Geospatial flood-risk prediction from satellite imagery",
            "sector": "ai_data",
            "stage": "series_a",
            "hq_city": "Edinburgh",
            "hq_country_iso2": "GB",
            "founded_year": 2022,
            "amount_raised_gbp": 3200000,
            "extraction_confidence": 0.93,
            "evidence_quote_company": "Quicksilver, an Edinburgh AI company, has closed a £3.2 million Series A",
            "evidence_quote_amount": "has closed a £3.2 million Series A round",
            "evidence_quote_stage": "Series A round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Quicksilver",
            "sector": "ai_data",
            "amount": 3200000,
            "stage": "series_a",
            "needs_review": False,
        },
        og_title="Edinburgh AI startup Quicksilver closes £3.2m Series A for geospatial models",
    ),
    Fixture(
        slug="clean_funding_announcement_5",
        title="Manchester climate startup Cellarstone raises £750,000 for battery software",
        url="https://www.northernbusiness.co.uk/cellarstone-750k",
        paragraphs=[
            "Cellarstone, a Manchester-based climate technology startup, has raised "
            "£750,000 in a seed round to grow its battery-health analytics software.",
            "The company helps warehouse operators extend the life of forklift fleets, "
            "and says early customers have cut replacement costs by a third.",
            "The investment was announced alongside a partnership with a large logistics "
            "group, and Cellarstone plans to open its first overseas office next year.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Cellarstone",
            "one_line_description": "Battery-health analytics for warehouse fleets",
            "sector": "climate_tech",
            "stage": "seed",
            "hq_city": "Manchester",
            "hq_country_iso2": "GB",
            "amount_raised_gbp": 750000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Cellarstone, a Manchester-based climate technology startup, has raised £750,000",
            "evidence_quote_amount": "has raised £750,000 in a seed round",
            "evidence_quote_stage": "in a seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Cellarstone",
            "sector": "climate_tech",
            "amount": 750000,
            "stage": "seed",
            "needs_review": False,
        },
        og_title="Manchester climate startup Cellarstone raises £750,000 for battery software",
    ),

    # ---- 6-8: round-ups / listicles — the model must say "not one company" --
    Fixture(
        slug="roundup_weekly_digest",
        title="The North's biggest funding stories this week",
        url="https://www.northerntechnology.co.uk/weekly-digest",
        paragraphs=[
            "A quiet week for exits but a busy one for cheques across the North's startup scene.",
            "Newcastle's Palisade Health raised £1.2 million for its care-home software, "
            "while Leeds outfit Brightbox took £400,000 for workplace analytics.",
            "Sheffield's Fern Energy closed a £2 million round for heat pumps, and Manchester "
            "startup Loopio secured £650,000 for its retail inventory tool.",
            "Analysts said the deals show regional investors remain willing to back early-stage teams.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.8,
            "evidence_quote_company": "Palisade Health raised £1.2 million",
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="The North's biggest funding stories this week",
    ),
    Fixture(
        slug="listicle_top_startups",
        title="Ten early-stage startups from the North East to watch in 2026",
        url="https://www.northerntechnology.co.uk/ten-to-watch",
        paragraphs=[
            "Each year we pick ten young companies from the North East that look worth watching.",
            "On the list are a Durham battery firm, a Sunderland robotics shop and a Middlesbrough "
            "data startup, alongside seven others across the region.",
            "All ten have been incorporated within the last three years, and several are expected "
            "to raise their first institutional rounds before the end of the year.",
            "The list is an editorial selection rather than a ranking, and we will revisit each "
            "company in the summer to see how they have fared.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.85,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="Ten early-stage startups from the North East to watch in 2026",
    ),
    Fixture(
        slug="roundup_funding_deals",
        title="Round-up: the funding deals that closed before Christmas",
        url="https://www.yorkshirebusinesspost.co.uk/deals-round-up",
        paragraphs=[
            "Our final funding round-up of the year collects the deals that slipped out before the holidays.",
            "A York medtech firm took £3.5 million, a Hull e-commerce platform raised £900,000, "
            "and a Bradford AI consultancy secured a £2.4 million growth round.",
            "Two smaller pre-seed cheques went to Sheffield and Leeds startups respectively, "
            "according to the companies involved.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.8,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="Round-up: the funding deals that closed before Christmas",
    ),

    # ---- 9-10: university spinouts ------------------------------------------
    Fixture(
        slug="university_spinout_1",
        title="Durham spin-out Quantia raises £1.8m to commercialise quantum sensors",
        url="https://www.northerntechnology.co.uk/quantia-spinout",
        paragraphs=[
            "Quantia, a quantum sensing company spun out of Durham University, has raised "
            "£1.8 million in a pre-seed round.",
            "The spinout is developing compact sensors for detecting underground infrastructure "
            "and has licensed core patents from the university's physics department.",
            "Professor Alan Reed, who led the research group, joins the board as chief "
            "scientific officer.",
            "The funding will support a pilot deployment with a water utility in the North East.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Quantia",
            "one_line_description": "Quantum sensors for underground infrastructure",
            "sector": "deeptech",
            "stage": "pre_seed",
            "hq_city": "Durham",
            "hq_country_iso2": "GB",
            "is_university_spinout": True,
            "university_name": "Durham University",
            "amount_raised_gbp": 1800000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Quantia, a quantum sensing company spun out of Durham University, has raised £1.8 million",
            "evidence_quote_amount": "has raised £1.8 million in a pre-seed round",
            "evidence_quote_stage": "in a pre-seed round",
            "evidence_quote_spinout": "spun out of Durham University",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Quantia",
            "sector": "deeptech",
            "amount": 1800000,
            "stage": "pre_seed",
            "is_university_spinout": True,
            "university_name": "Durham University",
            "needs_review": False,
        },
        og_title="Durham spin-out Quantia raises £1.8m to commercialise quantum sensors",
    ),
    Fixture(
        slug="university_spinout_2",
        title="Oxford spinout Fenwick Bio launches with £4m from university venture fund",
        url="https://www.oxfordinnovationpost.co.uk/fenwick-bio",
        paragraphs=[
            "Fenwick Bio, a new company spun out of Oxford University, has launched with "
            "£4 million in seed funding from the university's venture fund.",
            "The spinout is working on synthetic enzymes for sustainable chemical production "
            "and emerged from a decade of research in the chemistry department.",
            "Its founding team includes two Oxford academics and a former executive from a "
            "listed chemical company.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Fenwick Bio",
            "one_line_description": "Synthetic enzymes for sustainable chemical production",
            "sector": "life_sciences",
            "stage": "seed",
            "hq_city": "Oxford",
            "hq_country_iso2": "GB",
            "is_university_spinout": True,
            "university_name": "Oxford University",
            "amount_raised_gbp": 4000000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Fenwick Bio, a new company spun out of Oxford University, has launched with £4 million",
            "evidence_quote_amount": "has launched with £4 million in seed funding",
            "evidence_quote_stage": "in seed funding",
            "evidence_quote_spinout": "spun out of Oxford University",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Fenwick Bio",
            "sector": "life_sciences",
            "amount": 4000000,
            "stage": "seed",
            "is_university_spinout": True,
            "university_name": "Oxford University",
            "needs_review": False,
        },
        og_title="Oxford spinout Fenwick Bio launches with £4m from university venture fund",
    ),

    # ---- 11-12: Innovate UK grant awards — grants are not equity -------------
    Fixture(
        slug="innovate_uk_grant_1",
        title="Teesside startup Harbourside wins £250,000 Innovate UK grant for maritime tech",
        url="https://www.northerntechnology.co.uk/harbourside-grant",
        paragraphs=[
            "Harbourside, a Teesside-based startup, has been awarded a £250,000 Innovate UK "
            "grant to develop collision-avoidance technology for small vessels.",
            "The grant, part of the Smart Shipping competition, is non-dilutive and will "
            "fund a year of sea trials with a local ferry operator.",
            "Harbourside was formed last year and employs four people at Teesport.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Harbourside",
            "one_line_description": "Collision-avoidance technology for small vessels",
            "sector": "deeptech",
            "hq_city": "Middlesbrough",
            "hq_country_iso2": "GB",
            "grant_amount_gbp": 250000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Harbourside, a Teesside-based startup, has been awarded a £250,000 Innovate UK grant",
            "evidence_quote_amount": "awarded a £250,000 Innovate UK grant",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Harbourside",
            "sector": "deeptech",
            "amount": None,
            "grant_amount_gbp": 250000,
            "needs_review": False,
        },
        og_title="Teesside startup Harbourside wins £250,000 Innovate UK grant for maritime tech",
    ),
    Fixture(
        slug="innovate_uk_grant_2",
        title="Newcastle battery firm Voltrax receives £1.2m UKRI award",
        url="https://www.northerntechnology.co.uk/voltrax-award",
        paragraphs=[
            "Voltrax, a Newcastle startup building sodium-ion batteries, has received a "
            "£1.2 million grant award from UK Research and Innovation.",
            "The non-dilutive grant supports the company's scale-up lab, and unlike an "
            "equity round it does not dilute the founding team.",
            "Voltrax is one of twelve firms sharing £14 million from the agency's latest "
            "energy storage call.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Voltrax",
            "one_line_description": "Sodium-ion batteries for grid storage",
            "sector": "climate_tech",
            "hq_city": "Newcastle",
            "hq_country_iso2": "GB",
            "grant_amount_gbp": 1200000,
            "extraction_confidence": 0.9,
            "evidence_quote_company": "Voltrax, a Newcastle startup building sodium-ion batteries, has received a £1.2 million grant award",
            "evidence_quote_amount": "has received a £1.2 million grant award",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Voltrax",
            "sector": "climate_tech",
            "amount": None,
            "grant_amount_gbp": 1200000,
            "needs_review": False,
        },
        og_title="Newcastle battery firm Voltrax receives £1.2m UKRI award",
    ),

    # ---- 13: article about a large company ----------------------------------
    Fixture(
        slug="already_large_company",
        title="Supermarket giant GroceryCo launches new payment app",
        url="https://www.retaildaily.co.uk/groceryco-payments-app",
        paragraphs=[
            "GroceryCo, the national supermarket chain, has launched a new payment app for "
            "its 600 stores across the UK.",
            "The app, which the company says was developed by an internal team of 200 "
            "engineers, lets shoppers scan and pay with their phones.",
            "GroceryCo is listed on the London Stock Exchange and employs more than "
            "90,000 people, so the launch is a product update rather than a startup story.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "already_large_company",
            "extraction_confidence": 0.8,
            "evidence_quote_company": "GroceryCo, the national supermarket chain, has launched a new payment app",
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "already_large_company",
        },
        og_title="Supermarket giant GroceryCo launches new payment app",
    ),

    # ---- 14: paywalled stub --------------------------------------------------
    Fixture(
        slug="paywalled_stub",
        title="Exclusive: Liverpool startup raises £2m — subscribers only",
        url="https://www.northernbusiness.co.uk/exclusive-subscribers-only",
        paragraphs=[
            "This article is available to subscribers of Northern Business only.",
            "What we can tell you is that a Liverpool-based logistics startup has raised "
            "£2 million in its first institutional round, and that the company plans to "
            "triple its headcount this year.",
            "The full story, including the company name, the investors and the valuation, "
            "is behind the paywall.",
            "Subscribe today to read the complete piece.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "paywalled",
            "extraction_confidence": 0.7,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "paywalled",
        },
        og_title="Exclusive: Liverpool startup raises £2m — subscribers only",
    ),

    # ---- 15: two companies — acquirer and target -----------------------------
    Fixture(
        slug="two_companies_acquirer_target",
        title="Bristol medtech Halcyon acquires Sheffield startup Podmap",
        url="https://www.healthbusinessdaily.co.uk/halcyon-acquires-podmap",
        paragraphs=[
            "Halcyon, a Bristol-based medtech group, has acquired Podmap, a Sheffield "
            "startup founded in 2022 that makes foot-pressure sensors for diabetics.",
            "Podmap's twelve employees will join Halcyon's product team, and its sensor "
            "technology will be folded into Halcyon's existing monitoring platform.",
            "The financial terms of the acquisition were not disclosed, though Podmap had "
            "raised £800,000 in a pre-seed round from regional angels before the deal.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Podmap",
            "one_line_description": "Foot-pressure sensors for diabetics, acquired by Halcyon",
            "sector": "healthcare",
            "stage": "pre_seed",
            "hq_city": "Sheffield",
            "hq_country_iso2": "GB",
            "founded_year": 2022,
            "amount_raised_gbp": 800000,
            "extraction_confidence": 0.85,
            "evidence_quote_company": "Podmap, a Sheffield startup founded in 2022 that makes foot-pressure sensors",
            "evidence_quote_amount": "raised £800,000 in a pre-seed round from regional angels",
            "evidence_quote_stage": "in a pre-seed round from regional angels",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Podmap",
            "sector": "healthcare",
            "amount": 800000,
            "stage": "pre_seed",
            "needs_review": False,
        },
        og_title="Bristol medtech Halcyon acquires Sheffield startup Podmap",
    ),

    # ---- 16: non-English article — graceful handling -------------------------
    Fixture(
        slug="non_english_article",
        title="La startup parisienne Lumina AI lève 2 millions d'euros",
        url="https://www.techfrance.fr/lumina-ai-leve-2-meur",
        paragraphs=[
            "La startup parisienne Lumina AI a levé 2 millions d'euros en amorçage auprès "
            "de deux fonds français.",
            "L'entreprise développe des modèles de langage spécialisés pour les cabinets "
            "d'avocats, et a signé son premier client international ce trimestre.",
            "Le tour de table, qualifié de pre-seed par les investisseurs, servira à "
            "recruter dix personnes à Paris d'ici la fin de l'année.",
            "Les fondateurs, deux anciens ingénieurs de recherche, prévoient également "
            "d'ouvrir un bureau commercial à Londres au cours de l'année prochaine.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Lumina AI",
            "one_line_description": "Modèles de langage pour cabinets d'avocats",
            "sector": "ai_data",
            "stage": "pre_seed",
            "hq_city": "Paris",
            "hq_country_iso2": "FR",
            "amount_original": 2000000,
            "amount_currency": "EUR",
            "amount_raised_gbp": None,
            "extraction_confidence": 0.6,
            "evidence_quote_company": "La startup parisienne Lumina AI a levé 2 millions d'euros",
            "evidence_quote_amount": "a levé 2 millions d'euros en amorçage",
            "evidence_quote_stage": "qualifié de pre-seed par les investisseurs",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Lumina AI",
            "sector": "ai_data",
            "amount": None,
            "amount_original": 2000000,
            "amount_currency": "EUR",
            "stage": "pre_seed",
            # a non-GBP amount is flagged, not silently converted
            "needs_review": True,
        },
        og_title="La startup parisienne Lumina AI lève 2 millions d'euros",
    ),

    # ---- 17: company name that is also a common word -------------------------
    Fixture(
        slug="common_word_company_name",
        title="Belfast startup Beacon raises £600,000 for warehouse sensors",
        url="https://www.northernirelandtech.co.uk/beacon-raises-600k",
        paragraphs=[
            "Beacon, a Belfast-based startup, has raised £600,000 in a pre-seed round to "
            "grow its warehouse sensor business.",
            "The word beacon usually describes a light or a signal, but in this case it is "
            "the trading name of a company incorporated in 2023.",
            "Beacon's sensors monitor temperature and humidity in food warehouses, and the "
            "startup already has nine customers in Northern Ireland.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Beacon",
            "one_line_description": "Warehouse temperature and humidity sensors",
            "sector": "industrial_tech",
            "stage": "pre_seed",
            "hq_city": "Belfast",
            "hq_country_iso2": "GB",
            "founded_year": 2023,
            "amount_raised_gbp": 600000,
            "extraction_confidence": 0.88,
            "evidence_quote_company": "Beacon, a Belfast-based startup, has raised £600,000 in a pre-seed round",
            "evidence_quote_amount": "has raised £600,000 in a pre-seed round",
            "evidence_quote_stage": "in a pre-seed round to grow its warehouse sensor business",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Beacon",
            "sector": "industrial_tech",
            "amount": 600000,
            "stage": "pre_seed",
            "needs_review": False,
        },
        og_title="Belfast startup Beacon raises £600,000 for warehouse sensors",
    ),

    # ---- 18-20: accelerator cohort announcements -----------------------------
    Fixture(
        slug="accelerator_cohort_1",
        title="North East accelerator announces its spring cohort of nine startups",
        url="https://www.northerntechnology.co.uk/spring-cohort",
        paragraphs=[
            "The North East accelerator has announced the nine startups joining its spring cohort.",
            "The group includes a Gateshead fintech, two Durham deep-tech ventures, a "
            "Sunderland manufacturer and five software firms from Newcastle.",
            "Each company receives £20,000 in pre-seed investment plus twelve weeks of "
            "mentoring, and the programme ends with a demo day in June.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.8,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="North East accelerator announces its spring cohort of nine startups",
    ),
    Fixture(
        slug="accelerator_cohort_2",
        title="TechStars-style programme unveils eleven companies for London demo day",
        url="https://www.londonstartupnews.co.uk/cohort-unveiled",
        paragraphs=[
            "The capital's flagship accelerator has unveiled the eleven companies taking "
            "part in its autumn programme.",
            "The cohort spans health, climate and developer tools, and was founded by "
            "founders from twelve different countries.",
            "All eleven companies will pitch to investors at the demo day in October, "
            "following three months of structured support.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.8,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="TechStars-style programme unveils eleven companies for London demo day",
    ),
    Fixture(
        slug="accelerator_cohort_3",
        title="Programme welcome: eight early teams start at the Cambridge incubator",
        url="https://www.cambridgetech.co.uk/welcome-cohort",
        paragraphs=[
            "Eight early-stage teams began their residency at the Cambridge incubator this week.",
            "They include a lab-grown meat venture founded last year, a chip design startup "
            "and a climate analytics firm, selected from more than 200 applicants.",
            "The programme offers bench space, legal clinics and introductions to its "
            "network of deep-tech investors.",
        ],
        payload={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
            "extraction_confidence": 0.8,
        },
        expected={
            "is_about_single_company": False,
            "rejection_reason": "roundup",
        },
        og_title="Programme welcome: eight early teams start at the Cambridge incubator",
    ),

    # ---- 21: amount in USD — converted or flagged, never silently wrong -------
    Fixture(
        slug="usd_amount_round",
        title="Glasgow fintech Kilo raises $4m from US investors",
        url="https://www.scotlandinno.co.uk/kilo-4m-usd",
        paragraphs=[
            "Kilo, a Glasgow-based fintech startup, has raised $4 million from a group of "
            "US investors led by a California fund.",
            "The company, which provides invoice-financing software, says the money will "
            "be used to open a New York office and expand its sales team.",
            "Kilo was founded in 2022 and has processed more than £30 million of invoices "
            "to date, according to its founders.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Kilo",
            "one_line_description": "Invoice-financing software",
            "sector": "fintech",
            "stage": "seed",
            "hq_city": "Glasgow",
            "hq_country_iso2": "GB",
            "founded_year": 2022,
            "amount_original": 4000000,
            "amount_currency": "USD",
            "amount_raised_gbp": None,
            "extraction_confidence": 0.85,
            "evidence_quote_company": "Kilo, a Glasgow-based fintech startup, has raised $4 million",
            "evidence_quote_amount": "has raised $4 million from a group of US investors",
            "evidence_quote_stage": "from a group of US investors",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Kilo",
            "sector": "fintech",
            "amount": None,
            "amount_original": 4000000,
            "amount_currency": "USD",
            "stage": "seed",
            "needs_review": True,
        },
        og_title="Glasgow fintech Kilo raises $4m from US investors",
    ),

    # ---- 22: a company the model might invent — grounding catches it ---------
    Fixture(
        slug="invented_company_grounding",
        title="Preston startup raises £900,000 to digitise school catering",
        url="https://www.lancashirebusiness.co.uk/school-catering-startup",
        paragraphs=[
            "A Preston startup has raised £900,000 to digitise school catering, its founder "
            "confirmed this week.",
            "The company's platform handles meal choices, dietary allergies and kitchen "
            "stock for more than 300 schools.",
            "The founder said the funding round closes at the end of the month and that the "
            "team will grow from six to fifteen people.",
            "The startup's name was confirmed by the regional growth hub, which supported "
            "the raise.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "CafeteriaCloud",
            "one_line_description": "School catering digitisation",
            "sector": "vertical_saas",
            "stage": "pre_seed",
            "hq_city": "Preston",
            "hq_country_iso2": "GB",
            "amount_raised_gbp": 900000,
            "extraction_confidence": 0.5,
            # the model "invented" the name — the quote is not in the article
            "evidence_quote_company": "CafeteriaCloud, a Preston startup, has raised £900,000",
            "evidence_quote_amount": "has raised £900,000 to digitise school catering",
        },
        expected={
            "is_about_single_company": True,
            # grounding drops the invented name: company_name is gone. The
            # AMOUNT survives — its quote is verbatim — which is the point:
            # grounding drops the unsupported claim, not the whole record.
            "company_name": "",
            "sector": "vertical_saas",
            "stage": "pre_seed",
            "amount": 900000,
            "needs_review": True,
        },
        og_title="Preston startup raises £900,000 to digitise school catering",
    ),

    # ---- 23-25: regional news, thin content ----------------------------------
    Fixture(
        slug="thin_regional_news_1",
        title="Blackpool startup raises seed cash, says founder",
        url="https://www.lancashiregazette.co.uk/blackpool-startup",
        paragraphs=[
            "A Blackpool startup has raised a seed round, its founder told the Gazette this week.",
            "The company, which makes software for seaside hospitality businesses, is in the "
            "process of hiring its first two employees, the founder said.",
            "Further details of the investment were not available at the time of writing, and "
            "the founder declined to name the investors or the exact amount raised.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "Seaboard",
            "one_line_description": "Software for seaside hospitality",
            "sector": "b2b_saas",
            "stage": "pre_seed",
            "hq_city": "Blackpool",
            "hq_country_iso2": "GB",
            "extraction_confidence": 0.4,
            "evidence_quote_company": "A Blackpool startup has raised a seed round",
            "evidence_quote_amount": "has raised a seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "Seaboard",
            "sector": "b2b_saas",
            "stage": "pre_seed",
            "amount": None,
            "needs_review": True,
        },
        og_title="Blackpool startup raises seed cash, says founder",
    ),
    Fixture(
        slug="thin_regional_news_2",
        title="Hull firm confirms it has closed its funding round",
        url="https://www.hulldaily.co.uk/hull-firm-closes-round",
        paragraphs=[
            "A Hull-based engineering firm has confirmed it has closed its funding round, "
            "according to a brief statement issued on Monday.",
            "The statement gave no figure and no names, saying only that the investment "
            "would support the company's expansion plans across the Humber region.",
            "The firm, which was incorporated in 2024, is understood to work on marine "
            "engineering projects for small commercial vessels.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "HumberWorks",
            "one_line_description": "Marine engineering services",
            "sector": "industrial_tech",
            "stage": "pre_seed",
            "hq_city": "Hull",
            "hq_country_iso2": "GB",
            "extraction_confidence": 0.35,
            "evidence_quote_company": "A Hull-based engineering firm has confirmed it has closed its funding round",
            "evidence_quote_amount": "has confirmed it has closed its funding round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "HumberWorks",
            "sector": "industrial_tech",
            "stage": "pre_seed",
            "amount": None,
            "needs_review": True,
        },
        og_title="Hull firm confirms it has closed its funding round",
    ),
    Fixture(
        slug="thin_regional_news_3",
        title="Grimsby startup receives funding boost, local MP says",
        url="https://www.grimsbytelegraph.co.uk/grimsby-funding-boost",
        paragraphs=[
            "KeelLine, a Grimsby startup, has raised funding in a successful pre-seed round, "
            "the local MP said in a newsletter published on Friday.",
            "The MP welcomed the investment for the town's tech sector and named KeelLine as "
            "the recipient, though the amount involved was not disclosed.",
            "Local councillors described the move as a vote of confidence in the region's "
            "growing cluster of seafood-technology firms based around the town's docks.",
        ],
        payload={
            "is_about_single_company": True,
            "company_name": "KeelLine",
            "one_line_description": "Seafood technology",
            "sector": "other",
            "stage": "pre_seed",
            "hq_city": "Grimsby",
            "hq_country_iso2": "GB",
            "extraction_confidence": 0.3,
            "evidence_quote_company": "KeelLine, a Grimsby startup, has raised funding in a successful pre-seed round",
            "evidence_quote_amount": "has raised funding in a successful pre-seed round",
            "evidence_quote_stage": "in a successful pre-seed round",
        },
        expected={
            "is_about_single_company": True,
            "company_name": "KeelLine",
            "sector": "other",
            "stage": "pre_seed",
            "amount": None,
            "needs_review": True,
        },
        og_title="Grimsby startup receives funding boost, local MP says",
    ),
]


def main() -> int:
    ARTICLES.mkdir(parents=True, exist_ok=True)
    LLM_CACHE.mkdir(parents=True, exist_ok=True)

    print(f"building {len(FIXTURES)} fixtures (cache key via {PROMPT_VERSION} / {DEFAULT_MODEL})")
    live_keys: set[str] = set()
    for fixture in FIXTURES:
        key = fixture.write()
        if key:
            live_keys.add(key)

    # A stale entry is a silent liability: it makes a cache miss look like a
    # hit for whatever text used to hash to it. The committed cache is exactly
    # the set of keys the current fixtures ask for, and nothing else.
    for path in sorted(LLM_CACHE.glob("*.json")):
        if path.stem not in live_keys:
            path.unlink()
            print(f"  pruned stale cache entry {path.stem[:12]}…")

    slugs = {f.slug for f in FIXTURES}
    for path in sorted(ARTICLES.glob("*")):
        stem = path.name.split(".", 1)[0]
        if stem not in slugs:
            path.unlink()
            print(f"  pruned stale article {path.name}")

    failures = 0
    print("verifying every fixture end-to-end...")
    for fixture in FIXTURES:
        errors = fixture.verify()
        if errors:
            failures += 1
            print(f"  ✗ {fixture.slug}")
            for error in errors:
                print(error)
        else:
            print(f"  ✓ {fixture.slug}")

    n_cache = len(list(LLM_CACHE.glob("*.json")))
    print(f"\n{len(FIXTURES)} articles, {n_cache} cache entries (expected {len(live_keys)})")
    if n_cache != len(live_keys):
        failures += 1
        print("FAILED — cache directory does not match the fixture set")
    print("OK — all fixtures verified" if failures == 0 else f"FAILED — {failures} problem(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
