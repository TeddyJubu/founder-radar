# 03 — Data Model

**Every table, every column, and the reasoning behind the shape.**

The design principle: **never overwrite a fact, always add an observation.** When two sources disagree about a company's sector, we do not pick one and discard the other — we keep both, record where each came from, and compute the answer on demand. That is what makes "why does it say this?" always answerable, and what makes a bad merge reversible.

---

## 1. The shape of it

```
   company ──────< identifier          (every key ever seen: CH number, domain, alias)
      │
      ├──────────< observation         (every fact ever seen, with its source)
      │
      ├──────────< company_source      (which sources mentioned it, when)
      │
      ├──────────< founder             (people, minimal fields, GDPR-safe)
      │
      ├──────────< signal              (dated events: grant, cohort, SH01, article)
      │
      ├──────────< score ──────────< score_component
      │
      └──────────< user_field          (Aryan's own verdict + notes — never overwritten)

   merge_event        (every merge, reversible, with the rule and evidence)
   run / run_source   (operational history)
   fetch_log          (ETag / Last-Modified / content hash, per URL)
   llm_cache          (content-hash → response; also the cost ledger)
   quarantine         (records that failed validation, kept for inspection)
   config_snapshot    (validated config + hash, for reproducibility)
   postcode_region    (local cache of postcode prefix → UK region)
   suppression        (GDPR erasure list — must survive re-ingestion)
```

---

## 2. Core tables

### `company`

One row per real-world company.

```sql
CREATE TABLE company (
  id                   TEXT PRIMARY KEY,       -- ULID; stable forever, joins to the Sheet
  canonical_name       TEXT NOT NULL,          -- resolved display name
  norm_key             TEXT NOT NULL,          -- normalised name, for blocking
  companies_house_no   TEXT,                   -- 8 chars, zero-padded, UPPER. STRING not INT.
  domain               TEXT,                   -- registrable domain, lowercase, no www
  website_url          TEXT,
  incorporated_on      TEXT,                   -- ISO date. THE age field.
  age_source           TEXT,                   -- 'companies_house' | 'source_stated' | 'unknown'
  hq_postcode          TEXT,
  hq_region            TEXT,                   -- resolved via postcodes.io
  hq_city              TEXT,
  country_iso2         TEXT DEFAULT 'GB',
  sector               TEXT,                   -- resolved, from the Lists vocabulary
  stage                TEXT,                   -- idea|pre_seed|seed|series_a|series_b_plus|growth
  founder_signal       TEXT,                   -- see 06-scoring §3
  traction_signal      TEXT,
  total_funding_gbp    REAL,                   -- NULL = unknown, 0 = known none. NOT the same.
  one_liner            TEXT,
  sic_codes            TEXT,                   -- JSON array
  has_share_issue      INTEGER DEFAULT 0,      -- SH01 within 18 months of incorporation
  officer_count        INTEGER,
  news_mention_count   INTEGER DEFAULT 0,      -- articles in OUR tracked sources only
  on_vc_portfolio      INTEGER DEFAULT 0,      -- tracked portfolio page => already seen
  discovery_route      TEXT,                   -- 'registry'|'spinout'|'accelerator'|'grant'|'news'

  -- fields the per-vehicle hard rules need (06-scoring §4.5). NULL unless a source said so.
  is_university_spinout INTEGER,
  spinout_university   TEXT,
  last_round_gbp       REAL,
  prior_total_gbp      REAL,
  valuation_gbp        REAL,
  uk_exec_pct          REAL,
  seis_eis_qualifying  INTEGER,

  -- operational
  qualified            INTEGER DEFAULT 0,      -- passed the 06-scoring §3 qualification gate
  qualifiers           TEXT,                   -- JSON array: ['share_issue','grant',...]
  enriched_at          TEXT,                   -- NULL => still in the enrichment queue
  extraction_method    TEXT,                   -- 'structured'|'llm'|'heuristic'
  age_unknown          INTEGER DEFAULT 0,
  uk_unverified        INTEGER DEFAULT 0,
  date_confidence      TEXT,                   -- 'exact'|'stated'|'inferred'
  synced               INTEGER DEFAULT 0,      -- 0 => not yet written to the Sheet

  first_seen           TEXT NOT NULL,
  last_seen            TEXT NOT NULL,
  merged_into          TEXT REFERENCES company(id),   -- tombstone; NULL if canonical
  needs_review         INTEGER DEFAULT 0,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_company_ch  ON company(companies_house_no)
       WHERE companies_house_no IS NOT NULL AND merged_into IS NULL;
CREATE UNIQUE INDEX ux_company_dom ON company(domain)
       WHERE domain IS NOT NULL AND merged_into IS NULL;
CREATE INDEX ix_company_normkey    ON company(norm_key) WHERE merged_into IS NULL;
CREATE INDEX ix_company_inc        ON company(incorporated_on);
CREATE INDEX ix_company_region     ON company(hq_region);
```

