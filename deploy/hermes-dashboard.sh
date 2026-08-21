#!/usr/bin/env bash
#
# Start the Hermes Agent web dashboard on loopback.
#
# Invoked by hermes-dashboard.service. Binds 127.0.0.1 only. Caddy on
# the box terminates TLS and enforces the password; this wrapper does
# not touch Caddy.
#
# Never prints secrets. Never force-pushes. Never binds 0.0.0.0 (that
# engages Hermes's public auth gate and is the wrong shape behind Caddy).
set -euo pipefail

ROOT="${ROOT:-/opt/founder-radar}"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$ROOT/hermes.env}"

if [ -f "$HERMES_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$HERMES_ENV_FILE"
fi

if [ -n "${HERMES_HOME:-}" ]; then
  export HOME="$HERMES_HOME"
fi

HOST="${HERMES_DASHBOARD_HOST:-127.0.0.1}"
PORT="${HERMES_DASHBOARD_PORT:-9119}"

if [ -n "${HERMES_WEB_DOMAIN:-}" ] && [ -z "${HERMES_DASHBOARD_PUBLIC_URL:-}" ]; then
  export HERMES_DASHBOARD_PUBLIC_URL="https://${HERMES_WEB_DOMAIN}"
fi

resolve_bin() {
  if [ -n "${HERMES_BIN:-}" ] && [ -x "$HERMES_BIN" ]; then
    printf '%s\n' "$HERMES_BIN"
    return 0
  fi
  if [ -n "${HERMES_HOME:-}" ] && [ -x "${HERMES_HOME}/.local/bin/hermes" ]; then
    printf '%s\n' "${HERMES_HOME}/.local/bin/hermes"
    return 0
  fi
  command -v hermes 2>/dev/null || true
}

BIN="$(resolve_bin)"
if [ -z "$BIN" ]; then
  echo "hermes binary not found — cannot start the dashboard" >&2
  exit 1
fi

exec "$BIN" dashboard --host "$HOST" --port "$PORT" --no-open
