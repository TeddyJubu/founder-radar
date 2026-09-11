# UK Founder Radar — Teaching Guide

Interactive presentation site for students and teachers. Explains architecture,
the morning keep/reject loop, fund rules, the pipeline, and the build story.
This is **not** the live Today app.

## Run locally

```bash
cd guide
npm install
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`).

## Build

```bash
cd guide
npm run build
npm run preview   # optional local static preview of dist/
```

Production is served at **`/guide/`** on the radar hostname (Vite `base: "/guide/"`).
Static files land in `/opt/founder-radar/guide` and are public (no basic auth).

```bash
# from this machine, after npm run build:
rsync -az --delete dist/ aryan:/opt/founder-radar/guide/
ssh aryan 'chown -R radar:radar /opt/founder-radar/guide'
```

## Audience toggle

Sticky header: **Easy** (default) vs **Technical**. Same scroll path; Easy uses
plain language; Technical unlocks deeper panels (three-layer rule, CLI cheat
sheet, config_hash notes).

## Download PDF

**Download PDF** builds a multi-page A4 handout client-side with pdfcn Takumi +
Blueprint theme (architecture, morning loop, fund rules, CLI). Requires network
once to fetch Google Fonts used by the Blueprint preset.

## Content sources

Shared modules in `src/content/` are authored from:

- `docs/prd/` (architecture, pipeline, scoring, build plan)
- `radar/config/defaults.py` (canonical fund / vehicle hard rules)
- `docs/ops-guide.md` (sheet ↔ UI ↔ Telegram)

Do not invent fund marketing descriptors.

## Stack

Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui + Motion + pdfcn (Takumi).
