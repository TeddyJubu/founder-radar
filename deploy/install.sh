#!/usr/bin/env bash
#
# Install UK Founder Radar on a fresh Ubuntu 24.04 box (08-deployment §2, §4).
#
#   sudo bash deploy/install.sh
#
# Idempotent: safe to re-run after a `git pull`. It never overwrites an existing
# environment file, and it never prints the value of a secret — not to stdout,
# not to the journal, not on failure. The only thing it will ever say about a
# credential is whether it is present and what mode the file has.
set -euo pipefail

APP_USER="${APP_USER:-radar}"
ROOT="${ROOT:-/opt/founder-radar}"
APP_DIR="$ROOT/app"
VENV="$ROOT/venv"
ENV_FILE="$ROOT/.env"
SECRETS_DIR="$ROOT/secrets"
SA_FILE="$SECRETS_DIR/google-sa.json"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
LOGROTATE_DIR="${LOGROTATE_DIR:-/etc/logrotate.d}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root (try: sudo bash deploy/install.sh)" >&2
  exit 1
fi

# ---------------------------------------------------------------- 1. account

say "service account and directories"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  adduser --system --group --home "$ROOT" "$APP_USER"
fi

install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$ROOT" "$ROOT/data" "$ROOT/logs" "$ROOT/backups"
# 0700 on secrets: the directory listing is itself information.
install -d -o "$APP_USER" -g "$APP_USER" -m 700 "$SECRETS_DIR"

# ---------------------------------------------------------------- 2. runtime

say "system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git sqlite3 logrotate

# The clock the timers run against. OnCalendar carries an explicit Europe/London
# suffix as well, so this is belt and braces rather than the only defence.
timedatectl set-timezone Europe/London || true

if [ ! -d "$APP_DIR/.git" ] && [ ! -f "$APP_DIR/pyproject.toml" ]; then
  echo "no checkout at $APP_DIR — clone the repository there first" >&2
  exit 1
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "python environment"
# pip as $APP_USER inherits the caller's cwd. Running install.sh from /root
# then dies with PermissionError on an editable path hook under root's home.
# Always install from the checkout, with the service user's HOME.
cd "$APP_DIR"
if [ ! -x "$VENV/bin/python" ]; then
  sudo -H -u "$APP_USER" python3 -m venv "$VENV"
fi
sudo -H -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -H -u "$APP_USER" "$VENV/bin/pip" install --quiet -e .
ln -sf "$VENV/bin/founder-radar" /usr/local/bin/founder-radar
cd "$ROOT"

# ---------------------------------------------------------------- 3. secrets
#
# One environment file, mode 0600, owned by the service user. Created empty
# from the checked-in template the first time and then left alone forever — an
# installer that rewrites credentials on every run is an installer that
# eventually destroys them.

say "secrets"
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    install -o "$APP_USER" -g "$APP_USER" -m 600 "$APP_DIR/.env.example" "$ENV_FILE"
  else
    install -o "$APP_USER" -g "$APP_USER" -m 600 /dev/null "$ENV_FILE"
  fi
  {
    echo ""
    echo "RADAR_DB=$ROOT/data/radar.db"
    echo "GOOGLE_SA_JSON=$SA_FILE"
    echo "TZ=Europe/London"
  } >> "$ENV_FILE"
  say "created $ENV_FILE — fill in the blanks with an editor, then re-run"
fi

# Enforce the mode on every run: a hand-edit with a careless umask is the
# realistic way 0600 becomes 0644 six months from now.
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

if [ -f "$SA_FILE" ]; then
  chown "$APP_USER:$APP_USER" "$SA_FILE"
  chmod 600 "$SA_FILE"
else
  say "MISSING $SA_FILE"
  say "  upload the rotated Google service-account JSON there (08-deployment §1),"
  say "  then: chown $APP_USER:$APP_USER $SA_FILE && chmod 600 $SA_FILE"
fi

# Presence and mode only. Values are never read, echoed, or logged here.
say "environment file mode: $(stat -c '%a %U:%G' "$ENV_FILE")"

