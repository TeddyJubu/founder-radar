## Learned User Preferences

- Prefer company-first results (company as the unit of output; articles only as sources), not article-centric cards that force opening and scanning pieces.
- Prefer the web Today/Kept UI over the Excel sheet for daily review, but still want a simple durable place to track selected companies (Kept).
- Prefer explicit “unknown” over guessing on company cards; keep descriptions clean and scannable for a ~10-company morning pass.
- Scoring should read as strength relative to the target funds; Fit/Edge-style scores that look flat or opaque across results are not useful.
- Rejected companies must not resurface on Today after a reject/“not for me” decision.
- Geography focus is UK-based startups; non-UK companies should not appear on the morning list.
- Treat Companies House as verification / incorporation age evidence, not the primary way to surface high-quality startups; lean on signal sources for quality discovery.
- Telegram keep/reject/actions must update the same store as the dashboard and sheet (CLI/UI decisions), not stay chat-only.
- Fund criteria shown in product must match the real fund rules; do not invent descriptors (e.g. “government-backed” for Outward) that are not in criteria.
- Prefer Hermes to own hard ops and run a pre-publish QA check so bad Today lists are not shipped; treat founder-radar as a tool Hermes drives.
- When ops are needed on the production VPS, run the commands there rather than only handing back copy-paste instructions.
- Client wants an interactive teaching guide for architecture, usage, and how the system was built, with Easy/Technical audience modes; Easy copy must stay kid-clear plain language.

## Learned Workspace Facts

- This repo is UK Founder Radar: a daily early-stage UK startup scan matched to four funds — Northstar, DSW, Outward, and Anticus.
- Day-to-day surfaces are Google Sheet ↔ Today/Kept/Dashboard web UI ↔ Telegram digest; the `founder-radar` CLI is the real interface Telegram and the UI call.
- Kept is SQLite verdict storage (`worth contacting` / `unsure`); rejects (`not for me`) are stored for tuning but never listed on Kept.
- Hermes must run `founder-radar decide` for Telegram keep/reject/unsure; chat-only replies do not update Today, Kept, or the sheet.
- Stage ⑥ gate+score is deterministic (no AI, no network); AI may extract prose, map chat to commands, and run Today QA veto — not invent scores or add companies to the sheet.
- Morning pipeline includes Hermes Today QA before render/publish; `founder-radar today-qa` re-runs that check.
- Production install lives on the VPS under `/opt/founder-radar` (service user `radar`); local web API is typically `http://127.0.0.1:8787`; SSH host alias `aryan` reaches that box.
- Ops diagnostics center on `founder-radar doctor`, `why-today`, and rescoring when fund criteria / `config_hash` drift; deploy pull script is `deploy/update-from-main.sh`.
- Teaching guide is the Vite + React + shadcn app in `guide/` (pdfcn PDF); production serves it publicly at `/guide/` from `/opt/founder-radar/guide` with no basic auth (Today/Kept stay password-protected).
- Primary docs: `docs/prd/` for product spec, `docs/ops-guide.md` (also `/help` in the Today prototype) for sheet ↔ UI ↔ Telegram runbook, and the public teaching guide at `/guide/`.
