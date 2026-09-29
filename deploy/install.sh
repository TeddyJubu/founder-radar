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
# Root-owned, and — the point — under a root-owned parent. $ROOT is owned by
# $APP_USER, so a directory inside it could be renamed away and replaced by the
# service user, whatever its own mode says.
LIBEXEC="${LIBEXEC:-/usr/local/libexec/founder-radar}"
SUDOERS_DIR="${SUDOERS_DIR:-/etc/sudoers.d}"
LOGROTATE_DIR="${LOGROTATE_DIR:-/etc/logrotate.d}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root (try: sudo bash deploy/install.sh)" >&2
  exit 1
fi

# BEGIN maintenance service control
# A clean rebuild can install files and migrate the DB without reopening any
# service or timer. The operator starts them explicitly after rescore and QA.
systemctl() {
  if [ "${INSTALL_MAINTENANCE:-0}" = 1 ]; then
    case " $* " in
      *" enable "*|*" start "*|*" restart "*|*" try-restart "*|*" reload "*|*" reload-or-restart "*)
        printf 'maintenance: deferred systemctl %s\n' "$*"
        return 0 ;;
    esac
  fi
  command systemctl "$@"
}
# END maintenance service control

# BEGIN trusted install checks
# Changing ownership does not remove malicious Git hooks, pip launchers or .pth
# files. Refuse old untrusted inputs before executing any of them as root.
assert_trusted_install_path() {
  local parent="$1"
  while :; do
    if [ -L "$parent" ] || [ "$(stat -c %u "$parent")" != 0 ] ||
        [ -n "$(find "$parent" -maxdepth 0 -perm /022 -print)" ]; then
      echo "untrusted existing installation: $parent; rebuild from a trusted root-owned checkout and fresh venv" >&2
      exit 1
    fi
    [ "$parent" = / ] && break
    parent="$(dirname "$parent")"
  done
}
assert_trusted_install_input() {
  local tree="$1" link target
  assert_trusted_install_path "$tree"
  if [ -n "$(find -L "$tree" \( ! -user root -o -perm /022 \) -print -quit)" ]; then
    echo "untrusted existing installation: $tree; refusing to execute existing code" >&2
    exit 1
  fi
  # Venv Python links are normal, but their resolved targets and parents must
  # also be protected. A root-owned link into a writable directory is unsafe.
  while IFS= read -r -d '' link; do
    target="$(readlink -f "$link")"
    assert_trusted_install_path "$target"
  done < <(find "$tree" -type l -print0)
}
assert_trusted_install_input "$APP_DIR"
if [ -e "$VENV" ] || [ -L "$VENV" ]; then
  assert_trusted_install_input "$VENV"
fi
# END trusted install checks

# ---------------------------------------------------------------- 1. account

say "service account and directories"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  adduser --system --group --home "$ROOT" "$APP_USER"
fi

install -d -o root -g root -m 755 "$ROOT"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$ROOT/data" "$ROOT/logs" "$ROOT/backups"
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
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"

say "python environment"
# pip as $APP_USER inherits the caller's cwd. Running install.sh from /root
# then dies with PermissionError on an editable path hook under root's home.
# Always install from the checkout, with the service user's HOME.
cd "$APP_DIR"
if [ -d "$VENV" ]; then
  chown -R root:root "$VENV"
  chmod -R go-w "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --only-binary=:all: --require-hashes -r deploy/requirements.lock
"$VENV/bin/pip" install --quiet --require-hashes --only-binary=:all: -r deploy/build-requirements.lock
"$VENV/bin/pip" install --quiet --no-build-isolation --no-deps -e .
chown -R root:root "$VENV"
chmod -R go-w "$VENV"
# Hermes (operator user) cannot read radar-owned .env — install a wrapper that
# re-execs as $APP_USER so Telegram ops work without hand-rolled sudo.
install -m 755 "$HERE/founder-radar-wrap.sh" /usr/local/bin/founder-radar
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

# ------------------------------------------------- 3b. root-owned helpers
#
# Anything that root runs on the service user's behalf must not live where the
# service user can edit it. The checkout under $APP_DIR is owned by $APP_USER,
# so a sudoers rule or a unit `ExecStartPre=+` that names a script inside it is
# a one-line path from `radar` to root: edit the script, trigger the rule.
# hermes-acl.sh is the one helper `radar` legitimately needs run as root (Hermes
# rewrites ~/.hermes and clears the ACL mask that Today QA depends on), so it
# is installed from the checkout into $LIBEXEC and sudoers/units point there.

