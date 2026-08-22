#!/usr/bin/env bash
#
# Diagnose Founder Radar, apply safe remediations, then (if still a code-level
# bug) hand the box to the Hermes agent that already lives on this VPS.
#
# Invoked by founder-radar-repair.service:
#   - 09:05 Europe/London (after the heartbeat)
#   - OnFailure= of founder-radar.service (fatal scan, exit 2)
#
# Hermes is optional. If the binary is missing we still run
# `founder-radar repair --apply` and exit 0 — a missing chat agent must not
# fail the timer, same rule as digest delivery (FR-8.5).
#
# Never prints secrets. Never force-pushes. Never starts Hermes while the
# daily scan is active (NFR-2: 4 GB, scan MemoryMax=1G).
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_USER="${APP_USER:-radar}"
APP_DIR="${APP_DIR:-$ROOT/app}"
LOG="${RADAR_REPAIR_LOG:-$ROOT/logs/repair.log}"
LOCK="${RADAR_REPAIR_LOCK:-/run/founder-radar-repair.lock}"
REQUEST="${RADAR_HERMES_REQUEST:-$ROOT/logs/hermes-repair.requested}"
STAMP="${RADAR_HERMES_STAMP:-$ROOT/logs/hermes-repair.last}"
JSON_OUT="${RADAR_REPAIR_JSON:-$ROOT/logs/repair-last.json}"
COOLDOWN_SEC="${RADAR_HERMES_COOLDOWN_SEC:-21600}"
DRY="${RADAR_HERMES_DRY_RUN:-0}"
FORCE="${RADAR_HERMES_FORCE:-0}"
CLI="${RADAR_CLI:-$ROOT/venv/bin/founder-radar}"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$ROOT/hermes.env}"

# Optional, written by install.sh: HERMES_USER + HERMES_HOME only.
if [ -f "$HERMES_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$HERMES_ENV_FILE"
fi
HERMES_USER="${HERMES_USER:-}"
HERMES_HOME="${HERMES_HOME:-}"

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")" "$ROOT/logs"
touch "$LOG"
if [ "$(id -u)" -eq 0 ]; then
  chown "${APP_USER}:${APP_USER}" "$LOG" 2>/dev/null || true
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another founder-radar repair is already running — skipping" | tee -a "$LOG"
  exit 0
fi

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) return 0 ;;
    *) return 1 ;;
  esac
}

scan_running() {
  if [ "${RADAR_HERMES_SCAN_ACTIVE:-}" = "1" ]; then return 0; fi
  if [ "${RADAR_HERMES_SCAN_ACTIVE:-}" = "0" ]; then return 1; fi
  command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet founder-radar.service
}

run_cli() {
  cd "$ROOT"
  if [ "$(id -u)" -eq 0 ]; then
    sudo -H -u "$APP_USER" "$CLI" "$@"
  else
    "$CLI" "$@"
  fi
}

