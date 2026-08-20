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
            founder-radar-update.service founder-radar-update.timer \
            hermes-dashboard.service; do
  install -m 644 "$HERE/$unit" "$UNIT_DIR/$unit"
done
chmod 755 "$HERE/backup.sh" "$HERE/update-from-main.sh" "$HERE/hermes-dashboard.sh"

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

unquote() {
  local v="$1"
  v="${v#\"}"; v="${v%\"}"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}

# Do not `source` .env. Caddy bcrypt hashes are `$2y$...`; under `set -u`
# bash treats `$2` as an unbound positional and aborts the installer after
# the units are already in place (the timer then looks enabled while
# migrate/Caddy never run). Read only the keys we need, unexpanded.
web_domain=""
web_hash_set=0
hermes_domain=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    RADAR_WEB_DOMAIN=*)
      web_domain="$(unquote "${line#RADAR_WEB_DOMAIN=}")"
      ;;
    RADAR_WEB_PASS_HASH=*)
      web_hash_set=1
      ;;
    HERMES_WEB_DOMAIN=*)
      hermes_domain="$(unquote "${line#HERMES_WEB_DOMAIN=}")"
      ;;
  esac
done < "$ENV_FILE"

# ------------------------------------------------------------------ 5. hermes
#
# Skill file plus the web dashboard. The chat gateway is still optional
# (02-architecture §3); the dashboard is what https://hermes.<host>/ is.
# Deleting the skill and hermes-dashboard.service costs the chat + UI
# surfaces and nothing in radar/.

say "hermes"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$ROOT/hermes.env}"
HERMES_USER="${HERMES_USER:-}"
HERMES_HOME="${HERMES_HOME:-}"
HERMES_BIN="${HERMES_BIN:-}"

