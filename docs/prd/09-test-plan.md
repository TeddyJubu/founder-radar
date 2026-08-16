# 09 — Test Plan

**How to prove it works — and specifically, how to prove the client's complaint is fixed.**

Rules for the whole suite:

- The default suite runs **offline in under 60 seconds** with no network, no credentials, no API keys
- Tests that touch a real service are marked and excluded from the default run
- **A requirement without a passing test is not done.** Every FR in `01-product-requirements.md` maps to a named test below.

```bash
pytest                          # the default: offline, fast, in CI
pytest -m integration           # needs a scratch spreadsheet + Companies House key
pytest -m llm                   # opt-in, costs a few pence, run before releases
pytest -m live                  # hits real source websites; run weekly, not in CI
pytest -m perf                  # performance targets; excluded from the 60s budget
```

**Test names in this file are canonical.** `10-build-plan.md` gates each phase on them, so an agent running `pytest -k <name>` must collect a real test. Do not rename.

### The `C()` fixture helper

Used throughout. It builds a `Company`, never a score:

```python
# tests/factories.py
def C(**kw) -> Company:
    """A minimally-valid Company. Anything not passed is None, EXCEPT the
    fields needed to get past the universal gates, which default to valid
    values so a test can isolate one gate at a time."""
    base = dict(id=ulid(), canonical_name="Test Co", norm_key="testco",
                country_iso2="GB", hq_region="north_east", on_vc_portfolio=0,
                stage="pre_seed", sector="b2b_saas", founder_signal="technical_founder",
                traction_signal="pilot_customers", total_funding_gbp=None,
                discovery_route="news", qualifiers=["press"])
    if "age_months" in kw:
        m = kw.pop("age_months")
        base["incorporated_on"] = None if m is None else months_ago(m)
    if "funding" in kw:   base["total_funding_gbp"] = kw.pop("funding")
    if "geography" in kw: base["hq_region"] = kw.pop("geography")
    return Company(**{**base, **kw})
```

Scores are built with `S(...)`, a separate helper. Never overload `C()`.

---

## 1. The test that matters most

Everything else is supporting cast. **This is the one that proves version 2 fixes what version 1 got wrong**, and it must be written first, before any other code.

```python
# tests/unit/test_gates.py
import pytest
from radar.score.gates import apply_freshness_gates
from radar.config.defaults import DEFAULT_SETTINGS

@pytest.mark.parametrize("case,company,expect_pass,expect_reason", [
  # --- AGE: the client's exact complaint ---
  ("brand new",        C(age_months=1),    True,  None),
  ("one year",         C(age_months=12),   True,  None),
  ("at the limit",     C(age_months=36),   True,  None),
  ("one day over",     C(age_months=36.1), False, "max_company_age_months"),
  ("the v1 problem",   C(age_months=60),   False, "max_company_age_months"),
  ("the v1 problem 2", C(age_months=84),   False, "max_company_age_months"),
  ("age unknown",      C(age_months=None), True,  None),   # passes, but flagged

  # --- FUNDING: "many have already raised" ---
  ("no funding known", C(funding=None),      True,  None),
  ("known zero",       C(funding=0),         True,  None),
  ("seed sized",       C(funding=800_000),   True,  None),
  ("at the limit",     C(funding=3_000_000), True,  None),
  ("one pound over",   C(funding=3_000_001), False, "max_total_funding_gbp"),
  ("series A sized",   C(funding=12_000_000),False, "max_total_funding_gbp"),

  # --- STAGE ---
  ("pre-seed",         C(stage="pre_seed"),      True,  None),
  ("series A",         C(stage="series_a"),      True,  None),
  ("series B",         C(stage="series_b_plus"), False, "max_stage"),
  ("growth",           C(stage="growth"),        False, "max_stage"),

  # --- ALREADY SEEN: the whole point of being a scout ---
  ("not on any portfolio", C(on_vc_portfolio=False), True,  None),
  ("already in a portfolio",C(on_vc_portfolio=True), False, "already_on_vc_portfolio"),

  # --- UK ---
  ("UK company",  C(country="GB"), True,  None),
  ("US company",  C(country="US"), False, "min_uk_presence"),
])
def test_freshness_gates(case, company, expect_pass, expect_reason):
    result = apply_freshness_gates(company, DEFAULT_SETTINGS)
    assert result.passed is expect_pass, case
    assert result.reason == expect_reason, case


def test_unknown_age_cannot_reach_shortlist():
    """Unknown age passes the gate but must never be shortlisted.
    Rejecting would lose good early candidates; shortlisting would
    let old companies back in through the gap. Watchlist is honest."""
    c = C(age_months=None)                     # everything else scores well
    s = score_one(c, fund="dsw")
    assert "age_unknown" in s.flags
    assert s.tier == "watchlist"
    assert "age unknown" in s.explanation.lower()


def test_gate_with_null_input_passes_but_flags():
    """A vehicle hard rule we cannot evaluate must not silently reject
    or silently ignore. It passes, flags, and stays off the shortlist."""
    c = C(is_university_spinout=None, hq_region="north_east")
    s = score_one(c, fund="northstar")
    assert s.vehicle_key == "spinout_inspire"
    assert "gate_unverified" in s.flags
    assert s.tier == "watchlist"


def test_median_shortlist_age_stays_under_24_months():
    """Regression guard on the headline product metric."""
    db = seeded_db_from_fixture("realistic_30_days.sql")
    ages = [c.age_months for c in shortlisted_last_30_days(db)]
    assert statistics.median(ages) < 24, \
        f"median shortlist age {statistics.median(ages)}m — drifting back to v1"
```

