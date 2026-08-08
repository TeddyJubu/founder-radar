# 01 — Product Requirements

**UK Founder Radar v2.0** · Requirements and acceptance criteria

---

## 1. Who this is for

**Aryan Mishra.** A UK university student building relationships with four venture capital funds so he can act as an unpaid scout. He has no engineering background. He wants to open his phone in the morning, see five to ten startups worth a message, and know instantly which fund each one is for and why.

His success is not "I found a startup". It is **"I introduced a fund to a company they had not seen."** If a fund replies "we know them, we passed last year", the system has failed even though it technically worked.

**Secondary user: Teddy** (the developer), who needs to operate, debug and extend the system, and who has committed to the client that adding a new source later will not require rebuilding anything.

---

## 2. What success looks like

| Measure | Target | How it's checked |
|---|---|---|
| Median age of shortlisted companies | **under 24 months** | `SELECT median(age_months)` over the last 30 days of shortlist |
| Shortlisted companies with no prior funding announcement found | **≥ 60%** | `discovery_edge` component breakdown |
| Shortlisted companies already on a tracked VC portfolio page | **0** | Hard gate — this is a reject reason, so it should be structurally impossible |
| Daily shortlist size | **2–8** | Run log. Zero is acceptable and correct on quiet days. |
| Aryan's own verdict on a shortlisted company | **≥ 70% marked "worth contacting"** | The `Verdict` column he fills in |
| Duplicate companies in the sheet | **0** | Unique constraint + weekly audit query |
| Run completes despite a broken source | **always** | Chaos test in the test plan |
| Cost | **< £10/month** | Cost table in the run log |

The first row is the headline. Version 1 failed on it, twice. Everything else is secondary.

---

## 3. Scope

### In scope (v2.0)

1. Daily automated discovery of early-stage UK startups from ~14 verified public sources
2. Hard age, funding and stage gates that make old companies structurally impossible to shortlist
3. Per-vehicle fund matching for four funds and their eleven investment vehicles
4. A Discovery Edge score measuring how likely it is the fund hasn't seen the company
5. De-duplication across sources, with merge history and full provenance
6. Google Sheet output: shortlist, full pipeline, editable criteria, editable settings, source health, outreach tracker
7. Telegram digest and command interface
8. A command-line tool that does everything, so nothing is trapped inside the chat layer
9. Founder names where publicly available, with GDPR-safe handling
10. Full offline test suite

### Out of scope (v2.0) — say so plainly to the client

| Not included | Why |
|---|---|
| LinkedIn scraping | Account-risk and terms-of-service risk. Agreed with the client at the start. |
| Crunchbase / Dealroom / Beauhurst / PitchBook data | All verified in August 2026 as having **no free programmatic tier**. Cheapest relevant option is €12,600/year. |
| Product Hunt | API has commercial-use restrictions; normal scraping is blocked. |
| Automated outreach or email sending | Aryan writes his own messages. The system finds and ranks; it does not contact. |
| Insider Media, BusinessLive | Verified blocked by bot protection / robots.txt. Explicit exclusion so no time is wasted. |
| A web dashboard | The Google Sheet **is** the dashboard. Adding a second UI doubles the surface for no gain. |
| Company financials, cap tables, valuations | Not available free at this stage of company life. |

---

## 4. Functional requirements

Each requirement has an ID, a plain statement, and an acceptance test. **A requirement without a passing test is not done.**

### FR-1 — Registry-first discovery

| | |
|---|---|
| **FR-1.1** | The system queries Companies House Advanced Search with `incorporated_from` and `incorporated_to` covering a configurable trailing window (default 90 days on backfill, 10 days on daily runs). |
| **FR-1.2** | Queries are sliced into 7-day date windows and batched by SIC code group so no single query approaches the API's 10,000-result limit. |
| **FR-1.3** | Results are filtered to configured UK regions by resolving `registered_office_address.postal_code` through postcodes.io, with a local cache of postcode-prefix → region. |
| **FR-1.4** | Companies whose only SIC codes are on the shell-company denylist (`82990`, `70229`) are rejected before any further work. |
| **FR-1.5** | Surviving candidates are enriched with officers and persons-with-significant-control, giving founder names, appointment dates and control types. |
| **FR-1.6** | A company with an `SH01` (return of allotment of shares) filed within 18 months of incorporation is flagged `has_share_issue = true` — the register's fingerprint of a pre-seed round. |

**Acceptance:** given a mocked Companies House response set, a 90-day backfill produces at least one company per configured region, every returned company has `date_of_creation` inside the window, and no company has only denylisted SIC codes. Tests `test_companies_house_window_sweep` (offline, mocked) and `test_sweep_narrows_window_on_truncation`.

