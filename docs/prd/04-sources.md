# 04 — Data Sources

**Every source, verified live on 7 August 2026, with the exact access method.**

This is the file that fixes the client's complaint. Version 1 read portfolio pages, which are a record of companies that *already raised*. Version 2 reads the register and the places a company appears for the very first time.

---

## 1. The two tracks

### Track A — Signal-first *(someone already vetted these)*

A university spun this out. An accelerator selected it. A government panel awarded it a grant. These companies are young and credible, but other scouts can see them too. **Good volume, moderate edge.**

### Track B — Registry-first *(nobody has vetted these)*

Newly incorporated UK tech companies, straight from Companies House, then qualified by free public evidence. **Lower yield, very high edge** — this is where Aryan finds things no fund has seen.

The two tracks meet at the same de-duplication step, so a company found both ways becomes one record with two pieces of evidence — which is itself a strong signal.

---

## 2. Source ledger

Legend — **Access:** `API` · `JSON` (WordPress/CMS endpoint) · `RSS` · `HTML` · `BROWSER` (Playwright) · `FILE` (download).

### Tier 1 — build these first

| # | Source | Track | Access | Endpoint | Freq | Est. new UK cos/mo | Why it earns its place |
|---|---|---|---|---|---|---|---|
| 1 | **Companies House Advanced Search** | B | API | `api.company-information.service.gov.uk/advanced-search/companies` | daily | 200–600 candidates | **The backbone.** Only free source on earth with a true `incorporated_from`/`incorporated_to` filter. Structurally cannot return an old company. |
| 2 | **Companies House Officers + PSC** | B | API | `/company/{n}/officers`, `/persons-with-significant-control` | daily | — | Turns company numbers into *founders*. `links.officer.appointments` reveals repeat founders. |
| 3 | **Companies House Filing History** | B | API | `/company/{n}/filing-history` | daily | — | Detects `SH01` — a share allotment on a young company is a pre-seed round on the public record. |
| 4 | **Oxford University Innovation** | A | HTML | `innovation.ox.ac.uk/investing/our-portfolio-companies` | weekly | 3–5 | The only source verified to publish a literal **incorporation date** per company, plus website and department. |
| 5 | **Northern Accelerator** | A | JSON/RSS | `northernaccelerator.org/wp-json/wp/v2/posts` → `/feed/` fallback | daily | 2–4 | Covers Durham, Newcastle, Northumbria, Sunderland, York spinouts. **Direct match to Northstar's mandate.** No competing source covers it. |
| 6 | **Conception X** | A | HTML | `conceptionx.org/portfolio` | weekly | ~3 | PhD deeptech ventures **at or before incorporation** — structurally the youngest cohort in the entire list. Cohort codes CX18–CX26. |
| 7 | **UKTN** | A | JSON | `uktech.news/wp-json/wp/v2/posts/latest` | daily | 25–50 | Highest-volume UK-only funding coverage. Slugs carry the publish date. |
| 8 | **BusinessCloud** | A | RSS | `businesscloud.co.uk/feed/` | daily | 15–30 | **Full article text in the feed.** Genuine North of England coverage. Permissive robots. |
| 9 | **Zinc VC** | A | JSON | `zinc.vc/wp-json/wp/v2/posts` | weekly | 2–3 | Highest signal-to-noise of anything tested — posts are literally "Announcing Zinc's investment in X". First money in. |
| 10 | **Entrepreneur First** | A | HTML | `joinef.com/portfolio/` | weekly | ~5 | Company-builder output is pre-seed by construction. London filter, founded-year filter. Snapshot-diff for new entries. |
| 11 | **UKRI Gateway to Research** | A | API | `gtr.ukri.org/gtr/api/projects` | weekly | 5–15 | Innovate UK grant awards. Quality signal, not a freshness signal. |
| 12 | **Innovate UK funded projects** | A | FILE | `ukri.org` XLSX, updated every 2–4 weeks | monthly | 10–30 | The most current official Innovate UK award data available free. |
| 13 | **GOV.UK Search API** | A | API | `www.gov.uk/api/search.json` | daily | 5–10 | Keyless, no rate limit, day-level date stamps on Innovate UK announcements. Ten lines of code. |
| 14 | **VC portfolio pages (inverted)** | — | HTML | dsw.vc, northstarventures.co.uk, outwardvc.com, anticuspartners.com + ~20 UK VCs | weekly | — | **Used as a denylist.** A company here has already been found. Feeds the `on_vc_portfolio` flag and Discovery Edge. |

### Tier 2 — add after Tier 1 is proven