# Prefer a real ~/.hermes under /home over root: founder-radar-update.timer
# re-runs this script as root, so SUDO_USER is empty and the old fallback
# copied the skill into /root/.hermes while the agent ran as someone else.
for home in /home/*; do
  [ -d "$home/.hermes" ] || continue
  HERMES_HOME="$home"
  HERMES_USER="$(stat -c '%U' "$home")"
  break
done
if [ -z "$HERMES_HOME" ] && [ -n "${SUDO_USER:-}" ]; then
  sudo_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
  if [ -n "$sudo_home" ] && [ -d "$sudo_home/.hermes" ]; then
    HERMES_USER="$SUDO_USER"
    HERMES_HOME="$sudo_home"
  fi
fi
if [ -z "$HERMES_HOME" ] && [ -d /root/.hermes ]; then
  HERMES_USER=root
  HERMES_HOME=/root
fi

if [ -n "$HERMES_HOME" ] && [ -x "$HERMES_HOME/.local/bin/hermes" ]; then
  HERMES_BIN="$HERMES_HOME/.local/bin/hermes"
elif command -v hermes >/dev/null 2>&1; then
  HERMES_BIN="$(command -v hermes)"
fi

install_skill() {
  local home="$1"
  local owner="$2"
  install -d "$home/.hermes/skills/founder-radar"
  install -m 644 "$APP_DIR/hermes/skills/founder-radar/SKILL.md" \
    "$home/.hermes/skills/founder-radar/SKILL.md"
  if [ -n "$owner" ] && [ "$owner" != "root" ]; then
    chown -R "$owner" "$home/.hermes/skills/founder-radar"
  fi
}

if [ -n "$HERMES_HOME" ] && [ -d "$HERMES_HOME/.hermes" ]; then
  install_skill "$HERMES_HOME" "$HERMES_USER"
else
  say "no ~/.hermes yet — install Hermes, then copy"
  say "  $APP_DIR/hermes/skills/founder-radar/SKILL.md"
  say "  to ~/.hermes/skills/founder-radar/"
fi

if [ -z "$hermes_domain" ] && [ -n "$web_domain" ]; then
  case "$web_domain" in
    hermes.*) hermes_domain="$web_domain" ;;
    *)        hermes_domain="hermes.$web_domain" ;;
  esac
fi

write_hermes_env() {
  local upstream="$1"
  {
    printf 'HERMES_USER=%s\n' "${HERMES_USER:-}"
    printf 'HERMES_HOME=%s\n' "${HERMES_HOME:-}"
    printf 'HERMES_BIN=%s\n' "${HERMES_BIN:-}"
    printf 'HERMES_WEB_DOMAIN=%s\n' "${hermes_domain:-}"
    printf 'HERMES_DASHBOARD_UPSTREAM=%s\n' "$upstream"
    printf 'HERMES_DASHBOARD_HOST=127.0.0.1\n'
    printf 'HERMES_DASHBOARD_PORT=9119\n'
    if [ -n "$hermes_domain" ]; then
      printf 'HERMES_DASHBOARD_PUBLIC_URL=https://%s\n' "$hermes_domain"
    fi
  } > "$HERMES_ENV_FILE"
  chmod 644 "$HERMES_ENV_FILE"
}

# Write a stub env first so the unit can start (it sources this file).
if [ -n "$HERMES_USER" ] || [ -n "$hermes_domain" ] || [ -n "${HERMES_BIN:-}" ]; then
  write_hermes_env "127.0.0.1:9119"
fi

if [ -n "${HERMES_BIN:-}" ] && [ -n "${HERMES_USER:-}" ]; then
  agent_dir="$HERMES_HOME/.hermes/hermes-agent"
  if [ -x "$agent_dir/.venv/bin/pip" ]; then
    say "ensuring hermes-agent[web] for the dashboard"
    sudo -H -u "$HERMES_USER" env HOME="$HERMES_HOME" \
      "$agent_dir/.venv/bin/pip" install --quiet -e "$agent_dir[web]" || \
      say "pip install hermes-agent[web] failed — dashboard may refuse to start"
  fi
  dist="$agent_dir/hermes_cli/web_dist/index.html"
  if [ -f "$agent_dir/web/package.json" ] && [ ! -f "$dist" ]; then
    if ! command -v npm >/dev/null 2>&1; then
      say "installing nodejs so the Hermes dashboard UI can be built"
      apt-get install -y -qq nodejs npm || \
        say "nodejs/npm not available — dashboard UI may be blank"
    fi
    if command -v npm >/dev/null 2>&1; then
      say "building hermes dashboard frontend"
      if ! sudo -H -u "$HERMES_USER" env HOME="$HERMES_HOME" \
          bash -c "cd \"$agent_dir/web\" && npm install --no-audit --no-fund && npm run build"; then
        say "frontend build failed — dashboard may self-heal on first start"
      fi
    fi
  fi
  mkdir -p "$UNIT_DIR/hermes-dashboard.service.d"
  {
    printf '[Service]\n'
    printf 'User=%s\n' "$HERMES_USER"
    if id -gn "$HERMES_USER" >/dev/null 2>&1; then
      printf 'Group=%s\n' "$(id -gn "$HERMES_USER")"
    fi
    printf 'Environment=HOME=%s\n' "$HERMES_HOME"
    printf 'WorkingDirectory=%s\n' "$HERMES_HOME"
    printf 'ReadWritePaths=%s/.hermes\n' "$HERMES_HOME"
  } > "$UNIT_DIR/hermes-dashboard.service.d/user.conf"
  systemctl daemon-reload
  systemctl enable --now hermes-dashboard.service || \
    say "hermes-dashboard.service failed to start — see journalctl -u hermes-dashboard"
else
  say "hermes binary not found — dashboard stays unpublished on :9119"
fi

# If the dashboard unit is not actually listening, Caddy still publishes
# hermes.<host> (so TLS works) but falls back to the review surface.
dashboard_upstream="127.0.0.1:8787"
if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet hermes-dashboard.service 2>/dev/null; then
  dashboard_upstream="127.0.0.1:9119"
elif [ -n "${HERMES_BIN:-}" ]; then
  # Unit may still be coming up; prefer 9119 when we installed it.
  dashboard_upstream="127.0.0.1:9119"
fi
if [ -f "$HERMES_ENV_FILE" ]; then
  write_hermes_env "$dashboard_upstream"
fi

if [ -n "$web_domain" ] && [ "$web_hash_set" -eq 1 ]; then
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
  umask 022
  cat "$HERE/Caddyfile" > /etc/caddy/Caddyfile
  if [ -n "$hermes_domain" ] && [ "$hermes_domain" != "$web_domain" ]; then
    printf '\n' >> /etc/caddy/Caddyfile
    cat "$HERE/Caddyfile.hermes" >> /etc/caddy/Caddyfile
  fi
  mkdir -p /etc/systemd/system/caddy.service.d
  {
    printf '[Service]\n'
    printf 'EnvironmentFile=%s\n' "$ENV_FILE"
    printf 'EnvironmentFile=-%s\n' "$HERMES_ENV_FILE"
  } > /etc/systemd/system/caddy.service.d/override.conf
  systemctl daemon-reload
  systemctl enable --now caddy
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  say "review surface live at https://$web_domain (password required)"
  if [ -n "$hermes_domain" ] && [ "$hermes_domain" != "$web_domain" ]; then
    say "Hermes dashboard at https://$hermes_domain (same password)"
  fi
else
  say "RADAR_WEB_DOMAIN / RADAR_WEB_PASS_HASH not set in $ENV_FILE"
  say "  the review surface is running on 127.0.0.1:8787 and is NOT published."
  say "  to publish it, generate a hash and re-run:"
  say "    caddy hash-password --plaintext 'choose-a-password'"
  say "  then add RADAR_WEB_DOMAIN, RADAR_WEB_USER and RADAR_WEB_PASS_HASH."
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
