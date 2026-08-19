# Client Issues Regression Plan

**Goal: prove that none of the issues Aryan raised in the conversation still exist.**

Every numbered issue below is from the client-feedback list compiled from the
conversation (24 issues, grouped A–I). Each entry says how we verify it is gone:
an existing automated test (by name), a **NEW** test (implemented — §3), a
live check (§4), or a documentation check (§5).

Status legend:

- ✅ **Covered** — an automated test already pins this. If it passes, the issue
  cannot silently return.
- ✅ **NEW** — a test added by this plan (§3) now pins the exact complaint.
- 🔧 **Live** — cannot be proven from the repo alone (it is about the deployed
  box); a live check is specified.
- 📄 **Docs** — the complaint was "I need to understand X"; verified by content
  checks, not code.

## 0. How to run

```bash
uv run pytest                     # Tier 1 — offline unit suite (~5s, no keys, no network)
uv run pytest -m browser          # Tier 2 — real-browser suite against prototype/server.py
uv run pytest -m integration      # Tier 3a — needs scratch spreadsheet + Companies House key
uv run pytest -m live             # Tier 3b — weekly source-reachability sweep (network)
# §4 live acceptance checks run on the deployed VPS, not here.
```

**Baseline (17 Aug 2026):** `432 passed, 108 deselected in ~5s` — the offline
suite is green, including all eight new guards from §3. The deselected 108 are
the browser / integration / perf / live / llm / eval tiers, which need their
own environments (below).

---

## A. Sourcing / data quality

### A1 — Companies too old (founded 5–7 years, even ~a decade) — ✅
His most repeated complaint (initial feedback, Aug 12, Aug 14).

- `test_freshness_gates` — age 36.1/60/84 months all rejected
  (`max_company_age_months`, default 36).
- `test_median_shortlist_age_stays_under_24_months` — the headline metric:
  median shortlist age must stay under 24 months.
- `test_companies_house_window_sweep` + `test_sweep_narrows_window_on_truncation`
  — the register sweep enforces `incorporated_from`/`incorporated_to` and
  cannot return an out-of-window company (structural fix; 39 requests per
  90-day backfill).
- `test_today_requires_verified_age_and_uk_presence` — Today re-applies the
  age gate at the acceptance boundary, so a stale watchlist row cannot leak an
  old company into the queue.
- `test_track_b_end_to_end` — a registry company older than the window is
  rejected end to end.

### A2 — Companies that already raised funding — ✅
- `test_freshness_gates` — funding over £3m rejected; `max_total_funding_gbp`.
- `test_unknown_funding_is_not_known_zero` — unknown funding is *not* treated
  as zero (so "no funding known" ≠ "unfunded"), and the funding gate tests pin
  that unknown funding passes but is flagged.
- `test_portfolio_company_is_gated_not_just_scored` — being on a tracked VC
  portfolio is a hard reject (`already_on_vc_portfolio`), which also covers
  "already raised" in the visible sense.

### A3 — US / Dubai companies surfacing despite UK-only target — ✅ NEW
- `test_freshness_gates` — `country=US` rejected by `min_uk_presence`.
- Companies House is the UK register — structurally UK.
- News adapters stamp `hq_country_iso2: "GB"` (entrepreneur_first, bethnal_green,
  conception_x, oxford_innovation…).
- **NEW** `test_foreign_company_from_news_is_gated` (§3.2) — the one remaining
  path (a foreign company arriving via a news article) now has an end-to-end
  pin: extraction identifies the HQ country, and the gate rejects it.

### A4 — Parent / investing company shown instead of the actual startup — ✅ NEW
- The extraction prompt forbids picking the parent
  (`radar/extract/llm.py`: "Never use the parent company"), and `CompanyRole`
  includes `parent` / `investor` / `acquirer`.
- Entity resolution keeps near-identical corporate-role names apart:
  `test_entity_resolution_pairs` includes the parent/investor denylist
  (`group`, `holdings`, `parent`, …) so "Acme Group" never auto-merges with
  "Acme Robotics".
- **NEW** `test_parent_role_record_never_resolves_to_a_company` (§3.1) — a
  `parent`-role record is refused at the resolve stage (no company row), with
  a positive control proving the same article naming the operating startup
  resolves fine.

