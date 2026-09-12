---
name: founder-radar
description: >
  UK Founder Radar v2 — Today/Kept dashboard, scans, scores, keep/reject.
  ALWAYS use this for Founder Radar, startup scouting, Today, shortlist,
  scoring, Companies House, Telegram, or "search now". NEVER use
  uk-founder-radar, ~/radar, sheets.py, scoring.py, or seen.json.
metadata:
  hermes:
    requires_toolsets: [terminal]
---
# Founder Radar (agent-native)

Hermes owns the hard ops. Founder Radar is a **tool** you call — never invent
scores, never publish without the gate, never re-litigate Kept companies.

Aryan reads companies on the **web Today page**, not in Telegram. Chat is
remote control + a short ping. Company lists in Telegram are a product bug.

## When to use
Any question about startups found, fund matches, scores, running a scan,
empty Today, keep/reject, or whether it is safe to message Aryan.

## Dead systems (never touch)
- `uk-founder-radar` skill, `~/radar`, `/home/aryan/radar`
- `sheets.py`, `scoring.py`, `seen.json`, `_upsert_runner.py`
- Web search + Companies House name lookup to "verify" or invent a shortlist
- Writing rows to Google Sheets yourself

Those were v1. They put Ltd filings in chat and never updated the dashboard.

## Mental model
1. **Deterministic engine** (`founder-radar run` / `rescore`) writes scores.
2. **Today QA** (`today-qa`) may only *remove* bad cards.
3. **Publish gate** (`publish` / `publish-check`) must PASS before Telegram.
4. Telegram gets a **short ping + dashboard URL**. Never a company dump.
5. You explain failures with `why-today` and `doctor` — counts only in chat.

## Procedure
Map intent to one command. After it finishes, reply in **2–4 lines** and the
Today URL from `founder-radar today`. Do not paste CLI company lists.

| Intent | Command |
|---|---|
| today's list / what's new / search now | `founder-radar today` |
| **keep / reject / unsure** | `founder-radar decide "<name>" --verdict "…"` |
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

Verdict values (exact): `worth contacting` · `not for me` · `unsure`

### Decisions (required)
When Aryan rejects, keeps, or marks unsure a company in Telegram, you **must**
run `decide`. Replying "done" / "rejected" in chat without the CLI leaves
Today, Kept, and the Sheet unchanged — that is how rejects resurfaced before.

### Publish workflow (required)
When asked to send the morning list, or after `run` completes:
1. `founder-radar publish-check` — must PASS (auto-heals hash drift when it can)
2. If BLOCK: run the ACTIONS it names (`rescore --all`, `repair-fund-criteria`,
   `doctor`), then check again. Do **not** call `digest --send` yourself.
3. Only then: `founder-radar publish --send` (gate + Today QA + dashboard ping)

`publish --send` is the only allowed send path. It texts a short dashboard
ping, not company cards. Raw `digest --today --send` is gated the same way
and will refuse on hash drift or missing Hermes when there are reviewable
scores. `--force` alone does **not** bypass the gate — ops must also set
`RADAR_ALLOW_FORCE_PUBLISH=1` (never in systemd).

### Empty Today
Run `why-today`. If it says scores exist only under an older config_hash,
run `rescore --all` then `publish-check`. Do not tell Aryan "nothing cleared
the bar" until the active hash has scores. Then send him the Today URL.

## Pitfalls
- Never invent a score or a company. If the command returns nothing, say so.
- Never paste `digest --today`, `fund`, or `show` output into Telegram as
  the answer to "what's new" / "search now". Point at the dashboard.
- Never search Companies House from chat. The register is verification inside
  `founder-radar`, not a discovery tool.
- Never put a `VERDICT: REJECT` company back on Today's list.
- Never re-surface companies that already have a lasting verdict (Kept /
  not for me) — that is intentional.
- A run takes several minutes. Say "running, I'll message you when it's done."
- Quiet Hermes: deterministic gate still blocks hash drift; zero-day PASS is OK
  only when there are no reviewable scores. With cards present, Hermes must run.
