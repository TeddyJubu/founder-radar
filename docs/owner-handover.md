# Owner handover: running Founder Radar yourself

This guide is checked against the repository’s installer, service files, CLI and Hermes operator skill on 30 September 2026. The addresses below are the project’s deployed addresses; this document is not a new live uptime check. Run the status checks below to confirm the server today.

A **VPS** is the rented computer that stays on. **Services** are programs running there. **Timers** are its alarm clocks. **Hermes** is your chat assistant: it calls Founder Radar’s commands. The database saves companies, evidence, scores and your decisions. The Sheet holds editable settings and can mirror the results.

## 1. Know which door you are opening

| Door | Address / access | Use it for |
|---|---|---|
| Today | <https://srv1821489.hstgr.cloud/>; review username and password | Review companies |
| Kept | <https://srv1821489.hstgr.cloud/kept> | Your saved “worth contacting” and “unsure” companies |
| Dashboard | <https://srv1821489.hstgr.cloud/dashboard> | Dated history |
| Help | <https://srv1821489.hstgr.cloud/help> | Daily-use instructions |
| Teaching guide | <https://srv1821489.hstgr.cloud/guide/>; public, no review password | Learn how the system works |
| Review alias | <https://hermes.srv1821489.hstgr.cloud/>; same review login | The same Today application, **not** Hermes chat |
| Hermes / Hermex | <https://webui.srv1821489.hstgr.cloud>; WebUI app password | Chat from a browser or the Hermex phone app. Enter this URL and its app password; no custom headers or review basic-auth login |
| Telegram | Your existing approved bot conversation | Short dashboard pings and commands; not a second company database |
| Google Sheet | The existing Sheet identified by `SHEET_ID` on the server | Settings, fund rules, source switches and optional company mirror |
| Owner terminal | `ssh aryan@srv1821489.hstgr.cloud`, using the owner’s authorised SSH key | Server administration when chat is unavailable |

The optional ChatGPT Actions API is <https://actions.srv1821489.hstgr.cloud>; it uses its own bearer key, not either browser password. See [Actions setup](chatgpt-actions.md). It supports product commands, not general server administration.

## 2. Your daily and weekly routine

**Each morning:** open Today. Read the company, evidence and suggested fund. “Ready to review” and “awaiting final check” cover the whole eligible, undecided pool, not only the 20-card page. A waiting company is not approved. Press 1 to keep, 2 for unsure, or 3 to reject. Confirm saved companies in Kept. A high score is a rule match, not a promise that a fund will invest.

**Once a week:** ask Hermes for status, source health and an empty-list explanation if needed. Check the latest database backup exists, copy a backup securely off this server, and check disk space. Review any degraded source or repeated QA failure. Check your AI provider’s billing separately: Founder Radar does not provide a reliable spend total. You do not need to run another scan every morning; the server already schedules it.

**When rules change:** edit Fund Criteria, Scoring Weights, Settings or Sources in the Sheet. Keep existing fund/vehicle keys unchanged. Then rescore and run final checks. Changing a threshold moves labels; it does not change the facts. A new source needs code as well as a Sheet switch.

## 3. What to say to Hermes

Hermes has terminal tools on the VPS only when its installed configuration and permissions allow them. A chat prompt is a request, not proof that a command succeeded. Ask it to report the command’s result. Use the Founder Radar skill; never the retired `~/radar` scout.