### FR-2 — Signal-first discovery

| | |
|---|---|
| **FR-2.1** | The system reads every enabled source adapter listed in `04-sources.md`, each on its own schedule and each independently. |
| **FR-2.2** | A source that fails (timeout, 404, layout change, bot block) is recorded as failed, does not raise, and does not prevent other sources running. |
| **FR-2.3** | News articles are pre-filtered by URL shape, title pattern, length, signal keywords and organisation density **before** any AI call. |
| **FR-2.4** | Surviving articles are converted to a structured record by a schema-enforced AI call, with a verbatim evidence quote for the company name and each founder name. |
| **FR-2.5** | Any extracted field whose evidence quote does not appear verbatim in the source text is discarded and logged as a hallucination. |
| **FR-2.6** | If the AI provider is unavailable, a deterministic fallback extractor runs, and records are marked `extraction_method = heuristic`, `needs_review = true`. |

**Acceptance:** the golden fixture suite of 25 committed articles produces the expected records with ≥95% accuracy on company name and the round-up gate, and a hallucination rate of exactly zero. Tests `test_extraction_matches_expected` and `test_no_hallucinations`.

### FR-3 — Freshness gates

| | |
|---|---|
| **FR-3.1** | A company is rejected if `age_months > settings.max_company_age_months` (default 36). |
| **FR-3.2** | A company is rejected if `total_known_funding_gbp > settings.max_total_funding_gbp` (default £3,000,000). |
| **FR-3.3** | A company is rejected if `stage` is beyond `settings.max_stage` (default `series_a`). |
| **FR-3.4** | A company is rejected if it appears on any tracked VC portfolio page in the `already_seen` source group. |
| **FR-3.5** | Every gate is evaluated before any scoring, and the rejecting gate's name is stored as the reason. |
| **FR-3.6** | All four thresholds are read from the `Settings` tab of the Google Sheet, not from code. |
| **FR-3.7** | When a company's age cannot be determined, the gate **passes** but the company is flagged `age_unknown` and cannot reach the shortlist tier. |

**Acceptance:** a table-driven test of 20 companies across every gate boundary — including exactly-at-threshold and unknown-value cases — produces the expected pass/reject with the expected reason. Test `test_freshness_gates`. **This is the test that proves the client's complaint is fixed. It is the highest-priority test in the suite.**

### FR-4 — Fund and vehicle matching

| | |
|---|---|
| **FR-4.1** | Each of the four funds has one or more vehicles, each with its own hard rules, defined in the `Fund Criteria` sheet tab. |
| **FR-4.2** | A company is evaluated against every vehicle. Failing a vehicle's hard rule excludes that vehicle only, not the fund. |
| **FR-4.3** | A fund's score is the best score across its vehicles that the company did not fail. |
| **FR-4.4** | Weighted scoring uses the attribute/category matrix in the `Scoring Weights` tab — the client's own model, preserved. |
| **FR-4.5** | Scores are reported as a percentage of the maximum achievable over **known** attributes, plus a separate `coverage` figure. |
| **FR-4.6** | Attributes with no data are excluded from both numerator and denominator by default; per-attribute policy may override this to pessimistic or assumed. |
| **FR-4.8** | The five scored attributes are **derived** from raw evidence where a source does not state them: sector from SIC codes, geography from postcode, stage from share-allotment filings, founder signal from officer history. Without this, registry-sourced companies have nothing to score on. |
| **FR-4.9** | A registry-sourced company enters scoring only once it has at least `settings.min_qualifiers` qualifying signals. Unqualified companies are re-checked on every run, never rejected. |
| **FR-4.10** | A per-vehicle hard rule whose input is unknown **passes** and sets `gate_unverified`; the company cannot then reach the shortlist. |
| **FR-4.7** | Editing the sheet and re-running produces different scores with no code change. |

**Acceptance:** a company in Sunderland scores against Northstar's Venture Sunderland Fund and fails Anticus's Yorkshire gate; a London fintech scores against Outward VC and fails DSW's SEIS fund on the golden-triangle rule. A company known only from the register derives four attributes and reaches the shortlist. Tests `test_vehicle_routing`, `test_derivation_lets_a_registry_company_shortlist`, `test_qualification_gate`, `test_gate_with_null_input_passes_but_flags`.

### FR-5 — Discovery Edge

| | |
|---|---|
| **FR-5.1** | Every company receives a 0–100 Discovery Edge score built from deterministic, evidence-backed components. |
| **FR-5.2** | Four components: company age band, press coverage **in our tracked sources**, disclosed funding (unknown scored distinctly from known-zero), and discovery route. Presence on a tracked VC portfolio is a hard gate, not a component — a value every scored company shares is a constant, not a signal. |
| **FR-5.3** | Final ranking combines Fund Fit and Discovery Edge using a configurable split (default 60/40). |
| **FR-5.4** | Shortlist tier requires **both** Fund Fit ≥ threshold **and** Discovery Edge ≥ floor **and** coverage ≥ floor. |