**If `test_freshness_gates` passes, the core complaint is structurally fixed.** Everything below is about making the rest trustworthy.

---

## 2. Unit tests — offline, fast, the bulk of the suite

### 2.1 Entity resolution — `test_entity_resolution_pairs`

Forty committed name pairs. These are the traps that cause real bugs.

| A | B | Expect | Why |
|---|---|---|---|
| `Acme Robotics Ltd` | `Acme Robotics` | **merge** | suffix stripping |
| `Acme Robotics Limited` | `ACME ROBOTICS LTD` | **merge** | case + suffix |
| `Café Ltd` | `Cafe Limited` | **merge** | accent folding |
| `Smith & Sons Ltd` | `Smith and Sons` | **merge** | ampersand |
| `Acme Robotics` | `Acme Robotics Automotive Division` | **DISTINCT** | ⚠️ `token_set_ratio` scores this **100**. The classic false merge. |
| `Acme Labs` | `Acme Holdings` | **DISTINCT** | over-aggressive suffix stripping trap |
| `Acme Robotics Ltd` (GB) | `Acme Robotics Inc` (US) | **DISTINCT** | same name, different jurisdiction |
| `AI Labs` | `Tech Solutions` | **DISTINCT** | rare-token guard must refuse — no distinctive token |
| `Stealth` | `Stealth` | **DISTINCT** | placeholder blocklist |
| `Unknown` | `Unknown` | **DISTINCT** | placeholder blocklist |
| `Acme` (`00445790`) | `Acme Robotics` (`00445790`) | **merge** | CH number wins over everything |
| `Acme` (`445790`) | `Acme` (`00445790`) | **merge** | zero-padding — do not cast to int |
| `Acme` (`SC445790`) | `Acme` (`00445790`) | **DISTINCT** | Scottish prefix is a different company |
| `Acme` (acme.com) | `Acme Robotics` (acme.com) | **merge** | domain match |
| `Acme` (linkedin.com/co/acme) | `Beta` (linkedin.com/co/beta) | **DISTINCT** | social domains denylisted |
| `Kelvin Bio` (eng.ox.ac.uk/…) | `Oxford Nanopore` (ox.ac.uk) | **DISTINCT** | ⚠️ university domain is not company identity |
| `BLUE SKY 4471 LIMITED` | `Acme Robotics` | **merge if same CH number** | spinouts incorporate then rename |
| `Smith & Partners` | `Smith & Sons` | **review** | person-named companies share a rare token |
| `Acme Robotic` | `Acme Robotics` | **merge** | fuzzy 96 |
| `Acme Robotics` | `Acme Robotic Arms` | **review** | fuzzy 87 — the review band |

Plus the transitive-chain case: A~B = 93, B~C = 93, A~C = 78. **Assert A and C do not end up merged.**

### 2.2 Scoring — `test_scoring_*`