| Plain-language prompt | Supported command / effect |
|---|---|
| “Check Radar health. Do not scan, repair or send anything.” | `founder-radar doctor`, `status`, `why-today`; reads health/state |
| “Show me Today without running a scan.” | `founder-radar today`; returns the dashboard ping |
| “Why is Today empty? Explain the counts before changing anything.” | `founder-radar why-today` |
| “Explain why COMPANY fits.” | `founder-radar show "COMPANY"`; reads evidence and scores |
| “Keep COMPANY and save the decision.” | `founder-radar decide "COMPANY" --verdict "worth contacting"`; writes your decision |
| “Reject COMPANY and save it.” | `founder-radar decide "COMPANY" --verdict "not for me"` |
| “Mark COMPANY unsure and save it.” | `founder-radar decide "COMPANY" --verdict "unsure"` |
| “Recheck up to 80 waiting companies. Do not send a message.” | `founder-radar today-qa --limit 80`; checks links/cards and writes QA results; can use AI |
| “Recalculate every company using the current rules, then run final checks. Do not publish.” | `founder-radar rescore --all`, then `today-qa --limit 80`; writes scores/approvals |
| “Synchronise the Sheet with saved decisions.” | `founder-radar sync-sheet`; reads Sheet inputs and writes its mirror |
| “Check whether publishing is safe; do not send.” | `founder-radar publish-check`; may automatically repair supported configuration drift, so **not purely read-only** |
| “Run a new search and send the approved dashboard ping.” | `founder-radar search`; scan/publish workflow, writes data and can send Telegram |
| “Send the approved morning ping.” | `founder-radar publish --send`; gated delivery |

A “done” chat reply without `decide` does not save a decision. Search/start commands can be intercepted by the Telegram plugin; its response should still point to Today, not invent a list. Never bypass failed QA with a raw send or force flag. A broken or blocked source is a reason to investigate, not to declare the company approved.

## 4. What runs automatically

These schedules use **Europe/London**, including daylight-saving changes. Check installed timers to see the actual next run.

| Timer | When | Service / purpose |
|---|---|---|
| `founder-radar-backup.timer` | 05:00 daily | `founder-radar-backup.service`: consistent SQLite backup; local copies retained about 14 days |
| `founder-radar.timer` | 06:30 daily, with up to five minutes of random delay | `founder-radar.service`: scan, then gated `publish --send` |
| `founder-radar-heartbeat.timer` | 09:00 daily | `founder-radar-heartbeat.service`: alerts when the last run is older than 26 hours |
| `founder-radar-update.timer` | About two minutes after boot, then about every five minutes | `founder-radar-update.service`: checks GitHub `main`, installs changed code and rescores |

The review site is `founder-radar-web.service`. HTTPS/login is handled by `caddy.service`. The current owner maintenance check reports system services `hermes-gateway.service` and `hermes-webui.service` running as `aryan`; the review service runs as `radar`. On a different installation, confirm the system/user service form before restarting it. The WebUI unit is maintained on the VPS rather than fully specified in this repository. `hermes-dashboard.service` is the retired control-plane dashboard and should remain disabled.

## 5. Terminal checks and permission boundaries

Connect with `ssh aryan@srv1821489.hstgr.cloud` using the owner’s authorised SSH key. These checks do not repair or send:

```bash
founder-radar doctor
founder-radar status
founder-radar why-today
systemctl list-timers 'founder-radar*' --all
systemctl --failed
systemctl status founder-radar-web.service caddy.service --no-pager
journalctl -u founder-radar-update.service -n 50 --no-pager
df -h /opt/founder-radar
```

Review logs locally before sharing them; they may contain private company data or operational details. Logs live in `/opt/founder-radar/logs/`; start with `error.log`, `run.log`, `web-error.log` and `backup.log`. Caddy logs are under `/var/log/caddy/`. Reading some system logs requires `sudo`.

Always use `/usr/local/bin/founder-radar` (normally the `founder-radar` command). Its wrapper switches from the operator account, usually `aryan`, to the service account `radar`, loads the private configuration, and uses the same database. That switch requires the owner’s configured sudo permission; the wrapper does not grant it.

`radar` may write data, logs and backups. Application code, the Python environment and privileged deployment helpers are root-owned. The installer’s narrow sudo rule lets `radar` run only the installed Hermes permission-repair helper. It does **not** grant general server administration. Hermes running under an operator with existing sudo access can perform authorised administration; its actual permissions must be checked, not assumed. Updates, service restarts, password changes and restores belong in an owner/admin terminal unless those permissions are verified.

## 6. Updates and backups

Merging reviewed code into GitHub `main` triggers the update timer. This is a production change. The updater fast-forwards the protected checkout, installs pinned dependencies, migrates the database, restarts configured services and rescores. It skips while the scheduled scan is running. It does not guarantee fresh company approvals: run `today-qa`, then inspect Today and the Sheet. `publish-check` does not send; `publish --send` does.

