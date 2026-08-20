# 06 — Scoring

**How a company goes from "found" to "worth Aryan's Tuesday morning".**

Four things happen, in this order:

1. **Freshness gates** — young enough and unknown enough to be worth anyone's time?
2. **Attribute derivation** — turn raw evidence (SIC codes, filings, officers) into the five things we score on
3. **Fund Fit** — how well it matches a fund's criteria, per vehicle
4. **Discovery Edge** — how likely it is the fund hasn't already seen it

Step 2 is the one that makes registry-first discovery work at all, and it is easy to skip when reading. Do not skip it.

---

## 1. Freshness gates — the fix

Universal: applied before any fund is considered. Every threshold lives in the `Settings` tab.

| Gate key | Default | Rejects when | Why |
|---|---|---|---|
| `max_company_age_months` | **36** | `age_months > 36` | The client's exact complaint. Also aligns with SEIS, which DSW requires. |
| `max_total_funding_gbp` | **3000000** | `total_funding_gbp > £3m` | "Many have already raised." Above a large seed, below a Series A. |
| `max_stage` | **`series_a`** | stage is `series_b_plus` or `growth` | All four funds are pre-seed to Series A. |
| `already_on_vc_portfolio` | **on** | `on_vc_portfolio = 1` | "There's a good chance these funds have already come across them." |
| `min_uk_presence` | **on** | no UK region, postcode or Companies House record | All four funds require UK. |

### Unknown values at a gate — the rule

**A gate whose input is `NULL` PASSES, and sets a flag. Any flag set means the company cannot reach `shortlist`.**

```python
UNKNOWN_FLAGS = {
    "max_company_age_months": "age_unknown",
    "min_uk_presence":        "uk_unverified",
    "max_stage":              None,          # unknown stage is normal, not a flag
    "max_total_funding_gbp":  None,          # unknown funding is normal, not a flag
}
```

Rejecting on unknown would throw away good early candidates. Shortlisting on unknown would let old companies back in through the gap. **Watchlist with a stated reason is the honest answer**, and it matches how per-vehicle hard rules behave (§4.3).

The scoring pool and the surfaced review queue are intentionally different:
`age_unknown` rows may remain watchlist research prompts, but Today requires a
non-null `incorporated_on` before presenting one as an opportunity. This keeps
the unknown-value policy without allowing an undated portfolio record to look
fresh by default.

Age is determined in this order:

```python
def age_months(company) -> float | None:
    if company.incorporated_on:      # Companies House — the legal record
        return months_between(company.incorporated_on, today())
    if company.founded_year:         # stated by a source
        return months_between(date(company.founded_year, 7, 1), today())
    return None                      # unknown — NOT zero
```

### Why this structurally fixes version 1

Version 1 filtered *after* discovery, so its ceiling was set by what the sources returned — and portfolio pages return old companies by definition. Version 2 filters at the source: `incorporated_from` is a parameter the register enforces. **A company incorporated ninety days ago cannot be six years old.**

**`test_freshness_gates` is the single most important test in the suite.** It must cover every boundary: exactly 36 months, 36 months + 1 day, unknown age, unknown funding, exactly £3,000,000, £3,000,001.

---

## 2. Attribute derivation — how a register record becomes scoreable

This section exists because of a real design trap. **A Companies House record contains none of the five things we score on.** It has an incorporation date, SIC codes, a postcode, officers and filings. It has no sector, no stage, no founder signal, no traction signal in our vocabulary.

Without explicit derivation rules, a registry-sourced company would have one known attribute out of five, score 20% coverage, fail the coverage floor, and land in watchlist forever — which would make the entire registry-first approach decorative.

The fix is a set of **deterministic derivation rules that map free public evidence into the client's existing vocabulary.** No new vocabulary, no change to his matrix.

### 2.1 Sector ← SIC codes

A lookup table in the `Lists` tab, tab column `SIC → Sector`. Seeded values:

| SIC | Sector | SIC | Sector |
|---|---|---|---|
| 62012, 62020, 62090 | `b2b_saas` | 21100, 21200, 32500, 26600 | `life_sciences` |
| 63110, 63120, 63990 | `ai_data` | 72110 | `life_sciences` |
| 72190, 71121, 71122, 71129 | `deeptech` | 86xxx | `healthcare` |
| 26110, 26120, 26200, 26511, 26701 | `industrial_tech` | 35xxx, 38xxx | `climate_tech` |
| 64205, 66190 | `fintech` | 58210, 62011 | `consumer` |
| 58290 | `developer_tools` | *(anything else)* | `other` |

**Precedence:** a sector stated by a source (news, spinout page, company website) always beats a SIC-derived one, because `SOURCE_TRUST` ranks `news` (40) and `company_site` (70) above the derivation, which is recorded as `source_type = "derived"` with trust 30 and confidence 0.6.

