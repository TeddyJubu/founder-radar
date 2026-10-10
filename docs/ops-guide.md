# Ops guide — spreadsheet, web UI, Telegram

Practical runbook for the person using UK Founder Radar day to day.
Verified against the codebase (prototype + `radar/`). Spec depth lives in
[`docs/prd/`](prd/); this page is the short version.

The same walkthrough is served in the web prototype at **`/help`**.

---

## 1. How the three surfaces connect

```
Google Sheet (config + optional company mirror)
        │  read each morning
        ▼
  founder-radar run  →  SQLite (companies, scores, verdicts)
        │
        ├──▶ Today / Kept / Dashboard web UI (review, picks + history)
        ├──▶ Google Sheet render   (Today / Companies / Sources / …)
        └──▶ Telegram digest       (morning ping + /commands → same CLI)
```

| Surface | Role | What it owns |
|---|---|---|
| **Google Sheet** | Editable brain + optional export | Fund Criteria, Scoring Weights, Settings, Sources; Companies Z–AC when synced |
| **This web UI** (`prototype/`) | Primary place to review, keep, and revisit companies | Writes only to `user_field` (verdicts) |
| **Telegram** | Delivery + remote control | Digest text and CLI shortcuts; no separate store |

The CLI (`founder-radar …`) is the real interface. Telegram calls it; the sheet
is rendered by it; the prototype reads the same database.

**Decisions must go through the CLI or the web UI.** If Aryan rejects a company
in Telegram chat, Hermes must run
`founder-radar decide "<name>" --verdict "not for me"` — a chat-only reply does
not update Today, Kept, or the Sheet.

**Morning send path:** systemd runs `founder-radar publish --send` (not raw
`digest --send`). That path refuses Telegram delivery when the publish gate
BLOCKs or when Today QA cannot use Hermes while reviewable scores exist.

---

## 2. Shortlist vs Kept (easy to confuse)

| Term | Meaning | Where |
|---|---|---|
| **Engine shortlist** | `score.tier = 'shortlist'` — cleared fit / edge / coverage bars | System opinion; moves when thresholds change |
| **Kept** | Your “worth contacting” / “unsure” picks | SQLite `user_field` where `field = 'verdict'`; page **`/kept`** |
| **Dashboard** | Month-by-month history of dated events and kept companies | Read-only page **`/dashboard`** |

- On **Today**, press **1** (Worth contacting) or **2** (Unsure) → saved immediately.
- After scoring, a Hermes subagent reviews each selected company and drops
  already-backed, IPO / late-stage, or wrong-region cards before they appear.
- **3** (Not for me) is stored for tuning but **never** listed on Kept.
- The **Kept** badge on Today is `COUNT` of those two verdicts.
- **Dashboard** shows first-seen, incorporation, signal, and decision dates;
  use its month arrows to move through the history without changing data.
- Sheet column **Z (Verdict)** is updated when a web decision is saved, and a
  full `sync-sheet` run backfills any missed write. SQLite remains the primary
  record; the Sheet is the durable working mirror.

Persistence detail: daily sheet sync used to treat blank Z cells as “cleared”
and delete verdicts made in the web UI. That is fixed — blanks only delete when
the pipeline had previously rendered a value into that cell (real clear vs never
touched). See `radar/render/sheet.py` (`save_user_fields`, `sync_sheet`).

---

## 3. Update or replace funds later

1. Open the sheet tab **Fund Criteria** (one row per *vehicle*, not per fund).
2. To **change** rules: edit cells on that vehicle’s row (geo, cheque, hard
   rejects, sectors, Active, one-liner). Do **not** rename existing
   `Fund key` / `Vehicle key` values — those are the canonical IDs.
3. To **add** a fund or vehicle: append a row with new keys and names.
4. To **retire** without deleting history: set **Active** to `FALSE`.
5. Save. Next `founder-radar run` (or `founder-radar rescore`) loads the new
   config. Invalid cells fall back to the last-good snapshot and show a red
   status note in the sheet — they do not abort the run.

