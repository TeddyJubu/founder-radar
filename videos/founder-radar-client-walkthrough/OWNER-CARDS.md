# Owner handover: copyable cards

Use alongside the narrated walkthrough. These are examples, not actions already performed.

## Your control room and logins

### Review companies

https://srv1821489.hstgr.cloud/
/kept · /dashboard · /help
Review username + password

### Talk to Hermes

Hermex / browser:
https://webui.srv1821489.hstgr.cloud
App password · no custom headers

### Rules and teaching

Your shared Google Sheet link
Sources · Fund Criteria · Settings
Public guide: review host + /guide/

hermes.srv1821489.hstgr.cloud is a review-page alias, not the Hermes chat address.

## Ask Hermes for an action you can verify

### Scan or search

Run a fresh Founder Radar scan.
Complete the final checks, then give me the Today link.
Run a new UK company search.

### Save a choice

Mark [exact company] worth contacting.
Save it with founder-radar decide.
Show that it appears in Kept.

### Change a setting

Ask Hermes to explain the exact row and proposed change.
Edit the cell yourself in Google Sheet.
Then ask Hermes to rescore and complete QA.

A chat acknowledgement is not proof. Ask for the saved result and Today link.

## Your daily and weekly checklist

### Each morning

Open Today.
Check ready versus waiting.
Review evidence and save choices.
Check that Kept contains your picks.

### Each week

Ask for latest successful scan.
Check source health and Sheet sync.
Check free disk and newest backup.
Review any failed service or alert.

### Each month

Check real fund criteria.
Look for a source stuck at zero.
Review provider costs and renewals.
Check your off-server backup.

A source returning zero repeatedly deserves investigation, even when it says OK.

## Three useful checks, in plain language

### Is the system healthy?

READ ONLY
founder-radar doctor
Checks configuration, saved data, credential presence and disk. Check live access separately.

### Why is Today thin?

READ ONLY
founder-radar why-today
Explains counts, gates and what is waiting.

### Finish company checks

UPDATES CHECK STATUS
founder-radar today-qa --limit 80
Checks pending candidates; can withhold bad cards.

Chat works but final QA repeatedly fails? Ask Hermes to check shared access, use the installed repair, then recheck.

## What runs on the VPS

### Website

founder-radar-web.service
Runs as radar
Caddy serves the secure public address

### Hermes conversations

hermes-gateway.service
hermes-webui.service
Both run as aryan

### Scheduled jobs

founder-radar.timer
founder-radar-backup.timer
founder-radar-heartbeat.timer
founder-radar-update.timer

Use /usr/local/bin/founder-radar; it safely switches to the radar account.

## Updates, schedules and sensible reboots

### London-time schedule

05:00 database backup
06:30 scan + up to 5 minutes delay
09:00 missed-run heartbeat
Main code checked about every 5 minutes

### Before an update

Confirm a recent backup.
Check running scans and current versions.
Use the trusted updater.
Test Today, Hermex and final QA afterwards.

### Before a reboot

Check /var/run/reboot-required.
Ask what needs it and why.
Pick a quiet time; confirm the plan.
Verify services after the machine returns.

Security-update timers are enabled. Automatic reboot is not promised.

## Backups protect choices. Recovery needs a plan.

### Daily database snapshots

/opt/founder-radar/backups/
radar-YYYY-MM-DD.db
14 days retained
Ask for newest date + integrity check

### Complete recovery copy

Database + private configuration
Hermes configuration + Sheet rules
Code/version information
Encrypted copy in your own off-server storage

### Before restoring

Confirm the exact backup and lost changes.
Stop writers and relevant timers.
Back up the current state first.
Use Founder Radar’s restore command, then check everything.

Never copy over a live database or send a secrets archive in ordinary chat.

## When Today, Telegram or Hermex stops

### Today will not open

Check the address and review login.
Ask for web + Caddy service status.
If it opens but is empty: doctor + why-today.
An awaiting check is not a website outage.

### Telegram is silent

Send a simple message.
Check gateway, latest scan and publish gate.
If Hermex works, use it to request diagnosis.
Do not repeatedly trigger scans.

### Hermex will not connect

Use the webui address and app password.
Try that same address in a browser.
Check hermes-webui.service.
Use the VPS console if all chats are down.

Ask for a targeted repair, then verify the original surface. A whole-server reboot is not the first step.

## When the Google Sheet is behind

### First diagnose

Run doctor.
Check service-account Editor access.
Check the Sheet link and configuration Status cells.
Check the last successful sync.

### Catch up the mirror

Ask Hermes to run:
founder-radar sync-sheet
Then verify the intended rows.
This writes the Sheet mirror.

### Rules changed?

Do not rename fund or vehicle ID keys.
Invalid settings may use the last-good copy.
Check warnings, rescore and complete QA.
Do not clear saved verdicts to fix sync.

A failed mirror write does not erase a choice already saved in SQLite.

## Own the accounts and protect the keys

### Access inventory

Hostinger VPS account
Google Sheet + Google Cloud project
GitHub + authorized server access
Telegram bot + AI-provider accounts
Companies House developer key

### Passwords and renewals

Private password manager
Recovery contacts + two-factor authentication
VPS and provider renewal dates
Review actual billing, not old estimates

### Rotate safely

Prepare the new credential privately.
Update its protected server location.
Test the affected connection.
Revoke the old credential after success.

No account-ownership transfer or off-server backup is implied by this video.

## Change passwords without losing access

### Today review password

Administrator terminal:
caddy hash-password
Replace RADAR_WEB_PASS_HASH privately in .env.
Restart Caddy; test the new review login.

### Hermex app password

Identify the installed Web UI environment file without displaying it.
Update HERMES_WEBUI_PASSWORD privately.
Restart Web UI; update the phone login.

### Bot or provider access

Telegram token: renew through BotFather; update every consumer.
AI login: inspect the working installed version first.
Test before revoking old access.

Use your administrator access or verified Hermes permissions. Never paste secrets into chat.

## You can run the next day yourself

### Health request

Check Founder Radar on the VPS.
Diagnose only first. Show latest scan, ready/waiting counts, failed services, disk, backup date and Sheet access. Do not print secrets.

### Repair request

Explain the cause and smallest repair.
Take a backup where needed.
Ask before restore, delete, reboot or key rotation.
After repair, test the original problem.

### If chats are down

Use your VPS provider console, or:
ssh aryan@srv1821489.hstgr.cloud
Your own public key must be authorized.
Use the written owner handover checklist.

Review → ask clearly → verify the saved result → keep a recovery copy you control.