**Five details that will cause bugs if missed:**

1. `companies_house_no` is **TEXT**. Numbers have leading zeros (`00445790`) and Scottish/Northern Irish prefixes (`SC`, `NI`, `OC`, `SO`). Casting to an integer silently destroys them.
2. `total_funding_gbp` distinguishes `NULL` (we don't know) from `0` (we know there's been none). This distinction survives all the way into scoring and must never be collapsed.
3. `merged_into` is a tombstone, not a delete. A stale reference to a merged-away company still resolves by following the chain — with cycle detection.
4. `country_iso2` has **no default**. Defaulting it to `'GB'` would make the `min_uk_presence` gate a no-op for missing data — exactly the kind of silent pass this system is built to avoid. It is `NULL` until something confirms it.
5. `sector`, `stage`, `founder_signal` and `traction_signal` are **scalar** resolved values, not sets. The union rule in §2 applies to founders, aliases, domains and source links — not to these. Where two sources disagree on sector, `resolve()` picks by trust and the losing observation stays queryable.

### `company_source` — which sources mentioned it

```sql
CREATE TABLE company_source (
  company_id  TEXT NOT NULL REFERENCES company(id),
  source_key  TEXT NOT NULL,
  external_id TEXT NOT NULL,
  source_url  TEXT NOT NULL,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  PRIMARY KEY (company_id, source_key, external_id)
);
CREATE INDEX ix_company_source ON company_source(source_key);
```

This is what makes "found on three sources" a single row with three links, and what powers the source-yield query in §6.

### `identifier`

Every key ever associated with a company, including its old names.

```sql
CREATE TABLE identifier (
  company_id  TEXT NOT NULL REFERENCES company(id),
  kind        TEXT NOT NULL,   -- 'ch' | 'domain' | 'norm_key' | 'alias' | 'lei'
  value       TEXT NOT NULL,
  source_key  TEXT NOT NULL,   -- the adapter key that supplied it
  first_seen  TEXT NOT NULL,
  PRIMARY KEY (kind, value, company_id)
);
CREATE INDEX ix_identifier_lookup ON identifier(kind, value);
```

This is what makes a rename harmless: `BLUE SKY 4471 LIMITED` becomes an `alias` when the company renames to `Acme Robotics`, so a later mention under the old name still resolves.

### `observation` — the provenance spine

```sql
CREATE TABLE observation (
  id            INTEGER PRIMARY KEY,
  company_id    TEXT NOT NULL REFERENCES company(id),
  field         TEXT NOT NULL,       -- 'sector' | 'stage' | 'incorporated_on' | ...
  value_json    TEXT NOT NULL,
  source_key    TEXT NOT NULL,       -- adapter key
  source_type   TEXT NOT NULL,       -- companies_house|grant|spinout|accelerator|news|company_site|llm
  source_url    TEXT,
  confidence    REAL NOT NULL,       -- 0..1
  observed_at   TEXT NOT NULL,
  extractor_ver TEXT NOT NULL        -- prompt/parser version, for reproducibility
);
CREATE INDEX ix_obs ON observation(company_id, field, observed_at DESC);
```

**Resolution is a pure function**, recomputed on demand rather than stored:

```python
# Keys MUST cover every value of SourceAdapter.kind plus 'derived' and 'llm'.
# An unguarded lookup here is a KeyError on every Companies House observation.
SOURCE_TRUST = {
    "registry":        100,   # Companies House — the legal record
    "grant":            80,   # UKRI / Innovate UK
    "company_site":     70,
    "spinout":          65,   # university tech-transfer office
    "accelerator":      60,
    "news":             40,
    "portfolio":        35,   # VC portfolio page — used mainly as a denylist
    "derived":          30,   # our own rules, e.g. SIC → sector (06-scoring §2)
    "llm":              20,   # inferred from prose, not stated
}

def resolve(field, observations):
    obs = [o for o in observations if o.field == field and o.value is not None]
    if not obs:
        return None, []
    obs.sort(key=lambda o: (SOURCE_TRUST.get(o.source_type, 10),
                            o.confidence, o.observed_at), reverse=True)
    return obs[0].value, obs        # winner + the full audit trail
```

**Set-valued fields are never resolved — they are unioned.** Founders, sectors, aliases, domains, source links. Losing a founder's name because the second article didn't mention them is a real bug, not a tidy-up.

### `founder`

Deliberately minimal. Every column here was a conscious decision to include.

```sql
CREATE TABLE founder (
  id            INTEGER PRIMARY KEY,
  company_id    TEXT NOT NULL REFERENCES company(id),
  name          TEXT NOT NULL,
  norm_name     TEXT NOT NULL,
  role          TEXT,
  profile_url   TEXT,               -- public professional profile, if publicly linked
  is_psc        INTEGER DEFAULT 0,  -- person with significant control
  appointed_on  TEXT,
  prior_appointments INTEGER,       -- from CH officer appointments = repeat-founder signal
  source_url    TEXT NOT NULL,
  first_seen    TEXT NOT NULL,
  UNIQUE (company_id, norm_name)
);
```

**Forbidden columns — enforce with a schema test:** `email`, `phone`, `address`, `postcode`, `date_of_birth`, `dob_month`, `dob_year`, `nationality`, `country_of_residence`.

Companies House returns partial dates of birth and correspondence addresses on the officers endpoint. **Drop them at ingest**, in the adapter, before they reach the database. Not at render time.

```python
# tests/unit/test_schema_privacy.py
FORBIDDEN = {"email","phone","address","postcode","date_of_birth",
             "dob_month","dob_year","nationality","country_of_residence"}

def test_founder_table_stores_no_sensitive_fields(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(founder)")}
    assert not (cols & FORBIDDEN), f"forbidden columns present: {cols & FORBIDDEN}"
```

### `signal` — dated evidence

The most useful table in the system. Every reason a company is interesting, with a date and a link.

```sql
CREATE TABLE signal (
  id           INTEGER PRIMARY KEY,
  company_id   TEXT NOT NULL REFERENCES company(id),
  kind         TEXT NOT NULL,
  occurred_on  TEXT,
  headline     TEXT NOT NULL,      -- human-readable, goes straight into the sheet
  detail       TEXT,
  amount_gbp   REAL,
  source_key   TEXT NOT NULL,
  source_url   TEXT NOT NULL,      -- clickable in the sheet (client request, 24 July)
  first_seen   TEXT NOT NULL,
  UNIQUE (company_id, kind, source_url)
);
CREATE INDEX ix_signal_company ON signal(company_id, occurred_on DESC);
```

`kind` is a closed vocabulary:

| kind | Meaning | Typical source |
|---|---|---|
| `incorporation` | Registered at Companies House | Companies House |
| `share_issue` | SH01 filed — a round being papered | Companies House filings |
| `grant_award` | Public R&D funding won | UKRI GtR, Innovate UK |
| `spinout` | University spinout announced | TTO pages |
| `accelerator_cohort` | Joined a cohort or demo day | EF, Zinc, BGV, Conception X |
| `competition_win` | Won a startup competition | University news |
| `funding_round` | Publicly announced raise | News |
| `product_launch` | Launched something | News |
| `news_mention` | Any other coverage | News |
| `vc_portfolio_listing` | Appears on a tracked VC portfolio page — **already seen** | `vc_portfolios` |

