# 05 — The Pipeline

**What happens, in order, when `founder-radar run` executes.**

Seven stages. Each has a defined input, output, failure behaviour and test. A stage never raises into the stage above it — failures are recorded and the run continues.

```
① CONFIG → ② FETCH → ③ EXTRACT → ④ RESOLVE → ⑤ ENRICH → ⑥ GATE+SCORE → ⑦ RENDER
```

---

## Stage ① — Config

**Read the Google Sheet, validate it, and never let a typo stop the run.**

```python
def load_config(sheet) -> Config:
    raw = read_tabs(sheet, ["Settings", "Fund Criteria", "Scoring Weights", "Lists", "Sources"])
    coerced, errors = coerce(raw)          # generous about what humans type
    try:
        cfg = Config.model_validate(coerced)
        write_status_column(sheet, ok=True)
        save_snapshot(cfg, is_last_good=True)
        return cfg
    except ValidationError as e:
        last_good = load_last_good_snapshot()
        if last_good is None:
            abort_with_message_in_sheet(e)     # only possible on the very first run
        write_status_column(sheet, errors=per_field_errors(e), fallback=last_good)
        return last_good
```

**The coercion layer**, before Pydantic, because humans type inconsistently:

| They type | It becomes |
|---|---|
| `yes` `y` `TRUE` `1` `✓` `on` | `True` |
| `no` `n` `FALSE` `0` blank | `False` |
| `£1.5m` `1,500,000` `1.5M` | `1500000.0` |
| `Pre Seed` `pre-seed` `PRE_SEED` | `pre_seed` |
| `GB, IE` `gb;ie` | `["GB","IE"]` |
| blank cell | **use default** — *not* zero |
| explicit `0` | **weight this at zero** — *not* default |

That last pair matters: blank and zero mean different things and must not be collapsed.

**On failure, the sheet reports its own errors.** The `Status` column of `Settings` (**column D**) is written by the pipeline: `✅` when valid, or red text like `❌ "fourty five" is not a number — using last good value 45`. Aryan never has to read a log file.

**Output:** a validated `Config` plus a `config_hash` = `sha256(canonical_json(config))`. That hash is stamped on every score, which is what makes "why did this change?" answerable.

**Test:** `test_typo_uses_last_good_and_reports_in_sheet` — corrupt a cell, assert the run completes, the last-good value is used, and the error string lands in column D.

---

## Stage ② — Fetch

**Read every enabled source. In isolation. Politely.**

```python
def fetch_all(cfg, ctx) -> list[RawItem]:
    items = []
    for adapter in enabled_adapters(cfg):
        t0 = now()
        try:
            with per_host_limiter(adapter), robots_guard(adapter):
                got = list(adapter.fetch(ctx))
            record_source(adapter.key, "ok", len(got), elapsed(t0))
            items.extend(got)
        except Exception as e:
            record_source(adapter.key, "failed", 0, elapsed(t0), error=str(e))
            log.warning("source %s failed: %s", adapter.key, e)
            continue          # ← the whole point. One broken source never stops a run.
    return items
```

**Ordering:** Track B (Companies House) runs first because it has the tightest rate-limit budget and the most valuable output. News sources run last because they are the most likely to be slow.

**Deduplication of raw items** happens here, cheaply, before anything expensive: skip any `(source_key, external_id)` already seen, and skip any URL whose `fetch_log` returns `304 Not Modified`.

**Output:** `RawItem[]` — a flat list. Downstream code never knows or cares which adapter produced which item.

**Test:** `test_one_source_failure_does_not_stop_run` — an adapter that raises immediately still leaves the other thirteen with `status = ok`.

---

## Stage ③ — Extract

**The only place an AI model is used. Boxed on all six sides.**

### 3.1 Pre-filter — free gates first

Every article that reaches the model is a chance to pollute the database, so this cascade is about **quality** at least as much as cost.

