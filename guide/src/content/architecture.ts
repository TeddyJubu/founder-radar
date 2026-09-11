export type ArchNodeId =
  | "sqlite"
  | "cli"
  | "sheet"
  | "web"
  | "telegram"
  | "hermes"
  | "vps"

export interface ArchNode {
  id: ArchNodeId
  label: string
  role: string
  easy: string
  technical: string
  flowsTo: ArchNodeId[]
}

export interface ArchLayer {
  name: string
  subtitle: string
  bullets: string[]
}

export const architectureNodes: ArchNode[] = [
  {
    id: "sqlite",
    label: "SQLite",
    role: "The real notebook",
    easy:
      "This is the one true notebook. Company facts and your yes / maybe / no choices live here. Everything else is just a window onto it.",
    technical:
      "radar.db holds companies, scores stamped with config_hash, user_field verdicts, and run logs. Sheet and Telegram never own decisions.",
    flowsTo: ["cli"],
  },
  {
    id: "cli",
    label: "founder-radar CLI",
    role: "The engine",
    easy:
      "This is the machine that does the work: scan, score, save. The website and Telegram ask it to act — they are not separate brains.",
    technical:
      "Telegram maps chat to CLI; the sheet is rendered by it; the prototype reads the same database. Chat-only replies do not update Today, Kept, or the sheet.",
    flowsTo: ["sqlite", "sheet", "web", "telegram"],
  },
  {
    id: "sheet",
    label: "Google Sheet",
    role: "Rules you can edit",
    easy:
      "Here you change the rules: which funds care about what, how scoring weights work, which sources are on. For the morning list, use Today — not this sheet.",
    technical:
      "Fund Criteria is one row per vehicle. Invalid cells fall back to last-good config and write a status note — they do not abort the run. Next run or rescore loads the new config_hash.",
    flowsTo: ["cli"],
  },
  {
    id: "web",
    label: "Today / Kept / Dashboard",
    role: "Morning review",
    easy:
      "Open Today. Press 1 (yes), 2 (maybe), or 3 (no). Yes and maybe go to Kept. No stays out of Kept and will not show up again tomorrow.",
    technical:
      "Web writes only to user_field verdicts. Dashboard is month-by-month history. Local API is typically http://127.0.0.1:8787.",
    flowsTo: ["cli"],
  },
  {
    id: "telegram",
    label: "Telegram",
    role: "Morning ping",
    easy:
      "A short message says “your list is ready.” You can answer from your phone — but a keep or reject must still be saved for real, not just typed in chat.",
    technical:
      "publish --send refuses delivery when the publish gate BLOCKs or Today QA cannot use Hermes while reviewable scores exist. Digest is delivery, not a separate store.",
    flowsTo: ["hermes", "cli"],
  },
  {
    id: "hermes",
    label: "Hermes",
    role: "Front desk",
    easy:
      "Hermes is like a helpful front desk. It turns your chat into the right button press, and checks the morning list once before it goes out. It does not invent scores.",
    technical:
      "Layer 3 only: maps chat → CLI. If Hermes dies, the pipeline is unaffected. Today QA is veto-only — PASS|REJECT with a stored reason; fail-open if Hermes is down.",
    flowsTo: ["cli"],
  },
  {
    id: "vps",
    label: "VPS timer",
    role: "06:30 London time",
    easy:
      "A small computer wakes up early every day, runs the scan, and leaves a list ready for you — or an empty list on a quiet day, which is fine.",
    technical:
      "systemd timer at 06:30 Europe/London under /opt/founder-radar (user radar). Hermes is not the scheduler — OS cron/systemd survives hermes update.",
    flowsTo: ["cli"],
  },
]

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