| Source | Track | Access | Endpoint | Notes |
|---|---|---|---|---|
| Bethnal Green Ventures | A | HTML | `bethnalgreenventures.com/blog` + `/portfolio` | Verified cohort pattern: "Spring 2026 cohort", 21 Apr 2026, 12 ventures |
| Cambridge Enterprise | A | JSON | `enterprise.cam.ac.uk/wp-json/wp/v2/posts` | Full content + dates |
| Edinburgh Innovations | A | HTML | `edinburgh-innovations.ed.ac.uk/news` | ⚠️ Not `ed.ac.uk/edinburgh-innovations` — that redirects to a thin page |
| UCL Ventures | A | HTML | `uclventures.com/news` | ⚠️ `uclb.com` is dead; it 302s here |
| Startups Magazine | A | JSON | `startupsmagazine.co.uk/wp-json/wp/v2/posts` | wp-json gives full content; RSS gives summaries only |
| Tech.eu | A | RSS | `tech.eu/feed/` | Full `content_html` |
| Bdaily North East | A | RSS | `bdaily.co.uk/region/north-east/rss` | Regional; excerpt only |
| Sheffield Commercialisation | A | HTML | `sheffield.ac.uk/commercialisation/commercialisation-news` | Yorkshire spinouts → Anticus |
| Carbon13 | A | RSS+JSON | `carbonthirteen.com/feed/` | Climate → Northstar |
| Founders Factory | A | HTML | `foundersfactory.com/articles/` | "Investing in X" posts |
| Techstars London | A | HTML | `techstars.com/newsroom` | Cohort announcements |
| Venture Further (Manchester) | A | HTML | `entrepreneurship.manchester.ac.uk/venture-further/` | Annual burst, ~11 winners |
| Converge (Scotland) | A | RSS | `convergechallenge.com/updates/` | |
| Antler UK | A | BROWSER | `antler.co/portfolio` | ⚠️ robots.txt **disallows** `/new-portfolio-companies/`. Only the `/portfolio` root is permitted. Crawl-delay 10. |
| Deep Science Ventures | A | HTML | `deepscienceventures.com/our-portfolio` | No dates — snapshot-diff only |

### Explicitly excluded — do not spend time on these

| Source | Why | Verified |
|---|---|---|
| **Crunchbase** | Free API tier eliminated in 2025 | ✅ |
| **Dealroom** | €12,600/yr minimum; 3-day trial excludes API | ✅ |
| **Beauhurst** | Sales-gated, "we don't offer free trials" | ✅ |
| **PitchBook** | $12k–$70k/yr | ✅ |
| **Harmonic.ai / Specter / Tracxn** | No free programmatic tier | ✅ |
| **Insider Media** | robots.txt itself is disallowed | ✅ |
| **BusinessLive** | Unreachable; aggressive bot protection | ✅ |
| **Product Hunt** | Blocks scraping; API has commercial-use restrictions | ✅ |
| **LinkedIn** | Account risk; agreed out of scope with the client | — |
| **SFC Capital** | Portfolio behind an investor-portal login | ✅ |
| **`icure.co.uk`** | A domain-for-sale parking page, not the programme | ✅ |
| **Maddyness UK** | Feed carries headlines only — empty description and content | ✅ |
| **Hult Prize UK / Blue Sky Northumbria** | No UK winner list published / could not verify it exists | ✅ |
| **Antler UK cohort page** | robots.txt **disallows** `/new-portfolio-companies/`. The permitted `/portfolio` root is a lagging dump of every company Antler has ever backed. Not crawled. | ✅ |
| **Imperial / Warwick spinout portfolio pages** | Static lists of every company the TTO has ever backed — the version-1 "already 5–7 years old" failure mode. Imperial's enterprise news URL 404s. Do not scrape these portfolios. | ✅ |
| **Durham, Newcastle, Northumbria, Sunderland, Teesside individually** | Redundant — Northern Accelerator aggregates them | ✅ |

---

## 3. Companies House — the backbone, in detail

### 3.1 Access

- Register at `developer.company-information.service.gov.uk` → Your Applications → Register an application. Free, instant.
- HTTP Basic auth: **API key as username, empty password.**
- **Rate limit: 600 requests per 5-minute rolling window.** Exceeding returns `429`; repeated breaches ban the application.
- Terms: Companies House's service owner has stated on the developer forum that *"Companies House imposes no rules or requirements on how the information on the public register is used."* You are responsible for your own UK GDPR compliance.

### 3.2 The sweep — this is the whole trick

Never let a single query approach the API's result ceiling (it returns `500` past ~10,000 items). Slice by date, which is also exactly the axis we care about.