The last one is inverted: it is a *negative* signal. It is how Discovery Edge knows a fund already found this company.

### `score` and `score_component`

```sql
CREATE TABLE score (
  id              INTEGER PRIMARY KEY,
  company_id      TEXT NOT NULL REFERENCES company(id),
  fund_key        TEXT NOT NULL,
  vehicle_key     TEXT,                 -- NULL = fund-level best across vehicles
  fund_fit_pct    REAL NOT NULL,        -- 0..100
  coverage        REAL NOT NULL,        -- 0..1
  discovery_edge  REAL NOT NULL,        -- 0..100
  priority        REAL NOT NULL,        -- the ranking number
  tier            TEXT NOT NULL,        -- shortlist | watchlist | reject
  reject_reason   TEXT,                 -- the gate that fired, if any
  explanation     TEXT NOT NULL,        -- the deterministic sentence
  flags           TEXT,                 -- JSON array: age_unknown, gate_unverified, ...
  config_hash     TEXT NOT NULL,        -- reproducibility
  scorer_version  TEXT NOT NULL,
  scored_at       TEXT NOT NULL,
  -- COALESCE, because SQLite treats NULLs as DISTINCT in a UNIQUE index —
  -- without this, fund-level rows duplicate on every re-score.
  UNIQUE (company_id, fund_key, COALESCE(vehicle_key, ''), config_hash)
);
```

Freshness-gate rejects still need a `fund_key`. Write **one row per fund** with `vehicle_key = NULL`, `tier = 'reject'` and the gate name in `reject_reason` — not a single fundless row, which the `NOT NULL` constraint would refuse.

### `score_history` — the weekly view

```sql
CREATE TABLE score_history (
  company_id  TEXT NOT NULL REFERENCES company(id),
  fund_key    TEXT NOT NULL,
  run_id      INTEGER NOT NULL REFERENCES run(id),
  config_hash TEXT NOT NULL,
  fund_fit_pct   REAL NOT NULL,
  discovery_edge REAL NOT NULL,
  priority    REAL NOT NULL,
  tier        TEXT NOT NULL,
  scored_at   TEXT NOT NULL,
  PRIMARY KEY (company_id, fund_key, run_id)
);

CREATE TABLE score_component (
  score_id    INTEGER NOT NULL REFERENCES score(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,     -- 'sector' | 'stage' | 'geography' | ...
  label       TEXT NOT NULL,
  sub_score   REAL,              -- NULL = unknown. Never coerce to 0.
  weight      REAL NOT NULL,
  contribution REAL,             -- weight * sub_score, normalised
  evidence    TEXT NOT NULL,     -- 'Climate Tech'
  PRIMARY KEY (score_id, key)
);
```

`config_hash` in the unique key is what makes the question *"why did this drop off my shortlist?"* answerable: the old score is still there under the old hash, so the difference between two configurations is a `JOIN`.

### `user_field` — Aryan's columns, protected

```sql
CREATE TABLE user_field (
  company_id  TEXT NOT NULL REFERENCES company(id),
  field       TEXT NOT NULL,     -- 'verdict' | 'notes' | 'contacted' | 'fund_sent'
  value       TEXT,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (company_id, field)
);
```

**The pipeline reads these from the sheet before rendering and never overwrites them.** They are also the labelled data that makes threshold tuning possible: `verdict` ∈ `{worth contacting, not for me, unsure}`.

### `today_check` — Hermes Today QA

```sql
CREATE TABLE today_check (
  company_id     TEXT NOT NULL REFERENCES company(id),
  snapshot_hash  TEXT NOT NULL,
  verdict        TEXT NOT NULL,   -- pass | reject
  reason         TEXT,
  summary        TEXT,
  checker        TEXT NOT NULL,   -- hermes | rules | skip
  prompt_version TEXT NOT NULL,
  raw_text       TEXT,
  checked_at     TEXT NOT NULL,
  PRIMARY KEY (company_id, snapshot_hash)
);
```

