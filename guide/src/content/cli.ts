export interface CliCommand {
  command: string
  when: string
  audience: "both" | "technical"
}

export const howToUseEasy = [
  {
    title: "Today, then Kept",
    body: "Each morning: open Today, press 1 / 2 / 3, then check Kept for the companies you kept.",
  },
  {
    title: "Telegram is a doorbell",
    body: "The message tells you the list is ready. Commands from chat only count when they save into the real notebook.",
  },
  {
    title: "Sheet is for rules",
    body: "Change fund rules and weights in the sheet. You do not need the sheet for the daily pick list.",
  },
]

export const cliCheatSheet: CliCommand[] = [
  {
    command: 'founder-radar decide "<name>" --verdict "worth contacting"',
    when: "Keep from Telegram / ops — must update the shared store",
    audience: "both",
  },
  {
    command: 'founder-radar decide "<name>" --verdict "not for me"',
    when: "Reject so the company never resurfaces on Today",
    audience: "both",
  },
  {
    command: "founder-radar today-qa",
    when: "Re-run Hermes Today QA veto on the morning list",
    audience: "technical",
  },
  {
    command: "founder-radar why-today",
    when: "Empty or surprising Today — poison, config_hash drift, filters",
    audience: "technical",
  },
  {
    command: "founder-radar doctor",
    when: "Anything looks wrong — keys, quotas, disk, sheet access",
    audience: "technical",
  },
  {
    command: "founder-radar rescore [--all]",
    when: "After Fund Criteria / Weights change; stamps new config_hash",
    audience: "technical",
  },
  {
    command: "founder-radar digest --today",
    when: "Preview today's digest without guessing scores",
    audience: "technical",
  },
  {
    command: "founder-radar run",
    when: "Full morning pipeline",
    audience: "technical",
  },
  {
    command: "founder-radar status",
    when: "Last run, source health, cost",
    audience: "technical",
  },
  {
    command: 'founder-radar show "<name>"',
    when: "Full record + score breakdown",
    audience: "technical",
  },
]

export const surfacesTable = [
  {
    surface: "Google Sheet",
    role: "Editable brain + optional export",
    owns: "Fund Criteria, Weights, Settings, Sources; Companies mirror when synced",
  },
  {
    surface: "Web UI (Today / Kept / Dashboard)",
    role: "Primary review",
    owns: "Writes verdicts to user_field only",
  },
  {
    surface: "Telegram",
    role: "Delivery + remote control",
    owns: "Digest text and CLI shortcuts — no separate store",
  },
]
