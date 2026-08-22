#!/usr/bin/env bash
#
# Pull origin/main into the production checkout and reinstall.
#
# This is the I23/J26 auto-deploy path: the box watches GitHub itself, so a
# merge to main reaches /opt/founder-radar without GitHub Actions secrets.
# founder-radar-update.timer runs it; install.sh enables that timer.
#
#   sudo bash /opt/founder-radar/app/deploy/update-from-main.sh
#
# Fast-forward only. Never force-pushes, never prints secrets. Concurrent
# runs share a lock so a GitHub-optional SSH deploy cannot race the timer.
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_DIR="${APP_DIR:-$ROOT/app}"
APP_USER="${APP_USER:-radar}"
LOCK="${RADAR_UPDATE_LOCK:-/run/founder-radar-update.lock}"
LOG="${RADAR_UPDATE_LOG:-$ROOT/logs/update.log}"
DRY="${RADAR_UPDATE_DRY_RUN:-0}"
FORCE="${RADAR_UPDATE_FORCE_RESCORE:-0}"
ALLOW_NONROOT="${RADAR_UPDATE_ALLOW_NONROOT:-0}"

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) return 0 ;;
    *) return 1 ;;
  esac
}

if [ "$(id -u)" -ne 0 ] && [ "$ALLOW_NONROOT" != "1" ]; then
  echo "update-from-main.sh must run as root" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")"
touch "$LOG"
if [ "$(id -u)" -eq 0 ]; then
  chown "${APP_USER}:${APP_USER}" "$LOG" 2>/dev/null || true
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another founder-radar update is already running — skipping" | tee -a "$LOG"
  exit 0
fi

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

run_git() {
  if [ "$(id -u)" -eq 0 ]; then
    sudo -H -u "$APP_USER" git -C "$APP_DIR" "$@"
  else
    git -C "$APP_DIR" "$@"
  fi
}

if [ ! -d "$APP_DIR/.git" ]; then
  echo "no git checkout at $APP_DIR" >&2
  exit 1
fi

# Do not pip-install over a live daily scan.
if [ "$DRY" != "1" ] && command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet founder-radar.service; then
    say "daily scan is running — will try again next cycle"
    exit 0
  fi
fi

# Never inherit a caller cwd of /root: pip as the service user then tries to
# write an editable path hook under root's home and dies.
cd "$ROOT"

before="$(run_git rev-parse HEAD)"
say "before: $before"
run_git fetch --prune origin main
remote="$(run_git rev-parse origin/main)"

if [ "$before" = "$remote" ]; then
  if truthy "$FORCE"; then
    say "already at $before — forced rescore without a pull"
  else
    say "already at $before — nothing to do"
    exit 0
  fi
else
  say "updating $before -> $remote"
  run_git checkout main
  # merge, not `pull origin main`: after `fetch origin main`, git 2.43
  # errors with "Cannot fast-forward to multiple branches".
  run_git merge --ff-only origin/main
fi

if [ "$DRY" = "1" ]; then
  say "dry-run: skipped install.sh / doctor / rescore"
  say "after:  $(run_git rev-parse HEAD)"
  exit 0
fi

bash "$APP_DIR/deploy/install.sh"

cd "$ROOT"
run_cli() {
  # Explicit venv binary: do not depend on /usr/local/bin being on PATH
  # for a non-login sudo. cwd is already $ROOT so the CLI loads $ROOT/.env.
  if [ "$(id -u)" -eq 0 ]; then
    sudo -H -u "$APP_USER" "$ROOT/venv/bin/founder-radar" "$@"
  else
    "$ROOT/venv/bin/founder-radar" "$@"
  fi
}

run_cli doctor || say "doctor reported issues (see output above)"

# Heal a column-shifted Fund Criteria sheet / poisoned last-good before
# rescoring. Without --force-sheet this is a no-op when config is healthy.
say "repairing Fund Criteria if last-good or the sheet is poisoned"
repair_json="$(run_cli --json repair-fund-criteria || true)"
if printf '%s' "$repair_json" | grep -q '"repaired": true'; then
  say "Fund Criteria repaired — forcing full rescore"
  FORCE=1
fi

if [ "$before" != "$(run_git rev-parse HEAD)" ] || truthy "$FORCE"; then
  say "rescoring all companies under the current config"
  run_cli rescore --all
fi

if command -v systemctl >/dev/null 2>&1; then
  fail=0
  for unit in founder-radar-web.service founder-radar.timer founder-radar-update.timer; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null \
        || systemctl is-active --quiet "$unit" 2>/dev/null; then
      say "ok: $unit"
    else
      say "error: $unit is not enabled/active after update"
      systemctl status --no-pager --lines 20 "$unit" || true
      fail=1
    fi
  done
  # Hermes Agent control plane must not be public. If an old unit is still
  # active, surface it as a warning (install.sh already tries to disable it).
  if systemctl is-active --quiet hermes-dashboard.service 2>/dev/null; then
    say "warn: hermes-dashboard.service is still active — control plane should be Telegram-only"
  fi
  [ "$fail" -eq 0 ] || exit 1
fi

say "after:  $(run_git rev-parse HEAD)"
