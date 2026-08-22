---
name: founder-radar
description: >
  UK Founder Radar on this VPS — query today's startups and fund matches,
  and diagnose or fix production bugs (failed run, missing digest, source
  down, disk, sheet, web UI, adapter layout change). The person messaging
  is not technical: you repair it yourself. Use whenever someone asks about
  the radar, says it is broken, or says "fix it".
metadata:
  hermes:
    requires_toolsets: [terminal]
---
# Founder Radar

## When to use
Questions about startups, fund matches, a scan, **or a bug on this box**.

The human is not an engineer. Never ask them to run a command, paste a
log, pick a branch, or merge. You do the work. Message them in short
plain English and only when something they can understand has happened.

## Queries — map to one command, return the output as-is
Never compute a score yourself.

| Intent | Command |
|---|---|
| today's list, what's new | `founder-radar digest --today` |
| run it now, scan now | `founder-radar run` |
| just Northstar / DSW / Outward / Anticus | `founder-radar run --fund <key>` |
| top matches for a fund | `founder-radar fund <key>` |
| why this company, explain X | `founder-radar show "<name>"` |
| is it working, last run, cost | `founder-radar status` |
| this week | `founder-radar digest --week` |
| it's broken, fix it, /fix | `founder-radar repair --apply` then the code-fix workflow |

Fund keys: northstar · dsw · outward · anticus

Always `cd /opt/founder-radar` first. If you are not `radar`,
`sudo -u radar -H` the venv binary.

## Bug-fix procedure
1. Say: "I'm on it. I'll message you when it's done."
2. Run `founder-radar repair --apply`.
3. If that made it healthy, tell them it's sorted. Stop.
4. If a **code** fix is still needed, read `references/workflow.md` and
   do that loop **in order**:
   worktree → implement there → **review sub-agent** → **test sub-agent**
   → `hermes-ship.sh` only after `VERDICT: APPROVE` and `VERDICT: PASS`.
5. One sub-agent at a time (4 GB box). Never edit live `main` by hand.

## Hard limits
- Never invent a score or a company.
- Never edit `radar/score/`, gates, thresholds, or freshness rules.
- Never print `/opt/founder-radar/.env`, tokens, or `google-sa.json`.
- Never `git push --force` or `git reset --hard` on the live checkout.
- Never disable a source the sheet set to off.
- A run takes several minutes. Say you will message when it is done.
