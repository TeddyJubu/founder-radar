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

load_hermes_env() {
  HERMES_BIN="${HERMES_BIN:-}"
  if [ -f "${ROOT}/hermes.env" ]; then
    # Installer-written KEY=value only. No bcrypt hashes.
    set -a
    # shellcheck disable=SC1091
    . "${ROOT}/hermes.env"
    set +a
  fi
}

hermes_dashboard_expected() {
  [ -n "${HERMES_BIN:-}" ] && [ -x "${HERMES_BIN}" ]
}

dashboard_is_healthy() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl is-active --quiet hermes-dashboard.service || return 1
  command -v curl >/dev/null 2>&1 || return 0
  local code
  code="$(curl -sS --max-time 2 -o /tmp/hermes-dash-update.body \
    -w '%{http_code}' -H 'Host: 127.0.0.1:9119' \
    http://127.0.0.1:9119/ 2>/dev/null || true)"
  if grep -qi 'Frontend not built' /tmp/hermes-dash-update.body 2>/dev/null; then
    return 1
  fi
  [ -n "$code" ] && [ "$code" != "000" ]
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
    load_hermes_env
    if [ "$DRY" != "1" ] && hermes_dashboard_expected && ! dashboard_is_healthy; then
      say "HEAD current but hermes-dashboard is not serving UI — reinstalling"
    else
      say "already at $before — nothing to do"
      exit 0
    fi
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
  load_hermes_env
  if hermes_dashboard_expected; then
    if dashboard_is_healthy; then
      say "ok: hermes-dashboard.service"
    else
      say "error: hermes-dashboard.service is not serving UI after update"
      systemctl status --no-pager --lines 20 hermes-dashboard.service || true
      journalctl -u hermes-dashboard.service --no-pager -n 80 || true
      fail=1
    fi
  fi
  [ "$fail" -eq 0 ] || exit 1
fi

say "after:  $(run_git rev-parse HEAD)"