A reject hides the company from Today, the digest, and the sheet tab. It does
not rewrite `score`. The latest row by `checked_at` is what the surfaces read.

### `merge_event` — every merge is reversible

```sql
CREATE TABLE merge_event (
  id             INTEGER PRIMARY KEY,
  winner_id      TEXT NOT NULL,
  loser_id       TEXT NOT NULL,
  rule           TEXT NOT NULL,   -- ch_exact|domain_exact|normkey_exact|fuzzy|manual
  score          REAL,
  evidence_json  TEXT NOT NULL,   -- both names, the shared key, the scores
  merged_at      TEXT NOT NULL,
  merged_by      TEXT NOT NULL    -- 'auto' | 'user'
);
```

---

## 3. Operational tables

```sql
CREATE TABLE run (
  id              INTEGER PRIMARY KEY,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  mode            TEXT NOT NULL,   -- daily | backfill | rescore | manual
  scope           TEXT,            -- e.g. 'fund=northstar'
  items_fetched   INTEGER DEFAULT 0,
  items_extracted INTEGER DEFAULT 0,
  companies_new   INTEGER DEFAULT 0,
  companies_merged INTEGER DEFAULT 0,
  gated_out       INTEGER DEFAULT 0,
  shortlisted     INTEGER DEFAULT 0,
  llm_calls       INTEGER DEFAULT 0,
  llm_cost_usd    REAL DEFAULT 0,
  status          TEXT NOT NULL,   -- running | ok | partial | failed
  error           TEXT
);

CREATE TABLE run_source (
  run_id      INTEGER NOT NULL REFERENCES run(id),
  source_key  TEXT NOT NULL,
  status      TEXT NOT NULL,   -- ok | failed | skipped | disabled
  items       INTEGER DEFAULT 0,
  duration_ms INTEGER,
  error       TEXT,
  PRIMARY KEY (run_id, source_key)
);

CREATE TABLE fetch_log (
  url             TEXT PRIMARY KEY,
  etag            TEXT,
  last_modified   TEXT,
  content_sha256  TEXT,        -- of the EXTRACTED text, not the raw HTML
  status          INTEGER,
  fetched_at      TEXT,
  next_eligible_at TEXT
);

CREATE TABLE llm_cache (
  key         TEXT PRIMARY KEY,   -- sha256(prompt_version | model_id | normalised_text)
  response_json TEXT NOT NULL,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cost_usd    REAL,
  created_at  TEXT NOT NULL
);

CREATE TABLE quarantine (
  id          INTEGER PRIMARY KEY,
  source_key  TEXT NOT NULL,
  source_url  TEXT,
  raw_json    TEXT NOT NULL,
  error       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE config_snapshot (
  config_hash TEXT PRIMARY KEY,
  config_json TEXT NOT NULL,
  is_last_good INTEGER DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- postcodes.io returns region/country/admin_district as ARRAYS (an outcode can
-- straddle boundaries) and populates `region` for ENGLAND ONLY. Take element [0];
-- region MUST be nullable, country must not be. See 06-scoring §2.2.
CREATE TABLE postcode_region (
  outcode     TEXT PRIMARY KEY,   -- 'NE1', 'S75', 'LS2'
  region      TEXT,               -- NULL for Scotland / Wales / Northern Ireland
  country     TEXT NOT NULL,
  district    TEXT,
  cached_at   TEXT NOT NULL
);

CREATE TABLE placeholder_name (   -- never a merge key, never a duplicate
  norm_key TEXT PRIMARY KEY
);
-- seeded: na, unknown, stealth, confidential, tbc, tbd, newco, none, test,
-- plus anything matching ^(bluesky|company)\d+$

CREATE TABLE formation_agent_address (   -- 04-sources §3.4 step 3
  postcode    TEXT PRIMARY KEY,
  company_count INTEGER,
  added_at    TEXT NOT NULL
);
-- seeded from the top ~50 registered-office postcodes by company count in the
-- Companies House monthly bulk product; refreshed monthly. Editable in the sheet.

CREATE TABLE suppression (
  norm_name   TEXT PRIMARY KEY,
  reason      TEXT NOT NULL,      -- 'gdpr_erasure'
  created_at  TEXT NOT NULL
);

CREATE TABLE sheet_row_state (
  company_id  TEXT NOT NULL,
  tab         TEXT NOT NULL,
  col         TEXT NOT NULL,
  last_value  TEXT,
  PRIMARY KEY (company_id, tab, col)
);
```

