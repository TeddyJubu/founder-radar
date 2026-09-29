#!/bin/bash
# /usr/local/bin/founder-radar — Hermes-safe CLI entrypoint.
#
# The review DB and .env are owned by `radar` (mode 0600). Hermes gateway
# runs as the operator (usually `aryan`). Calling the venv binary directly
# fails with PermissionError on .env. This wrapper re-execs as `radar` with
# both env files loaded so Today QA / publish / decide work from Telegram.
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
APP_USER="${APP_USER:-radar}"
REAL="${FOUNDER_RADAR_BIN:-$ROOT/venv/bin/founder-radar}"

if [ ! -x "$REAL" ]; then
  echo "founder-radar binary missing: $REAL" >&2
  exit 127
fi

load_env_file() {
  # Parse KEY=VALUE lines like systemd EnvironmentFile — do NOT shell-source.
  # bcrypt hashes in RADAR_WEB_PASS_HASH contain `$2a$…` which breaks `set -u`.
  local file="$1"
  [[ -f "$file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" == export\ * ]]; then
      line="${line#export }"
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$file"
}

run_loaded() {
  load_env_file "$ROOT/.env"
  load_env_file "$ROOT/hermes.env"
  cd "$ROOT/app"
  exec "$REAL" "$@"
}

if [ "$(id -un)" = "$APP_USER" ]; then
  run_loaded "$@"
fi

# Passwordless sudo is expected for the Hermes operator (see sudoers).
exec sudo -n -H -u "$APP_USER" -- "$0" "$@"
