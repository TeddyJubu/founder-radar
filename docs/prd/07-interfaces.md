# 07 — Interfaces

**Three ways to use this system: the Google Sheet, Telegram, and the command line.**

The command line is the real interface. Telegram calls it. The Sheet is rendered by it. Nothing is trapped inside the chat layer.

---

## 1. The Google Sheet

Spreadsheet: `154BCy0g3r8JEDhFk5fmy9OWKszNRjFVRYm9Gqb9SOQA`
**Twelve tabs** (eleven visible + one hidden), created and formatted automatically on first run. This exact list is `EXPECTED_TABS` in `test_sheet_roundtrip`.

| # | Tab | Who writes it | Purpose |
|---|---|---|---|
| 1 | **📌 Today** | pipeline | Today's shortlist. The tab Aryan opens. |
| 2 | **Companies** | pipeline + Aryan | Every company ever found, with his verdict columns |
| 3 | **Needs Review** | pipeline + Aryan | Heuristic extractions, ambiguous merges, unverified gates |
| 4 | **Fund Criteria** | **Aryan** | Funds and vehicles — the editable brain |
| 5 | **Scoring Weights** | **Aryan** | The 0–4 matrix plus attribute importance |
| 6 | **Settings** | **Aryan** | Age limits, thresholds, regions, on/off switches |
| 7 | **Outreach** | **Aryan** | His tracker — the system never touches it |
| 8 | **Sources** | pipeline + Aryan | Source health. Failures live here, not in the main view. |
| 9 | **Run Log** | pipeline | Every run: counts, timings, cost |
| 10 | **Tuning** | pipeline | Threshold sweep against Aryan's verdicts |
| 11 | **Lists** | pipeline + Aryan | Controlled vocabularies, SIC → sector map, region map |
| — | `_meta` | pipeline | Hidden. Schema version, config hash, last-good marker. |

---

### Tab 1 — 📌 Today

The layout Aryan actually reads. *(He said on 17 and 24 July that the fund breakdown was hard to scan — this is the answer.)*

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  UK FOUNDER RADAR                          Friday 7 August 2026 · 06:34      │
├──────────────────────────────────────────────────────────────────────────────┤
│  Scanned 412  →  38 passed the age & funding gates  →  6 shortlisted         │
│  Median age of today's shortlist: 11 months                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│  Northstar 3   ·   DSW 2   ·   Outward 1   ·   Anticus 0                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

Then one block per company — not a wall of columns:

