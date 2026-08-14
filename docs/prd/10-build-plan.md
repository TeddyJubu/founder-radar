# 10 — Build Plan

**The order to build it in, with a definition of done for each phase.**

Written for an AI coding agent working alone. Each phase is independently testable and independently useful. **Do not start a phase until the previous phase's checklist is fully green** — every phase depends on the one before it being trustworthy.

Estimates assume a competent agent with this spec pack and no other context.

---

## Phase 0 — Skeleton *(half a day)*

Prove the plumbing before writing any logic.

**Build**

1. `pyproject.toml`, package layout per `02-architecture.md` §5
2. `radar/store/schema.sql` — the full schema from `03-data-model.md`, applied in one migration
3. `radar/store/db.py` — thin repository layer, hand-written SQL, no ORM
4. `radar/cli.py` — every command from `07-interfaces.md` §3 as a stub that prints "not implemented"
5. `radar/config/models.py` — the Pydantic models
6. `pytest` running with one trivial passing test
7. `founder-radar doctor` — checks env vars, database file, disk space, and prints a pass/fail table

**Done when**

- [ ] `founder-radar doctor` prints a clean table
- [ ] `founder-radar db migrate` creates every table
- [ ] `pytest` runs in under 5 seconds
- [ ] `test_founder_table_stores_no_sensitive_fields` passes *(write it now, before the temptation to add a convenient column)*

---

## Phase 1 — Gates and scoring *(1 day)* — **build this first**

> **Counter-intuitive but correct: build the scoring engine before the data pipeline.**
>
> It is pure functions with no dependencies, it is where the entire product value sits, and it is the part that fixes the client's complaint. Building it first means the highest-risk requirement is proven on day one rather than discovered on day ten. It also gives every later phase a target to feed.

**Build**

1. `radar/score/derive.py` — **the attribute derivation rules from `06-scoring.md` §2. Build this first; without it registry companies score on nothing.**
2. `radar/score/gates.py` — the freshness gates, plus the NULL-passes-and-flags policy
3. `radar/score/criteria.py` — `Criterion`, `ComponentScore`, the evaluators
4. `radar/score/fund_fit.py` — the weighted matrix, full-model percentage, coverage
5. `radar/score/discovery_edge.py`
6. `radar/score/tiering.py`
7. `radar/score/explain.py` — the deterministic sentence
8. `radar/config/defaults.py` — the four funds, **eleven vehicles with their canonical keys**, the full weight matrix, and the attribute-importance table, all seeded from `06-scoring.md` §4–§5
9. `radar/score/qualify.py` — the Track B qualification gate

**Done when**

- [ ] **`test_freshness_gates` passes — every case in `09-test-plan.md` §1**
- [ ] `test_derivation_lets_a_registry_company_shortlist` passes — **the registry-first fix**
- [ ] `test_gate_with_null_input_passes_but_flags` passes
- [ ] `test_qualification_gate` tests pass
- [ ] `test_unknown_age_cannot_reach_shortlist` passes
- [ ] `test_worked_example_metzero` passes — every number from `06-scoring.md` §11
- [ ] `test_vehicle_routing` passes — all eleven rows
- [ ] `test_unknown_never_becomes_zero` passes
- [ ] `test_sparse_evidence_cannot_look_like_a_perfect_match` passes
- [ ] `test_scoring_is_reproducible` passes
- [ ] `test_explanation_arithmetic_reconciles` passes
- [ ] `founder-radar show <fixture>` prints a full breakdown from a seeded database

**At the end of this phase the product's core claim is proven, with zero network code written.**

---

## Phase 2 — Entity resolution *(1 day)*

**Build**

1. `radar/resolve/normalise.py` — names, domains, money, dates, postcodes
2. `radar/resolve/match.py` — the precedence ladder
3. `radar/resolve/merge.py` — merge, provenance, reversibility
4. `founder-radar review` — work the fuzzy queue

**Done when**