To request a manual update as administrator:

```bash
sudo systemctl start founder-radar-update.service
journalctl -u founder-radar-update.service -n 80 --no-pager
```

Do not change ownership of the checkout to “fix” a trusted-input failure. Follow the protected rebuild instructions in [the ops guide](ops-guide.md#trusted-auto-updates). Avoid the updater’s `RADAR_UPDATE_DRY_RUN` as a harmless preview: it can still fetch and move the checkout.

Before maintenance, make a separate consistent snapshot:

```bash
founder-radar db backup --to /opt/founder-radar/backups/before-maintenance.db --retain-days 0
sqlite3 /opt/founder-radar/backups/before-maintenance.db 'PRAGMA integrity_check;'
```

The result must be `ok`. Do not copy a live `radar.db` file directly: SQLite can have recent writes in a separate WAL file. Keep an encrypted off-server copy. The automatic job backs up the database only; separately protect `.env`, `hermes.env`, Google credentials and the operator’s `.hermes` configuration/authentication files. These contain secrets. A backup on this VPS does not protect against losing the VPS.

## 7. Restore without losing your current state

Restoring replaces saved companies and decisions with an earlier state. Choose the date deliberately and preserve a separate “before restore” backup first. This is an owner/admin maintenance operation, not a casual chat repair.

1. Wait for an active update/install and, when possible, the active scan to finish. Stop all four Founder Radar timers so another job cannot start:

   ```bash
   sudo systemctl stop founder-radar.timer founder-radar-update.timer founder-radar-backup.timer founder-radar-heartbeat.timer
   ```
2. Stop the daily scan, review service and currently installed system chat services. Pause remote jobs/chat commands:

   ```bash
   sudo systemctl stop founder-radar.service founder-radar-web.service hermes-gateway.service hermes-webui.service
   ```

   Also stop `founder-radar-chatgpt-actions.service` if installed. On another installation, use the correct system/user gateway form.
3. Make and verify a separate current database backup. Verify the chosen restore file with `PRAGMA integrity_check` too.
4. Run the verified CLI restore, replacing the example date:

   ```bash
   founder-radar db restore /opt/founder-radar/backups/radar-YYYY-MM-DD.db
   ```

   It checks integrity and refuses an active database writer. Do not use the old deployment guide’s raw `cp` rollback recipe, or delete WAL files by hand.
5. Run `doctor`. If the installed code requires schema migration, run `founder-radar db migrate`. Check whether current configuration requires `rescore --all` and fresh `today-qa`; do not publish unchecked restored results.
6. Synchronise the Sheet **after** reviewing its inputs: `sync-sheet` reads editable cells as well as writing mirrors, so a newer Sheet verdict can affect a restored database. Preserve the Sheet’s current version before choosing the desired state.
7. Restart the long-running services and timers you stopped (start the optional Actions service separately if you stopped it):

   ```bash
   sudo systemctl start founder-radar-web.service hermes-gateway.service hermes-webui.service
   sudo systemctl start founder-radar.timer founder-radar-update.timer founder-radar-backup.timer founder-radar-heartbeat.timer
   ```

   Confirm Today, Kept, backup scheduling and chat. Only send a ping after the publish gate passes.

Database restore and code rollback are different operations. If code itself is broken, stop the update timer and have the administrator deploy a reviewed compatible revision with the trusted installer. Avoid an improvised `git checkout` plus unpinned `pip install` on the live server.

## 8. Passwords and expired credentials

There is no single password for every surface. Store the owner’s access details in a password manager, not this document or a chat.

| Credential | Where the configured reference lives | Owner renewal route |
|---|---|---|
| Review login | `RADAR_WEB_USER`, `RADAR_WEB_PASS_HASH` in `/opt/founder-radar/.env` | Generate a new hash interactively with `caddy hash-password`; update the hash privately, then `sudo systemctl restart caddy`. The installer’s Caddy template reads `.env` through the service’s EnvironmentFile; restart rereads it. Check that installed arrangement locally if the server was customised |
| Hermex/WebUI password | `HERMES_WEBUI_PASSWORD` in the installed WebUI service/configuration | Inspect that service locally to identify its environment file; change the app password there and restart `hermes-webui.service`. This password is separate from Today |
| Hermes AI login/provider | Operator’s `/home/aryan/.hermes/` configuration and auth files | As the operator, first confirm the installed launcher works; then use that version’s help/setup to renew the configured provider’s login or key. Its exact provider-specific login subcommand is not verified here |
| Extraction AI | `LLM_PROVIDER` / `LLM_API_KEY` in Radar `.env` | Renew with that provider; replace privately. It is separate from Hermes’s QA login |
| Companies House | `COMPANIES_HOUSE_API_KEY` in Radar `.env` | Create/renew in the owner’s Companies House developer account; replace privately |
| Google Sheet | `GOOGLE_SA_JSON` and `SHEET_ID`; normally `/opt/founder-radar/secrets/google-sa.json` | Rotate the service-account key in Google Cloud; give its account Editor access to the intended Sheet. Delete the superseded key after testing |
| Telegram | Radar `.env` and Hermes gateway’s operator-owned configuration | Renew the bot token through BotFather; update every configured consumer of that same bot token, check allowed users/chat ID, then restart the gateway as appropriate |
| Optional Actions key | `RADAR_CHATGPT_API_KEY` in Radar `.env` and the Custom GPT’s authentication setting | Replace both privately and restart the Actions service |

If a Hermes terminal command fails before opening (for example, a missing Python package), diagnose the installed launcher and its environment through SSH; do not build a second installation or run an unverified bare maintenance command. The owner launcher and Radar QA runner were verified after repairing shared folder permissions. Scheduled scans now retain access to the protected permission-repair helper.

Restart a long-running consumer after its environment changes. The CLI wrapper rereads its files on each invocation. Recheck with `doctor`, the relevant surface, and an explicitly authorised delivery test if messaging changed. Do not run a Hermes installer/update as `radar` against the operator-owned Hermes tree. Radar’s QA sets `HERMES_DISABLE_LAZY_INSTALLS=1` so it will not self-update that installation; updating Hermes itself is an operator task.

## 9. When something fails

| Problem | First check | Repair route |
|---|---|---|
| Today loads but has no cards | `why-today`; compare ready versus waiting counts | For outdated scores, rescore; for waiting checks, `today-qa --limit 80`. For missing age evidence, inspect `hydrate-ages` before using it. Do not weaken gates just to fill the page |
| Today cannot open / bad login | Web service, Caddy and owner’s review credentials | Owner restarts the failed service or corrects the installed auth config; use SSH if every web surface is down |
| No Telegram ping | Scan timer/service, `status`, `publish-check` | Fix the blocked scan/QA/send configuration. A failed publish can be intentional protection, not a broken bot |
| Telegram chat does not answer | `systemctl status hermes-gateway.service`, gateway journal | Operator checks provider authentication, allowed users and gateway service. A healthy scan can run while chat is down |
| Hermex cannot connect | Correct `webui.*` URL, app password, `hermes-webui.service`, Caddy | Repair WebUI; do not change the phone to the `hermes.*` review alias |
| Sheet is behind | `doctor`; Google key/account access and Sheet health | Restore Editor access or valid key; then `sync-sheet`. Saved web decisions remain in SQLite |
| Source dead, blocked or changed | Sources status and QA result | A 404/410 is a dead link; robots/403/timeout means verification could not finish. Retry transient failures later; disable an unhealthy source in Sources or ask a maintainer to fix its adapter |
| Update fails | Update journal, disk, protected ownership, Git access | Owner fixes the named failure; retain the current working installation rather than forcing a reset |
| Backups missing / disk full | Backup timer, `backup.log`, `df -h` | Save a verified off-server copy; remove only selected expendable files. Never delete the live database or all backups to make space |

When escalating, send the time, affected surface, command name and redacted error. Never include `.env`, passwords, private keys or full auth files. Start with the [ops guide](ops-guide.md), [deployment notes](prd/08-deployment.md) and [Founder Radar operator skill](../hermes/skills/founder-radar/SKILL.md); for the current truth use installed service configuration and command help.
