"""Seed data — the only place in the codebase where a literal is allowed.

Everything here is what the Google Sheet contains on first run: the four funds
and their **eleven vehicles with canonical keys** (07-interfaces tab 4), the
0-4 weight matrix preserved verbatim from Aryan's `VC Scout.xlsx`, the
attribute-importance table (06-scoring §5.1), and the lookup tables the
derivation rules read (06-scoring §2).

After the first run the sheet is the source of truth and these are only the
fallback. Nothing in `radar/score/` may hard-code a threshold, a weight, a
region or a toggle — it all comes from `Config`, and `Config` starts here.

Verification status (06-scoring §12) is preserved in comments where a value is
**unverified**: Northstar's EIS Growth cheque size and Anticus's cheque floors
are not published anywhere, so they are left blank rather than guessed.
"""

from __future__ import annotations

from typing import Any

from .models import Config, Fund, Settings, SourceConfig, Vehicle, Weights

DEFAULT_SETTINGS = Settings()

# --------------------------------------------------------------- 4 funds

FUND_NAMES = {
    "outward": "Outward VC",
    "dsw": "DSW Ventures",
    "northstar": "Northstar Ventures",
    "anticus": "Anticus Partners",
}

FUND_ORDER = ("outward", "dsw", "northstar", "anticus")

# ------------------------------------------------------------ 11 vehicles
#
# Order within a fund is meaningful: when two vehicles are both eligible and
# score identically (fit depends on the fund column, not the vehicle), the
# first one listed wins. That is why the specialist mandates come before the
# generalist ones — a Durham spinout should be routed to Spinout Inspire, not
# to the catch-all Innovation Fund.

