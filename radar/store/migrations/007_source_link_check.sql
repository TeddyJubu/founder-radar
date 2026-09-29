-- Network verification belongs to QA, never deterministic scoring.
CREATE TABLE IF NOT EXISTS source_link_check (
  url TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('reachable','dead','blocked','timeout','invalid','error')),
  final_url TEXT NOT NULL,
  status INTEGER,
  reason TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