find_hermes() {
  if [ -n "${HERMES_BIN:-}" ] && [ -x "${HERMES_BIN}" ]; then
    printf '%s\n' "$HERMES_BIN"
    return 0
  fi
  if command -v hermes >/dev/null 2>&1; then
    command -v hermes
    return 0
  fi
  local candidate
  for candidate in \
      "${HERMES_HOME:-}/.local/bin/hermes" \
      /usr/local/bin/hermes \
      /usr/bin/hermes
  do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

within_cooldown() {
  if truthy "$FORCE"; then return 1; fi
  if [ ! -f "$STAMP" ]; then return 1; fi
  local last now
  last="$(stat -c %Y "$STAMP" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  [ $((now - last)) -lt "$COOLDOWN_SEC" ]
}

# ------------------------------------------------------------------ 1. ops

say "repair start (dry=$DRY monitor=${MONITOR_UNIT:-none})"

if truthy "$DRY"; then
  say "dry-run: would run $CLI --json repair --apply --auto"
else
  # Repair itself must not abort this script: we still want the JSON / Hermes
  # hand-off when the box is unhealthy (exit 1).
  set +e
  run_cli --json repair --apply --auto --request-file "$REQUEST" >"$JSON_OUT"
  repair_rc=$?
  set -e
  if [ "$(id -u)" -eq 0 ]; then
    chown "${APP_USER}:${APP_USER}" "$JSON_OUT" 2>/dev/null || true
  fi
  say "founder-radar repair exited $repair_rc (json $JSON_OUT)"
fi

needs_agent=0
if [ -f "$REQUEST" ]; then
  needs_agent=1
  say "Hermes request present at $REQUEST"
fi

# A fatal scan (OnFailure) always wants the agent, even if repair saw nothing
# to hand off — the traceback is the bug.
if [ -n "${MONITOR_UNIT:-}" ]; then
  needs_agent=1
  say "OnFailure from ${MONITOR_UNIT} (exit ${MONITOR_EXIT_STATUS:-?})"
fi

if truthy "$FORCE"; then
  needs_agent=1
fi

json_stale=0
if [ -f "$JSON_OUT" ] && grep -q '"stale": true' "$JSON_OUT" 2>/dev/null; then
  json_stale=1
fi

# ------------------------------------------------------------------ 2. hermes

if [ "$needs_agent" -eq 0 ]; then
  # Operational-only: a stale box with nothing for Hermes to patch should
  # still get a scan. Never from OnFailure of that scan (loop).
  if [ "$json_stale" -eq 1 ] && [ -z "${MONITOR_UNIT:-}" ] && ! scan_running && ! truthy "$DRY"; then
    if command -v systemctl >/dev/null 2>&1; then
      say "starting founder-radar.service (stale, no code fix)"
      systemctl start founder-radar.service || say "could not start founder-radar.service"
    fi
  fi
  say "no code-level fix required — done"
  exit 0
fi

if scan_running; then
  say "daily scan is running — not starting Hermes (memory). will retry next cycle"
  exit 0
fi

if within_cooldown; then
  say "Hermes repair already ran within ${COOLDOWN_SEC}s — skipping to avoid a loop"
  exit 0
fi

hermes_bin=""
if hermes_bin="$(find_hermes)"; then
  say "hermes binary: $hermes_bin"
else
  say "hermes not installed — operational remediations only. install Hermes, then copy"
  say "  $APP_DIR/hermes/skills/founder-radar/"
  say "  to ~/.hermes/skills/founder-radar/"
  exit 0
fi

if truthy "$DRY"; then
  say "dry-run: would invoke $hermes_bin -s founder-radar"
  exit 0
fi

prompt="$(mktemp "$ROOT/logs/hermes-repair-prompt.XXXXXX")"
trap 'rm -f "$prompt"' EXIT

{
  printf '%s\n' \
    "You are repairing UK Founder Radar on this VPS for a non-technical person." \
    "Follow the founder-radar skill, then references/workflow.md." \
    "Talk to them in short plain English. Never ask them to run a command." \
    "First message: I'm on it. I'll message you when it's done." \
    "" \
    "Rules:" \
    "- Ops: founder-radar repair --apply first." \
    "- Code: worktree under /opt/founder-radar/worktrees, never live main." \
    "- Review sub-agent, then test sub-agent, one at a time." \
    "- Ship only with sudo bash deploy/hermes-ship.sh after APPROVE and PASS." \
    "- Never edit radar/score/, never print .env, never git push --force." \
    "- When done, one Telegram sentence: fixed and live, or could not finish." \
    "" \
    "Diagnosis JSON (may be empty):" \
    ""
  if [ -f "$REQUEST" ]; then
    cat "$REQUEST"
  else
    printf '%s\n' "{\"source\":\"OnFailure\",\"unit\":\"${MONITOR_UNIT:-unknown}\"}"
  fi
} > "$prompt"
chmod 644 "$prompt" 2>/dev/null || true

# Unattended: --yolo / HERMES_YOLO_MODE so the agent is not blocked on a
# TTY that does not exist. --query-file so the JSON is not shell-parsed.
set +e
if [ "$(id -u)" -eq 0 ] && [ -n "$HERMES_USER" ] && [ "$HERMES_USER" != "root" ]; then
  sudo -H -u "$HERMES_USER" env HOME="${HERMES_HOME:-/home/$HERMES_USER}" \
    HERMES_YOLO_MODE=1 \
    "$hermes_bin" chat --yolo -s founder-radar -t terminal \
    --query-file "$prompt" >>"$LOG" 2>&1
  hermes_rc=$?
else
  env HOME="${HERMES_HOME:-${HOME:-/root}}" HERMES_YOLO_MODE=1 \
    "$hermes_bin" chat --yolo -s founder-radar -t terminal \
    --query-file "$prompt" >>"$LOG" 2>&1
  hermes_rc=$?
fi
set -e

date -u +%Y-%m-%dT%H:%M:%SZ > "$STAMP"
if [ "$(id -u)" -eq 0 ]; then
  chown "${APP_USER}:${APP_USER}" "$STAMP" 2>/dev/null || true
fi
say "hermes exited $hermes_rc"
exit 0
