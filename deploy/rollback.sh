#!/usr/bin/env bash
#
# Root-only Control room rollback helper.
#
# Installed to /usr/local/libexec/founder-radar/rollback.sh (root-owned). The
# radar service account may invoke it via sudoers with no password, the same
# pattern as hermes-acl.sh. Accepts only:
#
#   restore-db   <radar-YYYY-MM-DD.db>
#   rollback-code <commit>
#   release
#
# Everything else is refused. Commits must be ancestors of origin/main.
# Snapshot names must match the nightly filename and resolve under BACKUP_DIR.
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_DIR="${APP_DIR:-$ROOT/app}"
APP_USER="${APP_USER:-radar}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
RADAR_DB="${RADAR_DB:-$ROOT/data/radar.db}"
VENV="${VENV:-$ROOT/venv}"
HOLD_DIR="${HOLD_DIR:-/etc/founder-radar}"
HOLD="${RADAR_ROLLBACK_HOLD:-$HOLD_DIR/rollback-hold}"
LIBEXEC="${LIBEXEC:-/usr/local/libexec/founder-radar}"
LOG="${RADAR_ROLLBACK_LOG:-$ROOT/logs/rollback.log}"
UPDATE_SCRIPT="${RADAR_UPDATE_SCRIPT:-$LIBEXEC/update-from-main.sh}"

say() {
  mkdir -p "$(dirname "$LOG")"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG" >&2
}

die() { say "error: $*"; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  die "rollback.sh must run as root"
fi

cmd="${1:-}"
arg="${2:-}"

case "$cmd" in
  restore-db|rollback-code|release) ;;
  *) die "usage: rollback.sh restore-db <file> | rollback-code <sha> | release" ;;
esac

scan_busy() {
  command -v systemctl >/dev/null 2>&1 || return 1
  local state
  state="$(systemctl show --property=ActiveState --value founder-radar.service 2>/dev/null || true)"
  case "$state" in
    active|activating|reloading|deactivating) return 0 ;;
    *) return 1 ;;
  esac
}

restore_db() {
  local name path date
  name="$(basename -- "$arg")"
  [[ "$name" =~ ^radar-[0-9]{4}-[0-9]{2}-[0-9]{2}\.db$ ]] \
    || die "not a nightly snapshot filename"
  date="${name#radar-}"
  date="${date%.db}"
  mkdir -p "$BACKUP_DIR"
  path="$(readlink -f "$BACKUP_DIR/$name" 2>/dev/null || realpath "$BACKUP_DIR/$name" 2>/dev/null || echo "$BACKUP_DIR/$name")"
  case "$path" in
    "$(readlink -f "$BACKUP_DIR" 2>/dev/null || realpath "$BACKUP_DIR" 2>/dev/null || echo "$BACKUP_DIR")"/*) ;;
    *) die "snapshot path is outside the backup directory" ;;
  esac
  [ -f "$path" ] || die "snapshot not found: $name"
  if scan_busy; then
    die "the morning scan is running — try again when it finishes"
  fi

  local stamp safety tmp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  safety="$BACKUP_DIR/radar-before-restore-$stamp.db"
  tmp="$safety.partial"
  rm -f "$tmp"
  [ -f "$RADAR_DB" ] || die "no database at $RADAR_DB"
  sqlite3 "$RADAR_DB" ".backup '$tmp'"
  mv -f "$tmp" "$safety"
  say "safety copy $safety"

  if [ -x "$VENV/bin/founder-radar" ]; then
    sudo -H -u "$APP_USER" "$VENV/bin/founder-radar" --db "$RADAR_DB" db restore "$path"
  else
    die "founder-radar CLI not installed at $VENV"
  fi
  say "restored $name (sheet sync skipped)"
}

rollback_code() {
  local sha full
  sha="$arg"
  [ -n "$sha" ] || die "commit id required"
  [[ "$sha" =~ ^[0-9a-fA-F]+$ ]] || die "invalid commit id"
  [ -d "$APP_DIR/.git" ] || die "no git checkout at $APP_DIR"

  # Root must never run git against a radar-writable checkout.
  if [ -L "$APP_DIR" ] || [ "$(stat -c %u "$APP_DIR")" != 0 ] || \
      [ -n "$(find "$APP_DIR" -maxdepth 0 -perm /022 -print)" ]; then
    die "unsafe checkout ownership: $APP_DIR"
  fi

  git -C "$APP_DIR" fetch --prune origin main >/dev/null 2>&1 || true
  full="$(git -C "$APP_DIR" rev-parse --verify "${sha}^{commit}")" \
    || die "unknown commit $sha"
  git -C "$APP_DIR" merge-base --is-ancestor "$full" origin/main \
    || die "commit is not on origin/main"

  mkdir -p "$HOLD_DIR"
  printf '%s\n' "$full" > "$HOLD"
  chmod 644 "$HOLD"
  say "hold $full"

  git -C "$APP_DIR" checkout --detach "$full"
  say "checked out $full"

  # install.sh restarts the site; return before that finishes so the Control
  # room can tell the operator to reload.
  if [ -f "$APP_DIR/deploy/install.sh" ]; then
    nohup bash "$APP_DIR/deploy/install.sh" >>"$LOG" 2>&1 &
    say "install.sh started in background (pid $!)"
  else
    die "install.sh missing at rolled-back commit"
  fi
  printf '%s\n' "$full"
}

release_hold() {
  rm -f "$HOLD"
  say "hold cleared"
  if [ -x "$UPDATE_SCRIPT" ]; then
    bash "$UPDATE_SCRIPT"
  elif [ -x "$APP_DIR/deploy/update-from-main.sh" ]; then
    bash "$APP_DIR/deploy/update-from-main.sh"
  else
    die "update-from-main.sh not found"
  fi
}

case "$cmd" in
  restore-db) restore_db ;;
  rollback-code) rollback_code ;;
  release) release_hold ;;
esac