VEHICLES: list[dict[str, Any]] = [
    # ---- Outward VC — 1 vehicle (ECF; no age cap, not an EIS fund)
    dict(
        fund_key="outward", vehicle_key="fund_ii",
        fund_name="Outward VC", vehicle_name="Outward VC Fund II (ECF)",
        active=True, stage_min="pre_seed", stage_max="series_a",
        cheque_min=250_000, cheque_max=2_500_000,
        geo_rule="HARD", geo_values=["uk_wide"], max_age_years=None,
        hard_rejects={"round_max": 5_000_000, "prior_total_max": 20_000_000,
                      "uk_exec_pct_min": 66},
        sectors_plus=["fintech", "insurtech", "regtech", "lending", "wealthtech", "ai_data"],
        sectors_minus=["consumer"],
        one_liner="Send if finance is the product or an essential layer in the workflow.",
    ),
    # ---- DSW Ventures — 3 vehicles (every deal requires SEIS/EIS)
    dict(
        fund_key="dsw", vehicle_key="seis_fund",
        fund_name="DSW Ventures", vehicle_name="DSW SEIS Fund",
        active=True, stage_min="idea", stage_max="pre_seed",
        cheque_min=50_000,  # UNVERIFIED: only the £10k investor minimum is public
        cheque_max=250_000,  # SEIS lifetime cap
        geo_rule="HARD", geo_values=["outside_golden_triangle"], max_age_years=3,
        hard_rejects={"requires_seis_eis": True, "valuation_max": 10_000_000,
                      "round_max": 2_500_000},
        sectors_plus=["deeptech", "b2b_saas", "life_sciences"],
        sectors_minus=["lending"],
        one_liner="Regional UK tech with defensibility.",
    ),
    dict(
        fund_key="dsw", vehicle_key="eis_service",
        fund_name="DSW Ventures", vehicle_name="DSW EIS Investment Service",
        active=True, stage_min="pre_seed", stage_max="series_a",
        cheque_min=100_000, cheque_max=1_000_000,
        geo_rule="SOFT", geo_values=["uk_regions"], max_age_years=7,
        hard_rejects={"requires_seis_eis": True, "valuation_max": 10_000_000},
        sectors_plus=["b2b_saas", "vertical_saas", "deeptech", "ai_data"],
        sectors_minus=["lending"],
        one_liner="Revenue or commercial validation.",
    ),
    dict(
        fund_key="dsw", vehicle_key="bbi_coinvest",
        fund_name="DSW Ventures", vehicle_name="British Business Investments co-investment",
        active=False,  # off by default — follows the other two
        stage_min="pre_seed", stage_max="series_a",
        cheque_min=None, cheque_max=None,
        geo_rule="SOFT", geo_values=["uk_regions"], max_age_years=7,
        hard_rejects={},
        sectors_plus=["b2b_saas", "vertical_saas", "deeptech", "ai_data"],
        sectors_minus=["lending"],
        one_liner="Off by default — follows the other two.",
    ),
    # ---- Northstar Ventures — 5 vehicles
    dict(
        fund_key="northstar", vehicle_key="spinout_inspire",
        fund_name="Northstar Ventures", vehicle_name="North East Spinout Inspire Fund",
        active=True, stage_min="pre_seed", stage_max="seed",
        cheque_min=200_000, cheque_max=750_000,
        geo_rule="HARD", geo_values=["north_east"], max_age_years=None,
        hard_rejects={"university_spinout_required":
                      ["durham", "newcastle", "northumbria", "sunderland", "teesside"]},
        sectors_plus=["climate_tech", "life_sciences", "healthy_ageing", "ai_data"],
        sectors_minus=[],
        one_liner="Meaningful challenge, tech substance, NE relevance.",
    ),
    dict(
        fund_key="northstar", vehicle_key="venture_sunderland",
        fund_name="Northstar Ventures", vehicle_name="Venture Sunderland Fund",
        active=True, stage_min="idea", stage_max="growth",
        cheque_min=200_000, cheque_max=750_000,
        # The fund's wording is "founders based in or relocating to Sunderland".
        # Implemented as company HQ in the Sunderland city region because the
        # data model forbids storing founder home addresses (03-data-model §2).
        geo_rule="HARD", geo_values=["sunderland"], max_age_years=None,
        hard_rejects={},
        sectors_plus=["industrial_tech", "healthcare", "climate_tech"],
        sectors_minus=[],
        one_liner="Sunderland HQ or relocating.",
    ),
    dict(
        fund_key="northstar", vehicle_key="ne_innovation_fund",
        fund_name="Northstar Ventures", vehicle_name="North East Innovation Fund",
        active=True, stage_min="idea", stage_max="series_a",
        cheque_min=50_000, cheque_max=500_000,
        geo_rule="HARD", geo_values=["north_east"], max_age_years=None,
        hard_rejects={},
        sectors_plus=[],  # agnostic
        sectors_minus=[],
        one_liner="County Durham, Tyne & Wear, Northumberland.",
    ),
    dict(
        fund_key="northstar", vehicle_key="eis_growth",
        fund_name="Northstar Ventures", vehicle_name="Northstar EIS Growth Fund",
        active=True, stage_min="seed", stage_max="series_a",
        cheque_min=None, cheque_max=None,  # UNVERIFIED: not published anywhere
        geo_rule="SOFT", geo_values=["north_england"], max_age_years=7,
        hard_rejects={"requires_seis_eis": True},
        sectors_plus=["climate_tech", "healthy_ageing", "ai_data"],
        sectors_minus=["lending"],
        one_liner="Late seed with revenue traction.",
    ),
    dict(
        fund_key="northstar", vehicle_key="ne_social",
        fund_name="Northstar Ventures", vehicle_name="NE Social Investment Fund",
        active=False,  # off by default — not equity VC
        stage_min="idea", stage_max="growth",
        cheque_min=100_000, cheque_max=1_000_000,
        geo_rule="HARD", geo_values=["north_east"], max_age_years=None,
        hard_rejects={},
        sectors_plus=[], sectors_minus=[],
        one_liner="Off by default — not equity VC.",
    ),
    # ---- Anticus Partners — 2 vehicles (recycled public money; no age cap)
    dict(
        fund_key="anticus", vehicle_key="fy_seedcorn",
        fund_name="Anticus Partners", vehicle_name="Finance Yorkshire Seedcorn Fund",
        active=True, stage_min="pre_seed", stage_max="series_a",
        cheque_min=100_000,  # UNVERIFIED: only the maximum is published
        cheque_max=1_500_000,
        geo_rule="HARD", geo_values=["yorkshire"], max_age_years=None,
        hard_rejects={"beyond_research_stage": True},
        sectors_plus=[],  # effectively agnostic — see the note below
        sectors_minus=[],
        one_liner="Yorkshire relevance + commercial path.",
    ),
    dict(
        fund_key="anticus", vehicle_key="fy_growth",
        fund_name="Anticus Partners", vehicle_name="Finance Yorkshire Growth Fund",
        active=True, stage_min="seed", stage_max="growth",
        cheque_min=100_000, cheque_max=1_500_000,  # floor UNVERIFIED
        geo_rule="HARD", geo_values=["yorkshire"], max_age_years=None,
        hard_rejects={},
        sectors_plus=[], sectors_minus=[],
        one_liner="Profitable or approaching profitability.",
    ),
]