### A5 — Expand sources: university spinouts, accelerators, Innovate UK — ✅ NEW
The requested categories all exist as adapters:

| Category | Adapters |
|---|---|
| University spinouts | cambridge_enterprise, oxford_innovation, ucl_ventures, edinburgh_innovations, sheffield, converge, entrepreneur_first |
| Accelerators / cohorts | northern_accelerator, conception_x, zinc_vc, founders_factory, techstars_london, carbon13, bethnal_green |
| Innovate UK / grants | innovate_uk, ukri_gtr, govuk_search |

- `test_phase8_sources.py` — grant adapters parse committed fixtures, detect
  layout change, dedupe, filter out universities/large companies, and register.
- `test_source_registry.py` — every registered adapter passes shape checks.
- **NEW** `test_client_requested_source_categories_are_registered_and_enabled`
  (§3.8) — each of the three categories has a registered adapter AND at least
  one enabled by default, so the request cannot silently become
  sheet-configuration nobody switched on.
- **Finding (RESOLVED):** the *dedicated* Innovate UK adapters
  (`innovate_uk`, `ukri_gtr`) are registered, fully tested, and now in
  `DEFAULT_SOURCES` — enabled by default, no longer satisfied by
  `govuk_search` alone. Pinned by
  `test_dedicated_innovate_uk_feeds_are_enabled_by_default`.

### A6 — "Why does ChatGPT agent mode find younger companies?" — 🔧 diagnostic
This was a question, not a defect. The answer is structural: the register sweep
(`incorporated_from`) is a hard floor no news source has. Verify on the live
box with the diagnostic in §4.6, and keep `test_median_shortlist_age_stays_under_24_months`
as the ongoing guard.

---

## B. Output / company cards

### B7 — Output is articles, not companies — ✅
- `test_today_says_what_each_company_does_before_it_cites_an_article` — the
  sheet's Today tab leads with the company, article is the source.
- Browser `test_b1_b2` (single card with a name), `test_b14` (card carries a
  primary source link) — the web UI shows the company, not the article.

### B8 — Every recommendation needs a direct source link — ✅
- `test_today_exposes_a_direct_source_url_for_each_recommendation`.
- `test_today_does_not_surface_a_company_without_provenance` — a company with
  no verifiable source URL is dropped, not shown.
- Browser `test_b14_every_card_has_a_direct_primary_source_link`.

### B9 — Card descriptions formatted for quick scanning — ✅
- Browser suite B/V: `test_b6_b7_route_and_reasoning_present`, `test_d4c`
  (criteria ledger, one row per rule), `test_b12_no_placeholder_leakage`
  (no `undefined`/`(None)` on cards), `test_v1…v6` (no wrap, no overlap).
- Visual acceptance of "10 companies in a morning" remains a human pass — the
  browser suite is the regression net, not the judge.

### B10 — Short business summary per company (similar names) — ✅
- `one_liner` is extracted, stored and rendered verbatim:
  `test_d4b_one_liner_is_honest_when_absent_and_verbatim_when_present`
  (registry companies get *no* invented summary — "unknown rather than guess"),
  `test_today_says_what_each_company_does_before_it_cites_an_article`.

---

## C. Scoring

### C11 — Fit/Edge confusing; Edge identical across results — ✅ NEW
- The UI now names the tiles Match/Fresh with hints and per-rule ledgers:
  browser `test_b3_b4_both_tiles_named_not_positional`, `test_d4c`.
- Edge is computed from four varying components and *must* differ when inputs
  differ: `test_discovery_edge_ranking` (identical fit, different visibility →
  different edge), `test_discovery_edge_has_no_portfolio_component`.
- **NEW** `test_edge_varies_across_the_today_queue` (§3.3) — the client's
  words ("Edge seems to be the same across the results") as a data-wide test:
  an obscure vs famous pair on the real Today queue must show ≥ 2 distinct
  Fresh values.

### C12 — Per-fund match breakdown (Outward 20%, DSW 60%, …) — ✅
- `test_today_exposes_match_scores_for_all_four_funds`.
- Browser `test_a4_company_keys` (4 `fund_scores`, all four keys),
  `test_b15_four_fund_match_scores_are_visible` (displayed == DB values).
- A rejected fund shows `fit: null` + `reject_reason`, never a fake 0%.