Hard-reject mini-language (column on Fund Criteria): `key:value` pairs separated
by ` · `. Known keys include `round_max`, `prior_total_max`, `valuation_max`,
`uk_exec_pct_min`, `university_spinout_required`, `beyond_research_stage`,
`requires_seis_eis`. Unknown keys warn and are ignored.

Also adjust preference strength in **Scoring Weights** (0–4 matrix + attribute
importance; blank importance = 1).

---

## 4. Edit global criteria (age, thresholds, regions)

**Settings** tab — columns Key / Value / Type / Status / What it does.

Common knobs:

| Key | Effect |
|---|---|
| `max_company_age_months` | Hard age gate |
| `max_total_funding_gbp` / `max_stage` | Funding / stage gates |
| `shortlist_fit` / `shortlist_edge` / `min_coverage` | Bar to reach engine shortlist |
| `watchlist_fit` | Softer review band |
| `regions_enabled` | CSV of regions to sweep |
| `daily_digest_max` | Telegram / digest length cap |
| `llm_enabled` | `FALSE` = heuristic extraction only |

---

## 5. Add or remove sourcing channels

**Sources** tab:

- Flip **Enabled** to `FALSE` to silence a noisy or broken adapter; `TRUE` to
  bring it back. Takes effect on the next run — no deploy. The tab is an
  allowlist: only Enabled rows crawl (plus any new default sources the next
  run appends for you).
- Health columns (last OK, items, status) are written by the pipeline.

**Brand-new source:** enablement alone is not enough. Someone must add an
adapter under `radar/sources/`, register it, then add a Sources-tab row (or
add it to `DEFAULT_SOURCES`). The sheet only toggles adapters that already
exist in code.

Useful CLI:

```bash
founder-radar sources --list
founder-radar sources --test KEY            # run one adapter against the live site
founder-radar sources --accept-layout KEY   # after checking a "layout changed" source by hand
founder-radar doctor
founder-radar sync-sheet
```

**"Layout changed".** The source's page structure no longer matches what was
stored, and its items are held back so a redesign cannot feed half-parsed
companies into the pipeline. Look at the site (or run `sources --test KEY`). If
it reads correctly, run `founder-radar sources --accept-layout KEY` on the server;
the next run learns the new structure. If it does not, the adapter needs a fix.

**Refused by the site.** A source that answers 403 to the crawler shows as
degraded every day. Northern Accelerator does this to any crawler User-Agent:
set its row to `FALSE` until it allowlists `founder-radar`.

---

## 6. Day-to-day path (recommended)

Today shows **ready to review** separately from **waiting for final checks**.
Both counts cover the whole eligible, undecided pool, even when only 20 cards
fit on a page. Kept and rejected companies are excluded. Waiting companies
become available after their evidence links and current company cards pass
the final check; an unfinished check does not mean approval.

The morning order softly spreads available companies across source families,
funds and sectors. Match and Fresh are unchanged. The list can still be uneven
when few genuine alternatives exist; there are no invented matches or fixed
four-fund quotas. For a London company, a suitable alternative fund is
suggested ahead of regional DSW EIS, with the reason and all scores visible.
DSW EIS remains eligible. DSW SEIS keeps its existing place restriction.

To check a larger waiting batch without sending a Telegram message:

```bash
founder-radar today-qa --limit 80
```

Completed current approvals do not spend these new-check slots. A broken
source page is held back; blocked or timed-out evidence remains unknown and
can be checked later. Innovate UK workbook links identify the project to
find in the official dataset rather than pretending a missing GtR profile
exists.

The scheduled scan can run the installed, root-owned Hermes permission-repair
helper before and after QA. Its service permits that narrow sudo rule; the
web service retains `NoNewPrivileges`. A privileged stop hook repairs shared
permissions even when a scan fails. This prevents a normal Hermes launch from
leaving the next scheduled check unable to open the operator's files.

1. Open **Today** in the web UI after the morning run (or after Telegram pings).
2. Decide with 1 / 2 / 3. Kept updates live; open **Kept** to track everything
   you have saved, or **Dashboard** to revisit the dated history.