# Anticus says "technology and knowledge-based businesses", but the portfolio
# includes a cereal brand, a cashmere retailer and an auction house. The sector
# filter is therefore genuinely broad and Yorkshire geography is the binding
# constraint — weighting their sector column heavily would systematically miss
# the companies they actually back (06-scoring §4.4). This is why `sector`
# importance for Anticus is 2 while `geography` is 4.

# ------------------------------------------------- the matrix (06-scoring §5)
# Column order throughout: DSW / Northstar / Outward / Anticus.

_COLUMNS = ("dsw", "northstar", "outward", "anticus")

_MATRIX_ROWS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "stage": {
        "idea":          (1, 1, 2, 1),
        "pre_seed":      (3, 2, 3, 2),
        "seed":          (3, 3, 3, 2),
        "series_a":      (2, 3, 1, 1),
        "series_b_plus": (0, 1, 0, 1),
        "growth":        (0, 1, 0, 3),
    },
    "sector": {
        "fintech":         (0, 0, 4, 0),
        "insurtech":       (0, 0, 4, 0),
        "wealthtech":      (0, 0, 4, 0),
        "lending":         (0, 0, 4, 0),
        "regtech":         (0, 0, 4, 0),
        "b2b_saas":        (3, 1, 1, 1),
        "vertical_saas":   (3, 1, 2, 1),
        "ai_data":         (2, 1, 3, 1),
        "climate_tech":    (1, 4, 0, 1),
        "healthy_ageing":  (0, 4, 0, 1),
        "life_sciences":   (1, 4, 0, 0),
        "healthcare":      (1, 2, 1, 1),
        "deeptech":        (3, 3, 0, 1),
        "developer_tools": (2, 1, 1, 0),
        "consumer":        (1, 0, 0, 1),
        "marketplace":     (0, 0, 0, 1),
        "industrial_tech": (1, 2, 0, 1),
        "other":           (0, 0, 0, 0),
    },
    "geography": {
        "london":       (0, 0, 1, 0),
        "uk_regions":   (4, 2, 1, 1),
        "north_east":   (2, 4, 0, 0),
        "yorkshire":    (1, 1, 0, 4),
        "uk_wide":      (2, 2, 1, 2),
        "europe_ex_uk": (0, 0, 1, 0),
        "global":       (0, 0, 1, 0),
        "us":           (0, 0, 0, 0),
        "other":        (0, 0, 0, 0),
    },
    "founder_signal": {
        "repeat_founder":     (2, 2, 2, 2),
        "technical_founder":  (3, 2, 2, 1),
        "domain_expert":      (1, 2, 4, 2),
        "research_spinout":   (4, 4, 0, 0),
        "operator_led":       (1, 1, 2, 2),
        "student_founder":    (0, 0, 1, 0),
        "generalist_unclear": (0, 0, 0, 0),
    },
    "traction_signal": {
        "pre_revenue_concept":       (0, 0, 1, 0),
        "pilot_customers":           (1, 1, 2, 1),
        "paying_customers":          (3, 2, 3, 2),
        "rapid_usage_growth":        (2, 1, 2, 1),
        "strong_revenue_growth":     (3, 3, 2, 3),
        "enterprise_contracts":      (2, 3, 2, 2),
        "clinical_grant_validation": (1, 3, 0, 1),
        "community_traction":        (1, 0, 1, 1),
    },
}

# ----------------------------------------- attribute importance (§5.1)
# Integers 0-10. A blank cell means 1, not 0 (handled by Weights).
# Outward cares less about region and more about traction; Anticus is nearly
# sector-agnostic but Yorkshire is everything.

_IMPORTANCE_ROWS: dict[str, tuple[int, int, int, int]] = {
    "stage":           (3, 3, 3, 3),
    "sector":          (4, 4, 4, 2),
    "geography":       (4, 4, 2, 4),
    "founder_signal":  (3, 3, 3, 3),
    "traction_signal": (2, 2, 3, 3),
}

# Absence of evidence is not evidence of absence — `neutral` everywhere by
# default. 06-scoring §6 allows `pessimistic` and `assume` per attribute.
_UNKNOWN_POLICY = {attr: "neutral" for attr in _IMPORTANCE_ROWS}


def _expand(rows: dict) -> dict:
    return {value: dict(zip(_COLUMNS, cells)) for value, cells in rows.items()}