say "root-owned helpers"
install -d -o root -g root -m 755 "$(dirname "$LIBEXEC")" "$LIBEXEC"
install -o root -g root -m 755 "$HERE/hermes-acl.sh" "$LIBEXEC/hermes-acl.sh"
install -o root -g root -m 755 "$HERE/update-from-main.sh" "$LIBEXEC/update-from-main.sh"

if command -v visudo >/dev/null 2>&1 && [ -d "$SUDOERS_DIR" ]; then
  sudoers_tmp="$(mktemp)"
  {
    echo "# Generated by deploy/install.sh — $APP_USER may restore Hermes ACLs only."
    echo "# The script is root-owned on purpose; never point this into the checkout."
    echo "$APP_USER ALL=(root) NOPASSWD: $LIBEXEC/hermes-acl.sh"
  } > "$sudoers_tmp"
  # Validate before installing: a syntax error in sudoers locks out sudo itself.
  if visudo -cf "$sudoers_tmp" >/dev/null 2>&1; then
    install -o root -g root -m 440 "$sudoers_tmp" "$SUDOERS_DIR/founder-radar-hermes-acl"
  else
    say "WARNING: generated sudoers rule failed validation — existing rule left in place"
  fi
  rm -f "$sudoers_tmp"
fi

# Earlier hand-made drop-ins pointed root at radar-writable paths (the checkout,
# or /opt/founder-radar/bin, which sits inside a radar-owned directory). The
# units below now carry the right path themselves, and a drop-in that resets
# ExecStartPre= would silently put the old one back — so retire only those.
for legacy in \
    "$UNIT_DIR/founder-radar.service.d/hermes-acl.conf" \
    "$UNIT_DIR/founder-radar.service.d/hermes-ops.conf" \
    "$UNIT_DIR/founder-radar-update.service.d/hermes-ops.conf"; do
  if [ -f "$legacy" ] && grep -qE '/opt/founder-radar/(bin|app/deploy)/hermes-acl\.sh' "$legacy"; then
    say "retiring legacy drop-in $legacy"
    rm -f "$legacy"
  fi
done

# ------------------------------------------------------------------ 4. units

say "systemd units"
for unit in founder-radar.service founder-radar.timer \
            founder-radar-heartbeat.service founder-radar-heartbeat.timer \
            founder-radar-backup.service founder-radar-backup.timer \
            founder-radar-web.service \
            founder-radar-chatgpt-actions.service \
            founder-radar-update.service founder-radar-update.timer \
            'founder-radar-alert@.service'; do
  install -m 644 "$HERE/$unit" "$UNIT_DIR/$unit"
done
chmod 755 "$HERE/backup.sh" "$HERE/update-from-main.sh"
if [ -f "$HERE/hermes-acl.sh" ]; then
  chmod 755 "$HERE/hermes-acl.sh"
fi
if [ -f "$HERE/founder-radar-wrap.sh" ]; then
  chmod 755 "$HERE/founder-radar-wrap.sh"
fi
# hermes-dashboard.sh remains in the tree for local ops, but is not published.
if [ -f "$HERE/hermes-dashboard.sh" ]; then
  chmod 755 "$HERE/hermes-dashboard.sh"
fi

install -m 644 "$HERE/logrotate.founder-radar" "$LOGROTATE_DIR/founder-radar"

# The Hermes gateway crash-loops when Today QA (which runs as radar under the
# operator's home) leaves ~/.hermes/auth.json unreadable to the operator, and
# `Restart=always` alone never repairs that. Heal the ownership and ACLs before
# every (re)start. `-` ignores a failed heal, `+` runs it as root although the
# unit itself is User=<operator>. Hermes' own unit is left untouched.
if [ -f "$UNIT_DIR/hermes-gateway.service" ]; then
  install -d -m 755 "$UNIT_DIR/hermes-gateway.service.d"
  {
    echo "# Generated by deploy/install.sh — do not edit by hand."
    echo "[Service]"
    echo "ExecStartPre=-+$LIBEXEC/hermes-acl.sh"
  } > "$UNIT_DIR/hermes-gateway.service.d/founder-radar-acl.conf"
  chmod 644 "$UNIT_DIR/hermes-gateway.service.d/founder-radar-acl.conf"
