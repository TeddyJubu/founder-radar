-- 002: keep Today decisions separate from lasting user verdicts.
-- The full schema contains the same CREATE IF NOT EXISTS for fresh databases;
-- this migration makes the table explicit for databases already in service.
CREATE TABLE IF NOT EXISTS daily_review (
  company_id   TEXT NOT NULL REFERENCES company(id),
  review_date  TEXT NOT NULL,
  verdict      TEXT NOT NULL,
  reviewed_at  TEXT NOT NULL,
  PRIMARY KEY (company_id, review_date)
);

CREATE INDEX IF NOT EXISTS ix_daily_review_date
    ON daily_review(review_date);
