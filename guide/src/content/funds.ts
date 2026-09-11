/** Canonical fund rules from radar/config/defaults.py — no invented marketing copy. */

export interface VehicleSummary {
  key: string
  name: string
  active: boolean
  stage: string
  cheque: string
  geo: string
  hardRules: string[]
  oneLiner: string
}

export interface FundCard {
  key: "dsw" | "northstar" | "outward" | "anticus"
  name: string
  mandate: string
  vehicles: VehicleSummary[]
}

export const fundCards: FundCard[] = [
  {
    key: "dsw",
    name: "DSW Ventures",
    mandate: "Regional UK tech with defensibility; every active deal path requires SEIS/EIS.",
    vehicles: [
      {
        key: "seis_fund",
        name: "DSW SEIS Fund",
        active: true,
        stage: "idea → pre_seed",
        cheque: "£50k–£250k (floor unverified publicly)",
        geo: "HARD · outside_golden_triangle · max age 3y",
        hardRules: ["requires_seis_eis", "valuation_max £10m", "round_max £2.5m"],
        oneLiner: "Regional UK tech with defensibility.",
      },
      {
        key: "eis_service",
        name: "DSW EIS Investment Service",
        active: true,
        stage: "pre_seed → series_a",
        cheque: "£100k–£1m",
        geo: "SOFT · uk_regions · max age 7y",
        hardRules: ["requires_seis_eis", "valuation_max £10m"],
        oneLiner: "Revenue or commercial validation.",
      },
      {
        key: "bbi_coinvest",
        name: "British Business Investments co-investment",
        active: false,
        stage: "pre_seed → series_a",
        cheque: "—",
        geo: "SOFT · uk_regions · max age 7y",
        hardRules: [],
        oneLiner: "Off by default — follows the other two.",
      },
    ],
  },
  {
    key: "northstar",
    name: "Northstar Ventures",
    mandate: "North East relevance across specialist and generalist vehicles.",
    vehicles: [
      {
        key: "spinout_inspire",
        name: "North East Spinout Inspire Fund",
        active: true,
        stage: "pre_seed → seed",
        cheque: "£200k–£750k",
        geo: "HARD · north_east · no age cap",
        hardRules: [
          "university_spinout_required: durham, newcastle, northumbria, sunderland, teesside",
        ],
        oneLiner: "Meaningful challenge, tech substance, NE relevance.",
      },
      {
        key: "venture_sunderland",
        name: "Venture Sunderland Fund",
        active: true,
        stage: "idea → growth",
        cheque: "£200k–£750k",
        geo: "HARD · sunderland · no age cap",
        hardRules: [],
        oneLiner: "Sunderland HQ or relocating.",
      },
      {
        key: "ne_innovation_fund",
        name: "North East Innovation Fund",
        active: true,
        stage: "idea → series_a",
        cheque: "£50k–£500k",
        geo: "HARD · north_east · no age cap",
        hardRules: [],
        oneLiner: "County Durham, Tyne & Wear, Northumberland.",
      },
      {
        key: "eis_growth",
        name: "Northstar EIS Growth Fund",
        active: true,
        stage: "seed → series_a",
        cheque: "unpublished (left blank, not guessed)",
        geo: "SOFT · north_england · max age 7y",
        hardRules: ["requires_seis_eis"],
        oneLiner: "Late seed with revenue traction.",
      },
      {
        key: "ne_social",
        name: "NE Social Investment Fund",
        active: false,
        stage: "idea → growth",
        cheque: "£100k–£1m",
        geo: "HARD · north_east",
        hardRules: [],
        oneLiner: "Off by default — not equity VC.",
      },
    ],
  },
  {
    key: "outward",
    name: "Outward VC",
    mandate:
      "Send if finance is the product or an essential layer in the workflow. One ECF vehicle — not an EIS fund; no age cap.",
    vehicles: [
      {
        key: "fund_ii",
        name: "Outward VC Fund II (ECF)",
        active: true,
        stage: "pre_seed → series_a",
        cheque: "£250k–£2.5m",
        geo: "HARD · uk_wide · no age cap",
        hardRules: [
          "round_max £5m",
          "prior_total_max £20m",
          "uk_exec_pct_min 66%",
        ],
        oneLiner: "Send if finance is the product or an essential layer in the workflow.",
      },
    ],
  },
  {
    key: "anticus",
    name: "Anticus Partners",
    mandate:
      "Yorkshire geography is the binding constraint; sector filter is genuinely broad.",
    vehicles: [
      {
        key: "fy_seedcorn",
        name: "Finance Yorkshire Seedcorn Fund",
        active: true,
        stage: "pre_seed → series_a",
        cheque: "£100k–£1.5m (floor unverified)",
        geo: "HARD · yorkshire · no age cap",
        hardRules: ["beyond_research_stage"],
        oneLiner: "Yorkshire relevance + commercial path.",
      },
      {
        key: "fy_growth",
        name: "Finance Yorkshire Growth Fund",
        active: true,
        stage: "seed → growth",
        cheque: "£100k–£1.5m (floor unverified)",
        geo: "HARD · yorkshire · no age cap",
        hardRules: [],
        oneLiner: "Profitable or approaching profitability.",
      },
    ],
  },
]

export const scoringExplainer = {
  easy: {
    matchVsFresh: [
      {
        title: "Match",
        body: "Does this company fit what the fund wants? Place, stage, and type of business. Higher Match means a closer fit.",
      },
      {
        title: "Fresh",
        body: "Is this company still new enough that the fund might not know it yet? Younger and less famous usually means higher Fresh.",
      },
    ],
    coverage:
      "If we only know a little about a company, we must not pretend we know a lot. Orange cards mean “thin info.” If we do not know something, we say unknown — we never invent it.",
  },
  technical: {
    gates: [
      "max_company_age_months default 36",
      "max_total_funding_gbp default £3m",
      "max_stage default series_a",
      "already_on_vc_portfolio reject",
      "min_uk_presence",
    ],
    nullPolicy:
      "A gate whose input is NULL passes and sets a flag. Any flag means the company cannot reach shortlist. Unknown is never coerced to zero.",
    derivation:
      "Companies House has no sector/stage/founder/traction in our vocabulary. Deterministic derivation maps SIC → sector, postcode → geography, filings/officers → signals before Fund Fit runs.",
    configHash:
      "Every score is stamped with config_hash = sha256(canonical_json(config)). Edit Fund Criteria or Weights → run rescore. Drift explains “why did this drop?” — use why-today and doctor.",
  },
}

/** Scan / breakdown order used on Today cards */
export const breakdownFundOrder = ["dsw", "northstar", "outward", "anticus"] as const