```python
SIC_TIERS = {
  "tier1": ["62012","62020","62090","63110","63120","72190"],
  "tier2": ["72110","71121","71122","71129","26110","26120","26200","26511",
            "26600","26701","21100","21200","32500"],
  "tier3": ["62011","58210","58290","63990","64205","66190","72200","74909"],
}
SIC_DENYLIST = ["82990", "70229"]   # formation-agent dumping grounds

def sweep(days_back: int, window_days: int = 7):
    for start, end in date_windows(days_back, window_days):
        for tier, codes in SIC_TIERS.items():
            r = GET("/advanced-search/companies", params={
                "incorporated_from": start.isoformat(),
                "incorporated_to":   end.isoformat(),
                "sic_codes":         ",".join(codes),
                "company_status":    "active",
                "company_type":      "ltd",
                "size":              5000,
            })
            # MANDATORY: results are silently truncated at `size`. Tier 1 contains
            # 62020 (IT consultancy), one of the most-used codes on the register,
            # so a 7-day window CAN exceed 5,000. Without this check companies
            # vanish with no error — the exact "200 OK, looks like a quiet week"
            # failure this system is built to avoid.
            if r["hits"] > len(r["items"]):
                yield from sweep_window(start, end, codes, window_days // 2 or 1)
            else:
                yield r
```

**A full 90-day backfill of the entire UK is 39 requests** — 13 seven-day windows × 3 SIC tiers, about twenty seconds of the rate-limit budget. The daily run uses a 10-day trailing window, which at `window_days = 7` is 2 windows × 3 tiers = **6 requests**, to catch late registrations and SIC amendments.

### 3.3 Response fields used

`company_number` · `company_name` · `company_status` · `company_type` · **`date_of_creation`** · `sic_codes[]` · `registered_office_address.postal_code` · `registered_office_address.locality` · `links.self`

### 3.4 The noise filter — apply in this order, cheapest first

```
FREE (no API calls)
  1. Drop if every SIC code is in SIC_DENYLIST
  2. Resolve postcode outcode → region via postcodes.io (cached, free).
     Drop if the region is not in settings.regions_enabled.
  3. Drop if the registered office postcode is in formation_agent_address
       (these cluster hard — ~50 postcodes account for thousands of shells)
  4. Drop if the name matches a placeholder pattern (BLUE SKY \d+ LIMITED etc.)
       — but KEEP the company number: these often rename into real companies

--- from here each step costs API calls, so order matters ---
PASS 1  (1 call each)   5. Filing history → SH01 → has_share_issue
PASS 2  (2 calls each)  6. Officers + PSC.  Now drop if the only officer is a
                           corporate secretary — this check needs the data from
                           step 6, so it cannot run before it.
PASS 3  (1 per founder) 7. Officer appointments → prior_appointments (repeat founder)
```

**The formation-agent list** is seeded from the top ~50 registered-office postcodes by company count in the Companies House monthly bulk product, stored in `formation_agent_address`, refreshed monthly, and editable in the sheet. It is one of the two highest-leverage noise filters and it costs nothing.

### 3.4a The enrichment budget — count requests, not companies

A naive reading gives "300 companies × 2 calls = 600 = one window". That is wrong: full enrichment is **4–8 calls per company** (name lookup, filing history, officers, PSC, plus one per founder). 300 companies would be ~1,800 requests — three full rate-limit windows, with nothing left for the sweep, retries or `sources --test`. Companies House **bans an application** for repeated breaches rather than merely throttling it, so this is the most likely way a first run bricks the key.

**`max_enrichment_requests_per_run` is a request budget, default 500**, decremented by the limiter on every call. Roughly one third is reserved for each of filing history, officers/PSC, and prior appointments so an ongoing registry backlog cannot starve the evidence that proves a repeat founder. When it runs out, incomplete rows remain queued with `enriched_at IS NULL` or a pending appointment marker for tomorrow.

Enrichment is ordered by expected value: companies with an existing signal (spinout, grant, press) first, then newest incorporations. Pass 1 checks a bounded cohort before Pass 2, while already-checked rows move directly into hydration; this keeps a large new-incorporation backlog from starving the qualifying evidence pass.

Typical daily cost: sweep 3 + pass 1 ×150 + pass 2 ×2×60 + pass 3 ×~80 ≈ **~350 requests**, comfortably inside one window.

### 3.5 The SH01 signal — the closest free thing to "they just raised"

Poll `/company/{n}/filing-history` and look for `category == "capital"` with a `type` of `SH01` ("Return of Allotment of Shares"). An SH01 filed within 18 months of incorporation is, in practice, a pre-seed or seed round being papered. It appears on the register within days, it is free, and **no portfolio-page scraper will ever see it.**