```python
def should_extract(url, title, html) -> tuple[bool, str]:
    if re.search(r"/(tag|category|author|page|archive|topics?)/", url):
        return False, "index_page"

    # NOTE the first alternative is anchored to a listicle noun. A bare ^\d+\s
    # would drop "3 years after founding, Acme raises £2m" — a real article.
    ROUNDUP = re.compile(
        r"(^\d+\s+(best|top|things|startups?|companies|founders|reasons))"
        r"|(\b(top|best|biggest)\s+\d+)|(round-?up)|(weekly|monthly)\s+(digest|wrap)"
        r"|(newsletter)|(deals? of the (week|month))|(funding round-?up)"
        r"|(\d+\s+(startups?|companies|founders)\s+to\s+watch)", re.I)
    if ROUNDUP.search(title):
        return False, "roundup_title"

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text or len(text) < 400:
        return False, "too_short"

    SIGNAL = re.compile(
        r"\b(raise[sd]?|raising|secure[sd]|closes?|closed|pre-?seed|seed round"
        r"|series\s+[a-c]|spin-?out|spin-?off|founded|launch(es|ed)?|grant"
        r"|Innovate UK|incorporat)", re.I)
    if not SIGNAL.search(text[:6000]):
        return False, "no_signal_keyword"

    orgs = set(re.findall(
        r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,2}"
        r"\s+(?:Ltd|Limited|Inc|AI|Labs|Technologies)\b", text))
    if len(orgs) > 6:
        return False, "too_many_orgs"

    return True, "ok"
```

Also: **parse JSON-LD before calling the model.** `<script type="application/ld+json">` carries `headline`, `datePublished`, `author`, sometimes `Organization` entities. It is free, structured and deterministic — use it to pre-fill fields and to cross-check what the model says.

### 3.2 The schema — one Pydantic model, two jobs

```python
class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the gate, decided by the model, part of the schema, therefore testable
    is_about_single_company: bool = Field(
        description="False for round-ups, listicles, market reports, opinion pieces, "
                    "or anything covering 3+ companies with no single subject.")
    rejection_reason: Optional[Literal[
        "roundup","market_report","opinion","not_a_startup",
        "already_large_company","no_company_identified","paywalled"]] = None

    company_name:        Optional[str] = None
    company_role:        Literal["startup", "parent", "investor", "acquirer", "university", "other"] = "startup"
    company_website:     Optional[str] = None
    one_line_description: Optional[str] = Field(None, max_length=200)
    sector:              Optional[Sector] = None        # closed enum from Lists
    stage:               Optional[Stage] = None
    hq_city:             Optional[str] = None
    hq_country_iso2:     Optional[str] = Field(None, pattern=r"^[A-Z]{2}$")
    founded_year:        Optional[int] = Field(None, ge=1990, le=2030)
    founders:            list[Founder] = Field(default_factory=list)
    amount_raised_gbp:   Optional[float] = None
    is_university_spinout: Optional[bool] = None
    university_name:     Optional[str] = None
    extraction_confidence: float = Field(ge=0.0, le=1.0)

    evidence_quote_company: Optional[str] = Field(
        None, description="Verbatim span from the article that names the company")
```

Two design choices worth keeping:

1. **The "is this even about one startup?" decision is a schema field, not a second call.** One request, one price, and it becomes a plain assertion in the golden tests.
2. **The subject role is explicit.** `company_name` must be the operating startup, not a parent, investor, fund, university or acquirer named around it. A non-startup role turns the record into `not_a_startup` rather than allowing the surrounding organisation to become a recommendation.
3. **`evidence_quote_*` gives a free, deterministic hallucination check.** After parsing, assert the quote appears verbatim in the source text (after whitespace normalisation). If it doesn't, the model invented it — drop the field and log it. This costs ~30 output tokens and catches a large fraction of extraction errors with no AI involved.

### 3.3 Calling the model

