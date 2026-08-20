---
name: founder-radar
description: >
  UK Founder Radar on this VPS — query today's startups and fund matches,
  and diagnose or fix production bugs (failed run, missing digest, source
  down, disk, sheet, web UI, adapter layout change). Use whenever someone
  asks about the radar, says it is broken, or says "fix it".
metadata:
  hermes:
    requires_toolsets: [terminal]
---
# Founder Radar

## When to use
Questions about startups found, fund matches, scores, a scan, **or a bug
on this box** — no digest, a red source, a crashed run, disk, sheet, web UI.

## Queries — map to one command, return the output as-is
Never compute a score yourself. The CLI already formats for Telegram.

| Intent | Command |
|---|---|
| today's list, what's new | `founder-radar digest --today` |
| run it now, scan now | `founder-radar run` |
| just Northstar / DSW / Outward / Anticus | `founder-radar run --fund <key>` |
| top matches for a fund | `founder-radar fund <key>` |
| why this company, explain X | `founder-radar show "<name>"` |
| is it working, last run, cost | `founder-radar status` |
| this week | `founder-radar digest --week` |
| it's broken, fix it, /fix | `founder-radar repair --apply` then the bug-fix procedure |

Fund keys: northstar · dsw · outward · anticus

Always `cd /opt/founder-radar` first so `.env` loads. If you are not the
`radar` user, run the venv binary with `sudo -u radar -H`.

## Bug-fix procedure
When anything looks wrong, do this in order. Read
`references/repair.md` before editing a file.

1. Say you are diagnosing, then run `founder-radar repair --apply`.
2. Apply the table it prints. Re-run `founder-radar doctor` and
   `founder-radar status` if you need a second look.
3. Logs: `tail -n 80 /opt/founder-radar/logs/error.log` (never `cat` `.env`).
4. One quiet source: `founder-radar sources --test <key>`, then
   `founder-radar sources --sniff <url>` if the parse is empty.
5. **Code fixes** (adapter layout, parse, crash): edit a copy, not `main`.
   Worktree or branch `hermes/fix-<short-reason>` off `origin/main`.
   Run `/opt/founder-radar/venv/bin/python -m pytest` if pytest is installed.
   Push the branch if git credentials exist; otherwise write
   `/opt/founder-radar/logs/hermes-fix.patch` and say so in Telegram.
   Do **not** commit to local `main` — the update timer fast-forwards
   `main` and will refuse a diverged checkout.
6. When done, send a short Telegram summary of what changed and what did not.

## Hard limits
- Never invent a score or a company. If a command returns nothing, say so.
- Never edit `radar/score/`, gates, thresholds, or freshness rules.
- Never print `/opt/founder-radar/.env`, tokens, or `google-sa.json`.
- Never `git push --force`, `git reset --hard` on the live checkout, or drop
  the database.
- Never disable a source the sheet set to off — that is operator intent.
- A run takes several minutes. Say "running, I'll message you when it's done."
