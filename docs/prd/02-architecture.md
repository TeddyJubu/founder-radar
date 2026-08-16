# 02 — Architecture

**How the system is put together, and why each piece is where it is.**

---

## 1. The shape in one picture

```
                      ┌─────────────────────────────────────────┐
                      │        HOSTINGER VPS (Ubuntu)           │
                      │        1 vCPU · 4 GB RAM · 50 GB        │
                      └─────────────────────────────────────────┘

  systemd timer 06:30 Europe/London
        │
        ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │  founder-radar run              (plain Python — the engine)           │
  │                                                                       │
  │  ① CONFIG      read Fund Criteria + Weights + Settings from the Sheet │
  │       ↓        validate → on error, use last known good, report back  │
  │  ② FETCH       14 source adapters, isolated, polite, cached           │
  │       ↓        Track A: signals   ·   Track B: Companies House        │
  │  ③ EXTRACT     ← THE ONLY AI CALL. Article prose → structured record  │
  │       ↓        schema-enforced · evidence-quoted · cached by hash     │
  │  ④ RESOLVE     normalise → match ladder → merge → provenance          │
  │       ↓                                                               │
  │  ⑤ ENRICH      Companies House officers/PSC/filings · postcode→region │
  │       ↓                                                               │
  │  ⑥ GATE+SCORE  hard gates → Fund Fit per vehicle → Discovery Edge     │
  │       ↓        100% deterministic. No AI. Fully unit-tested.          │
  │  ⑦ RENDER      SQLite → Google Sheet (minimal diff) → digest          │
  └───────────────────────────────────────────────────────────────────────┘
        │                    │                         │
        ▼                    ▼                         ▼
  ┌───────────┐      ┌──────────────┐          ┌──────────────┐
  │  SQLite   │      │ Google Sheet │          │   Telegram   │
  │  TRUTH    │      │   THE VIEW   │          │  THE ALERT   │
  └───────────┘      └──────────────┘          └──────────────┘
                            ▲                         ▲
                            │ edits criteria          │ chats
                            │ + own verdicts          │
                       ┌────┴─────────────────────────┴────┐
                       │             ARYAN                 │
                       └───────────────────────────────────┘

  ┌───────────────────────────────────────────────────────────────────────┐
  │  Hermes Agent  (systemd service, always on, Telegram adapter only)    │
  │  ~/.hermes/skills/founder-radar/SKILL.md  → maps chat to CLI commands │
  │  HOLDS NO LOGIC. If it dies, the pipeline is unaffected.              │
  └───────────────────────────────────────────────────────────────────────┘
```

---

## 2. The three-layer rule

Everything in this system belongs to exactly one of three layers, and the layers have different rules.

### Layer 1 — The Engine (deterministic Python)

Fetching, filtering, gating, matching, scoring, de-duplicating, rendering.

- Pure functions wherever possible: `(input, config) → output`
- No network in the scoring path
- No AI anywhere in the decision path
- 100% unit-testable offline in under a minute
- **This is where all the value is, and it is boring on purpose.**

### Layer 2 — The Reader (one AI call, tightly boxed)

Turning a paragraph of English into a structured record.

- One function, one schema, one prompt, one version constant
- Output validated by Pydantic before it touches anything
- Every extracted fact carries a verbatim quote that must appear in the source
- Cached by content hash — the same article is never paid for twice
- Has a deterministic fallback so the pipeline never stops when the provider does

### Layer 3 — The Front Desk (Hermes Agent)

Talking to Aryan on Telegram.

- Translates "show me the Northstar ones" into `founder-radar fund northstar`
- Contains no rules, no thresholds, no scoring, no data
- Removable in one file and one systemd unit if it proves flaky

**The rule, stated once:** *the AI may read prose into a record, and may turn a sentence into a command. It may never decide whether a company passes a gate, what it scores, whether it is a duplicate, or what lands in the sheet.*

