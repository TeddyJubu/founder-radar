# 08 — Deployment and Operations

**Getting it onto the VPS, keeping it running, and knowing what it costs.**

Target machine: **managed Linux VPS** — 1 vCPU, 4 GB RAM, ~50 GB disk, Ubuntu 24.04.

---

## 1. Before anything else — rotate the Google key

> ⚠️ **The Google service-account private key was shared in a third-party chat thread.** Any credential that has travelled through a chat should be treated as exposed.

1. Google Cloud Console → IAM & Admin → Service Accounts → the configured deployment service account
2. Keys tab → **Add Key → Create new key → JSON**
3. Upload the new file straight to the server; do not paste its contents anywhere
4. **Delete the old key** from the Keys tab
5. Confirm the spreadsheet is still shared with the service account as Editor

The spreadsheet identifier is supplied through the environment and must remain a placeholder in public documentation. The private key is the secret.

---

## 2. Server setup

```bash
# --- user and directories ---
sudo adduser --system --group --home /opt/founder-radar radar
sudo mkdir -p /opt/founder-radar/{app,data,secrets,backups,logs,worktrees}
sudo chown -R radar:radar /opt/founder-radar
sudo chmod 700 /opt/founder-radar/secrets

# --- python ---
# Ubuntu 24.04 ships Python 3.12 and has NO python3.11 package — `apt install
# python3.11` fails with "Unable to locate package". 3.12 satisfies the >=3.11
# requirement, so just use the system python.
sudo apt update && sudo apt install -y python3 python3-venv git sqlite3
sudo -u radar git clone <repo-url> /opt/founder-radar/app
sudo -u radar python3 -m venv /opt/founder-radar/venv
sudo -u radar /opt/founder-radar/venv/bin/pip install -e /opt/founder-radar/app

# put the CLI on the service user's PATH so `founder-radar ...` works bare
sudo ln -sf /opt/founder-radar/venv/bin/founder-radar /usr/local/bin/founder-radar

# only if a browser-backed source is enabled (at most two, per 04-sources)
# sudo -u radar /opt/founder-radar/venv/bin/playwright install chromium
# sudo /opt/founder-radar/venv/bin/playwright install-deps chromium