- Provider behind an `LLMClient` protocol — that protocol is also the mock seam in tests
- **Pin a dated model snapshot ID, never an alias.** An alias silently rolls the model under you and turns golden tests into flaky tests.
- `temperature = 0`, strict JSON schema enforcement on
- Truncate to title + first 1,500 words. Startup articles put who/what/how-much in the first three paragraphs; the rest is boilerplate.
- Cache key: `sha256(f"{PROMPT_VERSION}|{MODEL_ID}|{normalise_ws(text)}")`. Bumping either constant invalidates cleanly.
- Near-duplicate detection: also hash `text[200:1200]` after whitespace normalisation — this kills ~80% of syndicated copies that exact hashing misses.
- On `ValidationError`: retry **once** with the validation error appended to the prompt (this recovers most transient failures), then quarantine rather than silently drop.

### 3.4 The fallback

If the provider is unavailable after retries, `heuristic.py` runs:

- Company name from a relation-aware headline target (`X invests in Y`, `X acquires Y`) before JSON-LD `about` / the first `X Ltd|Limited` match
- Amount from a currency regex
- Date from article metadata
- Everything else `None`

Records are marked `extraction_method = "heuristic"`, `confidence = 0.3`, `needs_review = True`, and land on the `Needs Review` tab. **The run completes and exits 0.** The digest says: *"6 companies, 2 need review — AI was unavailable."*

### 3.5 Cost

At ~600 extractions/month with the recommended model: **about £2.50/month**. The gap between the cheapest credible option and the best small model is under £2.50/month, so **do not build a two-tier cheap-triage-then-expensive-extract cascade.** It would save the price of a coffee per year while adding a second prompt, a second fixture set and a second failure mode.

**Tests:** `test_extraction_matches_expected` (25 committed fixtures, served from a recorded cache, hard-failing on a cache miss; ≥95% on the round-up gate and company name, ≥85% on sector) and `test_no_hallucinations` (rate exactly 0).

---

## Stage ④ — Resolve

**Turn many mentions into one company. Deterministic. No network. No AI.**

### 4.1 Normalisation

```python
LEGAL_SUFFIXES = {"ltd","limited","plc","llp","lp","llc","cic","cio",
                  "inc","incorporated","corp","corporation","co","company",
                  "gmbh","ag","sa","sas","sarl","bv","nv","ab","oy","as","aps",
                  "srl","spa","pty"}
# Deliberately NOT stripped: group, holdings, ventures, partners, labs,
# technologies. Those are part of the trading name; stripping them causes
# false merges.

def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[​-‍﻿]", "", s)      # zero-width characters
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    toks = s.split()
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    return " ".join(toks)

def norm_key(s: str) -> str:
    return norm_name(s).replace(" ", "")
```

Domains: lowercase, strip trailing dot, strip `www.`, extract the registrable domain with `tldextract`, consistent IDN handling.

**Domain denylist** — never treat these as company identity: `linkedin.com`, `twitter.com`, `x.com`, `facebook.com`, `crunchbase.com`, `github.com`, `medium.com`, `notion.site`, `wixsite.com`, `webflow.io`, `github.io`, and **every `.ac.uk` domain**. A spinout page hosted at `eng.ox.ac.uk/spinouts/acme` gives you the company *name*, not the company *domain*.

### 4.2 The match ladder

Evaluate top-down; **stop at the first tier that matches.** Record which rule fired.

| # | Rule | Action | Confidence |
|---|---|---|---|
| 0 | Companies House number exact (8 chars, zero-padded, upper) | **auto-merge** | 1.00 |
| 1 | Registrable domain exact, both off the denylist | **auto-merge** | 0.97 |
| 2 | `norm_key` exact **and** country matches (or one unknown) | **auto-merge** | 0.95 |
| 3 | `norm_key` exact, countries **conflict** | **review queue** | 0.60 |
| 4 | Fuzzy ≥ 92 **and** rare-token guard passes **and** no country conflict | **auto-merge** | 0.90 |
| 5 | Fuzzy 84–91 | **review queue** | — |
| 6 | Fuzzy < 84 | **distinct** | — |

**Fuzzy matching: `rapidfuzz.fuzz.token_sort_ratio` over `norm_name()` output.**

