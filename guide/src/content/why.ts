import type { Audience } from "./types"

export interface WhyContent {
  easyLead: string
  easyBody: string[]
  technicalExtra: string[]
  successLine: string
}

export const whyContent: WhyContent = {
  easyLead: "Old lists were too late. We start from birthdays instead.",
  easyBody: [
    "This tool finds young companies in the UK. Then it says which of four money funds might care — and why.",
    "The first version looked at fund websites. Those pages only show companies that already got money. So the list looked old.",
    "The new version checks the official UK company list. Every company has a birthday there. If a company is only three months old, it cannot be six years old.",
  ],
  technicalExtra: [
    "The client's success metric is not “I found a startup.” It is “I introduced a fund to a company they had not seen.” If a fund replies “we know them, we passed last year,” the system failed even though it technically worked.",
    "Median age of shortlisted companies under 24 months is the headline acceptance measure. Freshness gates (age, funding, stage, already-on-portfolio, UK presence) make old companies structurally impossible to shortlist.",
    "Companies House is verification and incorporation-age evidence — not the primary way to surface high-quality startups. Signal sources (spinouts, accelerators, grants, curated news) carry quality discovery; the register enforces youth.",
  ],
  successLine:
    "A win is: you introduce a fund to a company they have not already seen.",
}

export function whyFor(audience: Audience): string[] {
  return audience === "technical"
    ? [...whyContent.easyBody, ...whyContent.technicalExtra]
    : whyContent.easyBody
}