# --- secrets ---
sudo -u radar tee /opt/founder-radar/.env > /dev/null <<'EOF'
COMPANIES_HOUSE_API_KEY=...
LLM_PROVIDER=anthropic
LLM_API_KEY=...
LLM_MODEL=<pinned-dated-snapshot-id>
GOOGLE_SA_JSON=/opt/founder-radar/secrets/google-sa.json
RADAR_SHEET_ID=<your-sheet-id>
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
RADAR_DB=/opt/founder-radar/data/radar.db
RADAR_UA=FounderRadar/2.0 (+https://<your-domain>/crawler; contact@<your-domain>)
TZ=Europe/London
EOF
sudo chmod 600 /opt/founder-radar/.env
sudo chown radar:radar /opt/founder-radar/.env
```

**Never log the contents of `.env`.** Add a logging filter that redacts anything matching a key pattern.

---

## 3. Companies House API key

1. Register at `developer.company-information.service.gov.uk`
2. Your Applications → **Register an application** → type: *Live*
3. Copy the key into `COMPANIES_HOUSE_API_KEY`
4. Auth is HTTP Basic: **key as username, empty password**

Free, instant, no approval step. Rate limit is 600 requests per 5-minute rolling window; the pipeline's own limiter must stay under it, because repeated breaches ban the application rather than just throttling it.

---

## 4. Scheduling — systemd, not Hermes

```ini
# /etc/systemd/system/founder-radar.service
[Unit]
Description=UK Founder Radar daily scan
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=radar
Group=radar
WorkingDirectory=/opt/founder-radar/app
EnvironmentFile=/opt/founder-radar/.env
ExecStart=/opt/founder-radar/venv/bin/founder-radar run
TimeoutStartSec=3600
Nice=10
MemoryMax=1G
StandardOutput=append:/opt/founder-radar/logs/run.log
StandardError=append:/opt/founder-radar/logs/error.log
```

```ini
# /etc/systemd/system/founder-radar.timer
[Unit]
Description=Run Founder Radar every morning

[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now founder-radar.timer
systemctl list-timers founder-radar.timer
```

**Why systemd and not Hermes's built-in scheduler.** Hermes's cron lives inside the gateway daemon, jails scripts to `~/.hermes/scripts/`, strips credentials from subprocess environments, and picks the interpreter by file extension — so a `.py` script would run in *Hermes's* virtual environment, not the project's. It also has no misfire catch-up, and an unpinned job silently fails closed if the global model is changed. The Hermes docs themselves recommend OS-level cron for anything that must survive the gateway being unhealthy. `Persistent=true` gives us catch-up after a reboot; `MemoryMax=1G` guarantees Hermes always has room.

`MemoryMax=1G` is comfortable for the pipeline alone. **If a browser-backed source is ever enabled, raise it to 2G** — Playwright adds ~300 MB per page and would otherwise OOM-kill the whole run.

**Heartbeat** — a second timer at 09:00 that alerts if no successful run landed in 26 hours:

```ini
# /etc/systemd/system/founder-radar-heartbeat.service
[Unit]
Description=Founder Radar staleness check

[Service]
Type=oneshot
User=radar
EnvironmentFile=/opt/founder-radar/.env
ExecStart=/opt/founder-radar/venv/bin/founder-radar status --alert-if-stale 26h
```

```ini
# /etc/systemd/system/founder-radar-heartbeat.timer
[Unit]
Description=Check Founder Radar ran this morning

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Backup** — daily at 05:00, 14 days retained:

```bash
sqlite3 $RADAR_DB ".backup /opt/founder-radar/backups/radar-$(date +%F).db"
find /opt/founder-radar/backups -name 'radar-*.db' -mtime +14 -delete
```

---

## 5. Hermes Agent — Telegram and VPS repair

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
```

**`--skip-browser` is not optional on this machine.** Playwright plus Chromium is the memory hog: Hermes runs at 300–600 MB chat-only and 1.2–1.8 GB with browser tools active. With the pipeline also needing room, 4 GB does not stretch to both.

```bash
hermes config set provider anthropic
hermes config set cron.model <pinned-model-id>     # guard against model-drift failures
hermes gateway setup                                # choose Telegram, paste bot token
hermes config set telegram.allowed_users <aryan-user-id>
sudo hermes gateway install --system                # systemd, starts at boot
```

Then copy the skill (or re-run `deploy/install.sh`, which does this and writes `/opt/founder-radar/hermes.env`):

```bash
mkdir -p ~/.hermes/skills
cp -a /opt/founder-radar/app/hermes/skills/founder-radar ~/.hermes/skills/
```

Finally, in Telegram, message the bot `/sethome` so scheduled deliveries know where to go.

**When a bug appears**, Hermes on this box is the repair agent. systemd is the trigger, not Hermes cron:

- `founder-radar-repair.timer` at 09:05, after the heartbeat
- `OnFailure=` of `founder-radar.service` (fatal exit 2 only; partial is a normal Tuesday)
- Telegram: `/fix` or “fix it”

Both paths run `deploy/hermes-repair.sh`: `founder-radar repair --apply` first (migrate, prune backups, restart web), then `hermes chat --yolo -s founder-radar` if a code-level fix is still needed. The person who typed `/fix` is **not** technical: Hermes must not ask them to SSH, pick a branch, or merge.

A code fix follows `hermes/skills/founder-radar/references/workflow.md` **in order**, one step at a time (4 GB box — never review and test in parallel, never while `founder-radar.service` is active):

1. Create `/opt/founder-radar/worktrees/fix-…` on branch `hermes/fix-*`. Do not edit live `main` by hand.
2. Implement and commit in that worktree.
3. A **different** sub-agent reviews (`review-prompt.md`) and must output `VERDICT: APPROVE`.
4. A **different** sub-agent tests the worktree (`test-prompt.md`) and must output `VERDICT: PASS`.
5. Only then: `sudo bash /opt/founder-radar/app/deploy/hermes-ship.sh <worktree>`.

`hermes-ship.sh` is the machine gate. It re-runs pytest against the worktree, refuses a `radar/score/` or secrets diff, fast-forwards `/opt/founder-radar/app` `main`, tries `git push origin main`, re-runs `install.sh`, and deletes the worktree. If it exits non-zero, live `main` was not changed. Scoring stays off-limits. A missing `hermes` binary is not a failed timer — ops remediations still run.

`deploy/update-from-main.sh` **keeps** a local `main` that is ahead of GitHub (a VPS-shipped fix waiting to push) instead of failing `--ff-only`. Diverged histories still refuse.

Do not start Hermes while the daily scan is active (MemoryMax 1G on the scan, 800M on repair). The script no-ops the agent in that case and retries next cycle. A 6-hour cooldown stops an OnFailure loop. Review and test sub-agents are sequential for the same reason.

**Digest delivery with fallback:**

```python
def send_digest(text: str) -> None:
    rc = subprocess.run(["hermes", "send", "--to", "telegram", text]).returncode
    if rc != 0:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20)
```

If Hermes is down, the digest still arrives. Aryan loses only the ability to chat back.

---

## 6. First run

```bash
sudo -u radar founder-radar doctor          # verify keys, quotas, disk, sheet access
sudo -u radar founder-radar db migrate      # create the schema
sudo -u radar founder-radar sync-sheet      # create and format all twelve tabs
# → open the sheet, review the seeded Fund Criteria and Settings, adjust
sudo -u radar founder-radar backfill --days 90   # ~15 min: the CH sweep
sudo -u radar founder-radar run --dry-run   # see what would happen, write nothing
sudo -u radar founder-radar run             # the real first run
```

Expect the backfill to produce a few hundred candidates and a handful of shortlist entries. **That is correct.** A first run producing forty shortlisted companies would mean the gates are too loose.

---

## 7. What it costs

| Item | Monthly | Notes |
|---|---|---|
| Managed Linux VPS | ~£6 | already provisioned |
| AI extraction | **~£2.50** | ~600 articles; scales with news volume, not company count |
| Companies House API | £0 | free, 600 req / 5 min |
| UKRI Gateway to Research | £0 | free, keyless |
| GOV.UK Search API | £0 | free, keyless, no rate limit |
| postcodes.io | £0 | free, keyless |
| Google Sheets API | £0 | within free quota |
| Telegram | £0 | |
| **Total** | **≈ £8.50 / month** | |

**Three levers if it needs to be cheaper:**

1. `llm_model` → a cheaper small model in Settings. Around 10× cheaper is available; run both against the golden fixtures first to see the accuracy gap on real data.
2. `llm_enabled = FALSE` → heuristic extraction only. **£0 AI cost**, lower extraction quality, everything else unchanged.
3. Disable the highest-volume news sources in the `Sources` tab. AI cost is roughly proportional to articles processed.

The cost ledger is in the database — `SELECT strftime('%Y-%m', created_at), ROUND(SUM(cost_usd),2) FROM llm_cache GROUP BY 1` — and the current month's figure appears in `/status`.

---

## 8. Runbook

### Every morning (automatic)
06:30 run → 06:45 sheet updated → digest in Telegram.

### Weekly, five minutes
```bash
founder-radar status                # any red sources?
founder-radar tune                  # has Aryan's verdict data shifted the ideal threshold?
```
Then run **the health query** — median age of shortlisted companies over the last 30 days. **If it climbs above 24 months, the system is drifting back toward the version 1 failure.** Tighten `max_company_age_months` or check whether a source has started returning old records.

### Monthly
```bash
founder-radar review                # clear the fuzzy-match review queue
founder-radar sources --list        # any source at zero for a fortnight?
```
Check the AI spend. Re-verify one or two adapters against live pages.

### When something breaks

| Symptom | First move |
|---|---|
| No digest arrived | In Telegram: `/fix` or “fix it”. Hermes looks, and messages when it is done. (On the box: `founder-radar repair --apply`, then `systemctl status founder-radar.timer`.) |
| Digest arrived but is empty | Normal on a quiet day. Confirm with `founder-radar status` — if sources are ✅ and gates rejected everything, it worked. |
| One source shows ⚠️ | `founder-radar sources --test <key>` — prints the raw response and the parse result |
| Source returns 0 but says OK | Layout change. `founder-radar sources --sniff <url>` to find the new endpoint. **This is the dangerous failure — it looks like a quiet week.** |
| Sheet not updating | `founder-radar doctor` — usually the service account lost Editor access |
| Scores changed unexpectedly | `SELECT * FROM config_snapshot ORDER BY created_at DESC LIMIT 5` — someone edited Settings |
| Companies House 429s | Reduce `max_enrichment_requests_per_run`. The limiter should prevent this; if it recurs, it's a bug. |
| AI errors | `founder-radar run --no-llm` to confirm everything else is healthy, then check the provider status page |
| Disk full | `du -sh /opt/founder-radar/*` — usually the backups directory or `llm_cache` |
| Duplicate companies in the sheet | Run duplicate-audit query 9 from `03-data-model.md`. Should be zero rows. If not, it's a resolution bug — file it with the two company IDs. |

### Rollback

```bash
sudo systemctl stop founder-radar.timer
cp /opt/founder-radar/backups/radar-YYYY-MM-DD.db /opt/founder-radar/data/radar.db
cd /opt/founder-radar/app && git checkout <last-good-tag>
/opt/founder-radar/venv/bin/pip install -e .
sudo -u radar founder-radar sync-sheet     # regenerate the sheet from the database
sudo systemctl start founder-radar.timer
```

Because the sheet is a **view**, regenerating it from a restored database fully recovers the visible state. That property is why the sheet is not the source of truth.

### Ship a new version (this is how the box stays on `main`)

The VPS watches GitHub. `founder-radar-update.timer` runs
`deploy/update-from-main.sh` a few minutes after boot and then every five
minutes: `git pull --ff-only origin main`, `deploy/install.sh`,
`founder-radar doctor`, then `rescore --all` when HEAD moved. That is the
fix for "I merged but the site is still the same" — it does **not** need
GitHub Actions secrets.

`install.sh` enables the timer. Logs land in `/opt/founder-radar/logs/update.log`.
Fast-forward only; a diverged checkout fails loudly rather than force-pushing.
A live daily scan (`founder-radar.service` active) skips the cycle so pip
does not race the oneshot.

Optional: `.github/workflows/deploy.yml` can SSH in and run the same script
if repository secrets `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (optional
`VPS_PORT`) are set under Settings → Secrets and variables → Actions.
`VPS_SSH_KEY` is a **private key** whose public half is in
`authorized_keys` — not the root password. Missing secrets skip with
success so the Actions tab does not go red; the box still ships via the
timer. A manual Actions run is only for a forced rescore without a commit.

---

## 9. Monitoring

Three alerts to Telegram, and only three — more than this and they get ignored:

| Alert | Trigger |
|---|---|
| **Stale** | No successful run in 26 hours |
| **Source down** | A Tier 1 source failed 3 consecutive runs, **or** any source returned zero items for 7 days having previously averaged more than two |
| **Disk** | Less than 5 GB free |

Logs rotate weekly, 8 weeks retained:

```
/opt/founder-radar/logs/*.log {
    weekly rotate 8 compress missingok notifempty
    create 0640 radar radar
}
```

---

## 10. Legal and data-protection checklist

Complete before the first live run.

- [ ] Privacy notice published at the URL in `RADAR_UA`, describing what is collected, why, the lawful basis, retention, and how to object
- [ ] `docs/legitimate-interests.md` written — purpose, necessity, balancing test. Half a page.
- [ ] Founder table verified to contain **no** email, phone, address, or date of birth — including the partial DOB Companies House returns. Enforced by `test_founder_table_stores_no_sensitive_fields`.
- [ ] `founder-radar forget "<name>"` tested end to end, including that re-ingestion does not resurrect the record
- [ ] Retention rule active: purge founder records for companies rejected more than 12 months ago
- [ ] Google Sheet restricted to named accounts; link-sharing **off**
- [ ] robots.txt compliance verified for every enabled source (`founder-radar sources --list` shows the robots verdict per source)
- [ ] User-Agent contains a **working** contact address, and someone reads it
- [ ] No source in the enabled list requires a login, a paywall bypass, or defeating a bot challenge

None of this is onerous, and all of it is the difference between a well-understood low-risk research crawler and an argument nobody wants to have.