### C13 — 100 Match when only 1–2 criteria confirmed — ✅ NEW
- `test_one_known_attribute_cannot_shortlist` — a sparse company scores 50%,
  coverage < 0.5, tier `watchlist`, never `shortlist`.
- `test_unknown_criteria_stay_in_the_full_model_denominator` — unknowns lower
  the percentage instead of vanishing.
- `test_unknown_never_becomes_zero` — `sub_score is None`, not 0.0 (and the CI
  grep guard bans `sub_score or 0`).
- Tiering: `shortlist` requires `coverage >= min_coverage` (`radar/score/tiering.py`).
- **NEW** `test_no_shortlist_row_under_the_coverage_floor` (§3.4) — the
  data-wide invariant over a scored database, so a future edit cannot let a
  sparse company sneak past the floor again.

---

## D. Fund criteria accuracy

### D14 — Outward "government-backed" claim; verify all fund rules — ✅ NEW
- The onboarding page no longer contains hand-written rules at all:
  `test_onboarding_page_carries_no_hand_written_fund_rules` (the placeholder is
  the contract).
- `test_onboarding_states_every_active_hard_rule` — every active vehicle's age
  caps and hard rejects must appear in the derived text; Outward's £5m round
  cap, £20m prior-total cap and 66% UK-exec rule asserted by name; inactive
  vehicles must not be described.
- **NEW** — the same test now asserts `"government" not in by_fund["outward"]`
  (§3.5), banning the literal prose that started this section (the ECF backing
  belongs to the fund, not the company).
- `test_onboarding_never_hardens_a_soft_geography` — Northstar's EIS Growth
  Fund reads as a soft preference, not a North East mandate.

---

## E. Shortlist / review workflow