```python
def test_unknown_criteria_stay_in_the_full_model_denominator():
    """Unknown criteria lower confidence rather than disappearing."""
    before = fund_fit(company, fund, cfg)
    cfg2   = cfg.with_extra_criterion(weight=3)
    after  = fund_fit(company, fund, cfg2)
    assert after.pct < before.pct

def test_unknown_never_becomes_zero():
    """The single most important invariant in the scoring code."""
    c = C(sector=None)
    comp = next(x for x in fund_fit(c, fund, cfg).components if x.key == "sector")
    assert comp.sub_score is None          # not 0.0
    assert comp.evidence == "unknown"

def test_one_known_attribute_cannot_shortlist():
    """A sparse company must not look like a perfect fit.
    NOTE geography is present — a NULL region would trip min_uk_presence
    and make this a reject, testing the wrong thing."""
    c = C(sector="climate_tech", geography="north_east",
          stage=None, founder_signal=None, traction_signal=None)
    s = score_one(c, fund="northstar")
    assert s.fund_fit_pct == 50.0
    assert s.coverage < 0.5
    assert s.tier == "watchlist"           # NOT shortlist


def test_derivation_lets_a_registry_company_shortlist():
    """THE regression guard on the registry-first fix. Without the
    derivation rules in 06-scoring §2 this company scores on geography
    alone, fails the coverage floor, and the whole Track B idea is dead."""
    c = registry_company(                       # ONLY register-derived facts
        incorporated_on=months_ago(5), sic_codes=["72110"],
        hq_postcode="NE1 4ST", has_share_issue=True,
        is_university_spinout=True, spinout_university="durham",
        founders=[F(prior_appointments=0)], discovery_route="registry")
    d = derive_attributes(c, cfg)
    assert d.sector          == "life_sciences"      # from SIC 72110
    assert d.geography       == "north_east"         # from NE1
    assert d.stage           == "pre_seed"           # from SH01 on a young co
    assert d.founder_signal  == "research_spinout"   # from the spinout flag
    s = score_one(d, fund="northstar")
    assert s.coverage >= 0.5
    assert s.tier == "shortlist"


def test_scoring_is_reproducible():
    a = score_one(company, cfg); b = score_one(company, cfg)
    assert a.model_dump() == b.model_dump()


def test_explanation_arithmetic_reconciles():
    """explain() emits '— X of Y total'. The two numbers must be the
    real top-3 contribution and the real headline score."""
    s = score_one(company, fund="northstar")
    m = re.search(r"— (\d+) of (\d+) total", s.explanation)
    assert m, "explanation must carry the reconciliation clause"
    top3, headline = int(m.group(1)), int(m.group(2))
    assert headline == round(s.fund_fit_pct)
    assert top3 <= headline


def test_worked_example_metzero():
    """Every number in 06-scoring.md §11, asserted."""
    s = score_all(METZERO_FIXTURE, cfg)
    assert s["northstar"].vehicle_key    == "spinout_inspire"
    assert s["northstar"].fund_fit_pct   == pytest.approx(92.2, abs=0.1)
    assert s["northstar"].coverage       == 1.00
    assert s["northstar"].discovery_edge == pytest.approx(51.0, abs=0.1)
    assert s["northstar"].priority       == pytest.approx(75.7, abs=0.1)
    assert s["northstar"].tier           == "watchlist"      # edge 51 < 55
    assert "already on their radar"      in s["northstar"].explanation
    assert s["anticus"].reject_reason    == "no_eligible_vehicle"
    assert s["outward"].fund_fit_pct     == pytest.approx(15.0, abs=0.1)
    assert s["outward"].tier             == "reject"
    assert s["dsw"].vehicle_key          == "eis_service"    # seis_fund fails on stage
    assert s["dsw"].fund_fit_pct         == pytest.approx(54.7, abs=0.1)
    assert s["dsw"].tier                 == "watchlist"
```

### 2.3 Vehicle routing — `test_vehicle_routing`

| Company | Fund | Expect |
|---|---|---|
| Durham University spinout, Newcastle | Northstar | `spinout_inspire`, pass |
| Newcastle, **not** a spinout | Northstar | `ne_innovation_fund`, pass — *the spinout vehicle fails, the fund does not* |
| Sunderland software company | Northstar | `venture_sunderland`, pass |
| Leeds company | Northstar | fail all NE vehicles; `eis_growth` may pass on the soft "north of England" rule |
| Leeds company | Anticus | `fy_seedcorn`, pass |
| Newcastle company | Anticus | reject — `no_eligible_vehicle` (Yorkshire is a hard mandate) |
| Oxford fintech | DSW SEIS | reject — golden triangle |
| Oxford fintech | Outward | pass |
| London fintech, raised £22m | Outward | reject — `prior_total_max` exceeded |
| Lending business | DSW | reject — SEIS/EIS excluded trade |
| Lending business | Outward | pass — no EIS constraint |

That last pair is the point of modelling vehicles: **the same company is a hard reject for one fund and a strong fit for another, for a reason that has nothing to do with quality.**

### 2.4 Discovery Edge — `test_discovery_edge_ranking`

```python
def test_discovery_edge_ranking():
    """Identical on every scored attribute; different only on visibility."""
    common = dict(sector="climate_tech", geography="north_east",
                  stage="pre_seed", founder_signal="technical_founder",
                  traction_signal="pilot_customers")
    obscure = C(**common, age_months=3,  news_mention_count=0,
                funding=0, discovery_route="registry")
    famous  = C(**common, age_months=30, news_mention_count=8,
                funding=2_000_000, discovery_route="news")
    a, b = score_one(obscure, "northstar"), score_one(famous, "northstar")
    assert a.fund_fit_pct == b.fund_fit_pct        # identical fit, by construction
    assert a.discovery_edge > b.discovery_edge
    assert a.priority > b.priority

def test_unknown_funding_is_not_known_zero():
    """The one invariant this system holds to, applied to Discovery Edge."""
    known_none = C(funding=0)
    unknown    = C(funding=None)
    assert discovery_edge_component(known_none, "funding").sub_score == 1.0
    assert discovery_edge_component(unknown,    "funding").sub_score == 0.5

def test_portfolio_company_is_gated_not_just_scored():
    """Being in a tracked portfolio is a hard reject, not a low score —
    which is exactly why it is NOT a Discovery Edge component."""
    c = C(on_vc_portfolio=True)
    s = score_one(c, "northstar")
    assert s.tier == "reject"
    assert s.reject_reason == "already_on_vc_portfolio"

def test_discovery_edge_has_no_portfolio_component():
    """A component every scored company gets identically is a constant,
    not a signal. Guard against it being re-added."""
    keys = {c.key for c in discovery_edge_components(C())}
    assert "vc_portfolio" not in keys
    assert keys == {"age", "press_coverage", "disclosed_funding", "discovery_route"}
```