**Acceptance:** two companies with identical Fund Fit but different visibility rank in the expected order, and the more visible one is excluded from the shortlist when it falls below the Discovery Edge floor. Test `test_discovery_edge_ranking`.

### FR-6 — De-duplication

| | |
|---|---|
| **FR-6.1** | The same company arriving from multiple sources produces exactly one record. |
| **FR-6.2** | Matching follows a fixed precedence ladder: Companies House number → registrable domain → normalised name with matching country → fuzzy name above threshold. |
| **FR-6.3** | Merges preserve `first_seen`, union all source links, union all founders, and never delete data. |
| **FR-6.4** | Every merge is recorded with the rule that fired and the evidence, and is reversible. |
| **FR-6.5** | Fuzzy matches in the review band are queued for human review rather than auto-merged. |
| **FR-6.6** | Placeholder names (`Stealth`, `Unknown`, `N/A`, `TBC`, `Newco`) never become a merge key. |

**Acceptance:** a 40-pair fixture of company names — including known false-merge traps like parent/subsidiary and same-name-different-country — produces the expected merge decisions. Test `test_entity_resolution_pairs`.

### FR-7 — Google Sheet as interface

| | |
|---|---|
| **FR-7.1** | Twelve tabs (eleven visible + hidden `_meta`) as specified in `07-interfaces.md`, created and formatted automatically on first run. |
| **FR-7.2** | `Fund Criteria`, `Scoring Weights` and `Settings` are read at the start of every run and are user-editable. |
| **FR-7.3** | Invalid user input never crashes a run. The last known-good configuration is used and the error is written back into the sheet, in red, next to the offending cell. |
| **FR-7.4** | User-owned columns (`Verdict`, `Notes`, `Contacted`, `Fund sent to`) are read before rendering and written back to the correct row after any re-sort. |
| **FR-7.5** | Source URLs and company websites are clickable, with the raw URL preserved in a hidden column. |
| **FR-7.6** | Source failures appear only on the `Sources` tab, never in the main view. *(Direct client request, 24 July.)* |
| **FR-7.7** | A full run costs no more than 10 Google Sheets API calls. |
| **FR-7.8** | A run that changes nothing writes nothing. |

**Acceptance:** an integration test against a scratch spreadsheet creates all tabs, writes 200 rows, corrupts a settings cell, re-runs successfully using the fallback, and reports the error in the sheet. Test `test_sheet_roundtrip`.

### FR-8 — Telegram

| | |
|---|---|
| **FR-8.1** | A daily digest is pushed after each run, listing shortlisted companies with fund, scores and one-line reason. |
| **FR-8.2** | Nine commands: `/today`, `/run`, `/run <fund>`, `/fund <name>`, `/why <company>`, `/status`, `/week`, `/sheet`, `/help`. |
| **FR-8.3** | `/run northstar` performs a fund-scoped run. *(Direct client request, 24 July.)* |
| **FR-8.4** | Only allow-listed Telegram user IDs may issue commands. |
| **FR-8.5** | If Hermes is unavailable, the digest is delivered by a direct Telegram Bot API call using the same token. |
| **FR-8.6** | No business logic lives in the Telegram layer. Every command maps to a command-line invocation. |

**Acceptance:** with the Hermes gateway stopped, a run still delivers the digest. Test `test_digest_delivered_when_hermes_is_down`.

### FR-9 — Operations

| | |
|---|---|
| **FR-9.1** | The daily run is scheduled by a systemd timer, not by any application-level scheduler, so it survives upgrades and daemon crashes. |
| **FR-9.2** | Every run writes a row to `Run Log` with counts, timings, per-source status, AI token spend and cost. |
| **FR-9.3** | A heartbeat alerts by Telegram if no successful run completed in the last 26 hours. |
| **FR-9.4** | The database is backed up daily, with 14 days retained. |
| **FR-9.5** | Secrets live in a `0600` env file outside the repository and are never logged. |
| **FR-9.6** | The command-line tool is the complete interface; anything the chat layer can do, a human can do in a shell. |

**Acceptance:** `09-test-plan.md` §7 — timer enabled and scheduled, run-log row complete, heartbeat fires when stale, backups create and prune, `.env` is `0600` and never appears in logs, every Telegram command resolves to a real CLI command.

