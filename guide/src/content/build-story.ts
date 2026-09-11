export interface BuildPhase {
  id: string
  phase: string
  title: string
  easy: string
  technical: string
  highlight?: boolean
}

export const buildOrderOneLinerEasy =
  "First we taught the computer how to score. Then we taught it where to look — starting with birthdays on the company register."

export const buildOrderOneLiner =
  "Prove the scoring first, then feed it — starting with the register, because that is what makes the companies young."

export const buildPhases: BuildPhase[] = [
  {
    id: "p0",
    phase: "0",
    title: "Skeleton",
    easy: "Build the empty house: folders, database, basic commands, a health check.",
    technical: "pyproject, schema.sql, db layer, Pydantic models, pytest green, doctor pass/fail table.",
  },
  {
    id: "p1",
    phase: "1",
    title: "Gates & scoring",
    easy: "The heart of the product: hard rules and Match / Fresh scores — before any crawling.",
    technical:
      "derive → gates → fund fit → discovery edge → tiering. test_freshness_gates and registry derivation must pass with zero network code.",
    highlight: true,
  },
  {
    id: "p2",
    phase: "2",
    title: "Entity resolution",
    easy: "Stop the same company showing up twice.",
    technical: "Match ladder, merge with provenance, no token_set_ratio / partial_ratio / WRatio.",
  },
  {
    id: "p3",
    phase: "3",
    title: "Companies House",
    easy: "Connect to the official UK register so we know real birthdays.",
    technical: "Date-windowed SIC batches, ≤40 requests on 90-day backfill, privacy filter at ingest.",
    highlight: true,
  },
  {
    id: "p4",
    phase: "4",
    title: "Extraction",
    easy: "Teach the tool to read news articles into clear facts — with a safe backup if AI is down.",
    technical: "Schema + prefilter + LLM cache + 25 golden fixtures; offline pytest blocks the socket.",
  },
  {
    id: "p5",
    phase: "5",
    title: "Signal sources",
    easy: "Add good clue sources: spinouts, accelerators, grants, carefully chosen UK tech news.",
    technical: "Isolated adapters + layout-change detector; one failure never stops the run.",
  },
  {
    id: "p6",
    phase: "6",
    title: "Sheet & config",
    easy: "Let people edit fund rules in a spreadsheet without rewriting the code.",
    technical: "Coerce + validate + last-good; minimal-diff sheet render; status column for humans.",
  },
  {
    id: "p7",
    phase: "7",
    title: "Telegram & ops",
    easy: "Morning ping on your phone, and remote controls that still save for real.",
    technical: "Digest, Hermes skill, systemd timer, heartbeat, backups — Hermes is not the scheduler.",
  },
  {
    id: "p8",
    phase: "8",
    title: "Tuning & polish",
    easy: "Lock the numbers against real yes / no choices until the list feels fair.",
    technical: "tune, forget (GDPR), chaos tests, privacy notice, acceptance checklist.",
  },
  {
    id: "p9",
    phase: "9",
    title: "Live validation",
    easy: "Use it every morning with the scout until everyone trusts the list.",
    technical: "Five consecutive unattended runs; median age <24 months; ≥70% worth contacting.",
  },
]