### 2.5 Qualification — `test_qualification_gate`

```python
def test_bare_registry_company_is_not_scored():
    """~60,000 companies incorporate every month. A SIC code alone is not
    a reason to spend Aryan's morning."""
    c = registry_company(qualifiers=[], discovery_route="registry")
    assert score_all(c, cfg) == []
    assert db.get(c.id).qualified == 0

@pytest.mark.parametrize("q", ["share_issue","grant","spinout","press",
                               "repeat_founder","website"])
def test_any_single_qualifier_admits_to_scoring(q):
    c = registry_company(qualifiers=[q], discovery_route="registry")
    assert score_all(c, cfg) != []

def test_unqualified_company_is_rechecked_not_rejected():
    """A company incorporated today may file an SH01 next month."""
    c = registry_company(qualifiers=[])
    run_pipeline(); assert db.get(c.id).qualified == 0
    add_signal(c, kind="share_issue")
    run_pipeline(); assert db.get(c.id).qualified == 1
```

### 2.6 Config — `test_config_*`

```python
def test_typo_uses_last_good_and_reports_in_sheet():
    save_snapshot(GOOD_CONFIG, is_last_good=True)
    cfg, errors = load_config(sheet_with(shortlist_fit="fourty five"))
    assert cfg.shortlist_fit == GOOD_CONFIG.shortlist_fit
    assert "not a number" in errors["shortlist_fit"]
    assert "45" in errors["shortlist_fit"]        # tells them the fallback used

@pytest.mark.parametrize("typed,expected", [
    ("yes", True), ("Y", True), ("1", True), ("✓", True),
    ("no", False), ("", False),
    ("£1.5m", 1_500_000.0), ("1,500,000", 1_500_000.0), ("1.5M", 1_500_000.0),
    ("Pre Seed", "pre_seed"), ("pre-seed", "pre_seed"), ("PRE_SEED", "pre_seed"),
    ("GB, IE", ["GB","IE"]), ("gb;ie", ["GB","IE"]),
])
def test_coercion_is_generous(typed, expected):
    assert coerce_value(typed) == expected

def test_blank_and_zero_mean_different_things():
    assert coerce_weight("") is None      # use default
    assert coerce_weight("0") == 0.0      # weight at zero
```

### 2.7 Privacy — `test_schema_privacy`

```python
FORBIDDEN = {"email","phone","address","postcode","date_of_birth",
             "dob_month","dob_year","nationality","country_of_residence"}

def test_founder_table_stores_no_sensitive_fields(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(founder)")}
    assert not (cols & FORBIDDEN)

def test_ch_officer_ingest_drops_dob_and_address():
    """CH returns partial DOB and correspondence address. Both must be
    dropped in the ADAPTER, not merely hidden at render."""
    raw = load_fixture("api/ch_officers_with_dob.json")
    founders = parse_officers(raw)
    for f in founders:
        assert not hasattr(f, "date_of_birth")
        assert not hasattr(f, "address")

def test_forget_removes_and_suppresses():
    founder_radar_forget("Jane Smith")
    assert query_founders(name="Jane Smith") == []
    reingest_same_article()
    assert query_founders(name="Jane Smith") == []   # suppression holds
```

---

## 3. Golden tests — recorded AI responses, never the network

```
tests/fixtures/
  articles/<slug>.html                # trimmed to <100 KB, committed
  articles/<slug>.expected.json       # hand-written truth
  llm_cache/<sha256>.json             # the recorded API response
```

```python
# conftest.py
@pytest.fixture
def offline_llm(monkeypatch):
    """Serve from the recorded cache. HARD-FAIL on a miss —
    a silent network call in CI is worse than a broken test."""
    def _call(prompt, **kw):
        key = cache_key(prompt, **kw)
        p = FIXTURES / "llm_cache" / f"{key}.json"
        if not p.exists():
            if os.environ.get("REFRESH_LLM") == "1":
                resp = real_call(prompt, **kw)
                p.write_text(json.dumps(resp, indent=2))
                return resp
            pytest.fail(f"LLM cache miss for {key}. Re-record with REFRESH_LLM=1.")
        return json.loads(p.read_text())
    monkeypatch.setattr("radar.extract.llm.client.call", _call)
```

`REFRESH_LLM=1 pytest` is the live-recording path and exists for when a real provider response is genuinely wanted. The committed entries, however, are **hand-authored by the fixture builder** (`tests/fixtures/build_extraction_fixtures.py`) and verified end-to-end at build time — recording needs an API key and pins the vendor's current whims, while hand-authored payloads pin the behaviour. The maintenance workflow — which tool does what, and the exact steps for a `PROMPT_VERSION` bump — is §3.1 below.