`fetch_log.content_sha256` hashes the **extracted text**, not the raw HTML. Raw HTML changes on every load — ad slots, CSRF tokens, cache-busting query strings — so a raw hash never matches. The extracted text is stable. This is what catches the common case of a server returning `200` with no `ETag` and unchanged content.

`sheet_row_state` is what makes FR-7.8 possible: a run that changes nothing computes a diff of zero and issues no write calls at all.

---

## 4. Controlled vocabularies

These are the exact allowed values. They come from the client's own `VC Scout.xlsx`, so his mental model is preserved. They live in the `Lists` tab of the Google Sheet and are read at runtime — **do not hard-code them.**

**Stage:** `idea` · `pre_seed` · `seed` · `series_a` · `series_b_plus` · `growth`

**Sector:** `fintech` · `insurtech` · `wealthtech` · `lending` · `regtech` · `b2b_saas` · `vertical_saas` · `ai_data` · `climate_tech` · `healthy_ageing` · `life_sciences` · `healthcare` · `deeptech` · `developer_tools` · `consumer` · `marketplace` · `industrial_tech` · `other`

**Geography:** `london` · `uk_regions` · `north_east` · `yorkshire` · `uk_wide` · `europe_ex_uk` · `global` · `us` · `other`

**Founder signal:** `repeat_founder` · `technical_founder` · `domain_expert` · `research_spinout` · `operator_led` · `student_founder` · `generalist_unclear`

**Traction signal:** `pre_revenue_concept` · `pilot_customers` · `paying_customers` · `rapid_usage_growth` · `strong_revenue_growth` · `enterprise_contracts` · `clinical_grant_validation` · `community_traction`

**Tier:** `shortlist` · `watchlist` · `reject`

