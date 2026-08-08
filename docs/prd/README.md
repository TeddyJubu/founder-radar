# UK Founder Radar — Specification Pack

**Version 2.0 · 7 August 2026 · Status: Ready to build**

This pack is everything needed to build and test the UK Founder Radar from scratch. It is written so an AI coding agent can implement it without asking questions, and so a non-technical reader can understand what is being built and why.

---

## The one-paragraph version

Aryan is a student who wants to become a scout for four UK venture capital funds. To be useful to those funds, he has to introduce them to startups they have **not already seen**. Version 1 of this system read the funds' own portfolio pages and startup news sites, so it kept finding companies that were five to seven years old and already funded — companies every VC in Britain already knows. Version 2 flips the approach: it starts from the **UK company register**, where every company has a legally recorded birthday, and from **the places a startup shows up for the very first time** — a university spinout announcement, an accelerator cohort, a government grant award. A company incorporated ninety days ago cannot be six years old. That single change is the fix.

---

## The problem, stated precisely

| | Version 1 (current) | Version 2 (this spec) |
|---|---|---|
| **Where it starts** | VC portfolio pages, news round-ups | Companies House register + first-appearance sources |
| **What that guarantees** | Nothing about age. Portfolio pages are a record of companies that *already raised*. | A hard floor. `incorporated_from` is a date filter the register enforces. |
| **Typical company age found** | 5–7 years | 0–36 months, enforced by a gate |
| **Already known to the funds?** | Almost always | Scored explicitly as **Discovery Edge** |
| **Fund matching** | One score per fund | Per-**vehicle** hard gates, then a score |
| **Why it surfaced** | A number | A sentence you can check the arithmetic on |

The root cause is worth naming once, because every design decision downstream follows from it: **a portfolio page is a lagging indicator.** By the time a company appears on one, a fund has already invested. Scouting from portfolio pages is structurally guaranteed to be late. The register is a leading indicator, and grant awards, spinout announcements and accelerator cohorts sit somewhere in between.

---

## Read in this order

| # | File | What it covers | Who it's for |
|---|---|---|---|
| — | **README.md** (this file) | Overview, decisions, glossary | Everyone |
| 1 | [01-product-requirements.md](01-product-requirements.md) | Goals, users, requirements, acceptance criteria | Teddy, Aryan, implementer |
| 2 | [02-architecture.md](02-architecture.md) | How the system is put together and why | Implementer |
| 3 | [03-data-model.md](03-data-model.md) | Database schema, every table and field | Implementer |
| 4 | [04-sources.md](04-sources.md) | Every data source, verified, with exact URLs | Implementer |
| 5 | [05-pipeline.md](05-pipeline.md) | The seven stages of a daily run | Implementer |
| 6 | [06-scoring.md](06-scoring.md) | Fund criteria, gates, weights, explanations | Teddy, Aryan, implementer |
| 7 | [07-interfaces.md](07-interfaces.md) | Google Sheet layout, Telegram, command line | Everyone |
| 8 | [08-deployment.md](08-deployment.md) | Server setup, secrets, cost, daily runbook | Implementer, Teddy |
| 9 | [09-test-plan.md](09-test-plan.md) | How to prove it works | Implementer |
| 10 | [10-build-plan.md](10-build-plan.md) | Phased build order with definitions of done | Implementer |
| — | [architecture.html](architecture.html) | One visual page of the whole system | Everyone |

---

## The six decisions that shape everything

### 1. Companies House is the backbone, not news

The Companies House Advanced Search API accepts `incorporated_from` and `incorporated_to`. It is free, it needs only a self-service key, and it allows 600 requests every five minutes. Thirty-nine requests sweep every tech-sector company incorporated in the UK in the last ninety days. No other free source in the world can promise "this company is new" as a matter of record rather than inference.

**Consequence:** the age problem is solved by construction, not by filtering after the fact.

### 2. Two ways in, deliberately

- **Track A — Signal-first.** A human already vetted these: university spinout announcements, accelerator cohorts, Innovate UK grants, curated UK tech news. Good companies, but other people can see them too.
- **Track B — Register-first.** Nobody has vetted these. We take newly incorporated tech companies and *qualify* them ourselves using free public evidence: a share-allotment filing (which is what a pre-seed round looks like on the register), founders with prior directorships, a live website, a matching grant award.

Track B is where Aryan's edge comes from. It surfaces companies that are not on anyone's list yet — which is exactly what he asked for.

### 3. Discovery Edge is a first-class score

Every company gets two numbers, not one:

- **Fund Fit** — how well it matches a fund's stated criteria (0–100)
- **Discovery Edge** — how likely it is that the fund has *not* already seen it (0–100)

A perfect-fit company that three funds already track is worth less to a scout than a good-fit company nobody has found. Ranking on fit alone is what produced the version 1 complaint. Ranking on both fixes it.

### 4. Funds have vehicles, and vehicles have hard rules

Research on the four funds turned up constraints that are not preferences — they are legal mandates from the public money behind the funds:

- **Northstar Ventures** runs five vehicles. Four require the company to be in North East England. The £22.5m Spinout Inspire Fund requires the company to have spun out of Durham, Newcastle, Northumbria, Sunderland or Teesside University. No connection, no investment.
- **Anticus Partners** manages Finance Yorkshire. The company must be in, or moving to, Yorkshire and Humber.
- **DSW Ventures** states that every deal must be SEIS or EIS qualifying, and its SEIS fund explicitly targets companies **outside** the London–Oxbridge triangle.
- **Outward VC** is a British Business Bank Enterprise Capital Fund, which caps round size at £5m, caps total prior fundraising at £20m, and requires at least two thirds of the executive team to be UK tax resident.

These are cheap, exact, high-precision rejects. Applying them before scoring removes most of the noise for free.

### 5. The computer does arithmetic, the AI does reading

The only place an AI model is used is turning a paragraph of English into a structured record — "this article is about Acme Robotics, a Newcastle spinout, pre-seed, robotics". Every decision that matters is plain code:

| Job | Done by |
|---|---|
| Reading an article into a record | AI model (schema-enforced) |
| Turning a Telegram sentence into a command | AI model |
| Is this company too old? | **Code** |
| What does it score? | **Code** |
| Is this a duplicate? | **Code** |
| What goes in the sheet? | **Code** |

This matters because the scoring must be **testable, repeatable and defensible**. When Aryan asks "why did this drop off the shortlist?", the answer must be a number he can check, not "the model felt differently today".

### 6. The database is SQLite; the Google Sheet is a view

The Sheet is what Aryan looks at, and it is where he edits fund criteria and marks his own decisions. But it is not the store. SQLite on the server holds the truth, so the sheet can be regenerated from scratch at any time, re-scoring 5,000 companies takes fifty milliseconds, and every test runs with no network and no credentials.

---

## What runs where

```
Managed Linux VPS (Ubuntu, 1 vCPU / 4 GB)
│
├── systemd timer, 06:30 Europe/London
│     └── founder-radar run          ← plain Python. Does all the real work.
│           ├── fetches ~14 sources
│           ├── one AI call per news article (nothing else)
│           ├── gates, de-duplicates, scores  (all plain code)
│           ├── writes to SQLite               (source of truth)
│           ├── renders the Google Sheet       (a view)
│           └── sends the digest to Telegram
│
└── Hermes Agent (always on, Telegram only)
      └── the chat surface: /today, /run, /why, /status
          It calls the same command-line tool. It holds no logic.
```

If Hermes stops, the pipeline still runs and the digest still arrives via a direct Telegram fallback. If the AI provider is down, the pipeline still runs using a plain-text fallback extractor and flags those records for review. **Nothing in this system has a single point of failure that stops the daily run.**

---

## What it costs to run

| Item | Monthly |
|---|---|
| Managed Linux VPS | ~£6 |
| AI extraction (~600 articles at Claude Haiku pricing) | ~£2.50 |
| Companies House API | Free |
| UKRI / Gateway to Research / postcodes.io / GOV.UK Search | Free |
| Google Sheets API | Free |
| Telegram | Free |
| **Total** | **under £10 / month** |

The AI cost scales with news volume, not with company count. Doubling the sources roughly doubles that £2.50 line and nothing else.

---

## Glossary

Plain-English definitions of every term used in this pack.

| Term | Means |
|---|---|
| **Source** | One place we look for startups — a website, a feed, or an API. |
| **Adapter** | The small piece of code that knows how to read one specific source. |
| **Candidate** | A raw mention of a company, before we know if it's real or relevant. |
| **Company** | A de-duplicated, confirmed record in our database. |
| **Signal** | A dated piece of evidence about a company — a grant, a cohort, a filing, an article. |
| **Gate** | A yes/no rule that rejects a company outright. Age, geography, fund mandate. |
| **Fund Fit** | 0–100. How well the company matches a fund's criteria. |
| **Discovery Edge** | 0–100. How likely it is the fund hasn't already seen this company. |
| **Coverage** | What fraction of the scoring criteria we actually had data for. Keeps the evidence level visible beside the full-model match score. |
| **Vehicle** | A specific fund pot with its own rules. Northstar has five. |
| **Tier** | Shortlist / Watchlist / Rejected. |
| **SH01** | The Companies House form filed when a company issues new shares. On a young company, this is what a pre-seed round looks like on the public record. |
| **SIC code** | The industry code a company picks when it incorporates. Rough, self-declared, but a useful first filter. |
| **SEIS / EIS** | UK tax schemes. SEIS needs the company to be under 3 years old; EIS under 7. Some funds can only invest in qualifying companies, which makes this a hard gate. |
| **ECF** | Enterprise Capital Fund — British Business Bank money with its own eligibility rules. Outward VC is one. |
| **Provenance** | The record of where each fact came from, so every claim can be traced to a source URL. |

---

## Verification status of the research behind this pack

Every source, API and price in this pack was checked against live documentation on **7 August 2026**. Where something could not be confirmed, it is marked `UNVERIFIED` in the file that uses it, with a note on what to check at build time. The implementer should treat those markers as the first tasks in each phase, not as footnotes.

Three things are known to be volatile and should be re-checked before launch:

1. **Companies House SIC list** — ONS has published UK SIC 2026; Companies House still uses SIC 2007. Keep the code list in configuration, never hard-coded.
2. **AI model pricing** — the recommended model and its price are in configuration for this reason.
3. **Source page layouts** — websites change. Every adapter must fail independently without stopping the run, and report its health in the sheet.