**The 25 fixtures must span the hard cases:**

| # | Fixture | Asserts |
|---|---|---|
| 1–5 | Clean single-company funding announcements | name, amount, stage, sector |
| 6–8 | **Round-ups / listicles** | `is_about_single_company == False` |
| 9–10 | University spinout announcements | `is_university_spinout`, `university_name` |
| 11–12 | Innovate UK grant awards | amount, no confusion with equity |
| 13 | Article about a large company | `rejection_reason == "already_large_company"` |
| 14 | Paywalled stub | `rejection_reason == "paywalled"` |
| 15 | Two companies — acquirer and target | picks the right subject or rejects |
| 16 | Non-English article | graceful handling |
| 17 | Company name that is also a common word | no false extraction |
| 18–20 | Accelerator cohort announcements | multiple companies handled correctly |
| 21 | Amount in USD or EUR | converted or flagged, not silently wrong |
| 22 | **Article with a company the model might invent** | evidence grounding catches it |
| 23–25 | Regional news, thin content | low confidence, `needs_review` |

```python
@pytest.mark.parametrize("fixture", ALL_ARTICLE_FIXTURES)
def test_extraction_matches_expected(fixture, offline_llm):
    got      = extract(load_html(fixture))
    expected = load_expected(fixture)
    assert got.is_about_single_company == expected["is_about_single_company"]
    if got.is_about_single_company:
        assert norm_key(got.company_name) == norm_key(expected["company_name"])
        assert got.sector == expected["sector"]
        assert got.amount_raised_gbp == pytest.approx(expected["amount"], rel=0.01)

def test_no_hallucinations(offline_llm):
    """Every evidence quote must appear verbatim in the source."""
    for fixture in ALL_ARTICLE_FIXTURES:
        text, got = load_text(fixture), extract(load_html(fixture))
        if got.evidence_quote_company:
            assert normalise_ws(got.evidence_quote_company) in normalise_ws(text)

def test_heuristic_fallback_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr("radar.extract.llm.client.call", raises(ProviderDown))
    result = extract(load_html("clean_funding_announcement"))
    assert result.extraction_method == "heuristic"
    assert result.needs_review is True
    assert result.company_name is not None     # heuristic still finds it
```

**Never assert exact string equality on free-text fields.** `temperature=0` does not guarantee byte-identical output — mixture-of-experts routing and batch-dependent floating-point reduction make provider inference non-deterministic in practice. Assert exact on enums and IDs, set-equality on founders after normalisation, and a rapidfuzz similarity ≥ 80 on descriptions.

### 3.1 Maintaining the golden fixtures — the pin, the builder, the rekey

Three committed pieces under `tests/fixtures/` keep the golden tests honest, and each has exactly one job:

| Piece | Job |
|---|---|
| `_golden_extractor.py` | **Pin.** Routes `prefilter.extract_text` to the dependency-free builtin extractor for every fixture-key computation (pytest session, the builder, the rekey tool). Without it, a machine with the `extract` extra installed hashes trafilatura's text — different keys than CI — and the golden suite fails there while staying green here. Production code is untouched. |
| `build_extraction_fixtures.py` | **Builder.** The source of truth for the 25 fixtures: renders `articles/<slug>.html`, writes the hand-authored `payload` blocks to `llm_cache/<key>.json` and the expectations to `articles/<slug>.expected.json`, prunes stale entries, then runs `extract()` end-to-end over the fresh cache and asserts every expectation. Self-verifying — a mis-generated fixture is a build error, not a surprise at test time. |
| `rekey_llm_cache.py` | **Rekey.** For when a key change moves every filename but the payloads stay valid: recomputes each fixture's key with the *real* code path (`prefilter` → `cache_key`) and moves the recorded entries to their new keys. No provider, no network, no payload edits. |

Run either with the project venv, from the repo root:

```
.venv/bin/python tests/fixtures/build_extraction_fixtures.py
.venv/bin/python tests/fixtures/rekey_llm_cache.py
```

**A `PROMPT_VERSION` bump, step by step.** The cache key is `sha256(prompt_version | model | normalise_ws(text))`, so bumping the constant invalidates every key while the hand-authored payloads stay valid — maintenance is usually just a rename, occasionally a payload edit:

1. Edit the prompt in `radar/extract/llm.py` and bump `PROMPT_VERSION`. (The bump is what invalidates the old keys; without it the new prompt silently reuses old answers.)
2. Decide which of the two the change actually is:
   - **Keys moved, behaviour unchanged** — e.g. a reworded prompt whose intended answers are the same. Run `rekey_llm_cache.py`; it moves every entry to its new key, and the `git diff` should read as pure renames.
   - **Behaviour changed** — the model is now *supposed* to answer differently. Hand-edit the affected `payload` blocks in the builder, then run `build_extraction_fixtures.py`. Its self-verification fails loudly on any fixture that no longer matches its own payload, so the edit-and-run loop is the review.