SIC on a newly incorporated company is **self-declared by the founder or their formation agent, never audited, and often lazily generic.** Treat it as a cheap high-recall signal, not ground truth — which is exactly what the low confidence encodes.

### 2.2 Geography ← postcode

`hq_postcode` → postcodes.io → `region` (England only) or `country` (Scotland/Wales/NI) → the vocabulary:

```python
def derive_geography(region: str | None, country: str, outcode: str) -> str:
    if region == "London":                    return "london"
    if region == "North East":                return "north_east"
    if region == "Yorkshire and The Humber":  return "yorkshire"
    if country in ("Scotland", "Wales", "Northern Ireland"):
        return "uk_regions"
    if region:                                return "uk_regions"
    return "uk_wide"                          # UK confirmed, location not resolved
```

> ⚠️ **postcodes.io returns arrays, and `region` is populated for England only.** An outcode can straddle boundaries, so `region`, `country` and `admin_district` come back as lists. Take element `[0]`. For Scotland, Wales and Northern Ireland `region` is empty — fall through to `country`. The `postcode_region.region` column must be **nullable**; `country` must not be.

The **golden-triangle rule** used by DSW's SEIS fund is an outcode-prefix check, not a fuzzy city match: `outcode` starts with `OX` or `CB`, or region is `London` → fails `outside_golden_triangle`.

### 2.3 Stage ← filings and announcements

```python
def derive_stage(c) -> str | None:
    if c.announced_round_stage:                  return c.announced_round_stage   # news wins
    if c.has_share_issue and c.age_months <= 24: return "pre_seed"   # SH01 on a young co
    if c.has_share_issue:                        return "seed"
    if c.age_months is not None and c.age_months <= 12 and not c.has_share_issue:
        return "idea"
    return None
```

**This is what makes the SH01 signal earn its place.** A return of allotment of shares filed on a company incorporated eight months ago is, in practice, a pre-seed round being papered. It hits the public register within days, it is free, and no portfolio-page scraper will ever see it. Here it becomes a scored attribute rather than a decorative flag.

### 2.4 Founder signal ← officers and PSC

Evaluated top-down; first match wins:

```python
def derive_founder_signal(c, founders) -> str | None:
    if c.is_university_spinout:                          return "research_spinout"
    if any(f.prior_appointments >= 1 for f in founders):  return "repeat_founder"
    if c.sector in ("deeptech","life_sciences") and any(f.is_psc for f in founders):
        return "technical_founder"
    if founders:                                          return "generalist_unclear"
    return None
```

`generalist_unclear` is a **known** value that scores 0, not an unknown. That distinction matters: it means "we looked and found no standout signal", which is a real finding and should count in the denominator.

### 2.5 Traction signal ← grants and evidence

```python
def derive_traction_signal(c, signals) -> str | None:
    if any(s.kind == "grant_award" for s in signals):  return "clinical_grant_validation"
    if any(s.kind == "competition_win" for s in signals): return "community_traction"
    if c.stage in ("pre_seed","idea") and c.has_share_issue is False:
        return "pre_revenue_concept"
    return None                                        # honestly unknown
```

Traction is the attribute the register genuinely cannot tell us about. Leaving it `None` for most registry companies is correct — that is what `coverage` is for.

### 2.6 What this yields in practice

| Company profile | Known attributes | Coverage |
|---|---|---|
| Register-only, no qualifying signal | sector, geography | 0.40 → **not scored** (§3) |
| Register + SH01 | sector, geography, stage | 0.60 |
| Register + SH01 + prior-appointment founder | + founder signal | 0.80 |
| Register + spinout + grant | sector, geography, founder, traction (+stage if SH01) | 0.80–1.00 |
| News article about a funding round | usually all five | 1.00 |

**Every derived value is written as an `observation` with `source_type = "derived"`, its own confidence, and the rule name in `extractor_ver`** — so the sheet can always answer "where did this sector come from?" with "derived from SIC 72190".

---

## 3. Qualification — Track B needs a reason to exist

Roughly 60,000 companies are incorporated in the UK every month. Most are dormant, holding vehicles, or one-person consultancies. Scoring all of them would be noise.

**A registry-sourced company enters scoring only if it has at least one qualifying signal:**

| Qualifier | Evidence |
|---|---|
| `share_issue` | SH01 filed since incorporation |
| `grant` | matched to a UKRI or Innovate UK award |
| `spinout` | matched to a university spinout announcement |
| `press` | matched to any news article |
| `repeat_founder` | an officer with a prior UK directorship |
| `website` | a live company website resolved and reachable. **Proven, not admitting by default** — add `website` to the Lists tab `qualifiers` column to loosen. |

