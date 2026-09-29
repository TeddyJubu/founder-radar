export type ArchNodeId =
  | "trackA"
  | "trackB"
  | "engine"
  | "notebook"
  | "sheet"
  | "today"
  | "telegram"
  | "hermes"
  | "alarm"

export interface ArchNode {
  id: ArchNodeId
  label: string
  role: string
  question: string
  easy: string
  technical: string
  related: ArchNodeId[]
  detail: "tracks" | "sheet" | "engine" | null
}

export interface ArchLayer {
  name: string
  subtitle: string
  bullets: string[]
}

export const architectureNodes: ArchNode[] = [
  {
    id: "trackA",
    label: "Track A",
    role: "Someone already chose them",
    question: "What does Track A mean?",
    easy:
      "This is the quality door. A university spun the company out, an accelerator picked it, a grant panel funded it, or a UK tech paper wrote about it. A person already said “this one is interesting.” Those companies are usually real — but other scouts can see the same lists.",
    technical:
      "Signal-first adapters (spinout, accelerator, grant, news). RawItem → extract → the same resolve step as Track B. Lean on this track for quality discovery, not a raw dump of new filings.",
    related: ["trackB", "engine", "notebook"],
    detail: "tracks",
  },
  {
    id: "trackB",
    label: "Track B",
    role: "Only a birthday so far",
    question: "What does Track B mean?",
    easy:
      "This is the register door. The UK company register says a company was born recently. Nobody has had to like it yet. Most of these are not good startups. The useful ones often also filed a share allotment (SH01) — that looks like an early funding round on the public record.",
    technical:
      "Companies House advanced search by incorporated_from, SIC tiers, SH01, officers/PSC. High edge, lower yield. The register is age and identity evidence — not “this is a good company.” Register-only names should not flood Today.",
    related: ["trackA", "engine", "sheet"],
    detail: "tracks",
  },
  {
    id: "engine",
    label: "founder-radar",
    role: "The engine",
    question: "What does the engine do?",
    easy:
      "This is the only machine that does the work. It reads the rules, gathers both tracks, merges the same company into one record, scores with math (not a guess), lets a helper drop a wrong card, then writes the notebook and the windows.",
    technical:
      "Telegram, the website, and the sheet call the CLI. Scoring is a pure function of the database and config_hash — no AI, no network. Chat-only replies do not update Today, Kept, or the sheet.",
    related: ["trackA", "trackB", "notebook", "sheet"],
    detail: "engine",
  },
  {
    id: "notebook",
    label: "SQLite",
    role: "The one notebook",
    question: "Where is the real list?",
    easy:
      "Everything that matters lives here: company facts, scores, and your yes / maybe / no. The sheet, the website, and the phone are windows onto this notebook. They do not keep a second secret list.",
    technical:
      "radar.db holds companies, scores stamped with config_hash, user_field verdicts, and run logs. Kept is worth contacting / unsure. Rejects are stored for tuning but never listed on Kept.",
    related: ["engine", "today", "sheet", "telegram"],
    detail: null,
  },
  {
    id: "sheet",
    label: "Google Sheet",
    role: "Rules in, a copy out",
    question: "What does the Google Sheet do?",
    easy:
      "Two jobs, not one. You type the rules here: which funds, how scoring weights work, age limits, which sources are on. After each run the engine writes a copy back — a Today tab, every company, source health, and a run log. For the morning pass, use the Today website, not this sheet.",
    technical:
      "Fund Criteria is one row per vehicle. Invalid cells fall back to last-good config and write a status note — they do not abort the run. Outreach is human-only. The engine never invents a sheet row from chat.",
    related: ["engine", "today", "notebook"],
    detail: "sheet",
  },
  {
    id: "today",
    label: "Today / Kept",
    role: "Morning review",
    question: "Where do I actually read the list?",
    easy:
      "Open Today. Press 1 for yes, 2 for maybe, 3 for no. Yes and maybe go to Kept. No never comes back tomorrow. That is the morning job — about ten companies, not a spreadsheet hunt.",
    technical:
      "Web writes only user_field verdicts. Production Today is https://srv1821489.hstgr.cloud/. Local API is typically http://127.0.0.1:8787. Telegram search must land here, not dump companies in chat.",
    related: ["notebook", "telegram", "sheet"],
    detail: null,
  },
  {
    id: "telegram",
    label: "Telegram",
    role: "The ping",
    question: "What is the phone for?",
    easy:
      "A short message says the list is ready. You can tap from your phone, but a keep or reject only counts if it is saved in the notebook — not if you only typed it in chat.",
    technical:
      "publish --send refuses delivery when the publish gate BLOCKs. Hermes must run founder-radar decide. Digest is delivery, not a separate store.",
    related: ["hermes", "today", "engine"],
    detail: null,
  },
  {
    id: "hermes",
    label: "Hermes",
    role: "Front desk",
    question: "What may the helper do?",
    easy:
      "Hermes is the front desk. It turns a chat sentence into the right engine command, and it may take a wrong card off Today after scoring. It does not invent scores, pass a hard rule, or add a company by itself.",
    technical:
      "Layer 3 only: maps chat → CLI. Today QA is veto-only — PASS|REJECT with a stored reason; fail-open if Hermes is down. Never the retired v1 ~/radar sheet scout.",
    related: ["telegram", "engine", "today"],
    detail: null,
  },
  {
    id: "alarm",
    label: "VPS timer",
    role: "6:30 London time",
    question: "What wakes the system?",
    easy:
      "A small computer wakes at half past six, London time, runs the engine, and leaves a list — or an empty list on a quiet day, which is the filter working.",
    technical:
      "systemd timer at 06:30 Europe/London under /opt/founder-radar (user radar). Hermes is not the scheduler — OS cron/systemd survives hermes update.",
    related: ["engine", "hermes"],
    detail: null,
  },
]

