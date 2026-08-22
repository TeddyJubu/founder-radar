-- 004: Hermes Today QA — veto-only check on the morning queue.
-- Fresh databases get the same CREATE from schema.sql; this migration
-- covers boxes that were already in service before the table existed.
CREATE TABLE IF NOT EXISTS today_check (
  company_id     TEXT NOT NULL REFERENCES company(id),
  snapshot_hash  TEXT NOT NULL,
  verdict        TEXT NOT NULL,
  reason         TEXT,
  summary        TEXT,
  checker        TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  raw_text       TEXT,
  checked_at     TEXT NOT NULL,
  PRIMARY KEY (company_id, snapshot_hash)
);

CREATE INDEX IF NOT EXISTS ix_today_check_company
    ON today_check(company_id, checked_at);
