# Control room (`/admin`) — HTTP API

The review server is stdlib-only `prototype/server.py`, behind Caddy basic auth
in production; the page is `/admin`. No dependencies, no build step.

Purpose: the client can (1) see the daily flow (stages ① Config → ② Fetch →
③ Extract → ④ Resolve → ⑤ Enrich → ⑥ Gate+score → ⑥½ Today QA → ⑦ Render),
with what the latest run did at each stage; (2) tweak Settings and turn
Sources on/off; (3) view/edit/reset the AI prompts; (4) see an audit log of
changes. Rule to respect: stage ⑥ is deterministic — no AI. Prompts only
affect ③ Extract (reads prose) and ⑥½ Today QA / publish check (veto only).

## DB tables (migration 008_admin.sql)

```sql
CREATE TABLE IF NOT EXISTS prompt_override (
  id          INTEGER PRIMARY KEY,
  prompt_key  TEXT NOT NULL,          -- extract.system | today_qa.brief | publish.brief
  body        TEXT NOT NULL,
  note        TEXT,
  created_at  TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1   -- at most one active row per key
);
CREATE TABLE IF NOT EXISTS admin_change (
  id          INTEGER PRIMARY KEY,
  at          TEXT NOT NULL,
  kind        TEXT NOT NULL,          -- setting | source | prompt | rescore
  key         TEXT,
  old_value   TEXT,
  new_value   TEXT,
  note        TEXT,
  sheet_sync  TEXT                    -- synced | skipped | n/a
);
```

Existing tables worth knowing (see radar/store/schema.sql): `run`,
`run_source`, `source_health`, `score` (company_id, fund_key, vehicle, tier,
config_hash, scored_at, ...), `today_check` (verdict pass|reject|incomplete,
checked_at), `llm_cache`, `quarantine`, `config_snapshot` (config_hash,
created_at, is_last_good), `company` (merged_into).

## Python modules

- `radar/admin/__init__.py` — empty docstring package.
- `radar/admin/flow.py` — `build_flow(conn: sqlite3.Connection, *,
  today: date | None = None, active_config_hash: str | None = None) -> dict`.
  Pure reads, never writes. Every query must tolerate a missing table
  (`sqlite3.OperationalError` → empty/zero) so a fresh DB renders.
  `conn.row_factory` is `sqlite3.Row`.
- `radar/admin/prompts.py` — registry + overrides.
- `radar/admin/settings.py` — view + apply settings/sources.

## HTTP API (all JSON; errors are `{"error": "..."}` with 4xx/5xx)

Writes are POST with `Content-Type: application/json` from the same origin
(`fetch()` from the page does this). Server guards them with the existing
`write_request_refusal`.

### GET /api/admin/flow
```json
{
  "generated_at": "2026-10-10T07:01:02+00:00",
  "active_config_hash": "abcd1234ef567890" ,
  "stages": [
    {"id": "config", "num": "①", "name": "Config",
     "summary": "Read and validate the Google Sheet",
     "ai": false, "network": true, "deterministic": true,
     "settings": ["llm_enabled"], "prompts": [],
     "stats": [{"label": "Config snapshots", "value": 3}]}
  ],
  "runs": [{"id": 12, "started_at": "...", "finished_at": "...", "mode": "daily",
            "status": "ok", "items_fetched": 120, "items_extracted": 40,
            "companies_new": 9, "companies_merged": 2, "gated_out": 30,
            "shortlisted": 6, "llm_calls": 18, "error": null,
            "duration_s": 512.3}],
  "latest_run_sources": [{"source_key": "uktn", "status": "ok", "items": 12,
                          "duration_ms": 900, "error": null}],
  "source_health": [{"source_key": "uktn",
                     "days": [{"observed_on": "2026-10-09", "items": 12, "status": "ok"}]}],
  "tiers": {"shortlist": 6, "watchlist": 20, "reject": 400},
  "today_qa": {"pass": 10, "reject": 2, "incomplete": 0},
  "llm_cache_entries": 812,
  "quarantine_count": 3,
  "config_snapshots": [{"config_hash": "...", "created_at": "...", "is_last_good": true}]
}
```
Stage ids in order: config, fetch, extract, resolve, enrich, score, today_qa,
render. `settings` lists Settings keys that affect the stage (keys from
`radar/render/sheet.py::SETTING_SPECS`); `prompts` lists prompt keys
(`extract.system` on extract; `today_qa.brief` on today_qa;
`publish.brief` on render). `runs` = latest 14 by id desc. `tiers` counts
DISTINCT company_id per tier for `active_config_hash` (if None, the latest
config_hash in score). `today_qa` counts today_check rows with
date(checked_at) = today. `source_health` = last 14 days, per source, days
ascending.

### GET /api/admin/settings
```json
{"config_hash": "...", "config_source": "snapshot",
 "sheet_configured": true,
 "settings": [{"key": "shortlist_fit", "value": "70", "default": "70",
               "type": "int 0–100", "description": "Fit needed to shortlist",
               "stage": "score", "choices": null}],
 "sources": [{"key": "uktn", "track": "A", "enabled": true, "note": ""}]}
```
`choices` is a list of allowed strings for enum settings (max_stage), else
null. `value`/`default` are display strings (lists joined with ", ").

### POST /api/admin/settings
Body `{"changes": {"shortlist_fit": "65"}, "note": "optional"}` (values are
raw strings, exactly as one would type in the Sheet).
200 → `{"ok": true, "applied": {"shortlist_fit": "65"}, "config_hash": "...",
"sheet_sync": "synced"|"skipped", "rescore": {"scored": 412, "shortlisted": 7},
"today_qa_needed": true}` — `today_qa_needed` means Today waits for the Hermes
check again (`founder-radar today-qa`), because the rescore made approvals stale.
400 → `{"error": "invalid settings", "errors": {"shortlist_fit": "message"}}`
502 → `{"error": "Google Sheet could not be updated; nothing was changed"}`

### POST /api/admin/sources
Body `{"key": "uktn", "enabled": false, "note": "..."}` → same 200 shape as
settings (applied = {"uktn": "off"}); 404 for unknown source.

### GET /api/admin/prompts
`{"prompts": [{"key": "extract.system", "label": "Article extraction",
"stage": "extract", "description": "...", "source": "default"|"override",
"version": "2026-08-12.1", "length": 2890, "updated_at": null}]}`

### GET /api/admin/prompts/<key>
`{"key", "label", "stage", "description", "effect": "what happens on save",
"text": "<effective text>", "default_text": "...", "source", "version",
"history": [{"id": 3, "created_at": "...", "note": "...", "active": true,
"length": 2900}]}` — 404 for unknown key.

### GET /api/admin/prompts/<key>/preview
`{"key", "sample": "short description of the sample used",
"preview": "<the full text the model would receive>"}`

### POST /api/admin/prompts
Body `{"key": "today_qa.brief", "text": "...", "note": "..."}` → `{"ok": true,
"version": "...", "source": "override"}`; 400 if text empty / unknown key.

### POST /api/admin/prompts/reset
Body `{"key": "...", "note": "..."}` → `{"ok": true, "version": "...",
"source": "default"}`

### GET /api/admin/changes
`{"changes": [{"id", "at", "kind", "key", "old_value", "new_value", "note",
"sheet_sync"}]}` latest 100 desc.

### POST /api/admin/rescore
Body `{}` → `{"ok": true, "rescore": {"scored": n, "shortlisted": n,
"config_hash": "..."}}`
