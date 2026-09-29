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

Telegram **search / start / /run / /search / what's new** are intercepted by
the founder-radar-telegram Hermes plugin. Those turns never reach you. If one
does, reply with `founder-radar today` stdout only — never a company list.

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
Map intent to one command. After it finishes, **your Telegram reply is that
command's stdout** — for a search that is the dashboard ping. Do not add a
company list, scores, or source-by-source narration.

| Intent | Command |
|---|---|
| **search / search now / start / run a scan** | `founder-radar search` |
| today's list / what's new (no scan) | `founder-radar today` |
| **keep / reject / unsure** | `founder-radar decide "<name>" --verdict "…"` |
| **publish to Aryan** | `founder-radar publish --send` |
| check if safe to publish | `founder-radar publish-check` |
| why is Today empty | `founder-radar why-today` |
| health / env / hash | `founder-radar doctor` |
| re-check Today's cards | `founder-radar today-qa --limit 80` |
| heal empty generation | `founder-radar rescore --all` |
| fill missing incorporation dates | `founder-radar hydrate-ages` then `rescore --all` |
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
When asked to send the morning list, or after `search` completes:
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
run `rescore --all` then `publish-check`. If watchlist exists but the
dashboard is empty, run `hydrate-ages` then `rescore --all` — grant companies
often have a CRN and no date, which hides them as unknown age. Do not tell
Aryan "nothing cleared the bar" until the active hash has scores. Then send
him the Today URL.

## Pitfalls
- Never invent a score or a company. If the command returns nothing, say so.
- Never paste `digest --today`, `fund`, `show`, or `run` JSON into Telegram
  as the answer to "what's new" / "search now" / "start". Point at the
  dashboard. `founder-radar search` already prints the ping — send that.
- Never search Companies House from chat. The register is verification inside
  `founder-radar`, not a discovery tool.
- Never put a `VERDICT: REJECT` company back on Today's list.
- Never re-surface companies that already have a lasting verdict (Kept /
  not for me) — that is intentional.
- A run takes several minutes. Say "running, I'll message you when it's done."
  On the VPS, call `founder-radar` directly — `/usr/local/bin/founder-radar`
  re-execs as `radar` with `.env` + `hermes.env` loaded. Do not hand-craft
  `sudo -u radar` unless the wrapper is missing. For authorised operations:
  use supported CLI repairs, and diagnose with `doctor` / `why-today`.
  Application code and the Python environment are root-protected. Do not edit
  `/opt/founder-radar/app` directly, change its ownership, or install packages
  into the protected runtime. Use the trusted main update path below. Never
  invent scores or sheet rows by hand.
- Quiet Hermes: deterministic gate still blocks hash drift; zero-day PASS is OK
  only when there are no reviewable scores. With cards present, Hermes must run.


## Owner operations: use the supported paths

Use the installed `/usr/local/bin/founder-radar` wrapper. It switches to `radar`
with the correct environment; it requires the operator's existing sudo permission
and does not grant it. `radar` has permission to write data, logs and backups and
run the narrow root-owned Hermes ACL helper, not administer arbitrary services.
Hermes runs as the operator. Use privileged actions only when the owner has
requested them and that account's actual sudo permission is established.

- **Read first:** `founder-radar doctor`, `status`, `why-today`,
  `systemctl list-timers 'founder-radar*' --all`, and relevant service/log status.
  `publish-check` is not purely read-only: it can auto-heal supported drift.
- **New QA batch:** `founder-radar today-qa --limit 80` checks sources and current
  cards without sending. Current completed approvals do not consume those new
  slots. After a rescore, changed cards need fresh completed checks.
- **Database backup:** `founder-radar db backup --to
  /opt/founder-radar/backups/before-maintenance.db --retain-days 0`, followed by
  a local SQLite integrity check. Use SQLite backup, not a copy of a live file.
  The automatic backup timer retains local database snapshots; it does not
  back up credentials or create an off-server copy.
- **Code update:** after reviewed code reaches GitHub `main`, the root-owned
  update timer deploys it. An authorised administrator can request
  `sudo systemctl start founder-radar-update.service` and inspect its journal.
  Do not use direct source rewrites, an unpinned `pip install`, or a forced Git
  reset as a repair. The updater's dry-run setting can still move the checkout.
- **Restart / restore / credential changes:** require the owner/admin boundary.
  Stop relevant writers and timers, preserve and verify a current backup, and
  use `founder-radar db restore <verified-backup>` for an intentional restore.
  Follow `docs/owner-handover.md`; do not copy a backup over a running database.
- **Hermes itself:** the operator owns `/home/aryan/.hermes`; Radar QA disables
  lazy installation updates. Do not update Hermes as `radar`. If its launcher
  fails before opening, diagnose it via owner SSH and installed service logs;
  verify the installed version's help before suggesting auth/update commands.

Owner SSH is `aryan@srv1821489.hstgr.cloud`, with an authorised owner key.
Today is `https://srv1821489.hstgr.cloud/`. The `hermes.` alias is the same
review UI. Hermex uses `https://webui.srv1821489.hstgr.cloud` and its separate
WebUI app password. Do not swap these login surfaces or expose auth files.