Companies with none of the *admitting* qualifiers stay in the `candidates` pool with `qualified = 0`. They are **not rejected** — they are re-checked on every run, because a company incorporated today may file an SH01 next month. They simply never reach the sheet until they earn it.

`min_qualifiers` is a Setting, default **1**. Raise it to 2 if the noise is still too high.

This is also the honest answer to "why isn't every new company on my list?" — because a company with nothing but a SIC code genuinely is not worth Aryan's time yet.

---

## 4. Fund criteria — the four funds, eleven vehicles

Verified against each fund's own site and the British Business Bank / Finance Yorkshire / North East Fund pages on 7 August 2026. This entire table lives in the `Fund Criteria` sheet tab, keyed so tests and code agree.

### Vehicle keys — the canonical strings

| `fund_key` | `vehicle_key` | Name |
|---|---|---|
| `outward` | `fund_ii` | Outward VC Fund II (ECF) |
| `dsw` | `seis_fund` | DSW SEIS Fund |
| `dsw` | `eis_service` | DSW EIS Investment Service |
| `dsw` | `bbi_coinvest` | British Business Investments co-investment |
| `northstar` | `spinout_inspire` | North East Spinout Inspire Fund |
| `northstar` | `venture_sunderland` | Venture Sunderland Fund |
| `northstar` | `ne_innovation_fund` | North East Innovation Fund |
| `northstar` | `eis_growth` | Northstar EIS Growth Fund |
| `northstar` | `ne_social` | NE Social Investment Fund |
| `anticus` | `fy_seedcorn` | Finance Yorkshire Seedcorn Fund |
| `anticus` | `fy_growth` | Finance Yorkshire Growth Fund |

These exact strings are what `test_vehicle_routing` and `test_worked_example_metzero` assert on. Seed them in `radar/config/defaults.py` and in the sheet's `Fund key` / `Vehicle key` columns.

### 4.1 Outward VC — 1 vehicle

| Field | Value |
|---|---|
| Stage | pre-seed · seed · pre-Series A |
| Cheque | £250,000 – £2,500,000 |
| Sectors + | fintech, insurtech, regtech, lending, wealthtech, legaltech, proptech, cybersecurity, healthtech, HR tech, AI-native enterprise software, data infrastructure |
| Sectors − | generic SaaS with no finance layer, deeptech with no fintech use case, consumer apps |
| Geography | **HARD: UK.** Registered address UK, principal place of business UK, ≥66% of the exec team UK tax resident *(ECF mandate)* |
| Hard rejects | `round_max:5000000` · `prior_total_max:20000000` · `uk_exec_pct_min:66` |
| Age cap | none — not an EIS fund |
| One-liner | *"Send if finance is the product or an essential layer in the workflow."* |

### 4.2 DSW Ventures — 3 vehicles

| Vehicle | Stage | Cheque | Geography |
|---|---|---|---|
| `seis_fund` | pre-seed | up to £250k *(SEIS lifetime cap)* | **HARD: outside the London–Oxbridge triangle** |
| `eis_service` | seed → Series A | £100k – £1m+ | UK regional |
| `bbi_coinvest` | alongside the above | — | UK regional |

Sectors +: B2B SaaS, vertical SaaS, deeptech, university spinouts, life sciences, medtech, AI tooling, consumer (secondary).
Sectors −: **SEIS/EIS excluded trades** — lending, banking, insurance, financial services, property development, hotels, nursing homes, energy generation, farming, leasing, coal, steel.
Hard rejects: `requires_seis_eis:true` · `valuation_max:10000000` · `round_max:2500000`. Age cap 3 years (SEIS) / 7 (EIS) / 10 (knowledge-intensive).

### 4.3 Northstar Ventures — 5 vehicles

| Vehicle | Stage | Cheque | Geography rule |
|---|---|---|---|
| `spinout_inspire` (£22.5m, Jun 2026) | pre-seed / seed | £200k – £750k | **HARD: `university_spinout_required:durham,newcastle,northumbria,sunderland,teesside`** |
| `venture_sunderland` (£16m) | all stages | £200k – £750k | **HARD: company HQ in the Sunderland city region** |
| `ne_innovation_fund` (£27m) | pre-start → early growth | £50k – £500k | **HARD: County Durham, Tyne & Wear, Northumberland** |
| `eis_growth` (tranche 3) | late seed → Series A | *unpublished* | SOFT: "primarily the north of England"; `requires_seis_eis:true` |
| `ne_social` | social enterprise | £100k – £1m | HARD: North East England |