export const trackCompare = {
  title: "How the two doors differ",
  rows: [
    { label: "Starts from", a: "A person already chose them", b: "A birthday on the UK register" },
    { label: "Examples", a: "Spinout, accelerator, grant, news", b: "New ltd + SIC code + SH01 filing" },
    { label: "Typical yield", a: "Fewer, more often real", b: "Many names, few worth a look" },
    { label: "Scout value", a: "Good volume, moderate edge", b: "Low volume, high edge if they pass" },
    { label: "Job", a: "Quality discovery", b: "Age and freshness evidence" },
  ],
  footer:
    "Both tracks meet at the engine. The same company found twice is one notebook row with two links.",
}

export const sheetJobs = {
  title: "Two jobs inside the same spreadsheet",
  youType: [
    "Fund Criteria — which funds and pots",
    "Scoring Weights — what matters more",
    "Settings — age limits, regions, on/off",
    "Lists — sector and region maps",
    "Outreach — your tracker only",
  ],
  engineWrites: [
    "Today tab — that day’s shortlist",
    "Companies — every company ever found",
    "Sources — which feeds failed",
    "Run Log — counts, time, cost",
    "Needs Review — things a human should check",
  ],
  footer:
    "Morning reading is the Today website. The sheet is the rule book and a backup view, not the daily desk.",
}

export const engineSteps = {
  title: "One morning, in order",
  rows: [
    { step: "1 Read rules", what: "Load the sheet. A typo uses last good rules.", ai: "No" },
    { step: "2 Gather", what: "Fetch Track A and Track B. One broken source does not stop the rest.", ai: "No" },
    { step: "3 Copy facts", what: "Turn article prose into a structured record.", ai: "Yes — boxed" },
    { step: "4 Merge", what: "Same company from two doors becomes one row.", ai: "No" },
    { step: "5 Score", what: "Hard gates, then Fit and Edge. Same inputs, same numbers.", ai: "No" },
    { step: "6 Last check", what: "Helper may drop a wrong Today card, with a stored reason.", ai: "Veto only" },
    { step: "7 Show", what: "Write the notebook, refresh the sheet, ping the phone.", ai: "No" },
  ],
}

export const architectureLayers: ArchLayer[] = [
  {
    name: "Engine",
    subtitle: "Deterministic Python",
    bullets: [
      "Fetch, resolve, enrich, gate, score, render",
      "No network in the scoring path",
      "No AI in the decision path",
      "Fully unit-testable offline",
    ],
  },
  {
    name: "Reader",
    subtitle: "Two boxed AI calls",
    bullets: [
      "Extract: article prose → structured record",
      "Today QA: Hermes subagent veto after scoring",
      "Schema-enforced, cached by content hash",
      "Deterministic fallback when the model is down",
    ],
  },
  {
    name: "Front desk",
    subtitle: "Hermes Agent",
    bullets: [
      "Telegram allow-lists, routing, formatting",
      "Translates chat into founder-radar commands",
      "Contains no thresholds, scores, or company data",
      "Removable without stopping the pipeline",
    ],
  },
]

export const aiBoundary = {
  may: [
    "Read a news story and pull out the facts",
    "Turn a chat message into the right command",
    "Say “this card should not be on Today” after scoring (with a reason)",
  ],
  mayNot: [
    "Decide if a company passes a hard rule",
    "Make up or change a score",
    "Decide if two records are the same company",
    "Add a company to the sheet by itself",
  ],
}
