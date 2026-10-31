#!/bin/bash
# Run the operator's Hermes CLI as that operator.
#
# Today QA runs as `radar`. Hermes refuses to touch a profile owned by
# another uid (`unsafe_control_path`), which is what dropped the morning
# check and left Today empty. This wrapper is root-owned and sudoers allows
# `radar` to run only this file: it execs the fixed Hermes binary, quiet
# chat or the legacy one-shot, never a shell.
set -euo pipefail

HERMES_USER="${HERMES_USER:-aryan}"
ALLOWED_BIN="${ALLOWED_BIN:-/home/aryan/.local/bin/hermes}"

if [ "$#" -lt 2 ]; then
  echo "hermes-as-owner: missing command" >&2
  exit 2
fi

bin="$1"
shift
resolved="$(readlink -f "$bin" 2>/dev/null || true)"
allowed="$(readlink -f "$ALLOWED_BIN" 2>/dev/null || true)"
if [ -z "$resolved" ] || [ "$resolved" != "$allowed" ]; then
  echo "hermes-as-owner: refused binary" >&2
  exit 2
fi
if [ -L "$ALLOWED_BIN" ]; then
  echo "hermes-as-owner: refused symlink" >&2
  exit 2
fi
owner="$(stat -c %u "$ALLOWED_BIN")"
mode="$(stat -c %a "$ALLOWED_BIN")"
expected="$(id -u "$HERMES_USER")"
# Other-write would let radar replace the binary and run it as the operator.
if [ "$owner" != "$expected" ] || [ "$((8#$mode & 8#002))" -ne 0 ]; then
  echo "hermes-as-owner: binary is other-writable or not owned by $HERMES_USER" >&2
  exit 2
fi

case "$1" in
  chat| -z) ;;
  *) echo "hermes-as-owner: refused command" >&2; exit 2 ;;
esac
if [ "$1" = "chat" ]; then
  quiet=0
  for arg in "$@"; do
    if [ "$arg" = "-Q" ]; then
      quiet=1
    fi
  done
  if [ "$quiet" -ne 1 ]; then
    echo "hermes-as-owner: chat must be quiet (-Q)" >&2
    exit 2
  fi
fi

home="$(getent passwd "$HERMES_USER" | cut -d: -f6)"
if [ -z "$home" ] || [ ! -d "$home/.hermes" ]; then
  echo "hermes-as-owner: no Hermes home for $HERMES_USER" >&2
  exit 2
fi

exec runuser -u "$HERMES_USER" -- env \
  HOME="$home" \
  HERMES_HOME="$home/.hermes" \
  HERMES_NONINTERACTIVE=1 \
  HERMES_DISABLE_LAZY_INSTALLS=1 \
  TERM=dumb \
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  "$bin" "$@"
