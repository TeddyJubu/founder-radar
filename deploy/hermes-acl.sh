#!/bin/bash
# Keep radar able to exec/write Hermes under the operator home.
# Hermes often `chmod 700 ~/.hermes`, which clears the ACL mask — after that
# only root/owner can restore it. Prefer root (sudo -n or systemd `+` prefix).
set -euo pipefail

APP_USER="${APP_USER:-radar}"
HERMES_USER="${HERMES_USER:-aryan}"
SELF="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"

# Re-exec as root when we are not root — radar cannot setfacl after mask::---.
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n -u root "$SELF" "$@"; then
    exit 0
  fi
  # Last resort: try as Hermes owner (can chmod their own tree).
  if [[ "$(id -un)" != "$HERMES_USER" ]] \
      && command -v sudo >/dev/null 2>&1 \
      && sudo -n -u "$HERMES_USER" env APP_USER="$APP_USER" HERMES_USER="$HERMES_USER" "$SELF" "$@"; then
    exit 0
  fi
  # Best-effort without privilege (works only when mask is still intact).
fi

HOME_DIR=$(getent passwd "$HERMES_USER" | cut -d: -f6)
H="$HOME_DIR/.hermes"
test -d "$H" || exit 0

acl_user() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  setfacl -m "u:${APP_USER}:rwx" "$path" 2>/dev/null || true
  setfacl -m "m::rwx" "$path" 2>/dev/null || true
}

acl_tree() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  setfacl -R -m "u:${APP_USER}:rwx" "$path" 2>/dev/null || true
  setfacl -R -m "m::rwx" "$path" 2>/dev/null || true
  if [[ -d "$path" ]]; then
    setfacl -d -m "u:${APP_USER}:rwx" "$path" 2>/dev/null || true
    setfacl -d -m "m::rwx" "$path" 2>/dev/null || true
    setfacl -d -m "g::r-x" "$path" 2>/dev/null || true
  fi
}

setfacl -m "u:${APP_USER}:rwx" "$HOME_DIR" 2>/dev/null || true

# Clear the empty mask Hermes leaves behind.
setfacl -m "g::r-x" "$H" 2>/dev/null || true
acl_user "$H"
setfacl -d -m "g::r-x" "$H" 2>/dev/null || true
setfacl -d -m "u:${APP_USER}:rwx" "$H" 2>/dev/null || true
setfacl -d -m "m::rwx" "$H" 2>/dev/null || true

mkdir -p \
  "$H/logs" "$H/logs/curator" \
  "$H/skills" \
  "$HOME_DIR/.local/share" \
  "$HOME_DIR/logs" \
  2>/dev/null || true

for path in \
  "$H/logs" \
  "$H/skills" \
  "$H/hermes-agent" \
  "$H/config.yaml" \
  "$H/auth.json" \
  "$HOME_DIR/.local" \
  "$HOME_DIR/.local/bin" \
  "$HOME_DIR/.local/share" \
  "$HOME_DIR/logs"
do
  acl_tree "$path"
done

if [[ -x "$H/hermes-agent/venv/bin/hermes" ]]; then
  acl_user "$H/hermes-agent/venv/bin/hermes"
fi
if [[ -x "$HOME_DIR/.local/bin/hermes" ]]; then
  acl_user "$HOME_DIR/.local/bin/hermes"
fi
