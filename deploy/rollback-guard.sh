#!/usr/bin/env bash
#
# Gate the five-minute updater behind a Control room rollback hold.
#
# If /etc/founder-radar/rollback-hold names the commit currently checked out,
# skip the fetch/install cycle so a deliberate rollback survives. Otherwise run
# the normal update-from-main.sh. A systemd drop-in points
# founder-radar-update.service at this script; an older install.sh that
# overwrites the unit file does not remove the drop-in or this guard.
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_DIR="${APP_DIR:-$ROOT/app}"
HOLD="${RADAR_ROLLBACK_HOLD:-/etc/founder-radar/rollback-hold}"
LIBEXEC="${LIBEXEC:-/usr/local/libexec/founder-radar}"
UPDATE_SCRIPT="${RADAR_UPDATE_SCRIPT:-$LIBEXEC/update-from-main.sh}"
LOG="${RADAR_UPDATE_LOG:-/var/log/founder-radar-update.log}"

say() {
  if [ -n "${INVOCATION_ID:-}" ]; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
  else
    mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
  fi
}

if [ -f "$HOLD" ] && [ -d "$APP_DIR/.git" ]; then
  held="$(tr -d '[:space:]' < "$HOLD" || true)"
  if [ -n "$held" ]; then
    current="$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || true)"
    if [ -n "$current" ] && [ "$held" = "$current" ]; then
      say "rollback hold active at $current — skipping update"
      exit 0
    fi
    say "rollback hold names $held but HEAD is ${current:-unknown} — running updater"
  fi
fi

if [ ! -x "$UPDATE_SCRIPT" ]; then
  echo "update script not found: $UPDATE_SCRIPT" >&2
  exit 1
fi
exec bash "$UPDATE_SCRIPT"