def default_weights() -> Weights:
    return Weights(
        matrix={attr: _expand(rows) for attr, rows in _MATRIX_ROWS.items()},
        importance={attr: dict(zip(_COLUMNS, cells)) for attr, cells in _IMPORTANCE_ROWS.items()},
        unknown_policy=dict(_UNKNOWN_POLICY),
    )


# ------------------------------------------------ derivation tables (§2)
#
# 06-scoring §12: the SIC → sector mapping is **judgement, not fact.** These
# are a starting point to be tuned against real output in Phase 3. SIC on a
# newly incorporated company is self-declared, never audited and often lazily
# generic, which is exactly what the low confidence on a derived observation
# encodes.

SIC_SECTOR: dict[str, dict[str, str]] = {
    "exact": {
        "62012": "b2b_saas", "62020": "b2b_saas", "62090": "b2b_saas",
        "63110": "ai_data", "63120": "ai_data", "63990": "ai_data",
        "72190": "deeptech", "71121": "deeptech", "71122": "deeptech", "71129": "deeptech",
        "26110": "industrial_tech", "26120": "industrial_tech", "26200": "industrial_tech",
        "26511": "industrial_tech", "26701": "industrial_tech",
        "64205": "fintech", "66190": "fintech",
        "58290": "developer_tools",
        "21100": "life_sciences", "21200": "life_sciences", "32500": "life_sciences",
        "26600": "life_sciences", "72110": "life_sciences",
        "58210": "consumer", "62011": "consumer",
    },
    "prefix": {
        "86": "healthcare",
        "35": "climate_tech",
        "38": "climate_tech",
    },
}

# Offline outcode → vocabulary map. The live path resolves postcodes through
# postcodes.io and caches them in `postcode_region` (Phase 3); scoring must stay
# a pure function, so it reads this instead. Postcode areas not listed fall
# through to `uk_wide` — UK confirmed, region not resolved.
OUTCODE_REGION: dict[str, str] = {
    # North East
    "NE": "north_east", "SR": "north_east", "DH": "north_east",
    "DL": "north_east", "TS": "north_east",
    # Yorkshire and The Humber
    "LS": "yorkshire", "BD": "yorkshire", "HD": "yorkshire", "HX": "yorkshire",
    "WF": "yorkshire", "S": "yorkshire", "YO": "yorkshire", "HU": "yorkshire",
    "DN": "yorkshire", "HG": "yorkshire",
    # London
    "E": "london", "EC": "london", "N": "london", "NW": "london", "SE": "london",
    "SW": "london", "W": "london", "WC": "london", "EN": "london", "IG": "london",
    "RM": "london", "CR": "london", "BR": "london", "DA": "london", "KT": "london",
    "SM": "london", "TW": "london", "UB": "london", "HA": "london", "WD": "london",
    # Scotland, Wales and Northern Ireland — postcodes.io leaves `region` empty
    # for all three, so `country` wins and they are all `uk_regions`.
    "AB": "uk_regions", "DD": "uk_regions", "DG": "uk_regions", "EH": "uk_regions",
    "FK": "uk_regions", "G": "uk_regions", "HS": "uk_regions", "IV": "uk_regions",
    "KA": "uk_regions", "KW": "uk_regions", "KY": "uk_regions", "ML": "uk_regions",
    "PA": "uk_regions", "PH": "uk_regions", "TD": "uk_regions", "ZE": "uk_regions",
    "CF": "uk_regions", "LD": "uk_regions", "LL": "uk_regions", "NP": "uk_regions",
    "SA": "uk_regions", "SY": "uk_regions", "BT": "uk_regions",
    # England outside London, the North East and Yorkshire.
    "OX": "uk_regions", "CB": "uk_regions", "M": "uk_regions", "L": "uk_regions",
    "B": "uk_regions", "BS": "uk_regions", "BA": "uk_regions", "BH": "uk_regions",
    "BN": "uk_regions", "CV": "uk_regions", "DE": "uk_regions", "LE": "uk_regions",
    "NG": "uk_regions", "NN": "uk_regions", "PE": "uk_regions", "RG": "uk_regions",
    "SO": "uk_regions", "ST": "uk_regions", "TF": "uk_regions", "WA": "uk_regions",
    "WV": "uk_regions", "SK": "uk_regions", "OL": "uk_regions", "BL": "uk_regions",
    "CH": "uk_regions", "CW": "uk_regions", "PR": "uk_regions", "FY": "uk_regions",
    "LA": "uk_regions", "CA": "uk_regions", "IP": "uk_regions", "NR": "uk_regions",
    "CO": "uk_regions", "CM": "uk_regions", "SS": "uk_regions", "ME": "uk_regions",
    "CT": "uk_regions", "TN": "uk_regions", "RH": "uk_regions", "GU": "uk_regions",
    "SL": "uk_regions", "HP": "uk_regions", "AL": "uk_regions", "LU": "uk_regions",
    "MK": "uk_regions", "SN": "uk_regions", "SP": "uk_regions", "GL": "uk_regions",
    "HR": "uk_regions", "WR": "uk_regions", "DY": "uk_regions", "WS": "uk_regions",
    "TQ": "uk_regions", "EX": "uk_regions", "PL": "uk_regions", "TR": "uk_regions",
    "DT": "uk_regions", "TA": "uk_regions", "WN": "uk_regions", "PO": "uk_regions",
    "SG": "uk_regions", "NX": "uk_regions",
}

