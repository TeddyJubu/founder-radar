# UK Founder Radar

A daily scan that finds early-stage UK startups **before** venture funds do, and
tells one student exactly which of four funds to send each one to, and why.

Version 1 kept surfacing companies that were five to seven years old and already
funded, because it read VC portfolio pages — a record of companies that *already
raised*, and therefore a lagging indicator by construction. Version 2 starts from
the **UK company register**, where every company has a legally recorded birthday.
A company incorporated ninety days ago cannot be six years old. That is the fix.

Full specification: [`docs/prd/`](docs/prd/) — start with
[`docs/prd/README.md`](docs/prd/README.md).

Day-to-day ops (sheet ↔ web UI ↔ Telegram, Kept storage, editing funds and
sources): [`docs/ops-guide.md`](docs/ops-guide.md). In the Today prototype the
same guide is at `/help`.

---

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env      # then fill it in, and chmod 600 .env
.venv/bin/founder-radar db migrate
.venv/bin/founder-radar doctor      # run this first whenever anything looks wrong
```

The only key that is genuinely required is `COMPANIES_HOUSE_API_KEY` (free, instant,
self-service at `developer.company-information.service.gov.uk`). Everything else
degrades rather than failing: with no `LLM_API_KEY` the system falls back to
heuristic extraction, and with no Google or Telegram credentials it still runs and
still writes to SQLite.

```bash
founder-radar backfill --days 90    # first Companies House sweep — ~39 requests
founder-radar run                   # the daily run
founder-radar digest --today
```

## The daily run

Seven stages, every morning at 06:30 Europe/London, driven by a systemd timer.

| # | Stage | AI? | Network? | Deterministic? |
|---|---|---|---|---|
| ① | **Config** — read and validate the Google Sheet | No | Yes | Yes |
| ② | **Fetch** — 14 sources, each isolated and polite | No | Yes | No (the web) |
| ③ | **Extract** — article prose → structured record | **Yes** | Yes | Cached → yes |
| ④ | **Resolve** — normalise, match ladder, merge, provenance | No | No | **Yes** |
| ⑤ | **Enrich** — officers, filings, postcode → region | No | Yes | Yes given inputs |
| ⑥ | **Gate + score** — derive, gate, fund fit, discovery edge | No | **No** | **Yes** |
| ⑦ | **Render** — sheet (minimal diff) then Telegram digest | No | Yes | **Yes** |

Stage ⑥ is the important row: **no AI and no network.** It is a pure function of the
database and the configuration, which is what makes re-scoring five thousand
companies take milliseconds and every score reproducible from a `config_hash`.

**The rule, stated once:** the AI may read prose into a record, may turn a chat
sentence into a command, and may repair operational and adapter bugs on the VPS.
It may never decide whether a company passes a gate, what it scores, whether it
is a duplicate, or what lands in the sheet. When the client asks "why did this
drop off my list?", the answer has to be a number he can check.

## Two ways in

- **Track A — signal-first.** University spinout announcements, accelerator cohorts,
  Innovate UK grants, curated UK tech news. Someone already vetted these: young and
  credible, but other scouts can see them too.
- **Track B — registry-first.** Newly incorporated UK tech companies straight from
  Companies House, qualified by free public evidence — a share-allotment filing
  (SH01), a grant, a university spinout, or press in a tracked source. A live
  website and a prior Companies House appointment are recorded when found, but
  neither admits a registry company on its own: almost every Ltd has a website,
  and formation agents sit on thousands of new companies. Lower yield, very high
  edge. This is where the client finds what no fund has seen.

Track B only works because of the derivation step: a register entry has no sector,
stage, founder type or traction, so `radar/score/derive.py` turns SIC code → sector,
postcode → region, share filing → stage, officer history → founder type and grant
award → traction. Skip it and every registry company scores on location alone.

## Two scores, not one

- **Fund Fit** (0–100) — how well the company matches a fund's stated criteria.
- **Discovery Edge** (0–100) — how likely it is the fund has *not* already seen it.

`priority = 0.60 × fit + 0.40 × edge`, both weights editable in the sheet. A company
reaches the shortlist only if it clears **all three** of `fit ≥ 70`, `edge ≥ 55` and
`coverage ≥ 50%` — that last one stops a company scoring 100% on the single fact we
happen to know about it.

## Command line

The command line is the real interface. Telegram calls it; the sheet is rendered by
it. Nothing is trapped inside the chat layer.

```bash
founder-radar run [--fund KEY] [--source KEY] [--since DATE] [--dry-run] [--no-llm]
founder-radar backfill --days 90
founder-radar status
founder-radar show "company name"
founder-radar fund northstar [--top 10]
founder-radar digest [--today|--week|--date YYYY-MM-DD] [--send]
founder-radar rescore [--all]
founder-radar sync-sheet
founder-radar sources [--list|--test KEY|--sniff URL]
founder-radar tune
founder-radar review
founder-radar forget "person name"
founder-radar db backup|restore|migrate
founder-radar doctor
founder-radar repair [--apply] [--run]
```

Exit codes: `0` success · `1` partial (some sources failed) · `2` fatal.
`--json` on any command emits machine-readable output.

## Layout

```
radar/
  cli.py            THE interface. Everything goes through here.
  pipeline.py       orchestrates the seven stages
  config/           Pydantic models, sheet loader, seeded defaults
  sources/          one file per adapter, all behind one protocol
  fetch/            http, robots, rate limiting, layout-change detection
  extract/          the one AI call, its cache, its fallback, its grounding check
  resolve/          normalise, match ladder, merge, provenance
  enrich/           Companies House officers/filings, postcode → region
  score/            derive, gates, fund fit, discovery edge, tiering, explain
  store/            schema.sql and a thin hand-written repository layer
  render/           sheet (batched, minimal diff) and the Telegram digest
  notify/           telegram delivery and the heartbeat
  privacy.py        GDPR erasure and the suppression that makes it stick