> ⚠️ **Do not use `token_set_ratio`, `partial_ratio` or `WRatio`.** All three score a subset at 100, so `token_set_ratio("acme robotics", "acme robotics automotive division") == 100`. That is a guaranteed false merge on every parent/subsidiary and every product-line variant, and it is the single most common entity-resolution bug in the wild. `WRatio` is especially dangerous because it *internally falls back* to `partial_ratio` when string lengths differ, reintroducing the bug non-obviously.

For short names (`norm_name` ≤ 12 chars), also require `JaroWinkler.normalized_similarity ≥ 0.90`. Indel-based ratios are unstable on short strings — one character of difference in a six-character name costs about 17 points.

**Rare-token guard — mandatory:**

```python
GENERIC = {"labs","lab","tech","technologies","technology","systems","solutions",
           "group","holdings","ventures","partners","digital","ai","robotics","bio",
           "biotech","sciences","science","research","innovation","innovations",
           "international","global","uk","london","services","software","data",
           "health","medical","energy","capital","studio","works","co","and","the"}

def guard_ok(a: str, b: str) -> bool:
    ra = {t for t in a.split() if t not in GENERIC and len(t) > 2}
    rb = {t for t in b.split() if t not in GENERIC and len(t) > 2}
    if not ra or not rb:
        return False              # "AI Labs" vs "Tech Solutions" — refuse to fuzzy-merge
    return bool(ra & rb)          # require a shared distinctive token
```

**Placeholder blocklist — never a merge key:** `n/a`, `unknown`, `stealth`, `confidential`, `tbc`, `newco`, `tbd`, single-letter names, and anything matching `^(blue sky|company) \d+$`.

### 4.3 Merging

1. `winner.first_seen = min(a, b)` — **preserved, never lost**
2. `winner.last_seen = max(a, b)`
3. Re-point all `observation`, `identifier`, `signal`, `founder`, `company_source` rows to the winner
4. `loser.merged_into = winner.id` — a tombstone, never a delete
5. Insert a `merge_event` with the rule, the score and the exact evidence
6. The loser's name automatically survives as an `identifier(kind='alias')`

Winner selection: prefer the record with a Companies House number; otherwise the older `first_seen`.

**Transitive-chain guard.** A~B scores 93 and B~C scores 93, but A~C scores 78. Naive union-find merges all three. **Only union-find on deterministic keys (tiers 0–2).** For fuzzy edges, merge pairwise into an existing canonical record and re-verify against the canonical's *resolved* name, not against the record it happened to match. Cap fuzzy clusters at 3 members before forcing review.

**Test:** `test_entity_resolution_pairs` — 40 committed name pairs including the classic traps: parent/subsidiary, same-name-different-country, person-named companies, rebrands, and Companies House numbers with leading zeros.

---

## Stage ⑤ — Enrich

**Add the free public evidence that makes a company qualifiable.**

For each company lacking enrichment, in priority order (highest Discovery Edge candidates first), within `settings.max_enrichment_requests_per_run`:

| Step | Source | Adds | Cost |
|---|---|---|---|
| Companies House lookup by name | `/advanced-search/companies?company_name_includes=` | `companies_house_no`, `incorporated_on`, `sic_codes`, postcode | 1 request |
| Filing history | `/company/{n}/filing-history` | `SH01` → `has_share_issue` → **`stage`** | 1 request |
| Officers | `/company/{n}/officers` | founder names, roles, `appointed_on` | 1 request |
| PSC | `/company/{n}/persons-with-significant-control` | which founders actually control it | 1 request |
| Prior appointments | `/officers/{id}/appointments` | `prior_appointments` → **`founder_signal = repeat_founder`** | 1 per founder |
| Postcode → region | postcodes.io (cached) | `hq_region` → **`geography`** | ~0 after cache warms |

Each of these feeds a derivation rule in `06-scoring.md` §2. That is the point: without them a registry-sourced company has nothing to score on.

### The name-lookup disambiguation rule

`company_name_includes=` can return many matches, and picking the wrong one assigns the wrong `incorporated_on` — which is the entire age gate, and the way **every Track A company** gets its age. So:

