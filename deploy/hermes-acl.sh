#!/bin/bash
# Keep radar ↔ Hermes operator able to share the Hermes home and the
# founder-radar checkout.
#
# Two failure modes this fixes:
# 1. Hermes `chmod 700 ~/.hermes` clears the ACL mask — radar cannot run
#    Today QA under HERMES_HOME.
# 2. Today QA (User=radar) creates/overwrites files in the operator's
#    ~/.hermes (auth.json, caches). Gateway then fails as the operator
#    with PermissionError — Hermes cannot make any changes.
#
# Prefer root (sudo -n or systemd `+` prefix).
set -euo pipefail

APP_USER="${APP_USER:-radar}"
HERMES_USER="${HERMES_USER:-aryan}"
ROOT="${ROOT:-/opt/founder-radar}"
SELF="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"

# Re-exec as root when we are not root — radar cannot setfacl after mask::---.
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n -u root "$SELF" "$@"; then
    exit 0
  fi
  # Last resort: try as Hermes owner (can chmod their own tree).
  if [[ "$(id -un)" != "$HERMES_USER" ]] \
      && command -v sudo >/dev/null 2>&1 \
      && sudo -n -u "$HERMES_USER" env APP_USER="$APP_USER" HERMES_USER="$HERMES_USER" ROOT="$ROOT" "$SELF" "$@"; then
    exit 0
  fi
  # Best-effort without privilege (works only when mask is still intact).
fi

HOME_DIR=$(getent passwd "$HERMES_USER" | cut -d: -f6)
H="$HOME_DIR/.hermes"
test -d "$H" || exit 0

acl_user() {
  local path="$1"
  local user="$2"
  local mode="${3:-rwx}"
  [[ -e "$path" ]] || return 0
  setfacl -m "u:${user}:${mode}" "$path" 2>/dev/null || true
  setfacl -m "m::rwx" "$path" 2>/dev/null || true
}

acl_tree() {
  local path="$1"
  local user="$2"
  local mode="${3:-rwx}"
  [[ -e "$path" ]] || return 0
  setfacl -R -m "u:${user}:${mode}" "$path" 2>/dev/null || true
  setfacl -R -m "m::rwx" "$path" 2>/dev/null || true
  if [[ -d "$path" ]]; then
    setfacl -d -m "u:${user}:${mode}" "$path" 2>/dev/null || true
    setfacl -d -m "m::rwx" "$path" 2>/dev/null || true
    setfacl -d -m "g::r-x" "$path" 2>/dev/null || true
  fi
}

# --- Hermes home: operator owns it; radar may read/write via ACL ----------
setfacl -m "u:${APP_USER}:rwx" "$HOME_DIR" 2>/dev/null || true

# Clear the empty mask Hermes leaves behind.
setfacl -m "g::r-x" "$H" 2>/dev/null || true
acl_user "$H" "$APP_USER"
setfacl -d -m "g::r-x" "$H" 2>/dev/null || true
setfacl -d -m "u:${APP_USER}:rwx" "$H" 2>/dev/null || true
setfacl -d -m "m::rwx" "$H" 2>/dev/null || true

mkdir -p \
  "$H/logs" "$H/logs/curator" \
  "$H/skills" \
  "$H/cache" \
  "$HOME_DIR/.local/share" \
  "$HOME_DIR/logs" \
  2>/dev/null || true

# Radar-created files under the operator home break the gateway. Hand them
# back to HERMES_USER, then re-apply the radar ACL so Today QA still works.
if [[ "$(id -u)" -eq 0 ]] && id -u "$HERMES_USER" >/dev/null 2>&1; then
  find "$H" \( -user "$APP_USER" -o ! -user "$HERMES_USER" \) \
    -exec chown "$HERMES_USER:$HERMES_USER" {} + 2>/dev/null || true
  # Critical paths — always operator-owned even if find missed them.
  for critical in \
    "$H/auth.json" \
    "$H/config.yaml" \
    "$H/.env" \
    "$H/SOUL.md" \
    "$H/state.db" \
    "$H/.skills_prompt_snapshot.json"
  do
    [[ -e "$critical" ]] || continue
    chown "$HERMES_USER:$HERMES_USER" "$critical" 2>/dev/null || true
  done
fi

for path in \
  "$H/logs" \
  "$H/skills" \
  "$H/cache" \
  "$H/hermes-agent" \
  "$H/config.yaml" \
  "$H/auth.json" \
  "$HOME_DIR/.local" \
  "$HOME_DIR/.local/bin" \
  "$HOME_DIR/.local/share" \
  "$HOME_DIR/logs"
do
  acl_tree "$path" "$APP_USER"
done

if [[ -x "$H/hermes-agent/venv/bin/hermes" ]]; then
  acl_user "$H/hermes-agent/venv/bin/hermes" "$APP_USER"
fi
if [[ -x "$HOME_DIR/.local/bin/hermes" ]]; then
  acl_user "$HOME_DIR/.local/bin/hermes" "$APP_USER"
fi

# --- Founder Radar tree: Hermes operator may edit / operate --------------
# Secrets stay 0600 radar-only (.env, secrets/). Ops go through the
# founder-radar wrapper (sudo -u radar). Code + data + logs are shared.
if [[ "$(id -u)" -eq 0 ]] && [[ -d "$ROOT" ]]; then
  for path in \
    "$ROOT/app" \
    "$ROOT/data" \
    "$ROOT/logs" \
    "$ROOT/guide" \
    "$ROOT/backups" \
    "$ROOT/venv"
  do
    [[ -e "$path" ]] || continue
    acl_tree "$path" "$HERMES_USER"
  done
  # Readable hermes.env is fine; never open .env / secrets to the world.
  if [[ -f "$ROOT/hermes.env" ]]; then
    acl_user "$ROOT/hermes.env" "$HERMES_USER" "r--"
  fi
fi
