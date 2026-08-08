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

| Intent | Command |
|---|---|
| today's list, what's new | `founder-radar digest --today` |
| run it now, scan now | `founder-radar run` |
| just Northstar / DSW / Outward / Anticus | `founder-radar run --fund <key>` |
| top matches for a fund | `founder-radar fund <key>` |
| why this company, explain X | `founder-radar show "<name>"` |
| is it working, last run, cost | `founder-radar status` |
| this week | `founder-radar digest --week` |

Fund keys: northstar · dsw · outward · anticus

## Pitfalls
- Never invent a score or a company. If the command returns nothing, say so.
- A run takes several minutes. Say "running, I'll message you when it's done."