---

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Daily run duration | < 25 minutes typical, < 60 minutes hard timeout |
| NFR-2 | Memory ceiling | < 700 MB for the pipeline, so it coexists with Hermes on 4 GB |
| NFR-3 | Total monthly cost | < £10 |
| NFR-4 | Test suite runtime, offline | < 60 seconds, zero network calls, zero credentials |
| NFR-5 | Adding a new source | One new adapter file + one config row. **No changes to pipeline, scoring, or sheet code.** *(Committed to the client on 9 July.)* |
| NFR-6 | Changing fund criteria | Sheet edit only. **No code change, no redeploy.** *(Committed to the client on 9 July.)* |
| NFR-7 | Crawler politeness | robots.txt honoured, crawl-delay honoured, honest User-Agent with working contact URL, 1 s per-host floor |
| NFR-8 | Reproducibility | Same inputs + same config hash → identical scores, byte for byte |
| NFR-9 | Data protection | Founder name, role and public profile URL only. No emails, phone numbers, home addresses or dates of birth — including the partial DOB Companies House exposes. |

---

## 6. Constraints and assumptions

**Constraints**

- Server is a Hostinger KVM 1: 1 vCPU, 4 GB RAM, ~50 GB disk, Ubuntu.
- Headless browsing is expensive on 1 vCPU. Maximum **two** sources may use it, and only where no JSON or HTML route exists.
- Companies House allows 600 requests per 5 minutes across the whole application. The enrichment step is the bottleneck and must be budgeted.
- Google Sheets allows 60 writes per minute per user, and the service account is one user. Batch everything.
- Aryan is non-technical. Every user-facing string must be readable without explanation.

**Assumptions**

- The existing Google service account (`founder-finder@founder-finder-502208.iam.gserviceaccount.com`) and spreadsheet remain in use.
- The existing Telegram bot token remains in use.
- A Companies House API key will be registered — free, self-service, instant.
- Aryan will fill in the `Verdict` column, which is what makes threshold tuning possible.

---

## 7. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Companies House volume is too noisy even after filtering | Medium | High | Qualification signals (SH01, officer history, live website, grant match) gate Track B separately. Track A works regardless. Threshold sweep tool tunes it against Aryan's labels. |
| Too few companies pass the gates; quiet days feel broken | Medium | Medium | Quiet days are correct behaviour and must be *stated* in the digest: "2 today — deliberately strict." Widen the region or age window in Settings if it persists for a week. |
| A source changes layout | High | Low | Per-source isolation, health tab, and a snapshot-diff test that flags when a source's output shape changes. |
| Founder-name storage raises GDPR questions | Low | Medium | Minimal fields, documented legitimate-interests assessment, published privacy notice, one-command erasure. |
| Companies House migrates to SIC 2026 | Medium | Low | SIC list is configuration, not code. |
| Client expects volume, gets quality | Medium | Medium | The digest states the funnel explicitly: "scanned 412, 38 passed gates, 6 shortlisted." Aryan asked for 5–10 good over 20 random; the numbers should show the strictness working. |

---

## 8. Explicit traceability to client requests

Every request Aryan made in the Fiverr thread, and where it is met.

| Client said | Date | Met by |
|---|---|---|
| "Fund criteria separate from the code… a simple Google Sheet" | 9 Jul | FR-7.2, NFR-6 |
| "If we add more sources later, straightforward to extend" | 9 Jul | NFR-5, `02-architecture.md` §4 |
| "Explain why it surfaced a startup, rather than only a score" | 9 Jul | FR-4.4, `06-scoring.md` §7 |
| "Recognise it's the same company instead of creating multiple entries" | 9 Jul | FR-6 |
| "5–10 really relevant startups rather than 20 random ones" | 9 Jul | FR-5.4, tiering |
| "Companies already 5–7 years old, many already raised" | 17 Jul, 1 Aug | FR-1, FR-3 — the core of this version |
| "University spinouts, accelerator/demo day cohorts, Innovate UK" | 17 Jul, 1 Aug | `04-sources.md` Tier 1 |
| "Fund breakdown could be laid out more clearly" | 17, 24 Jul | `07-interfaces.md` §3 |
| "Don't need the Source Failed section" | 24 Jul | FR-7.6 |
| "Include the actual URL so I can verify the source" | 24 Jul | FR-7.5 |
| "Run commands directly from Telegram… finder for just one fund" | 24 Jul | FR-8.3 |
| "Before they're already well known in the VC ecosystem" | 1 Aug | FR-5 — Discovery Edge exists for this sentence |
| "Workflow is something I can understand and improve over time" | 9 Jul | Plain code, plain config, this spec pack |
| "What will ongoing costs look like?" | 9 Jul | §5 NFR-3, `08-deployment.md` §7 |