Why this matters in practice: when Aryan asks "why did this company drop off my shortlist yesterday?", the answer must be "you changed the age limit from 36 to 24 months in Settings, and it's 30 months old" — not "the model saw it differently". Scoring must be arithmetic he can check.

---

## 3. Why this runtime, and not the alternatives

Three options were assessed. The decision is a **hybrid**, but a specific one.

| | All-Hermes | All-Python | **Hybrid (chosen)** |
|---|---|---|---|
| Matches the Fiverr offer wording | ✅ | ❌ | ✅ |
| Scoring is unit-testable | ❌ | ✅ | ✅ |
| Survives a Hermes crash / upgrade | ❌ | ✅ | ✅ |
| Telegram allow-lists, routing, formatting for free | ✅ | ❌ | ✅ |
| Re-runnable and reproducible | ❌ | ✅ | ✅ |
| Storage adequate for de-duplication | ❌ | ✅ | ✅ |

**Why not all-Hermes.** Hermes's memory is 2,200 characters of markdown — it cannot be a company database. There is no Google Sheets toolset, so the Python gets written regardless. A Hermes skill is a prompt with no signature and no return value, so there is nothing to unit-test. And every scheduled run re-sends the full system prompt, tool schemas and skill list, paying tokens to do arithmetic.

**Why not all-Python.** It is a perfectly good fallback, but it is not actually "no AI" — the article-to-record step still needs a model. What it gives up is the Telegram surface: allow-lists, command routing, retries, formatting, voice notes. That is a few hundred lines Teddy would then own forever, producing a bot that only does what was hardcoded. Hermes buys it for a thirty-line skill file.

**Two deliberate departures from a naive hybrid:**

1. **Hermes is not the scheduler.** Use systemd. Hermes's internal scheduler lives inside the gateway daemon, jails scripts to `~/.hermes/scripts/`, strips credentials from them, and picks the interpreter by file extension — so a `.py` cron script runs in *Hermes's* virtual environment, not the project's. The Hermes documentation itself recommends OS-level cron for anything that must survive the gateway being unhealthy. The daily run must survive `hermes update`.

2. **Extraction calls the provider SDK directly, not `hermes -z`.** Extraction is exactly where a guaranteed JSON schema, a controlled temperature, owned retries, per-record cost accounting and fixture-replay tests are needed. Hermes offers none of those and returns free text that would have to be regex-parsed. A direct SDK call with a Pydantic schema is about forty lines.

**Escape hatch:** if the Hermes gateway proves unreliable in the first two weeks, removing it costs one skill file and one systemd unit. The pipeline never knew it was there. That is the real reason the boundary sits exactly here.

---

## 4. How a new source gets added

This is the promise made to the client on 9 July: *"Adding a new source later just means writing that one piece and adding it to the list — it won't touch the rest of the system."* The architecture has to make that literally true.

Every adapter implements one protocol and returns one type:

```python
class SourceAdapter(Protocol):
    key: str                      # "northern_accelerator"
    kind: Literal["registry", "spinout", "accelerator", "grant", "news", "portfolio"]
    schedule: str                 # "daily" | "weekly" | "monthly"
    requires_browser: bool        # default False

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]: ...
```

```python
@dataclass(frozen=True)
class RawItem:
    source_key: str
    source_url: str               # the exact page a human can open
    external_id: str              # stable id within the source, for de-dup
    published_at: date | None     # None only if the source truly has no date
    title: str
    body_text: str | None         # for adapters that hand off to the reader
    structured: dict | None       # for adapters that already know the answer
    kind_hint: str | None         # "spinout" | "cohort" | "grant" | "funding_round"
```

Adding a source is:

1. Create `radar/sources/<key>.py` implementing the protocol
2. Register it in `radar/sources/__init__.py` (one line)
3. Add a row to the `Sources` sheet tab with `enabled = TRUE`
4. Add one fixture file and one test asserting the adapter parses it

Nothing else changes. Pipeline, scoring, sheet rendering and Telegram are all untouched because they only ever see `RawItem`.