3. Optionally use the sheet for bulk notes, outreach tracking (Outreach tab),
   or deeper config edits — not required for the pick list.
4. Use Telegram for the digest and remote `/run` / `/status` when away from the
   browser.

If something looks empty or wrong: `founder-radar doctor`, then check the
Sources and Run Log tabs in the sheet.

---

## 7. Change the login password

The web review surface sits behind a Caddy reverse proxy with basic auth.
`https://hermes.<the review host>/` is a TLS alias for the same review UI
(not the Hermes Agent control plane) and uses the same username and password.
To change the password, on the server:

```bash
caddy hash-password --plaintext 'your-new-password'   # prints a bcrypt hash
```

Put that hash into `RADAR_WEB_PASS_HASH` in `/opt/founder-radar/.env` (the
username is `RADAR_WEB_USER` in the same file), then restart:

```bash
systemctl restart caddy
```

Never write the plaintext password into the repo, a chat thread, or anywhere
that is not the server itself — the same rule as the Google service-account
key (README). Share the new password with team members over a channel that is
not this repo.


### Incomplete morning checks

A failed scan exits with code 2, so the scheduled service does not publish it.
Publishing requires the latest scan to have finished successfully within 30 hours;
a partial scan must have fetched at least one item. An old successful scan cannot
replace a newer failed scan.

Today, the Sheet's Today tab and Telegram counts require a completed company QA
pass. Never-checked companies, failed checks and passes older than the company's
latest score stay hidden until checked again. Hermes errors are saved as
`incomplete`, never as passes. A deliberate `--no-hermes` or `--no-llm` check can
still record a rules-only pass. Publishing refuses incomplete QA even if the
rules-only override is enabled or per-card QA was skipped.

Publish-time QA changes refresh the Sheet before the Telegram ping. A Sheet
outage is reported as a warning; the approved dashboard can still be announced,
but the Sheet may be behind until the next successful sync.

### Trusted auto-updates

The installation root, application checkout and Python environment are root-owned.
Radar can write data, logs, backups and secrets, but cannot edit code run by root.
The timer runs a root-owned helper under `/usr/local/libexec/founder-radar`.
Updates refuse writable inputs, symlinks and writable parent directories before Git runs.
An administrator must review and reinstall an older radar-owned checkout before enabling this timer.
Runtime packages are installed from `deploy/requirements.lock`, generated from `uv.lock`,
with versions and artifact hashes checked. The setuptools build backend is pinned separately in `deploy/build-requirements.lock`; the project installs with build isolation disabled so pip cannot select a different backend. Runtime and backend installs require wheels and verified hashes. A missing compatible wheel fails installation rather than building with unspecified tools.
Integration tests require `TEST_SHEET_ID` and a tab named `FOUNDER_RADAR_TEST_SCRATCH`;
the marker survives resets. Never put that marker on a real customer spreadsheet.

### Undoing a Today decision

Ctrl+Z asks the server to restore the previous saved verdict and today's review
marker. It says “Undone” only after the stored change succeeds. If the decision
was mirrored to the Sheet, that cell must also be restored. A Sheet outage
leaves the decision saved and shows an error so you can retry. Undo records
are limited to 200 recent web decisions and expire when the server restarts;
a later decision from another surface prevents an old undo from overwriting it.
While a decision is saving, the page pauses navigation and other decisions.
Write requests require JSON and reject cross-origin browser requests.


The installer refuses pre-existing service-owned or writable checkouts and
virtual environments before it runs any Git hooks, Python launchers or `.pth` files.
Changing their owner is not a safe migration. To migrate an old installation,
stop writers, preserve data and secrets separately, and use an administrator's
trusted copy of the installer. Move the old checkout and venv aside; clone the
reviewed repository afresh as root under a root-owned installation directory,
then let the installer build a new venv. Do not copy old Git configuration,
hooks, Python launchers or `.pth` files into the trusted rebuild. This requires
an explicit operator maintenance step; the timer fails closed until it is done.