```python
def pick_ch_match(candidates, company) -> dict | None:
    exact = [c for c in candidates
             if norm_key(c["company_name"]) == company.norm_key
             and c["company_status"] == "active"
             and within_age_limit(c["date_of_creation"])]
    if len(exact) == 1:
        return exact[0]
    return None       # ambiguous or none → leave incorporated_on NULL, set age_unknown
```

Ambiguous is not a guess. `age_unknown` keeps the company in the research pool
with "age unconfirmed — check Companies House", but it does not reach the Today
review queue. Today is the surfaced-opportunity queue, so every card must have
age evidence; the row can appear after enrichment verifies `incorporated_on`.

**Privacy filter at ingest**, in the adapter, before the database: drop `date_of_birth`, `address`, `country_of_residence` and `nationality` from every officer and PSC record. Do not merely avoid displaying them.

**Rate-limit budget** is counted in **requests, not companies** — full enrichment is 4–8 calls per company, not 2. `settings.max_enrichment_requests_per_run` (default 500) is decremented by the limiter on every call. Roughly half is reserved for officers/PSC hydration after filing-history checks so a growing queue cannot starve companies already ready for hydration. When it runs out, enrichment stops cleanly and the rest stay queued with `enriched_at IS NULL`. See `04-sources.md` §3.4a for the pass ordering.

**Test:** `test_enrichment_respects_budget` — 500 queued companies with a 500-request budget consumes at most 500 requests, never exceeds 600 in any rolling 5-minute window, and leaves the remainder queued.

---

## Stage ⑥ — Gate and Score

**No AI. No network. Pure functions. This stage is the product.**

```python
def evaluate(company, cfg) -> list[Score]:
    # 0. Derive the five scored attributes from raw evidence (06-scoring §2).
    #    Without this a registry-sourced company has nothing to score on.
    company = derive_attributes(company, cfg)

    # 1. Universal freshness gates — the fix for the client's complaint.
    #    A NULL input PASSES and sets a flag (06-scoring §1).
    gate_result = apply_freshness_gates(company, cfg)
    if not gate_result.passed:
        # one reject row PER FUND — score.fund_key is NOT NULL
        return [Score(fund_key=f.key, vehicle_key=None, tier="reject",
                      reject_reason=gate_result.reason, fund_fit_pct=0.0,
                      coverage=0.0, discovery_edge=0.0, priority=0.0,
                      explanation=f"Rejected: {gate_result.reason}.",
                      flags=gate_result.flags, config_hash=cfg.hash,
                      scorer_version=SCORER_VERSION)
                for f in cfg.funds]

    # 2. Qualification — a Track B company needs a reason to exist (06-scoring §3)
    if company.discovery_route == "registry" and \
       len(company.qualifiers) < cfg.min_qualifiers:
        mark_unqualified(company)
        return []                      # stays in the candidate pool, re-checked daily

    results = []
    for fund in cfg.funds:
        vehicle_scores, gate_flags = [], []
        for vehicle in fund.vehicles:
            # 3. Per-vehicle hard rules. NULL input passes and sets gate_unverified.
            verdict = evaluate_gates(vehicle.gates, company)
            if verdict.failed:
                continue                       # this vehicle only, not the fund
            gate_flags.extend(verdict.flags)
            vehicle_scores.append(fund_fit(company, fund, vehicle, cfg))

        if not vehicle_scores:
            results.append(Score(fund_key=fund.key, vehicle_key=None, tier="reject",
                                 reject_reason="no_eligible_vehicle", ...))
            continue

        best        = max(vehicle_scores, key=lambda s: s.pct)
        edge        = discovery_edge(company, cfg)
        prio        = cfg.weight_fit * best.pct + cfg.weight_edge * edge
        flags       = sorted(set(gate_result.flags) | set(gate_flags))
        tier, why   = tier_of(best, edge, flags, cfg)
        vehicle     = fund.vehicle(best.vehicle_key)
        explanation = explain(best, edge, company.signals, vehicle, flags)
        if why:
            explanation += f" {why.capitalize()}."

        results.append(Score(
            fund_key=fund.key, vehicle_key=best.vehicle_key,
            fund_fit_pct=best.pct, coverage=best.coverage,
            discovery_edge=edge, priority=prio, tier=tier,
            explanation=explanation, flags=flags,
            config_hash=cfg.hash, scorer_version=SCORER_VERSION))
    return results
```

