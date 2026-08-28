#!/bin/bash
# Radar-facing Hermes entrypoint. Always refresh ACLs before/after so a
# Hermes `chmod 700 ~/.hermes` cannot leave the next publish-check unable
# to exec. HERMES_BIN in hermes.env should point here; the real binary is
# HERMES_REAL_BIN (set by install.sh).
set -euo pipefail
ROOT="${ROOT:-/opt/founder-radar}"
ACL="${RADAR_HERMES_ACL:-$ROOT/app/deploy/hermes-acl.sh}"
REAL="${HERMES_REAL_BIN:-}"

if [[ -z "$REAL" ]]; then
  # Fall back to the operator install if env was not written yet.
  if [[ -n "${HERMES_HOME:-}" && -x "${HERMES_HOME}/.local/bin/hermes" ]]; then
    REAL="${HERMES_HOME}/.local/bin/hermes"
  elif [[ -x /home/aryan/.local/bin/hermes ]]; then
    REAL=/home/aryan/.local/bin/hermes
  else
    echo "hermes-wrap: HERMES_REAL_BIN not set and no hermes binary found" >&2
    exit 127
  fi
fi

if [[ -x "$ACL" ]]; then
  "$ACL" || true
fi
cleanup() {
  if [[ -x "$ACL" ]]; then
    "$ACL" || true
  fi
}
trap cleanup EXIT

exec "$REAL" "$@"