fi
# Same helper, same reason, for the WebUI drop-in an operator may have added.
if [ -f "$UNIT_DIR/hermes-webui.service.d/acl.conf" ] \
   && grep -q '/opt/founder-radar/bin/hermes-acl\.sh' "$UNIT_DIR/hermes-webui.service.d/acl.conf"; then
  sed -i "s#/opt/founder-radar/bin/hermes-acl\.sh#$LIBEXEC/hermes-acl.sh#" \
    "$UNIT_DIR/hermes-webui.service.d/acl.conf"
fi

# --------------------------------------------------- schema before services

say "database"
# Run from $ROOT, not the caller's cwd: the CLI loads .env from the working
# directory, and RADAR_DB=$ROOT/data/radar.db lives there. Run from anywhere
# else and migrate creates a shadow db under app/data/ that silently absorbs
# manual CLI runs while the timers write the real one.
cd "$ROOT"
sudo -u "$APP_USER" "$VENV/bin/founder-radar" db migrate

systemctl daemon-reload
systemctl enable --now founder-radar.timer
systemctl enable --now founder-radar-heartbeat.timer
systemctl enable --now founder-radar-backup.timer
systemctl enable --now founder-radar-update.timer

# Hermes Agent control plane stays Telegram-only. Older installs published
# hermes-dashboard.service — stop and disable that unit if present.
if systemctl cat hermes-dashboard.service >/dev/null 2>&1; then
  systemctl disable --now hermes-dashboard.service 2>/dev/null || true
fi

# ------------------------------------------------------------- 4b. the review
#
# The web surface is opt-in and refuses to start public without a password.
# `founder-radar-web.service` binds 127.0.0.1, so until Caddy is configured the
# review queue is reachable only from the box itself — which is the safe
# default, not a broken state.

say "review surface"
systemctl enable --now founder-radar-web.service
# enable --now does not reload an already-running unit; always restart so
# code and config already on disk become the live process.
systemctl restart founder-radar-web.service

# ChatGPT Actions API (loopback :8790). Starts only when RADAR_CHATGPT_API_KEY
# is set — the process refuses to bind without it. Optional; skip quietly.
if grep -qE '^[[:space:]]*RADAR_CHATGPT_API_KEY=.+' "$ENV_FILE" 2>/dev/null; then
  say "ChatGPT Actions API"
  systemctl enable --now founder-radar-chatgpt-actions.service
  systemctl restart founder-radar-chatgpt-actions.service
else
  say "RADAR_CHATGPT_API_KEY unset — ChatGPT Actions unit not enabled"
  say "  generate: openssl rand -hex 32"
  say "  then add RADAR_CHATGPT_API_KEY=… to $ENV_FILE and re-run install"
fi

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
# Skill file for Telegram chat. The Hermes Agent control plane is NOT
# published on the public internet — hermes.<host> is a TLS alias for the
# review UI (:8787), same basic auth as RADAR_WEB_DOMAIN.

say "hermes"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$ROOT/hermes.env}"
HERMES_USER="${HERMES_USER:-}"
HERMES_HOME="${HERMES_HOME:-}"
HERMES_BIN="${HERMES_BIN:-}"