Full details of gates, weights, coverage and explanations are in **[06-scoring.md](06-scoring.md)**.

Two properties this stage must have, and they are testable:

- **Reproducible.** Same company + same `config_hash` → byte-identical scores.
- **Re-runnable.** `founder-radar rescore --all` recomputes every score in the database in well under a second, so changing a weight in the sheet gives immediate feedback rather than a day's wait.

---

## Stage ⑦ — Render

**Write the sheet with the minimum possible number of API calls, then send the digest.**

```python
def render(cfg, sh, ws):                       # sh = Spreadsheet, ws = Worksheet
    user_edits = read_user_columns(ws)         # Sheet wins for Aryan's columns
    save_user_fields(user_edits)

    rows    = build_rows(query_current_state())
    current = read_sheet_row_state()
    diff    = minimal_diff(rows, current)      # coalesce adjacent changed rows

    if not diff:
        log.info("no changes — 0 write calls")
        return

    # gspread 6.x: these are DIFFERENT methods on DIFFERENT objects.
    #   Worksheet.batch_update(list_of_{range,values} dicts)  → values
    #   Spreadsheet.batch_update({"requests": [...]})         → raw API requests
    # Passing a value-range list to Spreadsheet.batch_update raises.
    ws.batch_update(diff.value_ranges, value_input_option="USER_ENTERED")  # 1 call
    sh.batch_update({"requests": diff.format_requests})                    # 1 call
    save_sheet_row_state(rows)
```

**Rules that keep it inside 10 API calls:**

- **Never** `update_cell()` or `append_row()` (singular) in a loop. 500 single-row writes = 500 requests ≈ eight minutes of `429`s. 500 rows in one `batch_update` = 1 request.
- Read the whole tab once, diff in Python, write back the minimal set of ranges.
- `value_input_option = "USER_ENTERED"` only where `=HYPERLINK(...)` formulas or real dates are needed; `RAW` everywhere else. `USER_ENTERED` will helpfully mangle `"0114"` into `114` and `"1-2"` into a date.
- All formatting, conditional rules, data validation and protected ranges go into **one** `batch_update({"requests": [...]})`.
- Chunk any single request body at ~10,000 cells.

**Column A holds the company ULID** — narrow, greyed, in a warning-only protected range. It is the join key. **Never key rows by row number**; Aryan will sort the sheet.

`warningOnly: True` on protected ranges, not hard protection — hard protection on a service-account-owned range can lock the human out of their own spreadsheet.

**The digest** then goes to Telegram via `hermes send`, falling back to a direct Bot API POST if that exits non-zero.

**Tests:** `test_no_change_means_no_writes` (a second identical run issues zero writes) and `test_render_call_budget` (a 200-row render uses ≤ 10 calls). The ≤ 10 budget in FR-7.7 covers the **whole run**: 5 config-tab reads + 1 user-column read + 1 status write + 2 render writes + 1 spare.

---

## Failure summary

| Stage | If it fails | Run continues? |
|---|---|---|
| ① Config | Last known-good config used, error written into the sheet | ✅ |
| ② Fetch | That source marked failed, others proceed | ✅ |
| ③ Extract | Retry → heuristic fallback → quarantine | ✅ |
| ④ Resolve | Ambiguous pairs go to the review queue | ✅ |
| ⑤ Enrich | Deferred to the next run via the queue | ✅ |
| ⑥ Score | Cannot fail — pure functions over validated input | ✅ |
| ⑦ Render | Rows stay `synced = 0`; next run upserts idempotently | ✅ |

**There is no failure mode in this pipeline that stops the daily run.** That is a design requirement, not an aspiration, and the chaos tests in `09-test-plan.md` prove it.