# `region` is populated by postcodes.io for England only; Scotland, Wales and
# Northern Ireland fall through to `country`, and all of them are `uk_regions`.
POSTCODE_COUNTRY_REGIONS = ("Scotland", "Wales", "Northern Ireland")

VALUE_LABELS: dict[str, str] = {
    "north_east": "North East", "yorkshire": "Yorkshire", "london": "London",
    "uk_regions": "UK Regions", "uk_wide": "UK Wide", "europe_ex_uk": "Europe ex-UK",
    "us": "US", "global": "Global",
    "pre_seed": "Pre-seed", "series_a": "Series A", "series_b_plus": "Series B+",
    "b2b_saas": "B2B SaaS", "ai_data": "AI/Data", "vertical_saas": "Vertical SaaS",
    "research_spinout": "research/spinout", "generalist_unclear": "generalist/unclear",
    "clinical_grant_validation": "clinical/grant validation",
    "pre_revenue_concept": "pre-revenue concept",
}

# Discovery Edge / Fresh curve (06-scoring §7). Continuous age so co-aged
# step-bands no longer flatten every young registry company to the same Fresh
# tile. **Judgement** — tune against Aryan's verdicts in Phase 9.
DISCOVERY_EDGE: dict[str, Any] = {
    "weights": {"age": 30, "press_coverage": 30, "disclosed_funding": 20, "discovery_route": 20},
    "age_curve": [[0, 1.0], [12, 0.85], [24, 0.55], [36, 0.15]],
    "age_bands": [[6, 1.0], [18, 0.8], [30, 0.5], [36, 0.2]],  # legacy fallback
    "age_unknown": 0.5,
    "press_bands": [[0, 1.0], [1, 0.7], [4, 0.4]],
    "press_unknown": 0.5,
    # known none → 1.0 · <£500k → 0.6 · £500k-£1.5m → 0.3 · >£1.5m → 0.0
    "funding_bands": [[0, 1.0], [499_999.99, 0.6], [1_500_000, 0.3]],
    "funding_unknown": 0.5,
    # Discovery rebalance (18 Aug 2026): source selectivity, not the
    # collection mechanism, determines the route premium.
    "route_scores": {"spinout": 1.0, "accelerator": 0.9, "grant": 0.8,
                     "registry": 0.7, "news": 0.5, "portfolio": 0.2},
    "route_unknown": 0.5,
    "route_registry_with_press": 0.6,
}

# SEIS/EIS excluded trades (gov.uk, current as of the 6 Apr 2026 changes):
# lending, banking, insurance, financial services, property development,
# hotels, nursing homes, energy generation, farming, leasing, coal, steel.
#
# ponytail: only `lending` maps cleanly onto the sector vocabulary in
# `03-data-model §4`. `insurtech` and `fintech` are software sold *to* those
# trades far more often than they are those trades, so excluding them would
# reject good DSW candidates on a technicality the fund itself would not
# apply. The remaining trades (hotels, farming, coal, steel...) have no
# vocabulary equivalent at all. Widen this list from the sheet if real output
# shows it is too narrow.
SEIS_EIS_EXCLUDED_SECTORS = ["lending"]