3. Run the full suite: `uv run python -m pytest`.
4. Review the diff in the pull request — which keys moved and which payloads changed is exactly the regression signal §3 promised.

A `DEFAULT_MODEL` change is the same shape: the model id is inside the hash, so keys move and rekey applies; the recorded `model_id` fields are then confirmed by the builder or the suite.

Two things the golden fixtures never do, deliberately:

- **Never let the extractor choice into the key.** The pin exists because trafilatura and the builtin extractor return different text for the same article (365 vs 418 chars when the drift was fixed), and the key hashes that text. `test_golden_cache_keys_are_independent_of_trafilatura` in `tests/unit/test_extraction.py` fakes trafilatura being installed and asserts the golden keys are unchanged — so if the pin is ever removed, the suite fails in CI even though CI has no trafilatura to drift with.
- **Never record from a live provider as the primary path.** The committed entries are hand-authored; `REFRESH_LLM=1` recording is the occasional exception, and its output should be reviewed into the builder rather than committed as-is.

---

## 4. Source adapter tests

Every adapter gets two tests. **No adapter merges without both.**

```python
def test_adapter_parses_committed_fixture():
    items = NorthernAcceleratorAdapter().parse(load_fixture("sources/northern_accelerator.json"))
    assert len(items) >= 3
    assert all(i.published_at is not None for i in items)
    assert all(i.source_url.startswith("https://") for i in items)
    assert len({i.external_id for i in items}) == len(items)   # ids are unique

def test_adapter_detects_layout_change():
    """The dangerous failure is 200 OK with an empty list — it looks
    like a quiet week rather than a bug."""
    with pytest.raises(LayoutChanged):
        OxfordAdapter().parse(load_fixture("sources/oxford_CHANGED.html"))
```

**Weekly live check** — not in CI, because websites change and a red CI nobody trusts is worse than no CI:

```python
@pytest.mark.live
@pytest.mark.parametrize("key", TIER_1_SOURCES)
def test_source_still_reachable_and_parseable(key):
    items = REGISTRY[key].fetch(FetchContext(limit=5))
    assert len(items) > 0, f"{key} returned nothing — check for a layout change"
```

Run it Mondays. It is the early-warning system for the failure mode that silently degrades quality.

---

## 5. Integration tests

Marked `integration`; need a scratch spreadsheet and a Companies House key.

```python
EXPECTED_TABS = {"📌 Today","Companies","Needs Review","Fund Criteria",
                 "Scoring Weights","Settings","Outreach","Sources","Run Log",
                 "Tuning","Lists","_meta"}          # 11 visible + 1 hidden

@pytest.mark.integration
def test_sheet_roundtrip(scratch_sheet):
    sync_sheet(scratch_sheet)
    assert set(tab_names(scratch_sheet)) == EXPECTED_TABS
    render(build_rows(200), scratch_sheet)
    assert row_count(scratch_sheet, "Companies") == 200

    set_cell(scratch_sheet, "Settings", "B2", "not a number")
    result = run_pipeline(scratch_sheet)
    assert result.status in ("ok", "partial")               # did not crash
    assert "❌" in get_cell(scratch_sheet, "Settings", "D2") # reported in the sheet

@pytest.mark.integration
def test_no_change_means_no_writes(scratch_sheet):
    render(rows, scratch_sheet)
    with count_api_calls() as n:
        render(rows, scratch_sheet)
    assert n.writes == 0

@pytest.mark.integration
def test_render_call_budget(scratch_sheet):
    with count_api_calls() as n:
        render(build_rows(200), scratch_sheet)
    assert n.total <= 10

@pytest.mark.integration
def test_user_columns_survive_a_resort(scratch_sheet):
    render(rows, scratch_sheet)
    set_cell(scratch_sheet, "Companies", "Z2", "worth contacting")
    manually_sort_by_column(scratch_sheet, "C")     # simulate Aryan sorting
    render(rows, scratch_sheet)
    assert verdict_for(scratch_sheet, company_id_of_row_2_before) == "worth contacting"

@pytest.mark.integration
def test_companies_house_window_sweep_live():
    got = CompaniesHouseAdapter().fetch(FetchContext(days_back=14))
    assert len(got) > 0
    for item in got:
        inc = date.fromisoformat(item.structured["date_of_creation"])
        assert (date.today() - inc).days <= 14      # THE guarantee
        assert not set(item.structured["sic_codes"]).issubset({"82990","70229"})
```

**The same sweep must also be covered offline**, because it is the most important discovery requirement in the system and the integration suite does not run in CI:

```python
def test_companies_house_window_sweep(mock_ch):     # offline, in the default suite
    mock_ch.load("api/ch_advanced_search_90d.json")
    got = list(CompaniesHouseAdapter().fetch(FetchContext(days_back=90)))
    assert mock_ch.request_count == 39              # 13 windows × 3 SIC tiers
    assert mock_ch.max_in_5min_window <= 600
    for item in got:
        assert within_window(item.structured["date_of_creation"], 90)

def test_sweep_narrows_window_on_truncation(mock_ch):
    """hits > len(items) means results were silently truncated. Companies
    would vanish with no error — the failure this system is built to avoid."""
    mock_ch.load("api/ch_truncated_5000.json")      # hits: 7200, items: 5000
    list(CompaniesHouseAdapter().fetch(FetchContext(days_back=7)))
    assert mock_ch.request_count > 1                # it re-queried with a smaller window
```

---

## 6. Chaos tests — proving nothing stops the run

These encode the failure table from `02-architecture.md` §7. **Every row of that table is a test.**

```python
def test_one_source_failure_does_not_stop_run(pipeline):
    with source_raising("uktn", ConnectionError):
        result = pipeline.run()
    assert result.status == "partial"
    assert source_status(result, "uktn") == "failed"
    assert len([s for s in result.sources if s.status == "ok"]) >= 12
    assert result.shortlisted >= 0          # the run completed

def test_llm_down_still_completes(pipeline):
    with llm_raising(ProviderDown):
        result = pipeline.run()
    assert result.status in ("ok", "partial")
    assert all(c.needs_review for c in result.companies_from_articles)

def test_sheets_down_keeps_data(pipeline, db):
    with sheets_raising(APIError(503)):
        pipeline.run()
    assert db.count("company WHERE synced = 0") > 0
    with sheets_working():
        pipeline.sync_sheet()
    assert db.count("company WHERE synced = 0") == 0

def test_digest_delivered_when_hermes_is_down(monkeypatch):
    monkeypatch.setattr("subprocess.run", returns(rc=1))       # hermes send fails
    with mock_telegram_api() as tg:
        send_digest("test")
    assert tg.called_direct_bot_api is True

def test_invalid_llm_json_is_quarantined_not_dropped(db):
    with llm_returning("{ not json"):
        extract(article)
    assert db.count("quarantine") == 1                          # kept for inspection

def test_rerun_is_idempotent(pipeline, db):
    pipeline.run(); a = snapshot(db)
    pipeline.run(); b = snapshot(db)
    assert a == b            # no duplicate companies, no duplicate signals

def test_ch_rate_limit_is_respected():
    with fake_clock() as clock:
        make_requests(700)
    assert max_requests_in_any_5_min_window(clock) <= 600
```

---

## 7. Operations — FR-9

Every clause of FR-9 has a test. These are cheap and they are the ones that catch a deployment that "looks fine" but silently never runs.

```python
@pytest.mark.integration
def test_timer_is_enabled_and_scheduled():
    out = run("systemctl list-timers founder-radar.timer --all")
    assert "founder-radar.timer" in out and "06:30" in out           # FR-9.1

def test_every_run_writes_a_run_log_row(pipeline, db):
    pipeline.run()
    row = db.one("SELECT * FROM run ORDER BY id DESC LIMIT 1")
    for f in ("items_fetched","companies_new","gated_out","shortlisted",
              "llm_calls","llm_cost_usd","status","finished_at"):
        assert row[f] is not None                                     # FR-9.2

def test_heartbeat_alerts_when_stale(db, mock_telegram):
    db.set_last_successful_run(hours_ago=27)
    run_cli("status --alert-if-stale 26h")
    assert mock_telegram.sent_count == 1                              # FR-9.3

def test_backup_creates_and_prunes(tmp_backups):
    run_cli("db backup")
    assert len(list(tmp_backups.glob("radar-*.db"))) == 1
    age_files(tmp_backups, days=15); run_cli("db backup")
    assert not any(f for f in tmp_backups.glob("radar-*.db") if older_than(f, 14))  # FR-9.4

@pytest.mark.integration
def test_env_file_is_0600_and_never_logged(caplog):
    assert oct(os.stat("/opt/founder-radar/.env").st_mode)[-3:] == "600"
    run_cli("run --dry-run")
    for secret in load_secret_values():
        assert secret not in caplog.text                              # FR-9.5

def test_every_telegram_command_maps_to_a_cli_command():
    """FR-9.6: nothing is trapped in the chat layer."""
    for cmd in parse_skill_commands("hermes/skills/founder-radar/SKILL.md"):
        assert cli_has_command(cmd), f"{cmd} is not a real CLI command"

def test_telegram_allowlist_rejects_unknown_user(bot):
    r = bot.handle(update(user_id=999999, text="/run"))
    assert r.status == "denied" and not r.ran_pipeline                # FR-8.4

def test_sh01_sets_has_share_issue(db):
    ingest_filing_history(load_fixture("api/ch_filing_history_sh01.json"))
    c = db.one("SELECT * FROM company WHERE id=?", cid)
    assert c["has_share_issue"] == 1                                  # FR-1.6
    assert derive_stage(c) == "pre_seed"

@pytest.mark.parametrize("outcode,expected", [
    ("NE1", "north_east"), ("S75", "yorkshire"), ("EC2A", "london"),
    ("EH1", "uk_regions"),        # Scotland: region is NULL, country wins
    ("CF10", "uk_regions"),       # Wales
    ("OX1", "uk_regions"),        # but fails outside_golden_triangle
])
def test_postcode_to_geography(outcode, expected):                    # FR-1.3
    assert derive_geography(*lookup_outcode(outcode)) == expected

@pytest.mark.parametrize("policy,sub,expect_num,expect_den", [
    ("neutral",     None, 0, 0), ("pessimistic", None, 0, 1),
    ("assume",      None, 1, 1), ("neutral",     0.5, 1, 1),
])
def test_unknown_value_policies(policy, sub, expect_num, expect_den): # FR-4.6
    ...

def test_sheet_edit_changes_scores_with_no_code_change(db):           # FR-4.7 / NFR-6
    before = score_one(company, cfg_with(max_company_age_months=36))
    after  = score_one(company, cfg_with(max_company_age_months=12))
    assert before.tier != after.tier
    assert before.config_hash != after.config_hash

def test_adding_a_source_touches_no_shared_code():                    # NFR-5
    """Guard the client's 9 July promise. A new adapter file plus a registry
    line must be the whole diff."""
    diff = git_diff_for_commit(FIXTURE_ADD_SOURCE_COMMIT)
    touched = {f for f in diff if not f.startswith("tests/")}
    assert touched <= {"radar/sources/newsource.py", "radar/sources/__init__.py"}
```

