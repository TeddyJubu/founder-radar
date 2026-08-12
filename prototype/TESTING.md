# Today screen — automated browser test plan

Written to be executed by an AI agent with browser automation (Playwright,
Puppeteer, or an equivalent CDP driver). Every check below is mechanical: a
selector, an action, and a pass condition that does not require judgement.

**The single most important suite is [D — Data integrity](#d--data-integrity).**
This interface has one architectural rule: *it computes nothing.* Every number
on screen must equal the value in SQLite exactly. If suite D fails, the scoring
has two sources of truth and stops being defensible — that outranks every
visual defect in this document.

---

## 0. Preconditions

### 0.1 Build a disposable database

**Verdicts are persistent writes.** Running this plan mutates `user_field`, so
every run must start from a fresh copy or results drift between runs.

```bash
cd "<repo root>"
cp /tmp/demo.db /tmp/test-run.db          # if /tmp/demo.db is gone, see 0.2
```

### 0.2 Rebuild the demo database from scratch (only if `/tmp/demo.db` is missing)

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
from radar.store.db import Db
from radar.config.defaults import default_config
from radar.pipeline import enrich_stage, resolve_item, score_company
from radar.sources.base import FetchContext
from radar.sources.companies_house import CompaniesHouseAdapter
from tests.unit.test_track_b_end_to_end import MockCompaniesHouse, TODAY

db = Db("/tmp/test-run.db"); db.migrate(); cfg = default_config()
http = MockCompaniesHouse()
for it in CompaniesHouseAdapter(api_key="demo", days_back=90, window_days=90).fetch(
        FetchContext(http=http, config=cfg, db=db, now=TODAY)):
    resolve_item(db, it, cfg)
enrich_stage(db, cfg, http, api_key="demo")
for r in db.query("SELECT id FROM company"):
    score_company(db, r["id"], cfg, today=TODAY)
print("companies:", db.scalar("SELECT COUNT(*) FROM company"))
PY
```

This yields register-derived companies only (all `coverage = 0.8`), so no card
will be `thin`. **D7b** and **X7** then have nothing to assert against — record
them as `SKIP (no thin cards in dataset)` rather than as failures. **D7** still
applies and must pass: no card may carry `data-thin="true"`.

### 0.3 Start the server

```bash
.venv/bin/python prototype/server.py --db /tmp/test-run.db --port 8788
```

Use **8788**, not 8787 — 8787 may already be serving a live preview. Wait for
`HTTP 200` on `http://127.0.0.1:8788/` before starting.

### 0.4 Teardown

Kill the server, then `rm /tmp/test-run.db`. Never point the plan at
`data/radar.db`.

---

## 1. Selector reference

**Address everything by `data-testid`.** CSS classes are for styling and may be
renamed at any time; the testids are the contract. Never select by class, tag
position, or visible text.

```js
const q   = (id) => document.querySelector(`[data-testid="${id}"]`);
const all = (id) => document.querySelectorAll(`[data-testid="${id}"]`);
```

| What | `data-testid` | Data attributes | Notes |
|---|---|---|---|
| Card | `card` | `data-company-id`, `data-tier`, `data-coverage`, `data-thin` | one at a time |
| Company name | `company-name` | | |
| Meta line | `company-meta` | | domain · city · age |
| Domain link | `company-domain` | | absent when the company has no domain |
| Age phrase | `company-age` | `data-exact` | empty when the date is unknown |
| Scores wrapper | `scores` | | |
| Fit tile | `score-fit` | `data-value` (**raw**), `data-band` | labelled **Match** in UI; testid unchanged |
| Edge tile | `score-edge` | `data-value` (**raw**), `data-band` | labelled **Fresh** in UI; testid unchanged |
| Score number | `score-value` | | rounded display text; two per card |
| Score label | `score-label` | | `Match` / `Fresh` (uppercase is CSS only) |
| Score hint | `score-hint` | | one-line explainer under the tiles |
| Fund score grid | `fund-scores` | | all four fund-specific Match values |
| Fund score | `fund-score` | `data-fund`, `data-value`, `data-coverage`, `data-tier` | one per configured fund; `data-value` is blank only when unscored |
| Route chip | `route` | `data-fund` | |
| Fund name | `route-fund` | | |
| Vehicle + cheque | `route-vehicle` | | |
| Explanation | `explanation` | `data-text` (full verbatim sentence), `data-collapsed` | stacked clauses for scanning; `data-text` is the contract |
| Explanation clause | `explanation-clause` | `data-kind` | one template part; kinds: `found` / `match` / `against` / `unknown` / `warn` / `note` |
| Explanation more | `explanation-more` | `data-expanded`, `aria-expanded` | present only when there are more than four clauses |
| Company one-liner | `one-liner` | | absent when `one_liner` is null — never invented |
| Evidence wrapper | `evidence` | | absent when there are no links |
| Evidence link | `evidence-link` | `data-kind`, `data-primary`, `data-primary-source` | every surfaced card has one valid primary source link with `data-primary-source="true"`; additional signal/source links may follow. A company without a valid `company_source` URL is not surfaced on Today. |
| Footnote row | `card-footnote` | | |
| Coverage note | `coverage-note` | `data-coverage` | present only when `thin` |
| Caveat | `caveat` | `data-flag` | raw flag name, e.g. `age_unknown` |
| Also fits | `also-fits` | | absent when no other fund matched |
| Progress | `progress` | | |
| Progress dot | `progress-dot` | `data-state` = `done` / `now` / `todo` | |
| Verdict bar | `verdict-bar` | | `hidden` on the done state |
| Verdict buttons | `verdict-worth-contacting`, `verdict-unsure`, `verdict-not-for-me` | | |
| Toast | `toast` | | `.show` class when visible |
| Main region | `today-main` | | carries `aria-live="polite"` |
| Header date | `today-date` | | hidden below 460px by design |
| Done state | `done-state` | | |
| Empty state | `empty-state` | | |

**`data-value` carries the unrounded number.** Compare *that* against SQLite —
`score-value` text is rounded for display and will differ by up to 0.5.

---

## A — API contract

Run before the UI suites. If A fails, every later failure is a symptom.

| ID | Check | Pass |
|---|---|---|
| A1 | `GET /` | 200, `content-type: text/html` |
| A2 | `GET /api/today` | 200, valid JSON |
| A3 | Top-level keys | `date`, `companies`, `totals`, `run` all present |
| A4 | Company keys | every element has `company_id`, `name`, `fit`, `edge`, `coverage`, `priority`, `explanation`, `flags`, `signals`, `also_fits`, `fund_scores`, `vehicle`, `tier`; `fund_scores` has exactly `outward`, `dsw`, `northstar`, `anticus` |
| A5 | Tier filter | no company has `tier == "reject"` |
| A6 | Ordering | `priority` is non-increasing across the array |
| A7 | Tie-break | where `priority` is equal, `coverage` is non-increasing |
| A8 | Types | `flags` is an array, `signals` is an array, `fit`/`edge`/`coverage` are numbers |
| A9 | `GET /api/nonsense` | 404 |
| A10 | `POST /api/verdict` with `{"verdict":"banana"}` | 400, body contains `error` |
| A11 | `POST /api/verdict` with no `company_id` | 400 |

---

## B — Rendering

| ID | Check | Pass |
|---|---|---|
| B1 | First card renders | exactly one `card` in the DOM |
| B2 | Name non-empty | `company-name` text length > 0 |
| B3 | Both tiles exist | `score-fit` and `score-edge` each present exactly once |
| B4 | Tiles labelled | `score-fit`'s `score-label` is `Match`; `score-edge`'s is `Fresh` |
| B5 | Scores are integers | both `score-value` match `/^\d+$/` |
| B6 | Fund present | `route-fund` text length > 0 |
| B7 | Explanation present | `explanation` text length > 20 |
| B8 | Dots match count | `progress-dot` count == `min(companies.length, 12)` |
| B9 | One current dot | exactly one `progress-dot[data-state="now"]` |
| B10 | Three buttons | the three `verdict-*` testids each present exactly once |
| B11 | Bar visible | `verdict-bar` not `hidden` while cards remain |
| B15 | Four fund matches | exactly four `fund-score` elements; each displayed `data-value` matches the corresponding API `fund_scores[*].fit` |

### B12 — No placeholder leakage *(high value)*

Concatenate `card` `innerText` and assert it contains **none** of:

```
undefined   null   NaN   [object Object]   (None)
```

### B13 — No tofu glyphs *(high value)*

Assert the rendered text contains no U+FFFD (`�`) and no codepoint in the
private-use range `U+F0000–U+FFFFD`.

```js
const t = q('card').innerText;
const bad = [...t].filter(c => c === '�' || (c.codePointAt(0) >= 0xF0000));
```

This regressed once already: SF Symbols glyphs only resolve on Apple platforms
with the font installed, and rendered as boxes everywhere else.

---

## C — Interaction

Reset by reloading the page between tests where noted.

| ID | Action | Pass |
|---|---|---|
| C1 | Press `ArrowRight` | `company-name` text changes; the `now` dot moves one right |
| C2 | Press `ArrowLeft` | returns to the previous card |
| C3 | `ArrowLeft` on card 1 | no change, no console error |
| C4 | Press `1` | toast appears containing `saved to Kept` (and the company name); advances one card |
| C5 | Press `2` | toast contains `saved to Kept` (and the company name); advances |
| C6 | Press `3` | toast contains `not for me`; advances |
| C7 | Click each `button[data-v]` | same behaviour as C4–C6 |
| C8 | Press `Cmd/Ctrl+Z` after a verdict | returns to the company just decided |
| C9 | Toast auto-hides | `toast` loses `.show` within 2.5s |
| C10 | Decide through every card | `verdict-bar` becomes `hidden`; `done-state` appears |
| C11 | Done state | `done-state` text contains `reviewed`; a `✓` is present |
| C12 | Keyboard-only run | complete C10 without a single mouse event |

### C13 — Modifier safety

With focus on the page, press `Cmd+1` / `Cmd+2` / `Cmd+3`. **No verdict may be
recorded** — those are browser tab-switch shortcuts. `user_field` row count must
be unchanged.

---

## D — Data integrity

**The suite that matters.** For each of the first 5 cards, read the rendered
values, then query the database and compare.

```bash
sqlite3 -json /tmp/test-run.db "
  SELECT c.canonical_name, s.fund_fit_pct, s.discovery_edge, s.coverage,
         s.priority, s.explanation, s.tier
    FROM score s JOIN company c ON c.id = s.company_id
    JOIN (SELECT s2.company_id, MAX(s2.priority) b
            FROM score s2 JOIN company c2 ON c2.id = s2.company_id
           WHERE s2.tier IN ('shortlist','watchlist')
             AND c2.incorporated_on IS NOT NULL
           GROUP BY s2.company_id) t
      ON t.company_id = s.company_id AND t.b = s.priority
   WHERE s.tier IN ('shortlist','watchlist')
     AND c.incorporated_on IS NOT NULL
   ORDER BY s.priority DESC, s.coverage DESC, c.canonical_name LIMIT 5;"
```

| ID | Check | Pass |
|---|---|---|
| D1 | Name | `company-name` == `canonical_name`, exactly |
| D2 | Fit, raw | `score-fit` `data-value` == `fund_fit_pct` **exactly** (no rounding) |
| D3 | Edge, raw | `score-edge` `data-value` == `discovery_edge` **exactly** |
| D4 | Explanation verbatim | `explanation` `data-text` == DB `explanation`, character for character; joining every `explanation-clause` with a space reconstructs the same string |
| D5 | Display rounding | each `score-value` == `Math.round(data-value)` of its own tile |
| D6 | Coverage passthrough | `card` `data-coverage` == DB `coverage` exactly |
| D7 | Amber tint ⇔ coverage | `card[data-thin="true"]` **iff** `coverage < 0.5` |
| D7b | Coverage note | when thin, `coverage-note` contains `we know N of 5`, `N == round(coverage * 5)` |
| D7c | Tier passthrough | `card` `data-tier` == DB `tier`, and is never `reject` |

### D8 — Verdict round-trip *(highest value in the plan)*

1. Note card 1's name.
2. Press `1`.
3. Query: `SELECT value FROM user_field WHERE field='verdict' AND company_id=(SELECT id FROM company WHERE canonical_name=?)`
4. **Pass:** value == `worth contacting`.
5. Re-decide the same company as `not for me` (navigate back, press `3`).
6. **Pass:** exactly **one** row for that company (upsert, not duplicate), value updated.

### D9 — The sweep consumes the verdicts

After at least 3 verdicts:

```bash
.venv/bin/python -c "
from radar.store.db import Db; from radar.score.tune import sweep
r = sweep(Db('/tmp/test-run.db'))
print(r['labels'], r['thresholds'], r['recommendation'])"
```

**Pass:** `labels` counts match the verdicts entered, and `recommendation` is
no longer the "No verdicts yet" string. This proves the UI's only write path
reaches the tuning engine.

---

## V — Visual and responsive

Run at **each** viewport, in **both** colour schemes
(`page.emulateMedia({ colorScheme })`).

| Viewport | Size |
|---|---|
| Desktop | 1440 × 900 |
| Laptop | 1280 × 800 |
| Tablet | 834 × 1112 |
| Phone | 393 × 852 |
| Small phone | 375 × 667 |

| ID | Check | Pass |
|---|---|---|
| V1 | No horizontal scroll | `document.documentElement.scrollWidth <= clientWidth + 1` |
| V2 | Bar never covers content | `card` bottom edge is above `verdict-bar` top edge, or the page scrolls to reveal it |
| V3 | Buttons single-line | each `verdict-*` button `scrollHeight <= offsetHeight + 2` |
| V4 | Header single-line | `today-date` `scrollHeight <= offsetHeight + 2` (hidden below 460px — expected) |
| V5 | Name not clipped | `company-name` `scrollWidth <= clientWidth + 1` |
| V6 | Dark mode differs | body `background-color` differs between the two schemes |
| V7 | Screenshot | capture full page for each combination; attach to the report |

### V8 — Contrast (WCAG AA, which Apple HIG also targets)

Sample `explanation`, `company-meta`, `caveat`, `score-label`, and button labels against their
backgrounds. **Pass:** ≥ 4.5:1 for body text, ≥ 3:1 for text ≥ 24px.
`caveat` on the amber-tinted card is the most likely failure.

---

## X — Accessibility

| ID | Check | Pass |
|---|---|---|
| X1 | Live region | `today-main` has `aria-live="polite"` |
| X2 | Focus visible | `Tab` to a button shows a visible ring (blue, 3px) |
| X3 | Tab order | reaches all three verdict buttons and every `evidence-link` |
| X4 | Links safe | every `evidence-link` and `company-domain` has `target="_blank"` **and** `rel` containing `noopener` |
| X5 | Reduced motion | with `prefers-reduced-motion: reduce`, `card` computed `animation-duration` ≤ 1ms |
| X6 | Language | `<html lang="en-GB">` |
| X7 | Colour is not the only cue | the amber state also renders `coverage-note`, not just the tint |
| X8 | Zoom | at 200% browser zoom, no content is clipped or overlapped |

---

## E — Resilience

| ID | Scenario | Pass |
|---|---|---|
| E1 | Empty dataset | point the server at a DB with no scores; UI shows `Nothing to review`, no console error |
| E2 | `/api/verdict` returns 500 | stub the route to fail; the UI must not advance silently *(current behaviour is unverified — record what actually happens)* |
| E3 | Slow API | throttle to 3s; no duplicate render, no flash of broken layout |
| E4 | Double keypress | press `1` twice within 100ms; **exactly one** `user_field` row is written |
| E5 | Reload mid-review | verdicts already given persist in the DB |
| E6 | Console clean | zero `error`-level console messages across a full run |
| E7 | Network clean | no 4xx/5xx in the network log except those deliberately provoked in A9–A11 |

---

## Known defects

Confirm these are **still present or fixed**. Report as `KNOWN` or `FIXED` —
do not file them as new findings.

*The three `explain.py` defects previously listed here (a `(None)` date, a
caveat stated three times, and a date echoed after itself) were fixed on
10 August 2026. `test_b12_no_placeholder_leakage` now sweeps for `(None)`
directly, and `tests/unit/test_scoring.py` pins each behaviour.*

| # | Defect | Where | Detect |
|---|---|---|---|
| K4 | SF Symbols glyphs render as tofu off Apple platforms | `prototype/index.html` | covered by B13 — should now be `FIXED` |

---

## Reporting format

Return one row per test ID. Do not summarise away failures.

```
| ID | Result | Evidence |
|----|--------|----------|
| A1 | PASS   | 200, text/html |
| D4 | FAIL   | [data-testid=explanation] differs at char 41: expected "…", got "…" |
| V4 | SKIP   | today-date hidden below 460px by design |
```

Close with, in this order:

1. **Suite D result** — pass/fail, stated first regardless of everything else.
2. Counts: PASS / FAIL / SKIP / KNOWN.
3. Every FAIL with the exact selector, expected value, actual value, viewport
   and colour scheme.
4. Screenshots from V7.
5. Any console or network error from E6/E7, verbatim.

**Do not fix anything.** Report only — several "defects" here are deliberate
(the amber tint, the missing shortlist tier, watchlist-only data), and the
distinction between a design decision and a bug is not safely inferable from
the screen alone.
