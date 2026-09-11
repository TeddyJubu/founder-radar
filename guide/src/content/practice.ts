export interface PracticeCompany {
  id: string
  name: string
  oneLiner: string
  region: string
  ageMonths: number
  bestFund: string
  fitNote: string
}

/** Fictional UK companies for the teaching deck — not live radar data. */
export const practiceCompanies: PracticeCompany[] = [
  {
    id: "northgate-labs",
    name: "Northgate Labs",
    oneLiner: "Durham spinout building climate sensors for industrial sites.",
    region: "North East",
    ageMonths: 11,
    bestFund: "Northstar",
    fitNote: "University spinout + NE HQ — Spinout Inspire / NE Innovation territory.",
  },
  {
    id: "ledgerlane",
    name: "Ledgerlane",
    oneLiner: "B2B workflow where payments reconciliation is the product.",
    region: "UK-wide (Manchester)",
    ageMonths: 18,
    bestFund: "Outward",
    fitNote: "Finance is the product layer — Outward Fund II mandate, not an EIS story.",
  },
  {
    id: "heather-grid",
    name: "Heather Grid",
    oneLiner: "Yorkshire deeptech tooling with a clear path to first revenue.",
    region: "Yorkshire",
    ageMonths: 22,
    bestFund: "Anticus",
    fitNote: "Yorkshire geo is binding; Seedcorn-style commercial path.",
  },
]

export const practiceTeaching = {
  quietMorning:
    "Some mornings the list is empty. That is okay. The tool should not invent companies just to fill space.",
  keyboard:
    "Press 1 (yes), 2 (maybe), or 3 (no). Watch the card go to Kept — or to “won’t show again.”",
}