- [ ] `test_entity_resolution_pairs` passes — all 40 pairs
- [ ] The transitive-chain test passes (A~B, B~C, but **not** A~C)
- [ ] `token_set_ratio`, `partial_ratio` and `WRatio` appear **nowhere** in the codebase — grep for them in CI
- [ ] Duplicate-audit query 9 returns zero rows against a seeded database
- [ ] A merge can be undone and the data is identical afterwards

---

## Phase 3 — Companies House *(1.5 days)* — **the source that fixes the problem**

**Build**

1. `radar/fetch/http.py` — session, retries with full jitter, timeouts, conditional GET
2. `radar/fetch/ratelimit.py` — per-host token bucket, and a hard 600/5min guard for Companies House
3. `radar/sources/companies_house.py` — the date-windowed SIC-batched sweep
4. `radar/enrich/postcode.py` — postcodes.io with a permanent local cache
5. `radar/enrich/ch_officers.py` — officers, PSC, prior appointments, **with the privacy filter at ingest**
6. `radar/enrich/ch_filings.py` — SH01 detection
7. `founder-radar backfill --days 90`

**Done when**

- [ ] A 90-day backfill uses **≤ 40 requests** and never exceeds 600 in any 5-minute window
- [ ] Every returned company has `date_of_creation` inside the requested window
- [ ] Denylisted-SIC-only companies are dropped before enrichment
- [ ] Postcodes resolve to regions; the cache means the second run makes almost no postcodes.io calls
- [ ] `test_ch_officer_ingest_drops_dob_and_address` passes
- [ ] SH01 detection works against a committed filing-history fixture
- [ ] `test_enrichment_respects_budget` passes
- [ ] Running the backfill twice creates **no duplicates**

**Sanity check before moving on:** run the backfill for real and eyeball twenty companies. Are they genuinely new? Are they genuinely tech? If the noise is overwhelming, tighten the SIC tiers or the formation-agent address filter now — not after ten more sources are wired in.

---

## Phase 4 — Extraction *(1 day)*

**Build**

1. `radar/extract/schema.py` — the Pydantic `Extraction` model
2. `radar/extract/prefilter.py` — the free cascade
3. `radar/extract/llm.py` — `LLMClient` protocol, one provider implementation, content-hash cache
4. `radar/extract/heuristic.py` — the no-AI fallback
5. `radar/extract/grounding.py` — the verbatim-quote check
6. `tests/conftest.py` — the `offline_llm` fixture that hard-fails on a cache miss
7. **The 25 article fixtures with hand-written expected JSON**

**Done when**

- [ ] `test_extraction_matches_expected` passes — ≥95% on the round-up gate and company name, ≥85% on sector
- [ ] `test_no_hallucinations` passes — hallucination rate exactly 0
- [ ] `test_heuristic_fallback_when_llm_unavailable` passes
- [ ] `pytest` makes **zero** network calls — verified by blocking the socket in `conftest.py`
- [ ] `REFRESH_LLM=1 pytest` re-records cleanly
- [ ] The cost ledger in `llm_cache` populates with real token counts
- [ ] `--no-llm` produces a complete run with heuristic records

**Note:** writing 25 good fixtures with correct expected output is the slow part, and it is worth doing properly. These fixtures are the regression suite for every future prompt change.

---

## Phase 5 — The signal sources *(2 days)*

Build the eight Tier 1 non-Companies-House adapters. Do them in this order, because it goes easiest to hardest and each teaches something the next needs.

1. **Northern Accelerator** (WordPress JSON) — establishes the JSON adapter pattern
2. **Cambridge Enterprise** (WordPress JSON) — confirms the pattern generalises
3. **Zinc VC** (WordPress JSON)
4. **BusinessCloud** (RSS, full text) — establishes the RSS pattern
5. **UKTN** (JSON + per-article fetch) — ⚠️ **never append a query string; robots disallows `/*?`**
6. **Oxford University Innovation** (HTML) — establishes the HTML pattern; gives incorporation dates directly, so no AI call needed
7. **Conception X** (HTML) — establishes snapshot-diff for undated pages
8. **Entrepreneur First** (HTML + snapshot-diff) — honour `Crawl-delay: 10`
9. **VC portfolios** (HTML, the denylist) — this is what makes `on_vc_portfolio` real
10. **GOV.UK Search API** — ten lines, free, no key

