#!/usr/bin/env python3
"""Build a disposable demo database for the Today prototype.

This codifies the "rebuild the demo database from scratch" recipe in
prototype/TESTING.md §0.2. It uses the committed Companies House fixtures via
the Track B end-to-end test double, so it needs no API key, no network and no
live sources. The result is register-derived companies only (all
``coverage = 0.8``), which is exactly what the Today prototype expects.

Usage:
    python scripts/seed_demo_db.py [path]      # defaults to /tmp/demo.db
"""

from __future__ import annotations

import sys
from pathlib import Path

# The recipe imports from tests/, so the repository root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config.defaults import default_config
from radar.pipeline import enrich_stage, resolve_item, score_company
from radar.sources.base import FetchContext
from radar.sources.companies_house import CompaniesHouseAdapter
from radar.store.db import Db
from tests.unit.test_track_b_end_to_end import TODAY, MockCompaniesHouse


def build(path: str) -> int:
    db = Db(path)
    db.migrate()
    cfg = default_config()
    http = MockCompaniesHouse()
    adapter = CompaniesHouseAdapter(api_key="demo", days_back=90, window_days=90)
    for item in adapter.fetch(FetchContext(http=http, config=cfg, db=db, now=TODAY)):
        resolve_item(db, item, cfg)
    enrich_stage(db, cfg, http, api_key="demo")
    for row in db.query("SELECT id FROM company"):
        score_company(db, row["id"], cfg, today=TODAY)
    return db.scalar("SELECT COUNT(*) FROM company")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo.db"
    count = build(path)
    print(f"seeded {count} companies into {path}", flush=True)


if __name__ == "__main__":
    main()