# ------------------------------------------------------------------ 4. units

say "systemd units"
for unit in founder-radar.service founder-radar.timer \
            founder-radar-heartbeat.service founder-radar-heartbeat.timer \
            founder-radar-backup.service founder-radar-backup.timer \
            founder-radar-web.service \
            founder-radar-update.service founder-radar-update.timer; do
  install -m 644 "$HERE/$unit" "$UNIT_DIR/$unit"
done
chmod 755 "$HERE/backup.sh" "$HERE/update-from-main.sh"

install -m 644 "$HERE/logrotate.founder-radar" "$LOGROTATE_DIR/founder-radar"

systemctl daemon-reload
systemctl enable --now founder-radar.timer
systemctl enable --now founder-radar-heartbeat.timer
systemctl enable --now founder-radar-backup.timer
systemctl enable --now founder-radar-update.timer

# ------------------------------------------------------------- 4b. the review
#
# The web surface is opt-in and refuses to start public without a password.
# `founder-radar-web.service` binds 127.0.0.1, so until Caddy is configured the
# review queue is reachable only from the box itself — which is the safe
# default, not a broken state.

say "review surface"
systemctl enable --now founder-radar-web.service

# shellcheck disable=SC1090
. "$ENV_FILE" 2>/dev/null || true
if [ -n "${RADAR_WEB_DOMAIN:-}" ] && [ -n "${RADAR_WEB_PASS_HASH:-}" ]; then
  if ! command -v caddy >/dev/null 2>&1; then
    say "installing caddy"
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] \
https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy
  fi
  install -m 644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
  mkdir -p /etc/systemd/system/caddy.service.d
  printf '[Service]\nEnvironmentFile=%s\n' "$ENV_FILE" \
    > /etc/systemd/system/caddy.service.d/override.conf
  systemctl daemon-reload
  systemctl enable --now caddy
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  say "review surface live at https://$RADAR_WEB_DOMAIN (password required)"
else
  say "RADAR_WEB_DOMAIN / RADAR_WEB_PASS_HASH not set in $ENV_FILE"
  say "  the review surface is running on 127.0.0.1:8787 and is NOT published."
  say "  to publish it, generate a hash and re-run:"
  say "    caddy hash-password --plaintext 'choose-a-password'"
  say "  then add RADAR_WEB_DOMAIN, RADAR_WEB_USER and RADAR_WEB_PASS_HASH."
fi

# ------------------------------------------------------------------ 5. hermes
#
# The entire Hermes footprint: one skill file. Deleting it and one unit costs
# the system nothing but the chat surface (02-architecture §3).

say "hermes skill"
HERMES_HOME="${HERMES_HOME:-$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)}"
if [ -d "$HERMES_HOME/.hermes" ]; then
  install -d "$HERMES_HOME/.hermes/skills/founder-radar"
  install -m 644 "$APP_DIR/hermes/skills/founder-radar/SKILL.md" \
    "$HERMES_HOME/.hermes/skills/founder-radar/SKILL.md"
  if [ -n "${SUDO_USER:-}" ]; then
    chown -R "$SUDO_USER" "$HERMES_HOME/.hermes/skills/founder-radar"
  fi
else
  say "no ~/.hermes yet — install Hermes, then copy"
  say "  $APP_DIR/hermes/skills/founder-radar/SKILL.md"
  say "  to ~/.hermes/skills/founder-radar/"
fi

# ------------------------------------------------------------------ 6. schema

say "database"
# Run from $ROOT, not the caller's cwd: the CLI loads .env from the working
# directory, and RADAR_DB=$ROOT/data/radar.db lives there. Run from anywhere
# else and migrate creates a shadow db under app/data/ that silently absorbs
# manual CLI runs while the timers write the real one.
cd "$ROOT"
sudo -u "$APP_USER" "$VENV/bin/founder-radar" db migrate

say "done. next:"
say "  sudo -u $APP_USER founder-radar doctor"
say "  systemctl list-timers 'founder-radar*'"
