# Founder Radar — VPS repair playbook

Hermes on this box follows this file when `founder-radar repair` reports
`needs_agent` or when a human says the radar is broken. Safe operational
fixes live in `founder-radar repair --apply`. This page is the rest.

Working directory for every CLI command: `/opt/founder-radar`.
Binary: `/opt/founder-radar/venv/bin/founder-radar` as user `radar`.
Checkout: `/opt/founder-radar/app`.

## 1. Diagnose first

```bash
cd /opt/founder-radar
sudo -u radar -H /opt/founder-radar/venv/bin/founder-radar repair --apply
sudo -u radar -H /opt/founder-radar/venv/bin/founder-radar doctor
sudo -u radar -H /opt/founder-radar/venv/bin/founder-radar status
sudo -u radar -H /opt/founder-radar/venv/bin/founder-radar sources --list
```

```bash
tail -n 80 /opt/founder-radar/logs/error.log
tail -n 80 /opt/founder-radar/logs/run.log
journalctl -u founder-radar.service -n 50 --no-pager
journalctl -u founder-radar-web.service -n 40 --no-pager
```

Never `cat /opt/founder-radar/.env`. Never paste a service-account JSON.

## 2. Symptom → first move

| Symptom | First move | Then |
|---|---|---|
| No digest / stale run | `repair --apply`. If still stale and the scan is not running: `sudo systemctl start founder-radar.service` | Wait; `status`. If it dies again, read `error.log` |
| Empty digest, sources ✅ | Not a bug. Say so. Quiet day. | — |
| One source ⚠️ or 0 items | `sources --test <key>` then `sources --sniff <url>` | Adapter layout change — §3 |
| Sheet not updating | `doctor`. Service account missing Editor? | Tell the human. Do not invent a key |
| Web UI down | `sudo systemctl restart founder-radar-web.service` | `systemctl status founder-radar-web` |
| Disk alert | `repair --apply` prunes old `radar-*.db` | If still tight, say so; do not delete `data/radar.db` |
| Companies House 429 | Do not hammer the API. Leave it. | Mention `max_enrichment_requests_per_run` |
| AI errors | `founder-radar run --no-llm` to prove the rest | Provider status; do not change the extract schema |
| Fatal traceback in app code | Reproduce with the CLI. Patch on a branch. | §3 |
| Checkout behind `origin/main` | Leave it. `founder-radar-update.timer` fast-forwards | Do not `git pull` as a side effect of a parse fix |

`repair --apply` already migrates an empty schema, prunes backups, and
restarts a dead web unit. Do not re-implement those in a one-off shell
loop.

## 3. Code fixes (adapters and crashes only)

Production `main` is owned by `deploy/update-from-main.sh` (`--ff-only`).
A local commit on `main` makes the next update fail closed. So:

```bash
cd /opt/founder-radar/app
sudo -u radar git fetch origin main
sudo -u radar git worktree add /tmp/hermes-fix -b hermes/fix-<reason> origin/main
cd /tmp/hermes-fix
```

Edit the adapter under `radar/sources/`. Match neighbouring adapters.
Add or update a `_CHANGED` fixture if the site layout broke.

Verify:

```bash
/opt/founder-radar/venv/bin/python -m pytest tests/unit/test_sources.py \
    tests/unit/test_repair.py -q
```

If pytest is not installed, run `founder-radar sources --test <key>`
against the live page and `founder-radar doctor`. Say that the offline
suite did not run.

Durable save:

- If `git push` works: push `hermes/fix-<reason>` and tell Telegram the
  branch name. Do not merge to `main` from the VPS.
- If push is refused: `git -C /tmp/hermes-fix diff origin/main > /opt/founder-radar/logs/hermes-fix.patch`
  and paste a short summary, not the whole diff, into Telegram.

Remove the worktree when finished: `git -C /opt/founder-radar/app worktree remove /tmp/hermes-fix`.

## 4. Hard limits (do not "just this once")

- `radar/score/` — freshness gates, fund fit, discovery edge, tiering.
  Arithmetic the client can check. Not yours to retune.
- `.env`, `secrets/google-sa.json`, Telegram tokens.
- `git push --force`, `git reset --hard` on `/opt/founder-radar/app`.
- `DROP` / delete `data/radar.db`. Restore is `founder-radar db restore`.
- Turning a source off in the sheet. That is Aryan's switch.
- Solving an anti-bot challenge or logging into a paywall. Drop the
  source in the report instead.
- Starting a second daily scan while `founder-radar.service` is active
  (4 GB box; MemoryMax on the scan is 1G). Wait or skip.

## 5. Aftercare

Re-run `founder-radar repair` (no `--apply` needed) and send Telegram:

- what you diagnosed
- what `repair --apply` already did
- what you changed in code (path + branch or patch path)
- what still needs a human (keys, sheet sharing, anti-bot block)