> **Note on Venture Sunderland.** The fund's own wording is "founders based in or relocating to Sunderland". We deliberately implement this as **company HQ in the Sunderland city region**, because the data model forbids storing founder home addresses (`03-data-model.md` §2). The gate is a good proxy and a defensible one; do not add founder addresses to make it exact.

Sectors +: cleantech / net zero, healthy ageing, healthcare, life sciences, AI / digital software, advanced manufacturing, computer games and esports, future of work, tech for good.

The `spinout_inspire` rule makes **Northern Accelerator the highest-value source in the system for this fund** — it publishes exactly those five universities' spinouts, dated, in clean JSON.

### 4.4 Anticus Partners — 2 vehicles

| Vehicle | Stage | Cheque | Geography |
|---|---|---|---|
| `fy_seedcorn` | seed / early, must be **beyond research stage** | up to £1.5m | **HARD: Yorkshire and Humber** |
| `fy_growth` | growth, profitable or approaching | up to £1.5m | Same |

Sectors: **effectively agnostic.** No age cap — public capital, not SEIS/EIS.

> ⚠️ **Stated versus revealed preference — the biggest divergence of the four.** Anticus says "technology and knowledge-based businesses", but the portfolio includes a cereal brand, a cashmere retailer and an auction house. **Treat the sector filter as genuinely broad; Yorkshire geography is the binding constraint.** Weighting their sector column heavily would systematically miss the companies they actually back.

### 4.5 Hard rejects — the mini-language

Supported keys, printed at the top of the `Fund Criteria` tab so Aryan never guesses:

```
round_max · prior_total_max · valuation_max · uk_exec_pct_min ·
university_spinout_required · beyond_research_stage · requires_seis_eis
```

Each needs a field in `company` (`last_round_gbp`, `prior_total_gbp`, `valuation_gbp`, `uk_exec_pct`, `is_university_spinout`, `spinout_university`, `seis_eis_qualifying`). **All are `NULL` unless a source supplied them, and a `NULL` input passes the gate and sets `gate_unverified`** — same policy as §1. A company with `gate_unverified` set cannot reach `shortlist`; it reaches `watchlist` with the reason *"eligibility unconfirmed — check <rule> manually"*.

That policy choice is deliberate and worth stating plainly: **these mandates are real, but we usually cannot verify them from public data.** Pretending otherwise in either direction — auto-rejecting or silently ignoring — would be worse than telling Aryan which box to tick himself.

### 4.6 Stage range: a gate, not a score

`Stage min` / `Stage max` on a vehicle **is a hard gate**. Stage is *also* a scored attribute, and that is fine — the gate decides eligibility, the score decides preference within it. A company whose stage is `NULL` passes the gate with `gate_unverified`.

### 4.7 Cross-fund observations worth encoding

1. **Geography is the strongest filter, and it is legally binding for three of the four.** Only Outward's constraint is UK-national rather than regional.
2. **Tax-scheme eligibility splits the group cleanly.** DSW (every deal) and Northstar's `eis_growth` require SEIS/EIS, which imports the excluded-trades list. Outward (ECF) and Anticus (recycled public money) do not. Carry `requires_seis_eis` per vehicle.
3. **A vehicle failing is not a fund failing.** A Yorkshire company fails Northstar's four North East vehicles but may still fit `eis_growth`'s softer rule. Scoring per vehicle and taking the best is what catches that.

---

## 5. The weight matrix — the client's own model

Preserved verbatim from Aryan's `VC Scout.xlsx`. It is his mental model and he can edit it. Lives in `Scoring Weights`, six columns: `Attribute | Category | DSW | Northstar | Outward | Anticus`.

<details>
<summary><strong>Full matrix (values 0–4)</strong></summary>

**Stage** — Idea 1/1/2/1 · Pre-seed 3/2/3/2 · Seed 3/3/3/2 · Series A 2/3/1/1 · Series B+ 0/1/0/1 · Growth 0/1/0/3

**Sector** — Fintech 0/0/4/0 · Insurtech 0/0/4/0 · Wealthtech 0/0/4/0 · Lending 0/0/4/0 · Regtech 0/0/4/0 · B2B SaaS 3/1/1/1 · Vertical SaaS 3/1/2/1 · AI/Data 2/1/3/1 · Climate Tech 1/4/0/1 · Healthy Ageing 0/4/0/1 · Life Sciences 1/4/0/0 · Healthcare 1/2/1/1 · Deeptech 3/3/0/1 · Developer Tools 2/1/1/0 · Consumer 1/0/0/1 · Marketplace 0/0/0/1 · Industrial Tech 1/2/0/1 · Other 0/0/0/0

**Geography** — London 0/0/1/0 · UK Regions 4/2/1/1 · North East 2/4/0/0 · Yorkshire 1/1/0/4 · UK Wide 2/2/1/2 · Europe ex-UK 0/0/1/0 · Global 0/0/1/0 · US 0/0/0/0

