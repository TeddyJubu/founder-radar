#!/usr/bin/env bash
#
# Runs the Today prototype web UI as a visible, long-lived dev server. Lives in
# `terminals` (not `install`/`start`) so its logs stay inspectable and it can be
# restarted freely. The disposable demo database is seeded on demand from the
# committed Companies House fixtures — no API key or network required.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DB="${RADAR_DEMO_DB:-/tmp/demo.db}"
PORT="${RADAR_WEB_PORT:-8788}"

if [ ! -f "$DB" ]; then
  .venv/bin/python scripts/seed_demo_db.py "$DB"
fi

exec .venv/bin/python prototype/server.py --db "$DB" --port "$PORT"