Cross-referencing "incorporated in the last 90 days + tech SIC + in-region" against "SH01 filed since incorporation" produces a very short, very high-conviction list. This is the single highest-value query in the system.

### 3.6 The Streaming API — deferred to v2.1

`stream.companieshouse.gov.uk/companies` gives real-time incorporations. It needs a separate key and an always-on process, and it has two traps: `event.type` is **always** `"changed"` (there is no `"created"`), and `event.fields_changed[]` is frequently empty. Detection must be done on `data.date_of_creation` instead. Worth adding later; not needed for a daily run.

---

## 4. Adapter specifications

Each adapter is one file implementing the `SourceAdapter` protocol from `02-architecture.md` §4.

### 4.1 WordPress JSON adapters — the easy ones

Northern Accelerator, Cambridge Enterprise, Zinc VC, Startups Magazine, UKTN, Carbon13.

```python
GET {base}/wp-json/wp/v2/posts?per_page=50&page=1&_embed
```

Northern Accelerator keeps the JSON route as its primary path because it carries
the richest post shape. If the site returns a WAF-style 401/403/429/451, the
adapter falls back to the official `https://northernaccelerator.org/feed/`
route, which carries `content:encoded`; if both routes are blocked, the source
is recorded as degraded rather than being mistaken for a quiet day.

Returns `id`, `date` (ISO), `link`, `title.rendered`, `content.rendered`, `excerpt.rendered`. Map `date` → `published_at`, `id` → `external_id`, strip HTML from `content.rendered` → `body_text`.

> ⚠️ **UKTN exception.** Its robots.txt disallows `/feed`, `/*/feed`, `/page/` and — critically — **`/*?`**. So: use `/wp-json/wp/v2/posts/latest` (which is *not* disallowed), and **never append a query string to any UKTN URL**. The `latest` endpoint returns titles, dates and links but no body; fetch each article page individually for the text.

### 4.2 RSS adapters

BusinessCloud, Tech.eu, Bdaily, Carbon13, Converge.

Use `feedparser`. Prefer `content:encoded` over `description` — BusinessCloud and Tech.eu carry full text there, which avoids a second fetch per article. Bdaily is excerpt-only, so it needs the article fetch.

### 4.3 HTML adapters

Oxford University Innovation, Conception X, Entrepreneur First, Bethnal Green Ventures, Edinburgh Innovations, UCL Ventures, Sheffield.

Parse with `selectolax`. Every HTML adapter must:

- Store a **structure fingerprint** (a hash of the selector path set) and fail loudly with `layout_changed` if it shifts. Silent zero-results is the failure mode to avoid.
- Return `RawItem.structured` directly when the page already gives clean fields (Oxford gives name, description, website, sector, **incorporation date**, department — no AI needed at all).
- Use snapshot-diff for undated portfolio pages: store the set of `external_id`s per run; new entries are new companies with `published_at = run_date` and `date_confidence = "inferred"`.

### 4.4 Onboarding a new source — the discovery ladder

Try these in order; each is cheaper and more robust than the next.

1. `robots.txt` → `Sitemap:` lines (Protego exposes `rp.sitemaps` free)
2. `/sitemap.xml`, `/news-sitemap.xml`
3. RSS: `<link rel="alternate" type="application/rss+xml">` in `<head>`; then `/feed`, `/rss`, `/atom.xml`, `/index.xml`
4. JSON-LD: `<script type="application/ld+json">` — free structured data on most modern sites
5. Platform endpoints:

| Platform | How to spot it | Endpoint |
|---|---|---|
| WordPress | `<link rel="https://api.w.org/">` | `/wp-json/wp/v2/posts?per_page=100` |
| Next.js pages | `<script id="__NEXT_DATA__">` | Parse that tag; it's the whole page props |
| Next.js app | `self.__next_f.push([...])` | Concatenate chunks, or just parse the server HTML |
| Nuxt | `window.__NUXT__ =` | Regex the JSON literal |
| Squarespace | `Static.SQUARESPACE_CONTEXT` | Append `?format=json` to **any** URL |
| Ghost | `<meta name="generator" content="Ghost">` | `/ghost/api/content/posts/?key=…` (key is in the inline JS) |
| Webflow | `w-dyn-item` classes | No public API — use sitemap.xml + parse `.w-dyn-item` |
| Algolia | `x-algolia-application-id` in XHR | `POST https://{APPID}-dsn.algolia.net/1/indexes/*/queries` |

6. DevTools → Network → **Fetch/XHR** → reload → sort by size. The biggest JSON response is usually the content. Right-click → Copy as cURL → strip headers until it breaks.
7. **Only then** consider Playwright.

