---
name: founder-radar
description: UK startup scouting for four VC funds
metadata:
  hermes:
    requires_toolsets: [terminal]
---
# Founder Radar

## When to use
Any question about startups found, fund matches, scores, or running a scan.

## Procedure
Map the user's intent to one command and run it. Return the output as-is —
it is already formatted for Telegram. Never compute scores yourself.

Today's list is already filtered by the Today QA subagent during
`founder-radar run`. Do not re-judge companies yourself.

| Intent | Command |
|---|---|
| today's list, what's new | `founder-radar digest --today` |
| re-check today's list | `founder-radar today-qa` |
| run it now, scan now | `founder-radar run` |
| just Northstar / DSW / Outward / Anticus | `founder-radar run --fund <key>` |
| top matches for a fund | `founder-radar fund <key>` |
| why this company, explain X | `founder-radar show "<name>"` |
| is it working, last run, cost | `founder-radar status` |
| this week | `founder-radar digest --week` |

Fund keys: northstar · dsw · outward · anticus

If asked to re-check today's list: run `founder-radar today-qa`, then
`founder-radar digest --today`. The QA command spawns the Today-check
subagent (`references/today-check.md`) for each selected company.

## Pitfalls
- Never invent a score or a company. If the command returns nothing, say so.
- Never put a `VERDICT: REJECT` company back on Today's list.
- A run takes several minutes. Say "running, I'll message you when it's done."
