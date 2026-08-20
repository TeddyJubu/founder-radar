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

# Seed to a sibling path and rename only on success. Db() creates the file on
# connect, so seeding straight into $DB would leave an empty/partial database
# after a crash, and the next start would skip seeding forever.
if [ ! -f "$DB" ]; then
  tmp="${DB}.seeding"
  rm -f "$tmp" "${tmp}-wal" "${tmp}-shm"
  if ! .venv/bin/python scripts/seed_demo_db.py "$tmp"; then
    rm -f "$tmp" "${tmp}-wal" "${tmp}-shm"
    exit 1
  fi
  mv -f "$tmp" "$DB"
  [ -f "${tmp}-wal" ] && mv -f "${tmp}-wal" "${DB}-wal"
  [ -f "${tmp}-shm" ] && mv -f "${tmp}-shm" "${DB}-shm"
fi

exec .venv/bin/python prototype/server.py --db "$DB" --port "$PORT"