### E15 — Where do kept companies go? Is it the Excel file? — ✅
- Kept is `user_field` (the same table the sheet's Verdict column mirrors):
  `test_kept_count_ignores_not_for_me`, `test_today_totals_include_kept`.
- Browser K suite: `test_k1` (kept company appears on /kept), `test_k2` (not
  for me never appears), `test_k3` (Today ↔ Kept navigation), `test_k4` (badge
  count), `test_k5` (help reachable, explains storage).
- Sheet mirror semantics (blank ≠ clear): `test_a_verdict_made_outside_the_sheet_survives_and_reaches_it`,
  `test_clearing_a_verdict_in_the_sheet_still_deletes_it`.

### E16 — A decided company must not reappear that day; "You've reviewed today's companies" + Review Again — ✅
- `test_today_excludes_a_company_after_a_decision_until_review_again`
  (`daily_review` table keyed by date).
- Browser `test_c10_c11` (done state), `test_c14` (refresh does not requeue),
  `test_c15` (Review Again restores the queue), `test_c16` (back navigation
  does not reopen), `test_e4` (double keypress writes one verdict).

---

## F. Access / credentials

### F17 — Change username/password to something easier to remember — ✅ NEW + 🔧
The web surface is behind Caddy `basic_auth` driven by `RADAR_WEB_PASS_HASH`
(`deploy/Caddyfile`); the install script refuses to start public without it.

- **NEW** `test_web_surface_requires_a_password` (§3.6) — the Caddyfile must
  keep a `basic_auth` block reading `RADAR_WEB_USER`/`RADAR_WEB_PASS_HASH`
  from the environment, and must contain no committed bcrypt hash or
  plaintext. The login surface is now protected by test, not just process.
- 🔧 Live: change the password (§4.3) and verify 401 without / 200 with the
  *new* credentials.

### F18 — How to change credentials himself (team sharing) — ✅ 📄
- **NEW** — the procedure now exists in two places: a "Change the login
  password" section in `prototype/help.html` (served at `/help`) and a
  §7 "Change the login password" section in `docs/ops-guide.md` (caddy
  hash-password → env var → `systemctl restart caddy` → share over a channel
  that is not the repo).
- Enforced by `test_help_covers_the_handover_sections` (§3.7), which requires
  the section to stay on the help page.

---

## G. Documentation / handover

### G19 — Walkthrough of how everything connects (sheet, interface, Telegram) — ✅ / 📄
- Delivered as `docs/ops-guide.md`, served in-app at `/help`.
- Browser `test_k5_help_page_is_reachable_from_kept`; onboarding `test_o1…o8`
  (sections present, no jargon, diagrams described).
- **NEW** `test_help_covers_the_handover_sections` (§3.7) — the help page
  cannot silently lose the sections he asked for: data flow, Shortlist vs
  Kept, where Kept is stored, update funds, edit criteria, add/remove sources,
  change the login.

### G20 — How to update/replace funds, edit criteria, add/remove sources — ✅ / 📄
- Same as G19 — help.html has "Update funds later", "Edit fund criteria &
  thresholds", "Turn off / on" sources sections; ops-guide §3–§5 mirror them.
- Engine behaviour is pinned by `test_sheet_edit_changes_scores_with_no_code_change`
  and the config fallback tests.

### G21 — Post-project maintenance: subscriptions/API costs, VPS/Telegram/sheet connections — ✅ / 📄
- README "Cost" section (< £10/mo) and ops-guide §1 (the three surfaces) and
  §7 (password change, the one self-serve operation he asked for last).
- Kept in place by the §3.7 content smoke test plus the README/ops-guide
  presence checks in §5.

---

## H. Trust / security

### H22 — Confirm the device code was his VPS, not Teddy's machine — ✅ resolved
Process question, answered in-thread (Codex CLI on the deployment VPS). Nothing
to test; the relevant guarantee is that the deployed web surface requires a
password (§F17) and nothing runs without env keys (`test_env_file_is_0600_and_never_logged`,
integration).

---

## I. Deployment / operational (the current blocker)

### I23 — Updates not reflected; "new companies" tab still shows the same — 🔧
Root cause was branch/deploy divergence, not product logic. Proven by §4.1–§4.2
(live): deployed commit matches intended commit, `founder-radar doctor` passes,
and the new features are actually on the deployed host. The automated suites
(C12, E16, B8…) already prove the *code*; this issue is purely "did the deploy
take".

### I24 — Cannot log in / dashboard removed — 🔧
Live checks §4.3–§4.5: web service up, Caddy answering, 401 without
credentials → 200 with them, and the review surface works end to end on the
host. F17's static guard prevents the "public with no password" failure mode
from ever being committed.

---

## J. Sourcing balance (raised after this plan was first written)

These complaints arrived on/after 18 Aug 2026, after the A–I list (compiled
17 Aug) was frozen. They are tracked here so the list stays complete.

### J25 — Over-reliance on Companies House as a discovery source — ✅ NEW
His clearest steer (18 Aug): *"use startup-focused sources to discover companies
first, then use Companies House to verify and enrich rather than driving the
discovery itself."* Symptoms: registered company names with little context, and
"many of these are simply small businesses rather than venture-backable
startups".

Fix (this change): a registry (Track B) company is admitted to scoring only by a
real venture signal — share allotment (SH01), grant, university spinout, press
in a tracked source, or a repeat founder. A live **website is no longer an
admitting qualifier**: almost every registered Ltd has one, so it was the exact
"small business" leak. Companies House keeps its verify/enrich role (the birthday
gate, officers, filings, the CH-verified badge); it no longer surfaces
context-less names on its own. The admitting set lives in the sheet
(`lists["qualifiers"]`), so the bar is tunable without code.

- **NEW** `test_website_alone_does_not_admit_a_registry_company`
  (tests/unit/test_qualification_gate.py) — a registry company whose only
  qualifier is a website is not scored, not surfaced, and marked `qualified = 0`;
  adding a real signal admits it.
- **NEW** `test_website_still_admits_when_the_sheet_re_enables_it` — proves the
  bar is sheet-editable (add `website` back to the Lists tab to loosen).
- `test_any_single_venture_signal_admits_to_scoring` — the five real signals
  each still admit, so the fix tightens noise without closing the high-edge
  Track B play (SH01 + prior directorships) the client asked for on Jul 9.

### J26 — "It's still the same" after fixes (18–20 Aug) — 🔧 deploy (mechanism added)
Restatement of I23: correct code was not reaching the deployed box (branch/
deploy divergence, GitHub Actions failing — confirmed in-thread 20 Aug). No new
product logic. The root cause was a manual, drift-prone deploy ritual (`ssh` →
`git pull` → `install.sh`).

Fix (mechanism): `.github/workflows/deploy.yml` — a one-click, repeatable deploy
from a clean checkout of `main`. It is `workflow_dispatch` only (never runs on
its own, never leaves a spurious red mark) and does exactly what the ritual did,
in order: `git pull --ff-only origin main` → `sudo bash deploy/install.sh` →
`founder-radar doctor` → optional `rescore --all`. Trigger it from the Actions
tab or `gh workflow run Deploy -f rescore_all=true`.

Still requires (one-time, owner action, outside the repo): add the VPS secrets
`VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (optional `VPS_PORT`, `VPS_APP_DIR`) under
Settings → Secrets and variables → Actions. Until those exist the workflow fails
fast with a clear message; once they exist, every future deploy is one action
and the §4.1–§4.2 live checks confirm the deployed commit matches `main`.

---

## 3. NEW tests — implemented (all in the offline suite, 17 Aug 2026)

All eight guards are implemented, named canonically, and passing
(`432 passed` on the default run). The two extraction sketches (3.1, 3.2)
were rewritten to need no provider: a golden fixture would require
re-recording an LLM response (`REFRESH_LLM=1`), so the offline-safe
deterministic paths pin the same complaints end to end.

| # | Issue | Test | File | What it pins |
|---|---|---|---|---|
| 3.1 | A4 parent shown | `test_parent_role_record_never_resolves_to_a_company` | tests/unit/test_extraction.py | A `parent`-role record is not usable AND `resolve_item` refuses it — zero company rows. Positive control: the same article naming the operating startup resolves. |
| 3.2 | A3 US/Dubai | `test_foreign_company_from_news_is_gated` | tests/unit/test_extraction.py | Heuristic reader extracts an explicit `hq_country_iso2="US"`; the record scores `reject` / `min_uk_presence`. |
| 3.3 | C11 Edge constant | `test_edge_varies_across_the_today_queue` | tests/unit/test_scoring.py | A seeded obscure vs famous pair on the real Today queue shows ≥ 2 distinct Fresh values. |
| 3.4 | C13 100-match bug | `test_no_shortlist_row_under_the_coverage_floor` | tests/unit/test_scoring.py | Every `score` row with `tier='shortlist'` in a scored database has `coverage >= min_coverage`. |
| 3.5 | D14 fund rules | assertion inside `test_onboarding_states_every_active_hard_rule` | tests/unit/test_config.py | `"government"` never describes Outward on the onboarding page. |
| 3.6 | F17 login | `test_web_surface_requires_a_password` | tests/unit/test_deploy_backup.py | Caddyfile keeps `basic_auth`, reads `RADAR_WEB_PASS_HASH` from the env, contains no bcrypt hash or plaintext. |
| 3.7 | G19–G21 handover | `test_help_covers_the_handover_sections` | tests/unit/test_help.py (new) | `/help` still explains data flow, Shortlist vs Kept, Kept storage, fund edits, sources, and the password change. Requires the new "Change the login password" section added to prototype/help.html (also closes F18). |
| 3.8 | A5 source categories | `test_client_requested_source_categories_are_registered_and_enabled` | tests/unit/test_source_registry.py | Spinouts / accelerators / Innovate-UK each have a registered adapter AND one enabled by default. |

**Browser counterparts** (`tests/browser/test_client_regressions.py`, all in
`pytest -m browser`) — the same complaints on the surface Aryan reviews:

| # | Test | What it pins end to end |
|---|---|---|
| 3.3 | `test_edge_varies_across_the_today_queue` | A two-company injection with identical fit but opposite visibility → ≥ 2 distinct Fresh values in the rendered queue, and the visible tile equals the queue value. |
| 3.4 | `test_sparse_company_is_never_offered_as_a_review_card` | A two-criteria company scores honestly (fit 34, never 100), is excluded from Today entirely, and page-wide nothing under the coverage floor is shortlisted. |
| 3.5 | `test_onboarding_fund_rules_never_call_outward_government_backed` | The served onboarding page's Outward row has no "government" claim and still carries the £5m / £20m / 66% rules; inactive vehicles are not described. |
| 3.7 | `test_help_covers_the_handover_sections` | The served /help page still explains data flow, Shortlist vs Kept, Kept storage, fund edits, sources, and the password change. |

---

## 4. Live acceptance — run on the deployed VPS after every deploy

These are the checks that would have caught I23/I24 before Aryan did.

### 4.1 The deployed commit is the intended one
```bash
git -C /opt/founder-radar rev-parse HEAD      # == the commit you deployed
git -C /opt/founder-radar status --porcelain  # no uncommitted drift
```

### 4.2 Doctor and services
```bash
founder-radar doctor                          # every check green
systemctl status founder-radar.timer          # 06:30 schedule present
systemctl status founder-radar-web caddy      # both running
```

### 4.3 Login works (the I24 complaint)
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<domain>/          # expect 401
curl -s -u '<user>:<password>' -o /dev/null -w "%{http_code}\n" https://<domain>/  # 200
```
Repeat with the *new* credentials after any password change (F17).

### 4.4 The new features are actually live (the I23 complaint)
```bash
curl -s -u '<user>:<password>' https://<domain>/api/today | python3 -c '
import json,sys
d = json.load(sys.stdin)
c = d["companies"][0]
assert len(c["fund_scores"]) == 4             # C12 — per-fund scores
assert c["source_url"].startswith("http")     # B8 — direct source link
assert "one_liner" in c                       # B10 — business summary
print("new features live ✓")
'
```

### 4.5 Daily review flow on the host (E16)
1. Open Today, press **1** on the first company.
2. Reload — the same company must **not** reappear.
3. Review everything → "You've reviewed today's companies."
4. Press **Review Again** → the queue is restored.
5. Open **Kept** → the company you kept is listed with its source link.

### 4.6 Sourcing diagnostic (A6)
```bash
sqlite3 /opt/founder-radar/radar.db "
  SELECT source_key, COUNT(*), ROUND(AVG(julianday('now')-julianday(incorporated_on))/30.44)
  FROM company_source cs JOIN company c ON c.id=cs.company_id
  WHERE c.incorporated_on IS NOT NULL
  GROUP BY source_key ORDER BY 3;"            # median age by source
```
The Companies House cohort's median age must sit inside the configured
window (`ch_daily_window_days`, default 10; 90 on backfill). Any source whose
median drifts above 24 months is leaking old companies → find it on the
Sources tab and disable or fix it.

### 4.7 One real unattended run
Two consecutive daily runs completing unattended (timer fires, run-log rows
written, digest delivered). This is the integration proof that "it keeps
running" (G21).

---

## 5. Documentation checks (📄 items)

- [x] `docs/ops-guide.md` has a "Change the login password" section (F18) — new §7.
- [x] `prototype/help.html` serves the same sections — "Change the login
      password" added; enforced by `test_help_covers_the_handover_sections`.
- [ ] README cost table still names every paid dependency (G21).
- [ ] The Companies House key / Google service-account rotation note is still
      present (H22-adjacent hygiene).

---

## 6. Findings and decisions needed

1. **Innovate UK sources not on by default — FIXED.** `innovate_uk` and
   `ukri_gtr` are now in `DEFAULT_SOURCES` (enabled by default), so the
   dedicated grant adapters feed the pipeline without sheet configuration.
   `test_dedicated_innovate_uk_feeds_are_enabled_by_default` pins both.
   On the deployed box the change takes effect on the next `sync-sheet`
   reseed; until then the sheet remains the source of truth.
2. **Config key mismatch — FIXED.** The seeded Sources tab used
   `oxford_university_innovation` while the adapter's registry key is
   `oxford_innovation`, so the Enabled toggle for Oxford was inert and its
   health column never filled. The seed now uses `oxford_innovation`, and
   three tests pin the fix: `test_every_default_source_key_resolves_in_the_registry`
   (the general invariant), `test_disabling_a_configured_source_removes_its_adapter`
   (the toggle behaviour), and `test_oxford_health_joins_under_its_registry_key`
   (the Sources-tab health join). `enabled_adapters` also logs a warning for
   any configured key that matches no adapter, so a stale sheet row can never
   be silently inert again.
3. **Deployment hygiene (I23/I24) still has no automated guard** — the §4 live
   checklist is manual by nature, but a CI step that fails when
   `deploy/Caddyfile` loses `basic_auth` (now covered, §3.6) or when the
   onboarding page stops deriving fund rules (already covered) closes the
   repeatable parts.
