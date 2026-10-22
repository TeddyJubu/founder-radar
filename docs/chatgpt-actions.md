# ChatGPT Actions — Custom GPT control plane

Authenticated HTTPS API so a **Custom GPT** can run UK Founder Radar **product
ops** (Today / Kept / scan / decide / publish) without shell or infra access.

Configured production base URL (not deployed or verified by this local change): `https://actions.srv1821489.hstgr.cloud`

## What it is

- OpenAPI Actions (not legacy ChatGPT Plugins).
- Loopback service `founder-radar-chatgpt-actions.service` on `127.0.0.1:8790`.
- Caddy terminates TLS at `actions.srv1821489.hstgr.cloud` (no Caddy basic auth —
  the Bearer API key is the gate).
- Every Action maps to an allowlisted `founder-radar` argv. No arbitrary shell,
  no systemd, no deploy, no `.env` edits, no `forget`, no `db restore`.

## Setup (Custom GPT)

1. ChatGPT → **Create a GPT** → **Configure** → **Actions** → **Create new action**.
2. **Import from file** (or paste) the schema at
   [`deploy/chatgpt-actions.openapi.yaml`](../deploy/chatgpt-actions.openapi.yaml)
   on the box: `/opt/founder-radar/app/deploy/chatgpt-actions.openapi.yaml`.
3. **Authentication** → **API Key** → Auth Type **Bearer**.
4. Paste the value of `RADAR_CHATGPT_API_KEY` from the VPS
   `/opt/founder-radar/.env` (generate with `openssl rand -hex 32` if missing).
   - Header name ChatGPT sends: `Authorization`
   - Header value shape: `Bearer <RADAR_CHATGPT_API_KEY>`
5. Save the GPT. Test **Actions** → `status` or `health` first.

Do **not** commit the key, paste it into `AGENTS.md`, or put it in chat logs
you will keep.

## Instructions to paste into the GPT

Use (or adapt) this system-style instructions block:

```text
You operate UK Founder Radar via Actions only. You never invent Fit/Edge scores,
company lists, or fund criteria — only report what the Actions/CLI return.

Surfaces:
- Today / Kept live on the web dashboard. Prefer linking dashboard_url from
  Action responses instead of pasting long company lists into chat.
- Use showCompany only when the user asks about one named company.
- decide must use verdict exactly: "worth contacting" | "not for me" | "unsure".
- publish/startPublish with send=true may Telegram-ping Aryan only after the CLI gate.
- startSearch, startRescore, and startPublish are async. Supply a stable
  Idempotency-Key for each operation and reuse it for transport retries.
- Poll jobStatus until done, partial, failed, or interrupted. Only done is full
  success. Stop on partial/failed/interrupted; do not auto-publish or create a
  new key to replay. While running, link the Today dashboard.
- Search already writes SQLite and syncs the Google Sheet. send=false suppresses
  Telegram only; it is not a dry run and does not suppress QA/Sheet changes.

Out of scope (refuse): systemd, deploy, git, editing .env, GDPR forget,
database restore, arbitrary shell, inventing scores.
```

## Endpoints (summary)

| Action | Method | Notes |
|--------|--------|-------|
| health | `GET /health` | No auth |
| today | `GET /v1/today` | Counts + dashboard URL |
| status | `GET /v1/status` | |
| doctor | `GET /v1/doctor` | |
| whyToday | `GET /v1/why-today` | |
| showCompany | `GET /v1/companies/{name}` | |
| fundTop | `GET /v1/funds/{key}` | northstar \| dsw \| outward \| anticus |
| decide | `POST /v1/decide` | `{name, verdict}` |
| publishCheck | `POST /v1/publish-check` | |
| publish | `POST /v1/publish` | Legacy synchronous; strict `{send?: boolean}`, default false |
| todayQa | `POST /v1/today-qa` | Veto only — does not rewrite scores |
| startSearch | `POST /v1/jobs/search` | Async; no Telegram send |
| startRescore | `POST /v1/jobs/rescore` | Async; optional `{all: true}` |
| startPublish | `POST /v1/jobs/publish` | Async existing CLI gate + QA; `{send: false}` by default |
| jobStatus | `GET /v1/jobs/{id}` | Poll |

## Retry-safe async contract

Use the async endpoints for automation. Send `Idempotency-Key`, with 1–128 ASCII
letters, digits, `.`, `_`, `:`, or `-`. Give search and publish separate keys;
retain each key across HTTP retries. No key means a new job for compatibility,
so unkeyed requests and the legacy synchronous publish endpoint are **not**
retry-safe. Keys are scoped across all three async operation types.