pick_hermes_home() {
  local home owner
  for home in /home/* /root; do
    [ -d "$home/.hermes" ] || continue
    if [ -x "$home/.local/bin/hermes" ] || [ -d "$home/.hermes/hermes-agent" ]; then
      owner="$(stat -c '%U' "$home")"
      HERMES_HOME="$home"
      HERMES_USER="$owner"
      return 0
    fi
  done
  for home in /home/* /root; do
    [ -d "$home/.hermes" ] || continue
    HERMES_HOME="$home"
    HERMES_USER="$(stat -c '%U' "$home")"
    return 0
  done
  if [ -n "${SUDO_USER:-}" ]; then
    local sudo_home
    sudo_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
    if [ -n "$sudo_home" ] && [ -d "$sudo_home/.hermes" ]; then
      HERMES_USER="$SUDO_USER"
      HERMES_HOME="$sudo_home"
    fi
  fi
}
pick_hermes_home

if [ -n "$HERMES_HOME" ] && [ -x "$HERMES_HOME/.local/bin/hermes" ]; then
  HERMES_BIN="$HERMES_HOME/.local/bin/hermes"
elif command -v hermes >/dev/null 2>&1; then
  HERMES_BIN="$(command -v hermes)"
else
  for candidate in /usr/local/bin/hermes /usr/bin/hermes; do
    if [ -x "$candidate" ]; then
      HERMES_BIN="$candidate"
      break
    fi
  done
fi

if [ -n "$HERMES_BIN" ] && [ -z "$HERMES_USER" ]; then
  HERMES_USER="$(stat -c '%U' "$HERMES_BIN" 2>/dev/null || true)"
  if [ -n "$HERMES_USER" ] && [ -z "$HERMES_HOME" ]; then
    HERMES_HOME="$(getent passwd "$HERMES_USER" | cut -d: -f6 || true)"
  fi
fi

install_skill() {
  local home="$1"
  local owner="$2"
  install -d "$home/.hermes/skills/founder-radar/references"
  install -m 644 "$APP_DIR/hermes/skills/founder-radar/SKILL.md" \
    "$home/.hermes/skills/founder-radar/SKILL.md"
  install -m 644 "$APP_DIR/hermes/skills/founder-radar/references/today-check.md" \
    "$home/.hermes/skills/founder-radar/references/today-check.md"
  if [ -f "$APP_DIR/hermes/skills/founder-radar/references/publish-check.md" ]; then
    install -m 644 "$APP_DIR/hermes/skills/founder-radar/references/publish-check.md" \
      "$home/.hermes/skills/founder-radar/references/publish-check.md"
  fi
  if [ -n "$owner" ] && [ "$owner" != "root" ]; then
    chown -R "$owner" "$home/.hermes/skills/founder-radar"
  fi
}

install_telegram_plugin() {
  local home="$1"
  local owner="$2"
  local src="$APP_DIR/hermes/plugins/founder-radar-telegram"
  local dest="$home/.hermes/plugins/founder-radar-telegram"
  if [ ! -f "$src/plugin.yaml" ] || [ ! -f "$src/__init__.py" ]; then
    say "telegram plugin missing in checkout — skip"
    return 0
  fi
  local need_restart=0
  if [ ! -f "$dest/__init__.py" ] || ! cmp -s "$src/__init__.py" "$dest/__init__.py" \
     || [ ! -f "$dest/plugin.yaml" ] || ! cmp -s "$src/plugin.yaml" "$dest/plugin.yaml"; then
    need_restart=1
  fi
  install -d "$dest"
  install -m 644 "$src/plugin.yaml" "$dest/plugin.yaml"
  install -m 644 "$src/__init__.py" "$dest/__init__.py"
  if [ -n "$owner" ] && [ "$owner" != "root" ]; then
    chown -R "$owner" "$home/.hermes/plugins/founder-radar-telegram"
  fi
  if [ "${INSTALL_MAINTENANCE:-0}" != 1 ] && [ -n "${HERMES_BIN:-}" ] && [ -x "$HERMES_BIN" ] && [ -n "$owner" ] && [ "$owner" != "root" ]; then
    # --no-allow-tool-override keeps enable non-interactive. Never print config.
    sudo -H -u "$owner" "$HERMES_BIN" plugins enable founder-radar-telegram \
      --no-allow-tool-override </dev/null >/dev/null 2>&1 || true
  fi
  if [ "$need_restart" = 1 ]; then
    restart_hermes_gateway "$owner"
  fi
}

restart_hermes_gateway() {
  if [ "${INSTALL_MAINTENANCE:-0}" = 1 ]; then return 0; fi
  local owner="$1"
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^hermes-gateway.service'; then
    systemctl restart hermes-gateway.service 2>/dev/null || true
    return 0
  fi
  if [ -n "$owner" ] && [ "$owner" != "root" ]; then
    local uid
    uid="$(id -u "$owner" 2>/dev/null || true)"
    if [ -n "$uid" ]; then
      sudo -u "$owner" XDG_RUNTIME_DIR="/run/user/$uid" \
        systemctl --user restart hermes-gateway.service 2>/dev/null || true
    fi
  fi
}

if [ -n "$HERMES_HOME" ] && [ -d "$HERMES_HOME/.hermes" ]; then
  install_skill "$HERMES_HOME" "$HERMES_USER"
  install_telegram_plugin "$HERMES_HOME" "$HERMES_USER"
  # v1 sheet scout (uk-founder-radar + ~/radar cron) dumped Companies House
  # lookups into Telegram and never wrote Today. Every deploy must kill it.
  if [ -x "$HERE/retire-v1-scout.sh" ]; then
    bash "$HERE/retire-v1-scout.sh" "$HERMES_HOME" "${HERMES_USER:-}"
  fi
  # Restore operator ownership of ~/.hermes (Today QA as radar can steal
  # auth.json) and grant Hermes write ACL on /opt/founder-radar/{app,data,…}.
  if [ -x "$LIBEXEC/hermes-acl.sh" ]; then
    APP_USER="$APP_USER" HERMES_USER="${HERMES_USER:-aryan}" ROOT="$ROOT" \
      "$LIBEXEC/hermes-acl.sh" || true
  fi
else
  say "no ~/.hermes yet — install Hermes, then copy"
  say "  $APP_DIR/hermes/skills/founder-radar/"
  say "  to ~/.hermes/skills/founder-radar/"
  say "  $APP_DIR/hermes/plugins/founder-radar-telegram/"
  say "  to ~/.hermes/plugins/founder-radar-telegram/"
fi

if [ -z "$hermes_domain" ] && [ -n "$web_domain" ]; then
  case "$web_domain" in
    hermes.*) hermes_domain="$web_domain" ;;
    *)        hermes_domain="hermes.$web_domain" ;;
  esac
fi

write_hermes_env() {
  {
    printf 'HERMES_USER=%s\n' "${HERMES_USER:-}"
    printf 'HERMES_HOME=%s\n' "${HERMES_HOME:-}"
    printf 'HERMES_BIN=%s\n' "${HERMES_BIN:-}"
    printf 'HERMES_WEB_DOMAIN=%s\n' "${hermes_domain:-}"
    if [ -n "$hermes_domain" ]; then
      # Same review UI as RADAR_WEB_DOMAIN — not the Agent control plane.
      printf 'HERMES_DASHBOARD_PUBLIC_URL=https://%s\n' "$hermes_domain"
    fi
  } > "$HERMES_ENV_FILE"
  chmod 644 "$HERMES_ENV_FILE"
}

# Caddy needs HERMES_WEB_DOMAIN set whenever the review surface is published
# (the site address list includes both hostnames).
if [ -n "$web_domain" ]; then
  write_hermes_env
elif [ -n "$HERMES_USER" ] || [ -n "${HERMES_BIN:-}" ]; then
  write_hermes_env
fi

# Always rewrite Caddy from git so hermes.<host> keeps a Let's Encrypt cert
# as a TLS alias to the review UI (not the Agent dashboard).
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
  install -m 644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
  if [ -f "$HERE/hermes-webui.caddy" ]; then
    install -m 644 "$HERE/hermes-webui.caddy" /etc/caddy/hermes-webui.caddy
  fi
  if [ -f "$HERE/chatgpt-actions.caddy" ]; then
    install -m 644 "$HERE/chatgpt-actions.caddy" /etc/caddy/chatgpt-actions.caddy
  fi
  # Teaching guide static build (Vite base `/guide/`). Optional: tree may be
  # absent until someone runs `cd guide && npm run build` and syncs dist/.
  if [ -d "$ROOT/guide/dist" ]; then
    mkdir -p /opt/founder-radar/guide
    rsync -a --delete "$ROOT/guide/dist/" /opt/founder-radar/guide/
    chown -R radar:radar /opt/founder-radar/guide
    say "teaching guide synced to /opt/founder-radar/guide"
  elif [ ! -d /opt/founder-radar/guide ]; then
    mkdir -p /opt/founder-radar/guide
    chown radar:radar /opt/founder-radar/guide
  fi
  mkdir -p /etc/systemd/system/caddy.service.d
  {
    printf '[Service]\n'
    printf 'EnvironmentFile=%s\n' "$ENV_FILE"
    printf 'EnvironmentFile=-%s\n' "$HERMES_ENV_FILE"
  } > /etc/systemd/system/caddy.service.d/override.conf
  systemctl daemon-reload
  systemctl enable --now caddy
  # Restart (not reload-only): pick up EnvironmentFile + re-issue certs for
  # the hermes.* alias when it is newly added to the site address list.
  systemctl restart caddy
  say "review surface live at https://$web_domain (password required)"
  say "teaching guide (public) at https://$web_domain/guide/"
  if [ -n "$hermes_domain" ] && [ "$hermes_domain" != "$web_domain" ]; then
    say "Hermes hostname https://$hermes_domain is a TLS alias to the same review UI"
  fi
else
  say "RADAR_WEB_DOMAIN / RADAR_WEB_PASS_HASH not set in $ENV_FILE"
  say "  the review surface is running on 127.0.0.1:8787 and is NOT published."
  say "  to publish it, generate a hash and re-run:"
  say "    caddy hash-password --plaintext 'choose-a-password'"
  say "  then add RADAR_WEB_DOMAIN, RADAR_WEB_USER and RADAR_WEB_PASS_HASH."
fi

say "done. next:"
say "  sudo -u $APP_USER founder-radar doctor"
say "  systemctl list-timers 'founder-radar*'"