**Founder signal** — Repeat founder 2/2/2/2 · Technical founder 3/2/2/1 · Domain expert 1/2/4/2 · Research/spinout 4/4/0/0 · Operator-led 1/1/2/2 · Student founder 0/0/1/0 · Generalist/unclear 0/0/0/0

**Traction signal** — Pre-revenue concept 0/0/1/0 · Pilot customers 1/1/2/1 · Paying customers 3/2/3/2 · Rapid usage growth 2/1/2/1 · Strong revenue growth 3/3/2/3 · Enterprise contracts 2/3/2/2 · Clinical/grant validation 1/3/0/1 · Community traction 1/0/1/1

*(Column order throughout: DSW / Northstar / Outward / Anticus)*

</details>

### 5.1 Attribute importance — the second, smaller table

The matrix says *how good* a value is for a fund. Attribute importance says *how much that attribute matters* relative to the other four. Both are needed, and the pair is what `fund_fit()` multiplies.

A block at the bottom of the `Scoring Weights` tab, headed `ATTRIBUTE IMPORTANCE`:

| Attribute | DSW | Northstar | Outward | Anticus |
|---|---|---|---|---|
| `stage` | 3 | 3 | 3 | 3 |
| `sector` | 4 | 4 | 4 | 2 |
| `geography` | 4 | 4 | 2 | 4 |
| `founder_signal` | 3 | 3 | 3 | 3 |
| `traction_signal` | 2 | 2 | 3 | 3 |

Integers **0–10**. A blank cell means **1**, not 0. These are the seeded defaults and they encode what the research found: Outward cares less about region and more about traction; Anticus is nearly sector-agnostic but Yorkshire is everything.

`cfg.attribute_weight(attr, fund_key)` reads exactly this block. `cfg.matrix_value(attr, value, fund_key)` reads the big matrix above. They are different functions over different tables and must not be confused.

### 5.2 One change from the client's version

His workbook summed the five attribute scores into a raw total. That number is not comparable across funds — their columns have different totals — and it silently invalidates every threshold the moment a new row is added. Version 2 reports a **percentage of the configured maximum across all attributes, plus evidence coverage.** Unknown attributes never become failures, but they remain in the headline denominator so a sparse record cannot look like a perfect fit. The raw sum is still shown in a column, because he is used to it.

---

## 6. Computing Fund Fit

```python
ATTRS = ["stage", "sector", "geography", "founder_signal", "traction_signal"]

def fund_fit(company, fund, vehicle, cfg) -> FitScore:
    comps = []
    for attr in ATTRS:
        value  = getattr(company, attr)                    # may be None
        weight = cfg.attribute_weight(attr, fund.key)      # the §5.1 table
        if value is None:
            comps.append(Component(attr, sub_score=None, weight=weight,
                                   evidence="unknown"))
        else:
            raw = cfg.matrix_value(attr, value, fund.key)  # 0..4 from the big matrix
            comps.append(Component(attr, sub_score=raw / 4.0, weight=weight,
                                   evidence=label_of(value)))

    known   = [c for c in comps if c.sub_score is not None]
    earned  = sum(c.weight * c.sub_score for c in known)
    max_ach = sum(c.weight for c in known)
    max_all = sum(c.weight for c in comps)

    pct      = 100.0 * earned / max_all if max_all else 0.0
    coverage = len(known) / len(comps) if comps else 0.0
    return FitScore(vehicle_key=vehicle.key, pct=round(pct, 1),
                    coverage=round(coverage, 2), components=comps)
```

### Why the full-model percentage, and the trap it prevents

**Why it's right:** comparable across funds with different criteria counts, and adding an attribute row does not inflate every sparse record into a perfect score. Coverage remains a separate count of confirmed attributes, so the reader can see how much of the record is evidenced.

**The trap it prevents:** a company with one known attribute scoring 1.0 would otherwise get 100%. Keeping unknown criteria in the denominator makes the headline honest even before the coverage gate is applied.

**The fix:** `coverage` is a first-class output, both numbers appear side by side in the sheet, and **shortlist eligibility requires a coverage floor.** Combined with the derivation rules in §2 and the qualification gate in §3, a registry-sourced company that reaches scoring will typically have coverage of 0.6–1.0 — comfortably above the floor.

### Unknown values

Three policies, set per attribute in the `Scoring Weights` tab, column `Unknown policy`:

| Policy | Numerator | Denominator | Use for |
|---|---|---|---|
| **`neutral`** *(default)* | excluded | included in headline | Most things. Absence of evidence is not evidence of absence, but an unknown still lowers confidence in the overall fit. |
| `pessimistic` | 0 | included | Attributes reliably observable when true. Not finding it *is* weak negative evidence. |
| `assume` | assumed value | included | Source-implied facts — a UKRI grant record implies `geography` is UK. |

