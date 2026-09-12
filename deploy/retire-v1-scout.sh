#!/usr/bin/env bash
#
# Retire the July v1 sheet scout so Telegram cannot pick it over v2.
#
# v1 lived in ~/.hermes/skills/uk-founder-radar and a Hermes cron job
# (founder-radar-daily, workdir ~/radar). Chat "Search now" followed that
# playbook: web search, Companies House name lookup, sheet upsert, dump in
# Telegram — and never wrote SQLite Today/Kept.
#
# Called from deploy/install.sh on every deploy so a leftover skill cannot
# come back. Safe to run on a box that never had v1 (no-op).
#
#   sudo bash deploy/retire-v1-scout.sh /home/aryan [aryan]
#
set -euo pipefail

HERMES_HOME="${1:?hermes home (e.g. /home/aryan)}"
HERMES_USER="${2:-}"

say() { printf 'retire-v1: %s\n' "$*"; }

if [ ! -d "$HERMES_HOME/.hermes" ]; then
  say "no $HERMES_HOME/.hermes — nothing to retire"
  exit 0
fi

retired="$HERMES_HOME/.hermes/_retired"
install -d "$retired"

retire_skill() {
  local src="$1"
  local name
  name="$(basename "$src")"
  if [ ! -e "$src" ]; then
    return 0
  fi
  rm -rf "$retired/$name"
  mv "$src" "$retired/$name"
  say "moved $src → $retired/$name"
}

# Must leave skills/: Hermes loads every directory under skills/, including
# nested ones. Parking v1 next door would still be callable.
retire_skill "$HERMES_HOME/.hermes/skills/uk-founder-radar"
retire_skill "$HERMES_HOME/.hermes/skills/_retired/uk-founder-radar"

if [ -d "$HERMES_HOME/.hermes/skills/_retired" ]; then
  # Empty leftover from an older retire that parked under skills/.
  rmdir "$HERMES_HOME/.hermes/skills/_retired" 2>/dev/null || true
fi

jobs="$HERMES_HOME/.hermes/cron/jobs.json"
if [ -f "$jobs" ]; then
  python3 - "$jobs" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if isinstance(data, list):
    jobs = data
    root = "list"
elif isinstance(data, dict):
    jobs = data.get("jobs") or data.get("items") or []
    root = "dict"
else:
    raise SystemExit(f"unexpected jobs.json shape: {type(data)}")

changed = 0
for job in jobs:
    if not isinstance(job, dict):
        continue
    skill = str(job.get("skill") or "")
    prompt = str(job.get("prompt") or "")
    name = str(job.get("name") or "")
    workdir = str(job.get("workdir") or "").rstrip("/")
    is_v1 = (
        skill == "uk-founder-radar"
        or "uk-founder-radar" in prompt
        or "~/radar" in prompt
        or workdir.endswith("/home/aryan/radar")
        or workdir.endswith("/radar") and "/opt/founder-radar" not in workdir
        or name == "founder-radar-daily"
    )
    if not is_v1:
        continue
    if job.get("enabled") is False and job.get("state") == "disabled":
        continue
    job["enabled"] = False
    job["state"] = "disabled"
    changed += 1
    print(f"disabled cron job {job.get('id') or name}")

if changed:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
fi

memory_dir="$HERMES_HOME/.hermes/memories"
install -d "$memory_dir"
cat > "$memory_dir/FOUNDER-RADAR-V2.md" <<'EOF'
# Founder Radar is v2 only

Do not use `uk-founder-radar`, `~/radar`, `sheets.py`, `scoring.py`, or
`seen.json`. Do not search Companies House from chat. Do not dump company
lists into Telegram.

Run `founder-radar` (especially `today`, `run`, `decide`, `publish --send`).
Aryan reviews companies on the web Today page.
EOF

# If chat history still says `cd ~/radar && python sheets.py`, fail closed.
radar_dir="$HERMES_HOME/radar"
if [ -d "$radar_dir/.venv" ] || [ -f "$radar_dir/check_sources.py" ]; then
  dest="$HERMES_HOME/radar-v1-archived"
  if [ ! -e "$dest" ]; then
    mv "$radar_dir" "$dest"
    say "moved $radar_dir → $dest"
  fi
fi
install -d "$radar_dir"
cat > "$radar_dir/DEAD" <<'EOF'
v1 UK Founder Radar is retired. Do not run anything in this directory.
Use the founder-radar CLI. Aryan's list is the web Today page.
EOF
for stub in sheets.py scoring.py check_sources.py _upsert_runner.py; do
  cat > "$radar_dir/$stub" <<'PY'
#!/usr/bin/env python3
import sys
sys.stderr.write(
    "v1 scout is retired. Use `founder-radar today` — companies belong on "
    "the dashboard, not in Telegram.\n"
)
sys.exit(2)
PY
  chmod +x "$radar_dir/$stub"
done

if [ -n "$HERMES_USER" ] && [ "$HERMES_USER" != "root" ]; then
  chown -R "$HERMES_USER" "$retired" "$memory_dir/FOUNDER-RADAR-V2.md" "$radar_dir" 2>/dev/null || true
  if [ -f "$jobs" ]; then
    chown "$HERMES_USER" "$jobs" 2>/dev/null || true
  fi
fi

say "v1 scout retired for $HERMES_HOME"
