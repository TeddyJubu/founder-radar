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
# This script runs as root and radar can write inside the operator's home, so
# it must never act on a symlink radar could have planted: `setfacl` and
# `chown` follow links, and "give radar rwx on <target>" would then apply to
# whatever the link points at.
if [[ -L "$H" || -L "$HOME_DIR" ]]; then
  echo "hermes-acl: $H or its parent is a symlink — refusing to act" >&2
  exit 1
fi

acl_user() {
  local path="$1"
  local user="$2"
  local mode="${3:-rwx}"
  [[ -e "$path" && ! -L "$path" ]] || return 0
  setfacl -m "u:${user}:${mode}" "$path" 2>/dev/null || true
  setfacl -m "m::rwx" "$path" 2>/dev/null || true
}

acl_tree() {
  local path="$1"
  local user="$2"
  local mode="${3:-rwx}"
  [[ -e "$path" && ! -L "$path" ]] || return 0
  # -P: physical walk — do not follow symlinks, neither the argument nor any
  # met inside the tree.
  setfacl -R -P -m "u:${user}:${mode}" "$path" 2>/dev/null || true
  setfacl -R -P -m "m::rwx" "$path" 2>/dev/null || true
  if [[ -d "$path" ]]; then
    setfacl -d -m "u:${user}:${mode}" "$path" 2>/dev/null || true
    setfacl -d -m "m::rwx" "$path" 2>/dev/null || true
    setfacl -d -m "g::r-x" "$path" 2>/dev/null || true
  fi
}

# --- Hermes home: operator owns it; radar gets access tree by tree --------
# radar may *enter* the operator's home but never create, rename or replace
# anything directly in it. Write access here would let a compromised radar
# (the web-facing service account) swap ~/.ssh/authorized_keys, ~/.bashrc or
# ~/.profile and log in as the operator, who has full sudo. It had exactly
# that: rwx on the home directory, default ACLs inherited by every new file,
# and rw on the SSH key file. Today QA needs only the trees granted below.
setfacl -k "$HOME_DIR" 2>/dev/null || true             # no inherited access
setfacl -m "u:${APP_USER}:--x" "$HOME_DIR" 2>/dev/null || true
setfacl -m "m::--x" "$HOME_DIR" 2>/dev/null || true
for login in .ssh .bashrc .profile .bash_profile .bash_login .bash_logout \
             .bash_history .zshrc .zprofile .zshenv .sudo_as_admin_successful; do
  login_path="$HOME_DIR/$login"
  [[ -e "$login_path" && ! -L "$login_path" ]] || continue
  setfacl -R -P -b "$login_path" 2>/dev/null || true    # drop every extended ACL
done
if [[ -d "$HOME_DIR/.ssh" && ! -L "$HOME_DIR/.ssh" ]]; then
  chmod 700 "$HOME_DIR/.ssh" 2>/dev/null || true
  [[ -f "$HOME_DIR/.ssh/authorized_keys" ]] && chmod 600 "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null || true
fi

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
    -exec chown -h "$HERMES_USER:$HERMES_USER" {} + 2>/dev/null || true
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
    chown -h "$HERMES_USER:$HERMES_USER" "$critical" 2>/dev/null || true
  done
fi

for path in \
  "$H/logs" \
  "$H/skills" \
  "$H/cache" \
  "$H/hermes-agent" \
  "$H/installs" \
  "$H/backups" \
  "$H/state" \
  "$H/sessions" \
  "$H/memories" \
  "$H/shared" \
  "$H/state.db" \
  "$H/state.db-wal" \
  "$H/state.db-shm" \
  "$H/config.yaml" \
  "$H/auth.json" \
  "$HOME_DIR/.local" \
  "$HOME_DIR/.local/bin" \
  "$HOME_DIR/.local/share" \
  "$HOME_DIR/logs"
do
  acl_tree "$path" "$APP_USER"
done

# Today QA uses the operator provider configuration; it only needs to read
# this file, unlike runtime backup/session trees.
acl_user "$H/.env" "$APP_USER" "r--"

if [[ -x "$H/hermes-agent/venv/bin/hermes" ]]; then
  acl_user "$H/hermes-agent/venv/bin/hermes" "$APP_USER"
fi
if [[ -x "$HOME_DIR/.local/bin/hermes" ]]; then
  acl_user "$HOME_DIR/.local/bin/hermes" "$APP_USER"
fi

# --- Founder Radar tree: Hermes operator may edit / operate --------------
# Secrets stay 0600 radar-only (.env, secrets/). Ops go through the
# founder-radar wrapper (sudo -u radar). Only mutable data and logs are shared.
# app/venv are protected root inputs: never grant writable ACLs on them.
if [[ "$(id -u)" -eq 0 ]] && [[ -d "$ROOT" ]]; then
  for path in \
    "$ROOT/data" \
    "$ROOT/logs" \
    "$ROOT/guide" \
    "$ROOT/backups"
  do
    [[ -e "$path" ]] || continue
    acl_tree "$path" "$HERMES_USER"
  done
  # Readable hermes.env is fine; never open .env / secrets to the world.
  if [[ -f "$ROOT/hermes.env" ]]; then
    acl_user "$ROOT/hermes.env" "$HERMES_USER" "r--"
  fi
fi