**The load-bearing detail:** the pipeline never asks "which source is this from?" when deciding anything. Source identity affects only *trust weighting* in provenance and *Discovery Edge* components — both of which are table lookups from configuration, not branching code.

---

## 5. Repository layout

```
founder-radar/
├── pyproject.toml
├── README.md
├── .env.example                    # never commit the real one
├── docs/
│   ├── privacy-notice.md           # published; URL goes in the User-Agent
│   └── legitimate-interests.md     # the GDPR LIA
├── radar/
│   ├── __init__.py
│   ├── cli.py                      # THE interface. Everything goes through here.
│   ├── config/
│   │   ├── models.py               # Pydantic: Settings, FundCriteria, Weights
│   │   ├── loader.py               # Sheet → validate → last-known-good fallback
│   │   └── defaults.py             # seed values used on very first run
│   ├── sources/
│   │   ├── __init__.py             # REGISTRY = {key: adapter}
│   │   ├── base.py                 # SourceAdapter protocol, RawItem
│   │   ├── companies_house.py      # Track B backbone
│   │   ├── ukri_gtr.py
│   │   ├── innovate_uk_awards.py
│   │   ├── govuk_search.py
│   │   ├── oxford_innovation.py
│   │   ├── northern_accelerator.py
│   │   ├── cambridge_enterprise.py
│   │   ├── edinburgh_innovations.py
│   │   ├── ucl_ventures.py
│   │   ├── conception_x.py
│   │   ├── entrepreneur_first.py
│   │   ├── zinc_vc.py
│   │   ├── bethnal_green.py
│   │   ├── uktn.py
│   │   ├── businesscloud.py
│   │   ├── startups_magazine.py
│   │   ├── bdaily_regional.py
│   │   └── vc_portfolios.py        # the "already seen" denylist source
│   ├── fetch/
│   │   ├── http.py                 # session, retries, timeouts, conditional GET
│   │   ├── robots.py               # Protego, 24h cache, fail-closed on 5xx
│   │   ├── ratelimit.py            # per-host token bucket
│   │   └── browser.py              # Playwright, opt-in per source only
│   ├── extract/
│   │   ├── schema.py               # the Pydantic Extraction model
│   │   ├── prefilter.py            # free gates before any AI call
│   │   ├── llm.py                  # LLMClient protocol + provider impls
│   │   ├── heuristic.py            # the no-AI fallback
│   │   └── grounding.py            # verbatim-quote hallucination check
│   ├── resolve/
│   │   ├── normalise.py            # names, domains, dates, money, postcodes
│   │   ├── match.py                # the precedence ladder
│   │   └── merge.py                # merge + provenance + reversibility
│   ├── enrich/
│   │   ├── ch_officers.py          # officers, PSC, prior appointments
│   │   ├── ch_filings.py           # SH01 detection
│   │   └── postcode.py             # postcodes.io + local cache
│   ├── score/
│   │   ├── gates.py                # hard gates — the age fix lives here
│   │   ├── criteria.py             # Criterion, ComponentScore, evaluators
│   │   ├── fund_fit.py             # weighted matrix → percentage + coverage
│   │   ├── discovery_edge.py       # the obscurity score
│   │   ├── tiering.py              # shortlist / watchlist / reject
│   │   └── explain.py              # deterministic "why", no AI
│   ├── store/
│   │   ├── schema.sql              # the whole database, one file
│   │   ├── db.py                   # thin repository layer, hand-written SQL
│   │   └── migrations/
│   ├── render/
│   │   ├── sheet.py                # gspread, batched, minimal diff
│   │   ├── formatting.py           # one batchUpdate with every format request
│   │   └── digest.py               # the Telegram message
│   ├── notify/
│   │   ├── telegram.py             # hermes send, with direct Bot API fallback
│   │   └── heartbeat.py
│   └── pipeline.py                 # orchestrates the seven stages
├── hermes/
│   └── skills/founder-radar/SKILL.md   # ~30 lines. Chat → CLI mapping.
├── deploy/
│   ├── founder-radar.service
│   ├── founder-radar.timer
│   ├── founder-radar-heartbeat.timer
│   └── install.sh
└── tests/
    ├── conftest.py                 # offline_llm fixture — fails on cache miss
    ├── fixtures/
    │   ├── articles/               # 25 committed HTML files + expected JSON
    │   ├── api/                    # recorded Companies House / GtR responses
    │   ├── llm_cache/              # recorded model responses; CI never calls out
    │   └── pairs/                  # 40 entity-resolution name pairs
    ├── unit/
    ├── integration/
    └── eval/                       # opt-in, marked, not in CI
```