- New job: HTTP 202, `{ok:true, accepted:true, reused:false, job_id, status, poll}`
- Same normalized command/arguments and key: same job, `reused:true`; HTTP 202
  while active, HTTP 200 when terminal. It is never executed again
- Same key with different command/arguments: HTTP 409, `error:idempotency_conflict`
- Another API mutation active: HTTP 409, `error:mutation_in_progress`, plus
  `job_id`/`poll` when known. No new job is created; retry later with the same key
- Invalid input: HTTP 400. `send`, `no_llm`, `all`, and `all_companies` accept
  literal JSON booleans only, not strings, numbers, or null. Conflicting aliases
  and unknown async request fields are rejected
- Unreadable persisted jobs fail closed with HTTP 503, `error:job_store_unavailable`

Poll returns `{ok:true, job:{id, command, status, exit_code, ...}}`. Outer `ok`
means the HTTP lookup succeeded, not that the command succeeded. Terminal states:

- `done`: CLI exit 0
- `partial`: **search only**, CLI exit 1; review problems, do not auto-publish
- `failed`: other nonzero exits, including CLI gate refusal and timeout
- `interrupted`: worker/restart outcome is uncertain; side effects might have
  completed. Review SQLite, Sheet, and delivery state before any new operation

Terminal jobs include stdout/stderr and parsed `result` when stdout is valid
JSON. Search data may be under `job.result.run`; there is no invented top-level
run id. Publish can emit ordinary text, so `result` is optional. Only the existing
CLI controls the publish gate and Today QA; the API exposes no bypass flags.

## Persistence and concurrency limits

Job acceptance is written atomically and fsynced before the worker starts. The
local POSIX filesystem directory defaults to `$RADAR_ROOT/data/chatgpt_jobs`, or
`RADAR_CHATGPT_JOBS_DIR`. Keep it persistent and retain records for the lifetime
of client retries: deleting records also deletes their idempotency protection.
Do not share this directory over NFS or between hosts.

One filesystem lease covers this API's search, rescore, publish, decide,
publish-check, and today-qa operations across threads/processes using the same
job directory. It does **not** serialize separate CLI, web UI, Hermes, or systemd
runs. Avoid those overlapping producers before deploying an orchestration change.

The CLI inherits the lease so a surviving CLI blocks new API mutations after an
API restart. Run the API and CLI under the same service user with a direct
executable or a descriptor-preserving wrapper; a `sudo` user transition can close
the inherited lease. The installed service already uses `radar`. On timeout,
the runner kills the CLI process group and performs bounded cleanup, including
ordinary Hermes descendants; allowlisted commands must not daemonize/detach.

Once no worker/CLI holds the lease, queued/running records become `interrupted`
on startup, polling, or next admission. They are never resumed or replayed.
An interrupted publish requires operator review, even if its previous attempt
might already have sent a message. A new key is a deliberate new operation,
not a recovery mechanism. This provides at-most-one execution per retained key,
not an exactly-once guarantee for external side effects.

## Ops on the VPS

```bash
# Key (once)
sudo grep -q '^RADAR_CHATGPT_API_KEY=.\+' /opt/founder-radar/.env \
  || echo "RADAR_CHATGPT_API_KEY=$(openssl rand -hex 32)" | sudo tee -a /opt/founder-radar/.env

sudo systemctl enable --now founder-radar-chatgpt-actions.service
sudo systemctl reload caddy   # or restart after install.sh copies the fragment

curl -sS -H "Authorization: Bearer $(sudo grep '^RADAR_CHATGPT_API_KEY=' /opt/founder-radar/.env | cut -d= -f2-)" \
  https://actions.srv1821489.hstgr.cloud/v1/status
```

Env vars (see `.env.example`):

- `RADAR_CHATGPT_API_KEY` — required; service refuses to start without it
- `RADAR_CHATGPT_ACTIONS_PORT` — optional, default `8790`
- `RADAR_CHATGPT_JOBS_DIR` — optional persistent local job/lock directory

## Related

- CLI: `founder-radar` (Telegram / web / Hermes call the same binary)
- Hermes skill: product ops via CLI; this API is the ChatGPT-shaped twin
- Deploy: `deploy/founder-radar-chatgpt-actions.service`,
  `deploy/chatgpt-actions.caddy`, wired from `deploy/install.sh`