**Never coerce `None` to `0` anywhere in the stack.** There is a unit test for this; do not "fix" it.

---

## 7. Discovery Edge

**How likely is it that this fund has *not* already seen this company?** 0–100, deterministic, evidence-backed.

| Component | Weight | Sub-score |
|---|---|---|
| **Company age** | 30 | ≤6 months → 1.0 · 7–18 → 0.8 · 19–30 → 0.5 · 31–36 → 0.2 · >36 → 0.0 |
| **Press coverage in tracked UK sources** | 30 | 0 articles → 1.0 · 1 → 0.7 · 2–4 → 0.4 · ≥5 → 0.0 |
| **Disclosed funding** | 20 | known none → 1.0 · **unknown → 0.5** · <£500k → 0.6 · £500k–£1.5m → 0.3 · >£1.5m → 0.0 |
| **Discovery route** | 20 | spinout → 1.0 · accelerator → 0.9 · grant → 0.8 · register → 0.7 · news → 0.5 |

Four deliberate corrections from the first draft of this model:

1. **There is no "VC portfolio presence" component.** Being on a tracked portfolio is a *hard gate* (§1) evaluated before scoring, so every company that reaches Discovery Edge would score identically on it — a constant dressed as a variable. Its weight is redistributed.
2. **"Press coverage" is named honestly.** It measures articles found *in our tracked sources*, not global fame. A company covered only by an outlet we don't read scores as obscure. That is a real limitation, and naming it stops it being mistaken for something stronger.
3. **Unknown funding scores 0.5, not 1.0.** Collapsing `NULL` into "known zero" would violate the one invariant this whole system holds to.
4. **Discovery route rewards source vetting, not one data-collection mechanism** (rebalanced 18 Aug 2026). The more selective the discovery source, the higher the band: spinout 1.0 → news 0.5. A registry find keeps a middle band — still invisible, but no longer the headline edge.

Scored with the same machinery as Fund Fit — same `Component` type, same normalisation, same explanation generator.

### Why this exists

Aryan's exact words on 1 August: *"there's a good chance these funds have already come across them, and I'm not really adding much value."*

A scout's value is **information the fund does not have.** Ranking on Fund Fit alone produced the version 1 complaint. Discovery Edge is the number that encodes his actual job.

### Final ranking

```python
priority = cfg.weight_fit * fund_fit_pct + cfg.weight_edge * discovery_edge
# defaults: weight_fit = 0.60, weight_edge = 0.40, both editable in Settings
```

---

## 8. Tiering

```python
def tier_of(fit, edge, flags, cfg) -> tuple[str, str]:
    """Returns (tier, reason). reason is '' when there is nothing to explain."""
    if flags:                                    # age_unknown, uk_unverified, gate_unverified
        if fit.pct >= cfg.watchlist_fit:
            return "watchlist", f"{flags[0].replace('_',' ')} — verify before sending"
        return "reject", "below fit threshold"
    if fit.pct >= cfg.shortlist_fit and edge >= cfg.shortlist_edge \
       and fit.coverage >= cfg.min_coverage:
        return "shortlist", ""
    if fit.pct >= cfg.shortlist_fit and fit.coverage < cfg.min_coverage:
        return "watchlist", "strong fit but too little known — needs 10 minutes of research"
    if fit.pct >= cfg.shortlist_fit and edge < cfg.shortlist_edge:
        return "watchlist", "good fit but likely already on their radar"
    if fit.pct >= cfg.watchlist_fit:
        return "watchlist", ""
    return "reject", "below fit threshold"
```

| Setting | Default |
|---|---|
| `shortlist_fit` | 70 |
| `shortlist_edge` | 55 |
| `min_coverage` | 0.50 |
| `watchlist_fit` | 45 |
| `min_qualifiers` | 1 |

The returned `reason` is appended to `score.explanation`, not stored separately — `reject_reason` is reserved for gates.

Note that "scores high but we know too little" lands in **watchlist with an explicit reason**, not reject. That is a research prompt, not a dismissal.

### Tuning against Aryan's own judgement

The `Verdict` column is labelled training data, and it is what makes the thresholds defensible instead of guessed. `founder-radar tune` writes a sweep to the `Tuning` tab:

| Threshold | Would shortlist | Precision | Recall | F1 |
|---|---|---|---|---|
| 60 | 41 | 0.61 | 0.94 | 0.74 |
| 65 | 32 | 0.69 | 0.88 | 0.77 |
| **70** | **23** | **0.78** | **0.81** | **0.79** |
| 75 | 15 | 0.87 | 0.62 | 0.72 |
| 80 | 9 | 0.89 | 0.41 | 0.56 |