LISTS: dict[str, Any] = {
    "sic_sector": SIC_SECTOR,
    "outcode_region": OUTCODE_REGION,
    "postcode_country_regions": list(POSTCODE_COUNTRY_REGIONS),
    "value_labels": VALUE_LABELS,
    "discovery_edge": DISCOVERY_EDGE,
    "seis_eis_excluded_sectors": SEIS_EIS_EXCLUDED_SECTORS,
    # DSW's SEIS golden-triangle rule is an outcode-prefix check, not a fuzzy
    # city match (06-scoring §2.2).
    "golden_triangle_outcodes": ["OX", "CB"],
    "sunderland_city_region": ["sunderland", "washington", "houghton-le-spring",
                               "hetton-le-hole", "ryhope", "seaburn"],
    "sunderland_outcodes": ["SR"],
    "north_england_geographies": ["north_east", "yorkshire"],
    "technical_founder_sectors": ["deeptech", "life_sciences"],
    "pre_revenue_stages": ["pre_seed", "idea"],
    "research_stages": ["idea"],
    "stage_derivation": {"share_issue_pre_seed_max_months": 24, "idea_max_months": 12},
    "signal_traction": {"grant_award": "clinical_grant_validation",
                        "competition_win": "community_traction"},
    "assume_values": {},
    # The qualifiers that admit a registry (Track B) company to scoring. A live
    # `website` is intentionally omitted: almost every registered Ltd has one,
    # so it is noise, not a venture signal (client feedback, 18 Aug 2026).
    # `repeat_founder` is scored but does not admit on its own: Companies House
    # officers with a prior appointment are accountants, formation agents, and
    # serial small-business directors, which is how random Ltd names kept
    # filling Today. Add either token back here to loosen without touching code.
    "qualifiers": ["share_issue", "grant", "spinout", "press"],
}

# ------------------------------------------------------------------ sources

DEFAULT_SOURCES = [
    SourceConfig(key="companies_house", track="B", note="The register sweep — Track B"),
    SourceConfig(key="northern_accelerator", track="A"),
    SourceConfig(key="cambridge_enterprise", track="A"),
    # Registry key, not a display name: the Sources-tab Enabled toggle and the
    # health join both key on `adapter.key`, so the seed must equal the
    # registry key exactly (this was `oxford_university_innovation` once, which
    # made the toggle inert and the health column blind).
    SourceConfig(key="oxford_innovation", track="A"),
    # Inverted — Zinc investment announcements feed `on_vc_portfolio`, they
    # are not a discovery source. Track column on the sheet stays A|B; the
    # adapter itself reports track "—".
    SourceConfig(key="zinc_vc", track="A",
                 note="Denylist — Zinc investment announcements"),
    SourceConfig(key="conception_x", track="A"),
    SourceConfig(key="entrepreneur_first", track="A"),
    # Client ask A5 — named early-stage announcement sources, not portfolio
    # dumps. Founders Factory "Investing in X" posts and UCL Ventures news
    # (not the dead uclb.com portfolio) are where companies appear first.
    SourceConfig(key="founders_factory", track="A",
                 note="Founders Factory announcement posts"),
    SourceConfig(key="ucl_ventures", track="A",
                 note="UCL Ventures spinout news"),
    SourceConfig(key="businesscloud", track="A"),
    SourceConfig(key="uktn", track="A"),
    SourceConfig(key="govuk_search", track="A"),
    # Client ask A5 — dedicated Innovate UK award feeds (04-sources Tier 1).
    # `ukri_gtr` (weekly) and `innovate_uk` (monthly) are the "first
    # appearance" grant announcements Aryan asked for; both filter by date and
    # dedupe on (source_key, external_id), so running them every day is cheap.
    SourceConfig(key="ukri_gtr", track="A", note="Innovate UK awards via UKRI GtR"),
    SourceConfig(key="innovate_uk", track="A", note="Innovate UK funded-projects workbook"),
    SourceConfig(key="vc_portfolios", track="A", note="The denylist — feeds on_vc_portfolio"),
]


# --------------------------------------------------------------- the config


def default_vehicles() -> list[Vehicle]:
    return [Vehicle(**row) for row in VEHICLES]


def default_funds() -> list[Fund]:
    vehicles = default_vehicles()
    return [
        Fund(
            key=key,
            name=FUND_NAMES[key],
            vehicles=[v for v in vehicles if v.fund_key == key],
        )
        for key in FUND_ORDER
    ]


def default_config() -> Config:
    """Four funds, eleven vehicles, the full matrix and the importance table.

    This is what `pytest`'s `config` fixture returns and what the pipeline
    falls back to when the sheet is unreachable.
    """
    return Config(
        settings=Settings(),
        funds=default_funds(),
        weights=default_weights(),
        sources=list(DEFAULT_SOURCES),
        lists=dict(LISTS),
    )