---

## 6. Data flow, stage by stage

| Stage | Input | Output | AI? | Network? | Deterministic? |
|---|---|---|---|---|---|
| ① Config | Google Sheet | validated `Config` | No | Yes | Yes |
| ② Fetch | source registry | `RawItem[]` | No | Yes | No (the web) |
| ③ Extract | `RawItem` with prose | `Extraction` | **Yes** | Yes | Cached → yes |
| ④ Resolve | `Extraction[]` | `Company[]` merged | No | No | **Yes** |
| ⑤ Enrich | `Company[]` | `Company[]` + signals | No | Yes | Yes given inputs |
| ⑥ Gate + Score | `Company[]`, `Config` | `Score[]`, `Tier[]` | No | **No** | **Yes** |
| ⑦ Render | `Score[]` | Sheet rows, digest | No | Yes | **Yes** |

Stage ⑥ is the important row: **no AI and no network.** It is a pure function of the database and the configuration. That is what makes re-scoring five thousand companies take fifty milliseconds, makes every score reproducible from a `config_hash`, and makes the whole scoring model testable with plain assertions.

---

## 7. Failure behaviour

The system is designed so that **no single failure stops the daily run.**

| What breaks | What happens | Aryan sees |
|---|---|---|
| One source 404s or changes layout | Marked failed, other 13 continue | A red row on the `Sources` tab |
| Companies House rate-limits | Back off, resume; enrichment deferred to next run | Nothing — it self-corrects |
| AI provider down or rate-limited | Retry with backoff → heuristic extractor → records flagged `needs_review` | "6 companies, 2 need review — AI unavailable" |
| AI returns invalid JSON | One retry with the validation error appended, then quarantine the record | Nothing; it lands in a quarantine table |
| AI invents a fact | Evidence quote fails verbatim check → field dropped, logged | Nothing; the field is simply blank |
| Google Sheets API down | Rows stay in SQLite with `synced = 0`, next run upserts them | A late sheet, no lost data |
| Hermes gateway down | Digest goes out via direct Bot API call | Digest arrives; chat commands don't work |
| Settings cell has a typo | Last known-good config used | Red error text in the `Settings` tab next to the cell |
| The whole run dies | Heartbeat timer fires at +26 h | A Telegram alert |
| Disk fills | Pre-run check aborts cleanly with a Telegram alert | An alert, not a corrupt database |

Two properties make this work: **every write is idempotent** (a retry can never double-write), and **every stage records what it did** (so a partial run resumes rather than restarts).

---

## 8. Security and data protection

**Secrets.** One `/opt/founder-radar/.env` file, mode `0600`, owned by the service user, never in git, never logged. Contents: Companies House key, AI provider key, Telegram bot token, path to the Google service-account JSON. The service-account JSON lives at `/opt/founder-radar/secrets/google-sa.json`, also `0600`.

> **Action required before build:** the Google service-account private key was shared in the Fiverr chat. Any key that has travelled through a chat thread should be treated as exposed. Generate a fresh key in the Google Cloud console, delete the old one, and share only the new file's path — never its contents.

**Personal data.** Founder names are personal data under UK GDPR, and public availability does not remove that.

