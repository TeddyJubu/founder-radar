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
LOG="${RADAR_UPDATE_LOG:-/var/log/founder-radar-update.log}"
DRY="${RADAR_UPDATE_DRY_RUN:-0}"
FORCE="${RADAR_UPDATE_FORCE_RESCORE:-0}"
ALLOW_NONROOT="${RADAR_UPDATE_ALLOW_NONROOT:-0}"
#: Written before install.sh, removed only when the whole update succeeded. Root-
#: owned and outside the app tree, so the service account cannot plant or clear it.
PENDING="${RADAR_UPDATE_PENDING:-/var/lib/founder-radar-update/pending}"

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

# Root must never load Git configuration, hooks, or deployment code writable by
# the service account. Check the whole checkout and each containing directory.
if [ "$(id -u)" -eq 0 ]; then
  parent="$APP_DIR"
  while :; do
    if [ -L "$parent" ] || [ "$(stat -c %u "$parent")" != 0 ] || \
        [ -n "$(find "$parent" -maxdepth 0 -perm /022 -print)" ]; then
      echo "unsafe root update input: $parent" >&2; exit 1
    fi
    [ "$parent" = / ] && break
    parent="$(dirname "$parent")"
  done
  if [ -n "$(find "$APP_DIR" \( -type l -o ! -user root -o -perm /022 \) -print -quit)" ]; then
    echo "unsafe root update input: checkout ownership or permissions" >&2; exit 1
  fi
fi

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")"
touch "$LOG"
if [ "$(id -u)" -eq 0 ]; then
  chown root:root "$LOG"
  chmod 600 "$LOG"
fi

say() {
  # Under systemd the unit already appends our stdout to $LOG, so tee would
  # print every line twice. Manual runs still need the tee.
  if [ -n "${INVOCATION_ID:-}" ]; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
  else
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
  fi
}

# Without flock the test below fails for the wrong reason and reads as "another
# update is running" — the timer would then skip every cycle, silently, for ever.
if ! command -v flock >/dev/null 2>&1; then
  echo "flock (util-linux) is required" >&2
  exit 1
fi
exec 9>"$LOCK"
if ! flock -n 9; then
  say "another founder-radar update is already running — skipping"
  exit 0
fi

run_git() {
  if [ "$(id -u)" -eq 0 ]; then
    git -C "$APP_DIR" "$@"
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
  # Type=oneshot stays "activating" throughout both ExecStart commands.
  # is-active returns nonzero for that state even with a live scan/publisher.
  if ! scan_state="$(systemctl show --property=ActiveState --value founder-radar.service 2>/dev/null)"; then
    say "cannot read daily scan state — postponing update"
    exit 1
  fi
  case "$scan_state" in
    active|activating|reloading|deactivating)
      say "daily scan is running ($scan_state) — will try again next cycle"
      exit 0 ;;
    inactive|failed) ;;
    *) say "unknown daily scan state — postponing update"; exit 1 ;;
  esac
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
  elif [ -s "$PENDING" ]; then
    # The checkout moved but install.sh / rescore never finished (10 Oct 2026:
    # every later cycle said "nothing to do" and the failure stayed invisible).
    say "already at $before, but the update to $(head -c 40 "$PENDING") did not finish — finishing it"
    FORCE=1
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

mkdir -p "$(dirname "$PENDING")"
run_git rev-parse HEAD > "$PENDING"
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

rm -f "$PENDING"
say "after:  $(run_git rev-parse HEAD)"
