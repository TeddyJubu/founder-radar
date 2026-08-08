#!/usr/bin/env bash
#
# Daily SQLite snapshot with a 14-day window (08-deployment §4).
#
# `sqlite3 .backup` rather than `cp`: the database runs in WAL mode, so a plain
# copy taken mid-write yields a file that opens fine and is quietly missing the
# last transactions. The backup API takes a consistent snapshot of a live
# database. That distinction is the difference between a backup and a souvenir.
#
# Everything is overridable so the test suite can run this against a temp dir.
set -euo pipefail

RADAR_DB="${RADAR_DB:-/opt/founder-radar/data/radar.db}"
BACKUP_DIR="${BACKUP_DIR:-/opt/founder-radar/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"

if [ ! -f "$RADAR_DB" ]; then
  echo "backup: no database at $RADAR_DB" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

stamp="$(date +%F)"
dest="$BACKUP_DIR/radar-$stamp.db"

# Write to a temp name first: an interrupted backup must not leave a truncated
# file wearing today's date, which is what a restore would then reach for.
tmp="$dest.partial"
sqlite3 "$RADAR_DB" ".backup '$tmp'"
mv -f "$tmp" "$dest"

# Prune only our own filenames, and only after today's snapshot succeeded —
# `set -e` above guarantees we never delete history to make room for nothing.
find "$BACKUP_DIR" -maxdepth 1 -name 'radar-*.db' -type f -mtime "+$RETAIN_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'radar-*.db.partial' -type f -mtime +1 -delete

kept="$(find "$BACKUP_DIR" -maxdepth 1 -name 'radar-*.db' -type f | wc -l | tr -d ' ')"
echo "backup: $dest ($kept kept, retention ${RETAIN_DAYS}d)"