**Verdict (Aryan's):** `worth contacting` · `not for me` · `unsure` · *(blank)*

Enum matching must be **case-insensitive and separator-insensitive** — `"Pre Seed"`, `"pre-seed"` and `"PRE_SEED"` all resolve to `pre_seed`. When a value doesn't match, suggest the nearest with rapidfuzz in the error message: `did you mean 'pre_seed'?`

---

## 5. Region mapping

A company's region is resolved from its postcode outcode via postcodes.io (free, no key), cached locally forever because outcodes are stable.

| Fund geography value | ONS regions it accepts |
|---|---|
| `north_east` | North East |
| `yorkshire` | Yorkshire and The Humber |
| `london` | London |
| `uk_regions` | Everything in England/Scotland/Wales/NI **except** London, and excluding Oxford (OX) and Cambridge (CB) outcodes when the fund's rule is `outside_golden_triangle` |
| `uk_wide` | Any UK region |

The golden-triangle rule exists because DSW's SEIS fund explicitly targets companies outside London–Oxbridge. It is implemented as an outcode-prefix check (`OX*`, `CB*`) plus the London region, not as a fuzzy city-name match.

---

## 6. Ten queries the implementer will need

```sql
-- 1. Today's shortlist, ranked
SELECT c.canonical_name, s.fund_key, s.fund_fit_pct, s.discovery_edge,
       s.priority, s.explanation, c.website_url
FROM score s JOIN company c ON c.id = s.company_id
WHERE s.tier = 'shortlist' AND date(s.scored_at) = date('now')
  AND c.merged_into IS NULL
ORDER BY s.priority DESC;

-- 2. THE HEALTH CHECK — median age of shortlisted companies. Must stay under 24.
-- One row per company (not per fund), last 30 days, known ages only.
WITH ages AS (
  SELECT DISTINCT c.id,
         (julianday('now') - julianday(c.incorporated_on)) / 30.44 AS age
  FROM score s JOIN company c ON c.id = s.company_id
  WHERE s.tier = 'shortlist'
    AND c.incorporated_on IS NOT NULL
    AND c.merged_into IS NULL
    AND s.scored_at > date('now','-30 day')
)
SELECT AVG(age) AS median_age FROM (
  SELECT age FROM ages ORDER BY age
  LIMIT  2 - (SELECT COUNT(*) FROM ages) % 2
  OFFSET (SELECT (COUNT(*) - 1) / 2 FROM ages)
);

-- 3. Why is this company here? Full evidence trail.
SELECT kind, occurred_on, headline, source_url
FROM signal WHERE company_id = ? ORDER BY occurred_on DESC;

-- 4. Score breakdown for one company and fund
SELECT sc.label, sc.sub_score, sc.weight, sc.contribution, sc.evidence
FROM score_component sc JOIN score s ON s.id = sc.score_id
WHERE s.company_id = ? AND s.fund_key = ? ORDER BY sc.contribution DESC;

-- 5. Which sources actually produce shortlisted companies?
SELECT cs.source_key, COUNT(DISTINCT c.id) AS companies,
       SUM(CASE WHEN s.tier='shortlist' THEN 1 ELSE 0 END) AS shortlisted
FROM company_source cs
JOIN company c ON c.id = cs.company_id
LEFT JOIN score s ON s.company_id = c.id
GROUP BY cs.source_key ORDER BY shortlisted DESC;

-- 6. Why was everything rejected today?
SELECT reject_reason, COUNT(*) FROM score
WHERE tier='reject' AND date(scored_at)=date('now')
GROUP BY reject_reason ORDER BY 2 DESC;

-- 7. New to the shortlist since yesterday
SELECT c.canonical_name, s.fund_key, s.priority
FROM score s JOIN company c ON c.id=s.company_id
WHERE s.tier='shortlist' AND date(s.scored_at)=date('now')
  AND c.id NOT IN (SELECT company_id FROM score
                   WHERE tier='shortlist' AND date(scored_at)=date('now','-1 day'));

-- 8. Precision against Aryan's own verdicts — the tuning input
SELECT s.tier, uf.value AS verdict, COUNT(*)
FROM score s JOIN user_field uf
  ON uf.company_id=s.company_id AND uf.field='verdict'
GROUP BY s.tier, uf.value;

-- 9. Duplicate audit — must return zero rows.
-- Grouped by (norm_key, country) because the match ladder DELIBERATELY keeps
-- same-name-different-jurisdiction companies apart; placeholder names excluded
-- for the same reason (two companies both called "Stealth" are not duplicates).
SELECT norm_key, country_iso2, COUNT(*) c
FROM company
WHERE merged_into IS NULL
  AND norm_key NOT IN (SELECT norm_key FROM placeholder_name)
GROUP BY norm_key, country_iso2
HAVING c > 1;

-- 10. Monthly AI spend
SELECT strftime('%Y-%m', created_at) m, COUNT(*), ROUND(SUM(cost_usd),2)
FROM llm_cache GROUP BY m ORDER BY m DESC;
```

Query 2 is the one to run weekly. If the median age of shortlisted companies creeps above 24 months, the system is drifting back toward the version 1 failure and the gates need tightening.

Query 9 must always return zero rows. If it doesn't, de-duplication has a bug and the sheet is about to show Aryan the same company twice.

---

## 7. Migrations

`schema_version` lives in a `_meta` table. Migrations are numbered SQL files in `radar/store/migrations/`, applied in order inside a transaction, each recorded on success. Never edit an applied migration; always add a new one.

Backups run before every migration and nightly:

```bash
sqlite3 /opt/founder-radar/data/radar.db ".backup /opt/founder-radar/backups/radar-$(date +%F).db"
find /opt/founder-radar/backups -name 'radar-*.db' -mtime +14 -delete
```

Fourteen days of backups on a database this size costs a few megabytes and makes "what did the sheet look like last Tuesday?" a solvable question.