Build the sniffer once (~40 lines): given a URL, probe `/wp-json/`, `/feed`, `/sitemap.xml`, `?format=json`; regex the body for `__NEXT_DATA__|__NUXT__|ld\+json|algolia|/api/`; print what it found.

---

## 5. Politeness rules — non-negotiable

**User-Agent** (must be a real, working URL):
```
FounderRadar/2.0 (+https://<your-domain>/founder-radar-crawler; contact@<your-domain>)
```

Never impersonate a browser. Never rotate user agents. Never use residential proxies. For a crawler doing hundreds of requests a day these are pure downside — they turn "a polite bot a webmaster might allow-list" into "an evasive scraper", which is both a legal and an operational problem. If someone emails that address asking you to stop, that is a *feature*: you find out cheaply.

**robots.txt** via Protego, cached 24 h per host with the raw text kept for debugging:

- `2xx` → parse and obey
- `404` / other `4xx` → treat as fully allowed
- `5xx` / network error → treat as fully **disallowed** (fail closed), retry next run

Honour `Crawl-delay`. Entrepreneur First and Antler both set `Crawl-delay: 10`. Use `delay = max(robots_crawl_delay or 0, 1.0)`.

Also honour `X-Robots-Tag` response headers and `<meta name="robots" content="noindex">`.

**Rate limiting:** per-host token bucket — one concurrent request per host, ≥ delay seconds between starts, 8 concurrent globally. A single global sleep would serialise thirty hosts and waste hours.

**Retries** with `tenacity`, full jitter:

- Retry only on `429`, `500`, `502`, `503`, `504`, `408`, and connection/timeout errors
- **Never** retry other `4xx` — a `403` will not fix itself, and retrying it is exactly the behaviour that gets an IP banned
- Always honour `Retry-After` over the computed backoff
- Circuit-break per host: after 5 consecutive failures, mark cold for 1 hour and move on
- Timeouts: `(connect=5, read=20)` — always set both. A missing read timeout is how a nightly job hangs for twelve hours.

**Conditional requests:** send `If-None-Match` and `If-Modified-Since` from `fetch_log`. A `304` costs zero bandwidth, zero parsing and zero AI.

**Hard stops.** Never scrape behind a login. Never bypass a paywall. Never solve an anti-bot challenge. If the honest User-Agent gets blocked, drop the source — do not disguise it.

---

## 6. Source health, surfaced not hidden

Every source writes a row to `run_source` each run. The `Sources` sheet tab shows: name, last success, items found this run, 7-day average, status, and the error if any.

*(Aryan asked on 24 July to remove the "Source Failed" section from the main view. It moves here — visible when he wants it, out of the way when he doesn't.)*

Alert to Telegram when: a Tier 1 source fails **3 consecutive runs**, or **any** source returns zero items for 7 consecutive days while previously averaging more than two. The second condition catches the dangerous failure — a layout change that returns `200 OK` and an empty list, which looks like a quiet week rather than a bug.

---

## 7. Expected yield

Realistic figures from the verification research, for a daily run with North East, Yorkshire and UK-wide regions enabled.

| Stage | Per day | Per month |
|---|---|---|
| Raw items fetched — Track B (Companies House) | 250–550 | 7,500–16,500 |
| Raw items fetched — Track A (all other sources) | 60–140 | 1,800–4,200 |
| **Total raw items** | **310–690** | **9,300–20,700** |
| Survive the free pre-filters | 80–160 | 2,400–4,800 |
| AI extractions performed *(Track A prose only)* | 10–25 | 300–750 |
| New companies created | 40–90 | 1,200–2,700 |
| Pass the qualification gate *(§06 §3)* | 12–30 | 360–900 |
| Survive the freshness gates | 25–50 | 750–1,500 |
| Match at least one fund vehicle | 12–25 | 360–750 |
| **Reach the shortlist** | **2–8** | **60–240** |

Track B contributes the bulk of the raw volume but only **1–3 shortlisted per day**, because the qualification bar in `06-scoring.md` §3 is deliberately high — a company with nothing but a SIC code never reaches the sheet. Those 1–3 are the highest-edge names in the whole system: nobody else has them.

The worked funnel used in the sheet mock and the architecture page (412 → 118 → 67 → 38 → 19 → 6) is one real day inside these bands.

**Zero-shortlist days are correct behaviour and must be reported as such.** The digest says "0 today — 340 scanned, 22 passed gates, none cleared the bar." Aryan explicitly asked for 5–10 good over 20 random; the funnel numbers are how he sees the strictness working rather than assuming the system is broken.