Stored: **name, role, public professional profile URL, source URL.**
Never stored: email, phone, home or correspondence address, date of birth — **including the partial month/year date of birth Companies House returns.** That field must be dropped at ingest, not merely hidden at render.

Also required, and cheap:

- A short legitimate-interests assessment at `docs/legitimate-interests.md`
- A published privacy notice, whose URL goes into the crawler's User-Agent string
- A `founder-radar forget <name>` command that deletes the rows and adds a suppression entry so re-ingestion cannot resurrect them
- A retention rule: purge founder records for companies rejected more than 12 months ago
- The Google Sheet restricted to named accounts, link-sharing off

**Crawler conduct.** Honest User-Agent with a working contact URL. robots.txt obeyed via Protego. Crawl-delay obeyed, with a 1-second floor even when the site is silent. Never scrape behind a login, never bypass a paywall, never solve an anti-bot challenge — a challenge page is an explicit statement that automation is unwelcome, and the correct engineering response is to drop that source.

---

## 9. Technology choices

| Choice | Version | Why this and not the obvious alternative |
|---|---|---|
| Python | 3.11+ | Hermes requires 3.11–3.13; matching avoids two runtimes |
| SQLite (stdlib) | — | Zero ops, transactional, fast enough for millions of rows. No ORM: hand-written SQL is more debuggable and the provenance queries are awkward in an ORM. |
| `gspread` | 6.2.x | Ergonomic **and** exposes raw `batchUpdate` passthrough, so it covers 100% of the Sheets API. Adding `google-api-python-client` would buy nothing and cost a heavy import. |
| `rapidfuzz` | 3.14.x | MIT, C++ backend. Use `token_sort_ratio` — **never** `token_set_ratio` or `partial_ratio`, which return 100 for a subset and will merge every parent/subsidiary pair you ever see. |
| `Protego` | 0.6.x | Implements Google's current robots spec. The stdlib `robotparser` implements a 1996 draft with no wildcard support, and would mis-parse `Disallow: /*?` — which UKTN actually uses. |
| `trafilatura` | latest | Best boilerplate removal for news, and returns date/author/site metadata free. |
| `pydantic` | 2.x | One model defines both the AI schema and the config schema, so drift is structurally impossible. |
| `tenacity` | latest | Retry with full jitter; honours `Retry-After`. |
| `tldextract` | 5.3.x | Correct registrable-domain extraction. Pin the suffix list for reproducibility. |
| Claude Haiku 4.5 | pinned snapshot | Strict JSON schema support confirmed; ~£2.50/month at this volume. Provider is behind an `LLMClient` protocol, so Gemini Flash-Lite (10× cheaper) is a config change. **Pin a dated model ID, never an alias** — an alias silently rolls the model and turns golden tests into flaky tests. |
| Playwright | latest | Opt-in per source only. Budget ~300 MB and 1–2 s per page against 5 MB and 50 ms for a plain request. Expect to need it for at most two sources. |
| systemd timer | — | Survives Hermes upgrades and daemon crashes. |
| Hermes Agent | 0.20.x | Telegram surface only. `hermes send` works with no gateway running, which is the fallback path. |

---

## 10. What deliberately does *not* exist

Naming the absences is as useful as naming the parts.

- **No web server, API, or dashboard in the radar core.** The isolated
  `prototype/` server is a thin review surface over SQLite (`/`, `/kept`, and
  `/dashboard`); it does not duplicate scoring or become a second editable
  system. The Sheet remains the production dashboard and configuration owner.
- **No message queue, no Redis, no Celery.** One machine, one daily run, fourteen sources. A queue would be pure ceremony.
- **No ORM.** The provenance model is graph-shaped and reads better as SQL.
- **No Docker.** One Python virtual environment plus one systemd unit is less to go wrong on 1 vCPU, and easier for Teddy to debug over SSH.
- **No vector database, no embeddings, no RAG.** Nothing in this problem is a similarity search over documents.
- **No machine-learned scoring.** The requirement is explainability. A learned model would score better and be impossible to defend to a fund.