### QA approval and dated history

A live card must match the exact facts and fund route that completed QA approved.
Even a change within the same second invalidates an old pass. Recheck changed
cards before showing them on Today, the Sheet or the current digest.

Historical score snapshots save the hash of their exact completed approval.
A previously approved Monday result remains available in Monday's history after
its current score changes; an unchecked Monday result cannot borrow a later
approval. The latest QA rejection still vetoes historical display. Old snapshot
rows without recorded approval stay withheld until valid evidence is recorded.


For a clean rebuild, run the trusted installer with `INSTALL_MAINTENANCE=1`
after stopping writers and timers and preserving a verified database backup.
It installs files and migrates the database before any service starts; maintenance
mode defers service/timer enables and restarts, including Caddy and Hermes gateway.
After configuration, rescore, completed Today QA and Sheet verification, explicitly
start the chosen services and timers. The ACL helper grants operator write access
to mutable data/logs, never the protected application checkout or Python environment.

---

## 8. Control room (`/admin`)

**Control room** is the password-protected admin page on the review server. It
uses the same login as Today and Kept. It has four tabs:

| Tab | What it does |
|---|---|
| **Flow** | Shows the daily flow stage by stage (① Config → ② Fetch → ③ Extract → ④ Resolve → ⑤ Enrich → ⑥ Gate+score → ⑥½ Today QA → ⑦ Render) and what the latest run did at each stage. Read-only. |
| **Settings** | Thresholds, scoring weights, digest size and the LLM on / off / model switch. The **Sources** on / off switches sit here too. |
| **Prompts** | The three AI prompts: article extraction (`extract.system`), Today QA brief (`today_qa.brief`) and publish check (`publish.brief`). View, edit or reset each one. |
| **Changes** | Audit log of every change made from the page or the CLI, newest first. |

**Settings and sources go to the Sheet first.** When the Sheet is configured,
the change is written to the Settings or Sources tab before anything local
changes. If that write fails, **nothing is changed** and the page shows an
error. On success the change is saved to the local last-good snapshot, and Today
is rescored automatically. The rescore is deterministic (no AI), so Today reads
the score generation that matches the new settings (no `config_hash` drift).
Without a configured Sheet, the change is saved locally only and the Changes tab
records the Sheet sync as skipped.

**After a rescore, Today waits for the Hermes check again.** A newer score makes
every card's Today QA approval stale, exactly as with `founder-radar rescore`.
Run `founder-radar today-qa` on the VPS (or ask Hermes to run Today QA); the
morning run also does it. The page says so after every save.

**Prompts live in SQLite, with history.** The built-in text stays in the code.
Each save creates a new version in the `prompt_override` table, and the old
versions stay in the history. A new version changes the prompt version, so the
next run reads new articles with it (extract; articles already read keep their
record until fetched again), re-checks every Today card (Today QA), or uses it at
the next publish check. **Reset** makes the built-in default active again. It is recorded
as a change, and the earlier versions remain in the history.

**AI never sets scores.** Stage ⑥ (gate and score) is deterministic. The prompts
only change how article prose is read, how Today cards are checked and how the
publish gate judges the morning. Today QA can veto a card and the publish check
can block a send. Neither can add a company or change a score.

CLI equivalents (the same store and rules as the page):

```bash
founder-radar admin flow                      # the stage view, as text
founder-radar admin settings show
founder-radar admin settings set KEY VALUE    # e.g. shortlist_fit 65
founder-radar admin prompts list
founder-radar admin prompts show KEY
founder-radar admin prompts set KEY --file new.md   # or --text "…"; --file - reads stdin
founder-radar admin prompts preview KEY       # what the model would receive (no AI call)
founder-radar admin prompts reset KEY         # back to the built-in default
founder-radar admin sources set KEY on|off
founder-radar admin rescore                   # rescore against the current snapshot
founder-radar admin changes                   # the audit log
```

Every write command takes `--note "why"`, and `--json` (before `admin`) gives
machine-readable output for Hermes.
