---
name: founder-radar
description: UK startup scouting for four VC funds — Hermes operates; Radar is the tool
metadata:
  hermes:
    requires_toolsets: [terminal]
---
# Founder Radar (agent-native)

Hermes owns the hard ops. Founder Radar is a **tool** you call — never invent
scores, never publish without the gate, never re-litigate Kept companies.

## When to use
Any question about startups found, fund matches, scores, running a scan,
empty Today, or whether it is safe to message Aryan.

## Mental model
1. **Deterministic engine** (`founder-radar run` / `rescore`) writes scores.
2. **Today QA** (`today-qa`) may only *remove* bad cards.
3. **Publish gate** (`publish` / `publish-check`) must PASS before Telegram.
4. You explain failures with `why-today` and `doctor` — counts only in chat.

## Procedure
Map intent to one command. Return the output as-is (already Telegram-shaped).

| Intent | Command |
|---|---|
| today's list (preview, no send) | `founder-radar digest --today` |
| **publish to Aryan** | `founder-radar publish --send` |
| check if safe to publish | `founder-radar publish-check` |
| why is Today empty | `founder-radar why-today` |
| health / env / hash | `founder-radar doctor` |
| re-check Today's cards | `founder-radar today-qa` |
| run the daily scan | `founder-radar run` |
| heal empty generation | `founder-radar rescore --all` |
| top matches for a fund | `founder-radar fund <key>` |
| why this company | `founder-radar show "<name>"` |
| is it working / cost | `founder-radar status` |
| this week | `founder-radar digest --week` |

Fund keys: northstar · dsw · outward · anticus

### Publish workflow (required)
When asked to send the morning list, or after `run` completes:
1. `founder-radar publish-check` — must PASS (auto-heals hash drift when it can)
2. If BLOCK: run the ACTIONS it names (`rescore --all`, `repair-fund-criteria`,
   `doctor`), then check again. Do **not** call `digest --send` yourself.
3. Only then: `founder-radar publish --send` (gate + Today QA + digest send)

`publish --send` is the only allowed send path. Raw `digest --today --send`
is gated the same way and will refuse on hash drift. `--force` alone does
**not** bypass the gate — ops must also set `RADAR_ALLOW_FORCE_PUBLISH=1`
(never in systemd).

### Empty Today
Run `why-today`. If it says scores exist only under an older config_hash,
run `rescore --all` then `publish-check`. Do not tell Aryan "nothing cleared
the bar" until the active hash has scores.

## Pitfalls
- Never invent a score or a company. If the command returns nothing, say so.
- Never put a `VERDICT: REJECT` company back on Today's list.
- Never re-surface companies that already have a lasting verdict (Kept /
  not for me) — that is intentional.
- A run takes several minutes. Say "running, I'll message you when it's done."
- Quiet Hermes: deterministic gate still blocks hash drift; zero-day PASS is OK.