```

## Tests

```bash
.venv/bin/python -m pytest          # offline, no credentials, seconds
```

Two guarantees are enforced by `tests/conftest.py` rather than by convention:

- **Zero network.** Non-loopback sockets are blocked for the whole session, so a
  forgotten real call fails loudly instead of making the suite slow and flaky.
- **Zero cache misses.** The `offline_llm` fixture hard-fails when an article has no
  recorded response, so no fixture can quietly depend on an API key.

`REFRESH_LLM=1 pytest` is the only path that talks to a provider.

## Data protection

Founder names are personal data under UK GDPR, and public availability does not
change that. The system stores **name, role, public profile URL, PSC flag, date of
appointment, prior-directorship count and source URL** — and nothing else. Dates of
birth and correspondence addresses returned by Companies House are dropped **in the
adapter, before the database write**, and the `founder` table has no column for them.
`tests/unit/test_schema_privacy.py` fails the build if one ever appears.

`founder-radar forget "<name>"` deletes the person and writes a suppression entry, so
tomorrow's crawl of the same article cannot reinstate them.

See [`docs/privacy-notice.md`](docs/privacy-notice.md) and
[`docs/legitimate-interests.md`](docs/legitimate-interests.md).

Before a public release, run the dependency-free [public-release safety
policy](docs/public-release-policy.md). CI checks every tracked tree; the
manual history gate checks all reachable branches and tags before visibility
changes.

## Cost

| Item | Monthly |
|---|---|
| Managed Linux VPS | ~£6 |
| AI extraction (~600 articles) | ~£2.50 |
| Companies House, UKRI, GOV.UK, postcodes.io, Sheets, Telegram | Free |
| **Total** | **under £10** |

Three levers to go lower, all of them sheet edits rather than code changes: switch
`llm_model` to a smaller model, set `llm_enabled = FALSE` for £0 and slightly rougher
reading, or disable the highest-volume news sources on the `Sources` tab.

## A note on the service-account key

The Google service-account private key was shared in a chat thread. Any key that has
travelled through a chat thread should be treated as exposed. **Generate a fresh key
in the Google Cloud console, delete the old one, and share only the new file's path
— never its contents.** `.gitignore` blocks `*service-account*.json`,
`founder-finder-*.json` and `google-sa.json` so the old one cannot be committed by
accident, but that is a seatbelt, not a fix.