Plus `radar/fetch/robots.py` (Protego, 24 h cache, fail-closed on 5xx) and the layout-change detector.

**Done when**

- [ ] Every adapter has a fixture test and a layout-change test
- [ ] `test_one_source_failure_does_not_stop_run` passes
- [ ] `founder-radar sources --list` shows all sources with their robots verdict
- [ ] `founder-radar sources --test <key>` works for each
- [ ] `founder-radar sources --sniff <url>` finds the JSON endpoint on a WordPress site
- [ ] robots.txt is honoured, including crawl-delay
- [ ] The User-Agent contains a real, working contact URL
- [ ] The VC portfolio denylist populates `on_vc_portfolio` for at least a few known companies

**Adding UKRI Gateway to Research and the Innovate UK XLSX can wait for Phase 8** — they are quality signals, not freshness signals, and the pipeline is useful without them.

---

## Phase 6 — Sheet and config *(1.5 days)*

**Build**

1. `radar/config/loader.py` — read, coerce, validate, last-known-good fallback, status write-back
2. `radar/render/sheet.py` — batched, minimal diff, `sheet_row_state`
3. `radar/render/formatting.py` — every format request in one `batchUpdate`
4. `founder-radar sync-sheet` — create and format all twelve tabs from scratch

**Done when**

- [ ] `sync-sheet` on an empty spreadsheet creates all twelve tabs, formatted, with dropdowns and protected ranges
- [ ] `test_sheet_roundtrip` passes — all twelve tabs
- [ ] `test_no_change_means_no_writes` passes
- [ ] `test_render_call_budget` passes — ≤ 10 calls for 200 rows
- [ ] `test_user_columns_survive_a_resort` passes
- [ ] `test_typo_uses_last_good_and_reports_in_sheet` passes
- [ ] All coercion tests pass
- [ ] Editing `max_company_age_months` in the sheet and re-running **changes the results with no code change**
- [ ] Source failures appear only on the `Sources` tab

---

## Phase 7 — Telegram and operations *(1 day)*

**Build**

1. `radar/render/digest.py`
2. `radar/notify/telegram.py` — `hermes send` with direct Bot API fallback
3. `radar/notify/heartbeat.py`
4. The Hermes skill file
5. systemd units, `deploy/install.sh`
6. Backup and log rotation

**Done when**

- [ ] The digest renders correctly for a full day, a quiet day and a zero day
- [ ] `test_digest_delivered_when_hermes_is_down` passes with Hermes stopped
- [ ] All nine Telegram commands work
- [ ] `/run northstar` performs a fund-scoped run
- [ ] The timer fires on schedule and `Persistent=true` catches up after a reboot
- [ ] The heartbeat alerts when a run is stale
- [ ] Backups run and old ones are pruned
- [ ] Memory stays under 700 MB alongside Hermes

---

## Phase 8 — Tuning and polish *(1 day)*

**Build**

1. `founder-radar tune` — the threshold sweep against Aryan's verdicts
2. `founder-radar forget` — GDPR erasure with suppression
3. UKRI Gateway to Research adapter
4. Innovate UK funded-projects XLSX adapter
5. The Tier 2 adapters, as time allows
6. `docs/privacy-notice.md` and `docs/legitimate-interests.md`
7. Chaos tests
8. Performance tests

**Done when**

