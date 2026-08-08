-- UK Founder Radar — full schema (03-data-model.md).
-- Applied in one transaction by radar.store.db.migrate().
-- Principle: never overwrite a fact, always add an observation.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS _meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- ---------------------------------------------------------------- core

CREATE TABLE IF NOT EXISTS company (
  id                   TEXT PRIMARY KEY,       -- ULID; stable forever, joins to the Sheet
  canonical_name       TEXT NOT NULL,
  norm_key             TEXT NOT NULL,
  companies_house_no   TEXT,                   -- STRING not INT. Leading zeros, SC/NI/OC prefixes.
  domain               TEXT,
  website_url          TEXT,
  incorporated_on      TEXT,                   -- ISO date. THE age field.
  age_source           TEXT,                   -- companies_house | source_stated | unknown
  hq_postcode          TEXT,
  hq_region            TEXT,
  hq_city              TEXT,
  country_iso2         TEXT,                   -- deliberately NO default: NULL until confirmed
  sector               TEXT,
  stage                TEXT,
  founder_signal       TEXT,
  traction_signal      TEXT,
  total_funding_gbp    REAL,                   -- NULL = unknown, 0 = known none. Never collapse.
  one_liner            TEXT,
  sic_codes            TEXT,                   -- JSON array
  has_share_issue      INTEGER DEFAULT 0,      -- SH01 within 18 months of incorporation
  officer_count        INTEGER,
  news_mention_count   INTEGER DEFAULT 0,      -- articles in OUR tracked sources only
  on_vc_portfolio      INTEGER DEFAULT 0,
  discovery_route      TEXT,                   -- registry|spinout|accelerator|grant|news

  -- per-vehicle hard-rule inputs (06-scoring §4.5). NULL unless a source said so.
  is_university_spinout INTEGER,
  spinout_university   TEXT,
  last_round_gbp       REAL,
  prior_total_gbp      REAL,
  valuation_gbp        REAL,
  uk_exec_pct          REAL,
  seis_eis_qualifying  INTEGER,

  -- operational
  qualified            INTEGER DEFAULT 0,
  qualifiers           TEXT,                   -- JSON array
  enriched_at          TEXT,
  extraction_method    TEXT,                   -- structured|llm|heuristic
  age_unknown          INTEGER DEFAULT 0,
  uk_unverified        INTEGER DEFAULT 0,
  date_confidence      TEXT,                   -- exact|stated|inferred
  synced               INTEGER DEFAULT 0,

  first_seen           TEXT NOT NULL,
  last_seen            TEXT NOT NULL,
  merged_into          TEXT REFERENCES company(id),
  needs_review         INTEGER DEFAULT 0,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_company_ch  ON company(companies_house_no)
       WHERE companies_house_no IS NOT NULL AND merged_into IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_dom ON company(domain)
       WHERE domain IS NOT NULL AND merged_into IS NULL;
CREATE INDEX IF NOT EXISTS ix_company_normkey    ON company(norm_key) WHERE merged_into IS NULL;
CREATE INDEX IF NOT EXISTS ix_company_inc        ON company(incorporated_on);
CREATE INDEX IF NOT EXISTS ix_company_region     ON company(hq_region);

CREATE TABLE IF NOT EXISTS company_source (
  company_id  TEXT NOT NULL REFERENCES company(id),
  source_key  TEXT NOT NULL,
  external_id TEXT NOT NULL,
  source_url  TEXT NOT NULL,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  PRIMARY KEY (company_id, source_key, external_id)
);
CREATE INDEX IF NOT EXISTS ix_company_source ON company_source(source_key);

CREATE TABLE IF NOT EXISTS identifier (
  company_id  TEXT NOT NULL REFERENCES company(id),
  kind        TEXT NOT NULL,   -- ch | domain | norm_key | alias | lei
  value       TEXT NOT NULL,
  source_key  TEXT NOT NULL,
  first_seen  TEXT NOT NULL,
  PRIMARY KEY (kind, value, company_id)
);
CREATE INDEX IF NOT EXISTS ix_identifier_lookup ON identifier(kind, value);

CREATE TABLE IF NOT EXISTS observation (
  id            INTEGER PRIMARY KEY,
  company_id    TEXT NOT NULL REFERENCES company(id),
  field         TEXT NOT NULL,
  value_json    TEXT NOT NULL,
  source_key    TEXT NOT NULL,
  source_type   TEXT NOT NULL,   -- registry|grant|company_site|spinout|accelerator|news|portfolio|derived|llm
  source_url    TEXT,
  confidence    REAL NOT NULL,
  observed_at   TEXT NOT NULL,
  extractor_ver TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs ON observation(company_id, field, observed_at DESC);

-- GDPR-minimal. Forbidden columns are enforced by tests/unit/test_schema_privacy.py.
CREATE TABLE IF NOT EXISTS founder (
  id            INTEGER PRIMARY KEY,
  company_id    TEXT NOT NULL REFERENCES company(id),
  name          TEXT NOT NULL,
  norm_name     TEXT NOT NULL,
  role          TEXT,
  profile_url   TEXT,
  is_psc        INTEGER DEFAULT 0,
  appointed_on  TEXT,
  prior_appointments INTEGER,
  source_url    TEXT NOT NULL,
  first_seen    TEXT NOT NULL,
  UNIQUE (company_id, norm_name)
);

CREATE TABLE IF NOT EXISTS signal (
  id           INTEGER PRIMARY KEY,
  company_id   TEXT NOT NULL REFERENCES company(id),
  kind         TEXT NOT NULL,
  occurred_on  TEXT,
  headline     TEXT NOT NULL,
  detail       TEXT,
  amount_gbp   REAL,
  source_key   TEXT NOT NULL,
  source_url   TEXT NOT NULL,
  first_seen   TEXT NOT NULL,
  UNIQUE (company_id, kind, source_url)
);
CREATE INDEX IF NOT EXISTS ix_signal_company ON signal(company_id, occurred_on DESC);

CREATE TABLE IF NOT EXISTS score (
  id              INTEGER PRIMARY KEY,
  company_id      TEXT NOT NULL REFERENCES company(id),
  fund_key        TEXT NOT NULL,
  vehicle_key     TEXT,
  fund_fit_pct    REAL NOT NULL,
  coverage        REAL NOT NULL,
  discovery_edge  REAL NOT NULL,
  priority        REAL NOT NULL,
  tier            TEXT NOT NULL,
  reject_reason   TEXT,
  explanation     TEXT NOT NULL,
  flags           TEXT,
  config_hash     TEXT NOT NULL,
  scorer_version  TEXT NOT NULL,
  scored_at       TEXT NOT NULL
);

-- COALESCE: SQLite treats NULLs as DISTINCT in a UNIQUE constraint, so
-- fund-level rows (vehicle_key IS NULL) would duplicate on every re-score.
-- 03-data-model.md writes this as an inline UNIQUE(...) table constraint, which
-- SQLite rejects — expressions are only legal in CREATE INDEX. Same guarantee,
-- legal syntax. Upserts must use the identical expression as their conflict
-- target: ON CONFLICT(company_id, fund_key, COALESCE(vehicle_key,''), config_hash).
CREATE UNIQUE INDEX IF NOT EXISTS ux_score
       ON score(company_id, fund_key, COALESCE(vehicle_key, ''), config_hash);
CREATE INDEX IF NOT EXISTS ix_score_tier ON score(tier, scored_at DESC);

CREATE TABLE IF NOT EXISTS score_component (
  score_id    INTEGER NOT NULL REFERENCES score(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,
  label       TEXT NOT NULL,
  sub_score   REAL,              -- NULL = unknown. Never coerce to 0.
  weight      REAL NOT NULL,
  contribution REAL,
  evidence    TEXT NOT NULL,
  PRIMARY KEY (score_id, key)
);

CREATE TABLE IF NOT EXISTS score_history (
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

CREATE TABLE IF NOT EXISTS user_field (
  company_id  TEXT NOT NULL REFERENCES company(id),
  field       TEXT NOT NULL,     -- verdict | notes | contacted | fund_sent
  value       TEXT,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (company_id, field)
);

CREATE TABLE IF NOT EXISTS merge_event (
  id             INTEGER PRIMARY KEY,
  winner_id      TEXT NOT NULL,
  loser_id       TEXT NOT NULL,
  rule           TEXT NOT NULL,   -- ch_exact|domain_exact|normkey_exact|fuzzy|manual
  score          REAL,
  evidence_json  TEXT NOT NULL,
  merged_at      TEXT NOT NULL,
  merged_by      TEXT NOT NULL    -- auto | user
);

-- ---------------------------------------------------------- operational

CREATE TABLE IF NOT EXISTS run (
  id              INTEGER PRIMARY KEY,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  mode            TEXT NOT NULL,   -- daily | backfill | rescore | manual
  scope           TEXT,
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

CREATE TABLE IF NOT EXISTS run_source (
  run_id      INTEGER NOT NULL REFERENCES run(id),
  source_key  TEXT NOT NULL,
  status      TEXT NOT NULL,   -- ok | failed | skipped | disabled
  items       INTEGER DEFAULT 0,
  duration_ms INTEGER,
  error       TEXT,
  PRIMARY KEY (run_id, source_key)
);

CREATE TABLE IF NOT EXISTS fetch_log (
  url             TEXT PRIMARY KEY,
  etag            TEXT,
  last_modified   TEXT,
  content_sha256  TEXT,        -- of the EXTRACTED text, not the raw HTML
  status          INTEGER,
  fetched_at      TEXT,
  next_eligible_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_cache (
  key         TEXT PRIMARY KEY,   -- sha256(prompt_version | model_id | normalised_text)
  response_json TEXT NOT NULL,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cost_usd    REAL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine (
  id          INTEGER PRIMARY KEY,
  source_key  TEXT NOT NULL,
  source_url  TEXT,
  raw_json    TEXT NOT NULL,
  error       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_snapshot (
  config_hash TEXT PRIMARY KEY,
  config_json TEXT NOT NULL,
  is_last_good INTEGER DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- postcodes.io returns region/country/admin_district as ARRAYS and populates
-- `region` for ENGLAND ONLY. Take element [0]; region MUST be nullable.
CREATE TABLE IF NOT EXISTS postcode_region (
  outcode     TEXT PRIMARY KEY,
  region      TEXT,
  country     TEXT NOT NULL,
  district    TEXT,
  cached_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS placeholder_name (
  norm_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS formation_agent_address (
  postcode      TEXT PRIMARY KEY,
  company_count INTEGER,
  added_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression (
  norm_name   TEXT PRIMARY KEY,
  reason      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sheet_row_state (
  company_id  TEXT NOT NULL,
  tab         TEXT NOT NULL,
  col         TEXT NOT NULL,
  last_value  TEXT,
  PRIMARY KEY (company_id, tab, col)
);

-- Layout-change detection (05-pipeline): a 200 OK with an empty list is the
-- dangerous failure, so per-source item counts are tracked over time.
CREATE TABLE IF NOT EXISTS source_health (
  source_key   TEXT NOT NULL,
  observed_on  TEXT NOT NULL,
  items        INTEGER NOT NULL,
  status       TEXT NOT NULL,
  note         TEXT,
  PRIMARY KEY (source_key, observed_on)
);
