#!/usr/bin/env bash
#
# Machine gate after Hermes review APPROVE + test PASS.
#
#   sudo bash /opt/founder-radar/app/deploy/hermes-ship.sh <worktree>
#
# Re-runs pytest against the worktree, refuses a radar/score/ (or secrets)
# diff, fast-forwards live main, tries to push, reinstalls, removes the
# worktree. Non-zero means the live checkout was not changed.
#
# The person who asked for the fix is not an engineer. This script is the
# "are we sure?" door so a confident model cannot merge a rejected patch.
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_DIR="${APP_DIR:-$ROOT/app}"
APP_USER="${APP_USER:-radar}"
PYTHON="${RADAR_PYTHON:-$ROOT/venv/bin/python}"
PIP="${RADAR_PIP:-$ROOT/venv/bin/pip}"
DRY="${RADAR_SHIP_DRY_RUN:-0}"
SKIP_INSTALL="${RADAR_SHIP_SKIP_INSTALL:-0}"
LOG="${RADAR_SHIP_LOG:-$ROOT/logs/ship.log}"

WORKTREE="${1:-${RADAR_SHIP_WORKTREE:-}}"
if [ -z "$WORKTREE" ]; then
  echo "usage: hermes-ship.sh <worktree>" >&2
  exit 2
fi
WORKTREE="$(cd "$WORKTREE" && pwd)"

mkdir -p "$(dirname "$LOG")" "$ROOT/logs"
touch "$LOG"
say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) return 0 ;;
    *) return 1 ;;
  esac
}

die() { say "refusing to ship: $*"; exit 1; }

run_git_app() {
  if [ "$(id -u)" -eq 0 ]; then
    sudo -H -u "$APP_USER" git -C "$APP_DIR" "$@"
  else
    git -C "$APP_DIR" "$@"
  fi
}

run_git_wt() {
  if [ "$(id -u)" -eq 0 ]; then
    sudo -H -u "$APP_USER" git -C "$WORKTREE" "$@"
  else
    git -C "$WORKTREE" "$@"
  fi
}

# ------------------------------------------------------------------ guards

# Share the update-from-main lock so a timer pull cannot race a ship.
LOCK="${RADAR_SHIP_LOCK:-${RADAR_UPDATE_LOCK:-/run/founder-radar-update.lock}}"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  die "another update or ship is already running"
fi

[ "$(run_git_wt rev-parse --is-inside-work-tree)" = "true" ] \
  || die "not a git worktree: $WORKTREE"
[ -d "$APP_DIR/.git" ] || [ -f "$APP_DIR/.git" ] || die "no live checkout at $APP_DIR"

branch="$(run_git_wt rev-parse --abbrev-ref HEAD)"
case "$branch" in
  hermes/fix-*|hermes/fix[0-9]*) ;;
  *) die "worktree branch must be hermes/fix-*, not $branch" ;;
esac

if ! run_git_wt diff --quiet || ! run_git_wt diff --cached --quiet; then
  die "worktree is dirty — commit or discard before shipping"
fi

base="$(run_git_app rev-parse HEAD)"
ahead_live="$(run_git_wt rev-list --count "${base}..HEAD")"
[ "$ahead_live" -gt 0 ] || die "worktree has no commits ahead of live main"

changed="$(run_git_wt diff --name-only "$base"...HEAD)"
[ -n "$changed" ] || die "empty diff"

if echo "$changed" | grep -Eq '(^|/)radar/score/'; then
  die "diff touches radar/score/"
fi
if echo "$changed" | grep -Eiq '(^|/)(\.env$|google-sa\.json|service-account.*\.json)$'; then
  die "diff looks like a secret file"
fi

if command -v systemctl >/dev/null 2>&1 && [ -z "${RADAR_HERMES_SCAN_ACTIVE:-}" ]; then
  if systemctl is-active --quiet founder-radar.service; then
    die "daily scan is running — try again when it finishes"
  fi
elif [ "${RADAR_HERMES_SCAN_ACTIVE:-}" = "1" ]; then
  die "daily scan is running — try again when it finishes"
fi

# ------------------------------------------------------------------ tests

if [ ! -x "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  else
    die "no python interpreter (looked for $RADAR_PYTHON / python3)"
  fi
fi

if ! "$PYTHON" -m pytest --version >/dev/null 2>&1; then
  say "pytest missing — installing into the venv"
  if [ -x "$PIP" ]; then
    "$PIP" install --quiet pytest
  else
    die "pytest is not installed and no venv pip to add it"
  fi
fi

# Tests must run from the worktree. PYTHONPATH beats the live editable install.
say "pytest against worktree $WORKTREE"
set +e
(
  cd "$WORKTREE"
  PYTHONPATH="$WORKTREE" "$PYTHON" -m pytest -q --tb=line
)
pytest_rc=$?
set -e
[ "$pytest_rc" -eq 0 ] || die "pytest exited $pytest_rc"

if truthy "$DRY"; then
  say "dry-run: would fast-forward $APP_DIR main with $branch"
  say "changed:"$'\n'"$changed"
  exit 0
fi

# ------------------------------------------------------------------ merge

live_before="$(run_git_app rev-parse HEAD)"
run_git_app checkout main
run_git_app merge --ff-only "$branch" || die "fast-forward into live main failed"
live_after="$(run_git_app rev-parse HEAD)"
say "live $live_before -> $live_after"

set +e
run_git_app push origin main
push_rc=$?
set -e
if [ "$push_rc" -ne 0 ]; then
  say "git push origin main failed (local is ahead). update-from-main.sh will keep this checkout"
fi

if ! truthy "$SKIP_INSTALL"; then
  if [ "$(id -u)" -eq 0 ] && [ -x "$APP_DIR/deploy/install.sh" ]; then
    bash "$APP_DIR/deploy/install.sh"
  else
    say "skipping install.sh (not root or script missing)"
    if [ -x "$PIP" ]; then
      "$PIP" install --quiet -e "$APP_DIR" || true
    fi
  fi
fi

# ------------------------------------------------------------------ cleanup

set +e
run_git_app worktree remove --force "$WORKTREE"
run_git_app branch -d "$branch"
set -e
say "shipped $branch"
exit 0