| | |
|---|---|
| **Company** | **Kelvin Bio** · [kelvinbio.com](#) · Newcastle · incorporated **2 months ago** (14 Jun 2026) |
| **Send to** | **Northstar Ventures** — North East Spinout Inspire Fund (£200k–£750k) |
| **Fit / Edge** | **88** / **90** → priority **89** |
| **Why** | Found via Northern Accelerator spinout announcement (28 Jul); Companies House SH01 filed (22 Jul). Matches on geography (North East, +25pts); sector (Life Sciences, +25pts); founder signal (research/spinout, +19pts) — 69 of 88 total. Low visibility — no coverage found in our tracked sources. |
| **Evidence** | [Northern Accelerator announcement](#) · [Companies House 15234891](#) · [SH01 filed 22 Jul](#) |
| **Also fits** | DSW Ventures 71 (watchlist) |
| **Your call** | `[ Verdict ▾ ]` `[ Notes ]` |

Colour rules: green header band, amber where coverage < 0.5, grey for anything on watchlist. Score cells carry a **note on hover** with the full component breakdown.

Every URL is clickable **and** kept in a hidden column so read-back and CSV export both work. *(Client request, 24 July: "would it be possible to include the actual URL as well?")*

---

### Tab 2 — Companies

The full grid. One row per company, sortable, filterable.

| Col | Header | Source | Editable |
|---|---|---|---|
| A | `ID` | ULID — the join key, greyed and narrow | 🔒 |
| B | First Seen | pipeline | 🔒 |
| C | Company | pipeline | 🔒 |
| D | Website | clickable | 🔒 |
| E | Incorporated | pipeline | 🔒 |
| F | **Age (months)** | pipeline — colour-coded | 🔒 |
| G | Region | pipeline | 🔒 |
| H | Sector | pipeline | 🔒 |
| I | Stage | pipeline | 🔒 |
| J | Founders | comma-separated names | 🔒 |
| K | Funding known | pipeline — blank means unknown | 🔒 |
| L | Signals | e.g. `spinout · grant · SH01` | 🔒 |
| M–P | DSW / Northstar / Outward / Anticus fit | pipeline | 🔒 |
| Q | **Best fund** | pipeline | 🔒 |
| R | **Vehicle** | pipeline | 🔒 |
| S | Fit % | pipeline | 🔒 |
| T | **Discovery Edge** | pipeline | 🔒 |
| U | Coverage | pipeline | 🔒 |
| V | **Priority** | pipeline — the sort column | 🔒 |
| W | Tier | pipeline | 🔒 |
| X | **Why** | pipeline | 🔒 |
| Y | Sources | clickable | 🔒 |
| Z | **Verdict** | **Aryan** — dropdown | ✏️ |
| AA | **Notes** | **Aryan** | ✏️ |
| AB | **Contacted** | **Aryan** — date | ✏️ |
| AC | **Fund sent to** | **Aryan** — dropdown | ✏️ |

Columns A–Y sit in a **warning-only** protected range — Aryan gets a "are you sure?" prompt if he edits a generated cell, but is never locked out of his own spreadsheet. Columns Z–AC are read before every render and written back to the correct row after any re-sort.

Conditional formatting on **F (Age)**: green ≤ 12 months, yellow 13–24, amber 25–36, red above. This makes the version 1 failure mode instantly visible — a screen full of red means something has drifted.

---

### Tab 4 — Fund Criteria *(editable — the client's key requirement)*

One row per **vehicle**, not per fund. **Eleven rows.** Aryan can add a fifth fund by adding rows. The `Fund key` and `Vehicle key` columns are the canonical strings the code and tests use — they must not be edited.

| Fund key | Vehicle key | Fund | Vehicle | Active | Stage min | Stage max | Cheque min | Cheque max | Geo rule | Geo values | Max age (yrs) | Hard rejects | Sectors + | Sectors − | One-liner |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `northstar` | `spinout_inspire` | Northstar Ventures | Spinout Inspire Fund | TRUE | pre_seed | seed | 200000 | 750000 | HARD | north_east | | `university_spinout_required:durham,newcastle,northumbria,sunderland,teesside` | climate_tech, life_sciences, healthy_ageing, ai_data | | Meaningful challenge, tech substance, NE relevance |
| `northstar` | `venture_sunderland` | Northstar Ventures | Venture Sunderland Fund | TRUE | idea | growth | 200000 | 750000 | HARD | sunderland | | | industrial_tech, healthcare, climate_tech | | Sunderland HQ or relocating |
| `northstar` | `ne_innovation_fund` | Northstar Ventures | NE Innovation Fund | TRUE | idea | series_a | 50000 | 500000 | HARD | north_east | | | *(agnostic)* | | Durham, Tyne & Wear, Northumberland |
| `northstar` | `eis_growth` | Northstar Ventures | EIS Growth Fund | TRUE | seed | series_a | | | SOFT | north_england | 7 | `requires_seis_eis:true` | climate_tech, healthy_ageing, ai_data | *(EIS excluded trades)* | Late seed with revenue traction |
| `northstar` | `ne_social` | Northstar Ventures | NE Social Investment Fund | FALSE | idea | growth | 100000 | 1000000 | HARD | north_east | | | *(social enterprise)* | | Off by default — not equity VC |
| `dsw` | `seis_fund` | DSW Ventures | SEIS Fund | TRUE | idea | pre_seed | 50000 | 250000 | HARD | outside_golden_triangle | **3** | `requires_seis_eis:true` · `valuation_max:10000000` · `round_max:2500000` | deeptech, b2b_saas, life_sciences | *(SEIS excluded trades)* | Regional UK tech with defensibility |
| `dsw` | `eis_service` | DSW Ventures | EIS Investment Service | TRUE | pre_seed | series_a | 100000 | 1000000 | SOFT | uk_regions | 7 | `requires_seis_eis:true` · `valuation_max:10000000` | b2b_saas, vertical_saas, deeptech, ai_data | *(EIS excluded trades)* | Revenue or commercial validation |
| `dsw` | `bbi_coinvest` | DSW Ventures | BBI co-investment | FALSE | pre_seed | series_a | | | SOFT | uk_regions | 7 | | *(as above)* | | Off by default — follows the other two |
| `outward` | `fund_ii` | Outward VC | Fund II (ECF) | TRUE | pre_seed | series_a | 250000 | 2500000 | HARD | uk_wide | | `round_max:5000000` · `prior_total_max:20000000` · `uk_exec_pct_min:66` | fintech, insurtech, regtech, lending, wealthtech, ai_data | consumer | Finance is the product or an essential layer |
| `anticus` | `fy_seedcorn` | Anticus Partners | FY Seedcorn Fund | TRUE | pre_seed | series_a | 100000 | 1500000 | HARD | yorkshire | | `beyond_research_stage:true` | *(agnostic)* | | Yorkshire relevance + commercial path |
| `anticus` | `fy_growth` | Anticus Partners | FY Growth Fund | TRUE | seed | growth | 100000 | 1500000 | HARD | yorkshire | | | *(agnostic)* | | Profitable or approaching profitability |

**Hard reject syntax** is a small documented mini-language — `key:value` pairs separated by ` · `, printed at the top of the tab so Aryan never guesses:

```
round_max · prior_total_max · valuation_max · uk_exec_pct_min ·
university_spinout_required · beyond_research_stage · requires_seis_eis
```

Unknown keys produce a warning in the status column and are ignored, never an error. **A rule whose input is `NULL` passes and sets `gate_unverified`**, which keeps the company off the shortlist and adds "eligibility unconfirmed" to its explanation — see `06-scoring.md` §4.5.

`Geo values` may use `sunderland`, `north_england` and `outside_golden_triangle` in addition to the standard vocabulary. These three are **gate-only region rules**, defined in `06-scoring.md` §2.2; they are not values the `geography` attribute can take.

---

### Tab 5 — Scoring Weights *(editable)*

**Block 1 — the matrix**, exactly the layout from Aryan's `VC Scout.xlsx` so it looks familiar. Answers *how good is this value for this fund?*

| Attribute | Category | DSW | Northstar | Outward | Anticus | Unknown policy | Notes |
|---|---|---|---|---|---|---|---|
| Stage | Pre-seed | 3 | 2 | 3 | 2 | neutral | |
| Sector | Climate Tech | 1 | 4 | 0 | 1 | neutral | |
| … *(the full matrix from `06-scoring.md` §5)* | | | | | | | |

Whole numbers 0–4, validated. `Unknown policy` ∈ `neutral` · `pessimistic` · `assume`, set per attribute row group.

**Block 2 — attribute importance**, below the matrix under a header row reading `ATTRIBUTE IMPORTANCE`. Answers *how much does this attribute matter relative to the other four?* This is `cfg.attribute_weight()`, and it is the denominator of every score.

| Attribute | DSW | Northstar | Outward | Anticus |
|---|---|---|---|---|
| `stage` | 3 | 3 | 3 | 3 |
| `sector` | 4 | 4 | 4 | 2 |
| `geography` | 4 | 4 | 2 | 4 |
| `founder_signal` | 3 | 3 | 3 | 3 |
| `traction_signal` | 2 | 2 | 3 | 3 |

Whole numbers **0–10**. **A blank cell means 1, not 0.** These seeded defaults encode what the fund research found: Outward cares less about region and more about traction; Anticus is nearly sector-agnostic but Yorkshire is everything.

The two blocks are different tables read by different functions — `matrix_value()` and `attribute_weight()` — and confusing them is the easiest way to get every score wrong.

A value outside range falls back to the last good value and reports it in the status column.

---

### Tab 6 — Settings *(editable)*

Columns: **A** key · **B** value · **C** type · **D** status · **E** what it does.

| A: Key | B: Value | C: Type | D: Status | E: What it does |
|---|---|---|---|---|
| `max_company_age_months` | 36 | int 1–120 | ✅ | Reject anything older |
| `max_total_funding_gbp` | 3000000 | money | ✅ | Reject anything better funded |
| `max_stage` | series_a | enum | ✅ | Reject anything later |
| `shortlist_fit` | 70 | int 0–100 | ✅ | Fit needed to shortlist |
| `shortlist_edge` | 55 | int 0–100 | ✅ | Discovery Edge needed to shortlist |
| `min_coverage` | 0.50 | 0–1 | ✅ | Minimum data completeness |
| `watchlist_fit` | 45 | int 0–100 | ✅ | Fit needed to watchlist |
| `min_qualifiers` | 1 | int 0–5 | ✅ | Signals a registry company needs before it's scored |
| `weight_fit` | 0.60 | 0–1 | ✅ | Fit's share of the ranking |
| `weight_edge` | 0.40 | 0–1 | ✅ | Edge's share of the ranking |
| `regions_enabled` | north_east, yorkshire, uk_wide | csv | ✅ | Which UK regions to sweep |
| `ch_backfill_days` | 90 | int | ✅ | Companies House first-run window |
| `ch_daily_window_days` | 10 | int | ✅ | Daily trailing window |
| `max_enrichment_requests_per_run` | 500 | int | ✅ | Companies House **request** budget (not companies) |
| `daily_digest_max` | 10 | int | ✅ | Cap on digest length |
| `llm_model` | *(pinned snapshot id)* | string | ✅ | Swap provider without a redeploy |
| `llm_enabled` | TRUE | bool | ✅ | Off = heuristic extraction only, zero AI cost |

**Column D (Status) is written by the pipeline.** On a good run every cell shows ✅. On a bad value it shows, in red, next to the offending cell:

> ❌ `"fourty five"` is not a number — using last good value **45**

That is the whole error-reporting strategy. Aryan never reads a log.

---

### Tab 8 — Sources

*(Client, 24 July: "I don't think we need the Source Failed section. I'd probably just leave that out." It moves here.)*

| Source | Track | Enabled | Last OK | Items today | 7-day avg | Status | Note |
|---|---|---|---|---|---|---|---|
| Companies House | B | TRUE | 07 Aug 06:31 | 412 | 388 | ✅ | |
| Northern Accelerator | A | TRUE | 07 Aug 06:32 | 2 | 1.4 | ✅ | |
| Conception X | A | TRUE | 05 Aug 06:33 | 0 | 0.2 | ⚠️ | 0 items for 3 days |
| Antler UK | A | FALSE | — | — | — | ⏸ | Disabled — robots restricts the cohort page |

`Enabled` is user-editable, so Aryan can switch a noisy source off himself.

---

## 2. Telegram

Bot already created; token is in the environment. Hermes Agent handles the conversation; every command maps to a CLI call.

### The daily digest

```
📡 UK Founder Radar — Fri 7 Aug

Scanned 412 → 38 passed gates → 6 shortlisted
Median age today: 11 months

━━━━━━━━━━━━━━━━━━━━━━━
1. Kelvin Bio                     89
   → Northstar · Spinout Inspire Fund (£200k–£750k)
   Newcastle · 2 months old · Life Sciences
   Durham spinout, SH01 filed 22 Jul, no press yet
   🔗 kelvinbio.com

2. Ledgerly                       81
   → Outward VC · Fund II
   London · 9 months old · Fintech
   Embedded payments for legal firms; SH01 filed last week
   🔗 ledgerly.io
━━━━━━━━━━━━━━━━━━━━━━━
+4 more in the sheet · /today for the full list
```

On a quiet day, the message says so plainly:

```
📡 UK Founder Radar — Sat 8 Aug

0 shortlisted today.
Scanned 340 → 22 passed gates → none cleared the bar.

That's the filter working, not a fault.
Loosen it in Settings if you want more volume.
```

### Commands

| Command | Does | CLI it calls |
|---|---|---|
| `/today` | Today's shortlist | `founder-radar digest --today` |
| `/run` | Run now | `founder-radar run` |
| `/run northstar` | Run scoped to one fund *(client request, 24 July)* | `founder-radar run --fund northstar` |
| `/fund northstar` | Top 10 current matches for one fund | `founder-radar fund northstar` |
| `/why kelvin bio` | Full score breakdown | `founder-radar show "kelvin bio"` |
| `/status` | Last run, source health, month's cost | `founder-radar status` |
| `/sheet` | Link to the spreadsheet | — |
| `/week` | This week's new shortlist entries | `founder-radar digest --week` |
| `/help` | The command list in plain English | — |

Only allow-listed Telegram user IDs may issue commands (`TELEGRAM_ALLOWED_USERS`).

### The Hermes skill — thirty lines, no logic

`~/.hermes/skills/founder-radar/SKILL.md`

```markdown
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
```

**That file is the entire Hermes footprint.** Delete it and one systemd unit, and the system still works — it just loses the chat surface.

---

## 3. Command line

The complete interface. Everything Telegram can do, a human can do here.

```bash
founder-radar run [--fund KEY] [--source KEY] [--since DATE] [--dry-run] [--no-llm]
founder-radar backfill --days 90        # first-run Companies House sweep
founder-radar status                    # last run, source health, cost
founder-radar show "company name"       # full record, signals, score breakdown
founder-radar fund northstar [--top 10]
founder-radar digest [--today|--week|--date YYYY-MM-DD] [--send]
founder-radar rescore [--all]           # recompute after a weights change
founder-radar sync-sheet                # re-render without fetching
founder-radar sources [--list|--test KEY|--sniff URL]
founder-radar tune                      # threshold sweep against Aryan's verdicts
founder-radar review                    # work the fuzzy-match review queue
founder-radar forget "person name"      # GDPR erasure + suppression
founder-radar db backup|restore|migrate
founder-radar doctor                    # check keys, quotas, disk, sheet access
```

Four flags worth knowing:

- `--dry-run` — do everything, write nothing. Prints what *would* change.
- `--no-llm` — heuristic extraction only. Zero AI cost. Useful for debugging and for proving the fallback works.
- `--source KEY` — run one adapter in isolation. The fastest way to debug a broken source.
- `founder-radar doctor` — run this first when anything looks wrong. It checks every key, every quota, disk space and sheet access, and prints a clear pass/fail table.

Exit codes: `0` success · `1` partial (some sources failed) · `2` fatal.
`--json` on any command emits machine-readable output.

---

## 4. What each person touches

| | Aryan | Teddy | The system |
|---|---|---|---|
| 📌 Today | reads | — | writes |
| Companies | reads, verdict columns | — | writes A–Y |
| Fund Criteria | **edits** | seeds | reads |
| Scoring Weights | **edits** | seeds | reads |
| Settings | **edits** | seeds | reads + writes status |
| Outreach | **owns entirely** | — | never touches |
| Sources | toggles on/off | debugs | writes health |
| Run Log | glances | reads | writes |
| Telegram | chats | — | pushes |
| CLI | — | **everything** | — |

The one rule that keeps this stable: **the pipeline never writes to a cell Aryan owns, and Aryan never has to write to a cell the pipeline owns.** Where the two meet — the verdict columns — the sheet always wins and is read back before every render.