Aryan reads: *"at 70 you'd get 23 companies and like 78% of them."* About 50 labels is enough. The same sweep runs over the §5.1 importance weights, perturbing one at a time and reporting the F1 change — which tells him which attributes are doing work and which are decorative.

---

## 9. The explanation — deterministic, no AI

Aryan asked on 9 July for the system to *"explain why it surfaced a startup, rather than only giving it a score."*

```python
def explain(fit, edge, signals, vehicle, flags) -> str:
    known = [c for c in fit.components if c.sub_score is not None]
    tw = sum(c.weight for c in known) or 1.0
    pts = lambda c: 100 * c.weight * c.sub_score / tw

    pos = sorted((c for c in known if c.sub_score >= 0.6), key=pts, reverse=True)[:3]
    neg = sorted((c for c in known if c.sub_score <= 0.34),
                 key=lambda c: c.weight * (1 - c.sub_score), reverse=True)[:2]
    unk = sorted((c for c in fit.components if c.sub_score is None),
                 key=lambda c: c.weight, reverse=True)[:2]

    parts = []
    if signals:
        parts.append("Found via " + "; ".join(
            f"{s.headline} ({s.occurred_on})" for s in signals[:2]))
    if pos:
        parts.append("Matches on " + "; ".join(
            f"{c.label.lower()} ({c.evidence}, +{pts(c):.0f}pts)" for c in pos)
            + f" — {sum(pts(c) for c in pos):.0f} of {fit.pct:.0f} total")
    if neg:
        parts.append("Against: " + "; ".join(
            f"{c.label.lower()} ({c.evidence})" for c in neg))
    if unk:
        parts.append("Unknown: " + ", ".join(c.label.lower() for c in unk))
    if vehicle:
        parts.append(f"Route to {vehicle.name} ({vehicle.cheque_range})")
    if edge >= 70:
        parts.append("Low visibility — no coverage found in our tracked sources")
    if fit.coverage < 0.5:
        parts.append(f"Only {fit.coverage:.0%} of criteria could be assessed")
    for f in flags:
        parts.append(f"⚠ {f.replace('_',' ')}")
    return ". ".join(parts) + "."
```

The `— X of Y total` clause is what makes the arithmetic checkable: the reader sees the top-three contribution *and* the headline, so the two never appear to contradict. `test_explanation_arithmetic_reconciles` asserts against that clause, not against a loose tolerance.

**What Aryan sees:**

> Found via Northern Accelerator spinout announcement (2026-07-28); Companies House incorporation (2026-06-14). Matches on geography (North East, +25pts); sector (Life Sciences, +25pts); founder signal (research/spinout, +19pts) — 69 of 92 total. Against: traction signal (pre-revenue concept). Route to North East Spinout Inspire Fund (£200k–£750k). Low visibility — no coverage found in our tracked sources.

Why this beats an AI-written summary: **free, instant, byte-identical for identical inputs** (so it can be asserted in a plain string test), **cannot hallucinate** (every clause comes from a computed number), and **the totals reconcile**. That last property matters because Aryan will be showing these to real investors.

The full component breakdown also goes into the **cell note** on the score cell — rich detail on hover, no clutter in the grid.

---

## 10. Governance

- **`config_hash`** = `sha256(canonical_json(validated_config))`, stamped on every score row with `scored_at` and `scorer_version`. Without it, *"why did this drop off my shortlist?"* is unanswerable.
- **Re-score on config change**, rendering a `Δ` column and a `tier changed` column so an edit produces visible feedback.
- **Cap single-attribute dominance** on the **effective** share (weight ÷ `max_ach`), not the configured share — for a company with two known attributes the effective share is 50% each. Warn in the config status column above ~50% effective.
- **Rank within fund as well as across.** A stricter fund systematically scores lower, so any "top N overall" view should prefer rank-within-fund.
- **Snapshot every run** into `score_history`, which makes "new to the shortlist this week" a one-line query.

---

## 11. Worked example

**METzero Technologies** — from Aryan's own spreadsheet, re-scored under version 2 with the seeded importance weights from §5.1.

