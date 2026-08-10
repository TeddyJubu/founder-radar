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
if [ ! -x "$VENV/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$VENV"
fi
sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet -e "$APP_DIR"
ln -sf "$VENV/bin/founder-radar" /usr/local/bin/founder-radar

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
            founder-radar-backup.service founder-radar-backup.timer; do
  install -m 644 "$HERE/$unit" "$UNIT_DIR/$unit"
done
chmod 755 "$HERE/backup.sh"

install -m 644 "$HERE/logrotate.founder-radar" "$LOGROTATE_DIR/founder-radar"

systemctl daemon-reload
systemctl enable --now founder-radar.timer
systemctl enable --now founder-radar-heartbeat.timer
systemctl enable --now founder-radar-backup.timer

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
