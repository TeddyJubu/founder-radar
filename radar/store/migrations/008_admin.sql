-- Control room (/admin): prompt edits and an audit log of every change made
-- from it. Settings themselves still live in the Sheet / config_snapshot.
CREATE TABLE IF NOT EXISTS prompt_override (
  id          INTEGER PRIMARY KEY,
  prompt_key  TEXT NOT NULL,      -- extract.system | today_qa.brief | publish.brief
  body        TEXT NOT NULL,
  note        TEXT,
  created_at  TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1   -- at most one active row per key
);
CREATE INDEX IF NOT EXISTS ix_prompt_override_key
    ON prompt_override(prompt_key, active);

CREATE TABLE IF NOT EXISTS admin_change (
  id          INTEGER PRIMARY KEY,
  at          TEXT NOT NULL,
  kind        TEXT NOT NULL,      -- setting | source | prompt | rescore
  key         TEXT,
  old_value   TEXT,
  new_value   TEXT,
  note        TEXT,
  sheet_sync  TEXT                -- synced | skipped | n/a
);
