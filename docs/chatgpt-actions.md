# ChatGPT Actions — Custom GPT control plane

Authenticated HTTPS API so a **Custom GPT** can run UK Founder Radar **product
ops** (Today / Kept / scan / decide / publish) without shell or infra access.

Live base URL: `https://actions.srv1821489.hstgr.cloud`

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
- publish with send=true is the only path that may Telegram-ping Aryan.
- startSearch and startRescore are async: poll jobStatus until status is done
  or failed. While running, tell the user to refresh the Today dashboard.

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
| publish | `POST /v1/publish` | `{send?: bool}` |
| todayQa | `POST /v1/today-qa` | Veto only — does not rewrite scores |
| startSearch | `POST /v1/jobs/search` | Async; no Telegram send |
| startRescore | `POST /v1/jobs/rescore` | Async; optional `{all: true}` |
| jobStatus | `GET /v1/jobs/{id}` | Poll |

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

## Related

- CLI: `founder-radar` (Telegram / web / Hermes call the same binary)
- Hermes skill: product ops via CLI; this API is the ChatGPT-shaped twin
- Deploy: `deploy/founder-radar-chatgpt-actions.service`,
  `deploy/chatgpt-actions.caddy`, wired from `deploy/install.sh`