```
Company:        METzero Technologies
Found via:      Northern Accelerator spinout announcement (Durham)
                Companies House: incorporated 2024-03-11
Derived:        sector      Climate Tech      (stated by the spinout page)
                geography   north_east        (NE1 → postcodes.io → North East)
                stage       seed              (announced round)
                founder     research_spinout  (is_university_spinout = true)
                traction    clinical_grant_validation  (Innovate UK award matched)
Age:            29 months     Funding: £450,000     Press: 2 articles
On a VC portfolio: no

FRESHNESS GATES
  age 29m ≤ 36 · funding £450k ≤ £3m · stage seed ≤ series_a
  not on a tracked portfolio · UK confirmed                    ALL PASS
  flags: none

NORTHSTAR — vehicle spinout_inspire
  HARD GATE  university_spinout_required → Durham            PASS
  HARD GATE  stage range pre_seed..seed → seed               PASS

  attr        value                     matrix  sub   weight  earned
  stage       seed                        3/4   0.75    3      2.25
  sector      climate_tech                4/4   1.00    4      4.00
  geography   north_east                  4/4   1.00    4      4.00
  founder     research_spinout            4/4   1.00    3      3.00
  traction    clinical_grant_validation   3/4   0.75    2      1.50
                                                 ────────────────────
  earned 14.75  ·  max achievable 16  ·  max all 16
  FUND FIT  = 100 × 14.75 / 16          = 92.2%
  COVERAGE  = 16 / 16                   = 1.00

  DISCOVERY EDGE
    age 29m (continuous curve) 0.38 × 30 = 11.5
    2 tracked articles       0.4 × 30 = 12.0
    £450k disclosed          0.6 × 20 = 12.0
    route: spinout (1.0)     1.0 × 20 = 20.0
                                       ───────
                                          55.5

  PRIORITY = 0.60 × 92.2 + 0.40 × 55.5 = 77.5
  TIER     : fit 92.2 ≥ 70 ✓ · edge 55.5 ≥ 55 ✓
           → SHORTLIST
```

**Read the route line carefully — it is the discovery rebalance (18 Aug 2026).** METzero is a near-perfect fit for Northstar, and it arrived through a spinout source — a highly selective discovery route — so it clears the edge floor. A company found only through a generic news article would score 0.5 on this component and stay on watchlist: the system still says, in writing, when a high-fit company has probably already been seen.

```
ANTICUS   both vehicles: HARD GATE Yorkshire → North East      FAIL
          → reject, reason "no_eligible_vehicle"

OUTWARD   fund_ii passes hard gates (UK; round size unknown → gate_unverified)
  stage seed 3/4=0.75 ×3 = 2.25 · sector climate_tech 0/4=0.00 ×4 = 0.00
  geography north_east 0/4=0.00 ×2 = 0.00 · founder research_spinout 0/4=0.00 ×3 = 0.00
  traction clinical_grant 0/4=0.00 ×3 = 0.00
  earned 2.25 / max 15  → FUND FIT 15.0%   → reject, below threshold

DSW       seis_fund: HARD GATE outside_golden_triangle → NE1  PASS
                     HARD GATE stage range idea..pre_seed → seed  FAIL
          eis_service: stage range pre_seed..series_a → seed   PASS
  stage seed 3/4=0.75 ×3 = 2.25 · sector climate_tech 1/4=0.25 ×4 = 1.00
  geography north_east 2/4=0.50 ×4 = 2.00 · founder research_spinout 4/4=1.00 ×3 = 3.00
  traction clinical_grant 1/4=0.25 ×2 = 0.50
  earned 8.75 / max 16  → FUND FIT 54.7%   → watchlist
```

Notice three things version 1 could not do: it routes to the **specific vehicle** whose mandate is satisfied and quotes the cheque range; it rejects Anticus on a **legal geography rule** rather than a low score; and it demotes a high-fit company because the fund has probably already seen it.

**Every number above is asserted in `test_worked_example_metzero`.**

---

## 12. Verification status

| Claim | Status |
|---|---|
| Fund stages, cheque sizes, geographies, vehicle names | **Verified** against each fund's own site, 7 Aug 2026 |
| ECF round/prior-fundraising caps | **Verified** — British Business Bank ECF Key Features |
| SEIS 3-year / EIS 7-year age limits, excluded trades | **Verified** — gov.uk, current as of the 6 Apr 2026 EIS changes |
| Northstar EIS Growth Fund cheque size | **UNVERIFIED** — not published anywhere. Left blank; do not guess. |
| Anticus cheque **floors** | **UNVERIFIED** — only maxima are published |
| DSW SEIS per-company floor | **UNVERIFIED** — only the £10k investor minimum is public |
| Whether Finance Yorkshire carries subsidy-control sector exclusions | **UNVERIFIED** — nothing published; do not assume |
| Anticus's Innovate UK Investor Partnerships listing (possible third vehicle) | **UNVERIFIED** |
| SIC → sector mapping (§2.1) | **Judgement, not fact.** Seeded values are a starting point; tune against real output in Phase 3. |
| Discovery Edge band boundaries | **Judgement.** Tune against Aryan's verdicts in Phase 9. |