---

## 8. Performance

Marked `perf` so they sit outside the 60-second offline budget (NFR-4).

```python
@pytest.mark.perf
def test_full_run_under_25_minutes(): ...          # NFR-1
@pytest.mark.perf
def test_memory_under_700mb(): ...                 # NFR-2, must coexist with Hermes
@pytest.mark.perf
def test_rescore_5000_companies_under_1s(): ...    # makes weight-tuning interactive
def test_offline_suite_under_60s(): ...            # NFR-4, measured by a CI step
```

A CI grep step, cheap and worth having:

```bash
! grep -rn "token_set_ratio\|partial_ratio\|WRatio" radar/   # they merge subsets at 100
! grep -rn "or 0\b.*sub_score\|sub_score or 0" radar/score/  # None is not 0
```

---

## 9. Acceptance — the checklist before telling the client it's ready

**Ordered by what the client actually complained about.**

- [ ] **`test_freshness_gates` passes.** Every boundary case. *(The whole point.)*
- [ ] Backfill produces companies whose **median age is under 24 months**
- [ ] **Zero** shortlisted companies appear on any tracked VC portfolio page
- [ ] A Durham spinout routes to Northstar's Spinout Inspire Fund with the cheque range shown
- [ ] A Leeds company routes to Anticus and is rejected by Northstar's North East vehicles
- [ ] The same company found on three sources produces **one row**, with all three source links
- [ ] Every shortlist row has a **clickable source URL** *(client request, 24 July)*
- [ ] The "why" sentence names the evidence, and its points reconcile to the score
- [ ] Editing `max_company_age_months` in the sheet and re-running **changes the results with no code change**
- [ ] Editing a fund's sector weight and re-running **changes the ranking with no code change**
- [ ] A deliberate typo in Settings does not stop the run and **reports itself in the sheet in red**
- [ ] `/run northstar` in Telegram performs a fund-scoped run *(client request, 24 July)*
- [ ] With Hermes stopped, the digest **still arrives**
- [ ] With the AI provider blocked, the run **still completes**
- [ ] Killing a Tier 1 source leaves the other thirteen green
- [ ] Founder table contains **no** email, phone, address or date of birth
- [ ] `founder-radar forget` works and survives re-ingestion
- [ ] Source failures appear **only** on the Sources tab *(client request, 24 July)*
- [ ] `founder-radar doctor` passes every check on the live VPS
- [ ] Two consecutive real daily runs complete unattended
- [ ] Monthly cost projection is **under £10**

---

## 10. The one-week validation, before calling it done

Automated tests prove the code is correct. They cannot prove the *product* is right. That takes Aryan.

**Day 1–5.** Run daily. Aryan fills in the `Verdict` column on every shortlisted company.

**Day 6.** Run `founder-radar tune` and read the numbers:

| What the numbers say | What to do |
|---|---|
| Precision < 60% | Raise `shortlist_fit`, or check whether one attribute weight is dominating |
| Fewer than 2 per day | Lower `shortlist_fit`, widen `regions_enabled`, or extend `max_company_age_months` to 42 |
| More than 12 per day | Raise thresholds — Aryan asked for 5–10 |
| Median age above 24 months | **A source is leaking old companies.** Find it in the `Sources` breakdown. |
| Discovery Edge consistently low | The VC portfolio denylist may be too broad, or the news sources are dominating over Companies House |

**Day 7.** Lock the thresholds. Send Aryan the tuning table so he can see *why* they were chosen — not just what they are.

The real success signal is not in any test. It is Aryan sending one of these companies to a fund and the fund replying **"we hadn't seen that one."**