- [ ] All chaos tests pass
- [ ] All FR-9 operations tests pass (`09-test-plan.md` §7)
- [ ] All performance targets met
- [ ] `test_forget_removes_and_suppresses` passes
- [ ] The privacy notice is published at the URL in the User-Agent
- [ ] The full acceptance checklist from `09-test-plan.md` §9 is green
- [ ] Two consecutive real daily runs complete unattended

---

## Phase 9 — Live validation *(1 week, mostly waiting)*

Not a build phase. This is where the product gets decided.

Run daily. Aryan fills in `Verdict`. On day six, run `founder-radar tune` and read the table with him. Lock the thresholds on day seven.

**Done when**

- [ ] Five consecutive unattended runs
- [ ] Median shortlist age under 24 months
- [ ] Aryan marks ≥70% of shortlisted companies "worth contacting"
- [ ] Cost confirmed under £10/month from the actual ledger
- [ ] Thresholds locked, with the tuning table shared so he can see *why*

---

## Total: 10–11 working days of build, plus a week of live validation

| Phase | Days | Risk | What it buys |
|---|---|---|---|
| 0 Skeleton | 0.5 | Low | Plumbing |
| **1 Scoring** | **1** | **Low** | **The product's core claim, proven** |
| 2 Resolution | 1 | Medium | No duplicates |
| **3 Companies House** | **1.5** | **Medium** | **The fix for the client's complaint** |
| 4 Extraction | 1 | Medium | Reading news reliably and cheaply |
| 5 Signal sources | 2 | **High** | Volume and quality — websites change |
| 6 Sheet and config | 1.5 | Medium | The client's editable brain |
| 7 Telegram and ops | 1 | Low | Daily usability |
| 8 Tuning and polish | 1 | Low | Defensibility |
| 9 Live validation | 5 | — | Confidence |

**Phase 5 carries the most risk**, because it depends on websites that can change without warning. Mitigate it by keeping every adapter isolated, adding the layout-change detector from the start, and accepting that one or two Tier 1 sources may need to be swapped for Tier 2 ones.

---

## The order in one line

> **Prove the scoring first, then feed it — starting with the register, because that is what makes the companies young.**

---

## For the implementing agent: rules that will save you

1. **Write `test_freshness_gates` before anything else.** It is the requirement. Everything else is machinery.
2. **Derive before you score.** A Companies House record has no sector, stage, founder signal or traction signal. `06-scoring.md` §2 turns SIC codes, postcodes, filings and officers into those five attributes. Skip it and every registry company scores on one attribute, fails the coverage floor, and the whole registry-first idea is decorative.
3. **Never use `token_set_ratio`, `partial_ratio` or `WRatio`.** They score a subset at 100 and will silently merge every parent/subsidiary pair. Add a CI grep that fails the build if they appear.
3. **`None` is not `0`.** Unknown and known-zero are different facts, all the way from extraction to scoring. There is a test for this; do not "fix" it.
4. **Companies House numbers are strings.** Leading zeros, `SC`/`NI`/`OC` prefixes. Casting to int destroys them.
5. **Pin the AI model to a dated snapshot, never an alias.** An alias rolls under you and turns golden tests into flaky tests.
6. **Never `update_cell()` or `append_row()` in a loop.** 500 rows one at a time is eight minutes of `429`s; one `batch_update` is one request.
7. **Hash the extracted text, not the raw HTML.** Raw HTML changes on every load, so a raw hash never matches.
8. **A source returning `200 OK` with an empty list is the dangerous failure.** It looks like a quiet week. The layout-change detector exists for exactly this.
9. **Drop personal data in the adapter, not at render time.** Companies House hands you dates of birth and correspondence addresses; they must never reach the database.
10. **If a site blocks your honest User-Agent, drop the source.** Do not disguise the crawler. That is a one-way door on both the legal and the operational side.
11. **Every threshold, weight, region and toggle lives in the sheet.** If you find yourself typing a number into a `.py` file that a user might one day want to change, stop and put it in `Settings`.
12. **When something is `UNVERIFIED` in this pack, verify it as the first task of that phase** — do not build on it and hope.
