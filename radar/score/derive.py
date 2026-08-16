"""Attribute derivation — how a Companies House record becomes scoreable.

06-scoring §2. This module exists because of a real design trap: a register
record has an incorporation date, SIC codes, a postcode, officers and filings,
and **none of the five attributes we score on**. Without deterministic rules
that map free public evidence into the client's existing vocabulary, every
registry-sourced company would know one attribute out of five, fail the
coverage floor, and sit in watchlist forever — which would make the whole
registry-first idea decorative.

Nothing here touches the network, the database, or a model. Every lookup table
comes from `Config.lists`, seeded in `radar.config.defaults`.

`Company`, `Founder` and `Signal` are defined here rather than in a separate
module because derivation is the first thing in the system that needs the
shape, and the scoring path is a pure function of `(company, config)`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from radar.config.models import (
    FOUNDER_SIGNALS,
    GEOGRAPHIES,
    SECTORS,
    STAGES,
    TRACTION_SIGNALS,
    canon_enum,
)
from radar.resolve.normalise import outcode_of

# One month, averaged over the Gregorian cycle. Used everywhere an age is
# expressed in months so that "36 months" means the same number in the gate,
# in Discovery Edge and in the test factory.
DAYS_PER_MONTH = 30.4375


# --------------------------------------------------------------- the records


class Founder(BaseModel):
    """GDPR-minimal by construction — see `03-data-model.md` §2 and the
    schema's `founder` table. No address, no date of birth, no nationality."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    role: str | None = None
    profile_url: str | None = None
    is_psc: bool = False
    appointed_on: date | None = None
    prior_appointments: int | None = None


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    headline: str = ""
    detail: str | None = None
    occurred_on: date | None = None
    amount_gbp: float | None = None
    source_key: str = ""
    source_url: str = ""


class Company(BaseModel):
    """The scoring contract. Mirrors the `company` table in `schema.sql`.

    Every field that a source may not have supplied is `None`, and `None` is
    never `0`: unknown and known-zero are different facts end to end.
    """

    model_config = ConfigDict(extra="allow")

    id: str = ""
    canonical_name: str = ""
    norm_key: str = ""

    # Companies House numbers are STRINGS. `00445790` cast to int is a
    # different company, or none at all.
    companies_house_no: str | None = None
    domain: str | None = None
    website_url: str | None = None

    incorporated_on: date | None = None
    founded_year: int | None = None
    age_source: str | None = None

    hq_postcode: str | None = None
    hq_region: str | None = None
    hq_city: str | None = None
    country_iso2: str | None = None

    sector: str | None = None
    stage: str | None = None
    founder_signal: str | None = None
    traction_signal: str | None = None

    total_funding_gbp: float | None = None
    one_liner: str | None = None
    sic_codes: list[str] = Field(default_factory=list)

    has_share_issue: bool | None = None
    officer_count: int | None = None
    news_mention_count: int = 0
    on_vc_portfolio: bool = False
    discovery_route: str | None = None
    announced_round_stage: str | None = None

    # per-vehicle hard-rule inputs (06-scoring §4.5)
    is_university_spinout: bool | None = None
    spinout_university: str | None = None
    last_round_gbp: float | None = None
    prior_total_gbp: float | None = None
    valuation_gbp: float | None = None
    uk_exec_pct: float | None = None
    seis_eis_qualifying: bool | None = None

    qualified: bool = False
    qualifiers: list[str] = Field(default_factory=list)
    extraction_method: str | None = None

    founders: list[Founder] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)

    # ---- derived views

    @property
    def geography(self) -> str | None:
        """`hq_region` under the name the weight matrix uses (06-scoring §6)."""
        return self.hq_region

    @property
    def outcode(self) -> str | None:
        return outcode_of(self.hq_postcode)

    def age_months(self, today: date | None = None) -> float | None:
        return age_months(self, today)


# ------------------------------------------------------------------- helpers


def months_between(start: date, end: date) -> float:
    """Whole days converted to months. Deterministic and reversible, which is
    what the boundary cases in `test_freshness_gates` need."""
    return (end - start).days / DAYS_PER_MONTH


def age_months(company: Any, today: date | None = None) -> float | None:
    """06-scoring §1. Companies House first, a stated year second, then
    honestly unknown — which is **not** zero."""
    today = today or date.today()
    incorporated = _get(company, "incorporated_on")
    if incorporated:
        if isinstance(incorporated, str):
            incorporated = date.fromisoformat(incorporated[:10])
        return months_between(incorporated, today)
    founded_year = _get(company, "founded_year")
    if founded_year:
        return months_between(date(int(founded_year), 7, 1), today)
    return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a model, a dict or a `sqlite3.Row` alike."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    try:
        return obj[key]  # sqlite3.Row
    except (TypeError, IndexError, KeyError):
        pass
    return getattr(obj, key, default)


def _lists(config: Any) -> dict:
    if config is None:
        return {}
    return getattr(config, "lists", None) or {}


# -------------------------------------------------------- 2.1 sector ← SIC


def derive_sector(sic_codes: Sequence[str] | None, config: Any = None) -> str | None:
    """SIC codes are **self-declared by the founder or their formation agent,
    never audited, and often lazily generic** (06-scoring §2.1). Treated as a
    cheap high-recall signal, which is what the low confidence on the recorded
    observation encodes — not as ground truth.
    """
    if not sic_codes:
        return None
    table = _lists(config).get("sic_sector", {})
    exact: Mapping[str, str] = table.get("exact", {})
    prefix: Mapping[str, str] = table.get("prefix", {})

    for raw in sic_codes:
        code = str(raw).strip()
        if code in exact:
            return exact[code]
        for length in (3, 2):
            if code[:length] in prefix:
                return prefix[code[:length]]
    # A code we do not recognise is still a *known* declaration of "something
    # else", so `other` is a known value, not an unknown one.
    return "other"


# ---------------------------------------------------- 2.2 geography ← postcode


def derive_geography(region: str | None, country: str | None, outcode: str | None) -> str:
    """06-scoring §2.2, verbatim.

    postcodes.io returns arrays and populates `region` for **England only**;
    for Scotland, Wales and Northern Ireland it is empty and `country` wins.
    """
    if region == "London":
        return "london"
    if region == "North East":
        return "north_east"
    if region == "Yorkshire and The Humber":
        return "yorkshire"
    if country in ("Scotland", "Wales", "Northern Ireland"):
        return "uk_regions"
    if region:
        return "uk_regions"
    return "uk_wide"  # UK confirmed, location not resolved


def geography_from_outcode(outcode: str | None, config: Any = None) -> str | None:
    """Offline outcode → vocabulary lookup.

    The live path resolves postcodes through postcodes.io and caches them in
    `postcode_region` (Phase 3). Scoring must stay a pure function, so it reads
    the seeded outcode-prefix map from `Config.lists` instead. A prefix that is
    not in the map is a UK postcode we simply have not classified, which is
    `uk_wide` — not unknown.
    """
    if not outcode:
        return None
    table = _lists(config).get("outcode_region", {})
    # The postcode *area* is the leading alphabetic run and nothing else:
    # "EC2A" is area EC, "S75" is area S. Falling back to shorter prefixes
    # would read "EH1" (Edinburgh) as area E (London).
    area = re.match(r"^[A-Z]+", outcode.upper())
    if area and area.group(0) in table:
        return table[area.group(0)]
    return "uk_wide"


def is_outside_golden_triangle(company: Any, config: Any = None) -> bool | None:
    """DSW's SEIS rule is an **outcode-prefix check, not a fuzzy city match**
    (06-scoring §2.2). Returns None when we cannot tell."""
    prefixes = tuple(_lists(config).get("golden_triangle_outcodes", ("OX", "CB")))
    geography = _get(company, "hq_region")
    outcode = outcode_of(_get(company, "hq_postcode"))
    if outcode and outcode.upper().startswith(prefixes):
        return False
    if geography == "london":
        return False
    if outcode or (geography and geography != "uk_wide"):
        return True
    return None


# ------------------------------------------------- 2.3 stage ← filings & news


def derive_stage(company: Any, config: Any = None, today: date | None = None) -> str | None:
    """06-scoring §2.3.

    This is what makes the SH01 signal earn its place: a return of allotment
    of shares filed on a company incorporated eight months ago is, in practice,
    a pre-seed round being papered. It hits the public register within days,
    it is free, and no portfolio-page scraper will ever see it.
    """
    announced = _get(company, "announced_round_stage")
    if announced:
        return canon_enum(announced, STAGES) or announced

    bands = _lists(config).get("stage_derivation", {})
    pre_seed_max = bands.get("share_issue_pre_seed_max_months", 24)
    idea_max = bands.get("idea_max_months", 12)

    has_share_issue = _get(company, "has_share_issue")
    age = age_months(company, today)

    if has_share_issue and age is not None and age <= pre_seed_max:
        return "pre_seed"
    if has_share_issue:
        return "seed"
    if age is not None and age <= idea_max and not has_share_issue:
        return "idea"
    return None


# --------------------------------------------- 2.4 founder signal ← officers


def derive_founder_signal(
    company: Any, founders: Iterable[Founder] | None = None, config: Any = None
) -> str | None:
    """06-scoring §2.4 — evaluated top-down, first match wins.

    `generalist_unclear` is a **known** value that scores 0, not an unknown:
    it means "we looked and found no standout signal", which is a real finding
    and belongs in the coverage denominator.
    """
    founders = list(founders if founders is not None else (_get(company, "founders") or []))
    technical_sectors = tuple(
        _lists(config).get("technical_founder_sectors", ("deeptech", "life_sciences"))
    )

    if _get(company, "is_university_spinout"):
        return "research_spinout"
    if any((_get(f, "prior_appointments") or 0) >= 1 for f in founders):
        return "repeat_founder"
    if _get(company, "sector") in technical_sectors and any(_get(f, "is_psc") for f in founders):
        return "technical_founder"
    if founders:
        return "generalist_unclear"
    return None


# --------------------------------------------- 2.5 traction signal ← evidence


def derive_traction_signal(
    company: Any, signals: Iterable[Signal] | None = None, config: Any = None
) -> str | None:
    """06-scoring §2.5.

    Traction is the attribute the register genuinely cannot tell us about.
    Leaving it None for most registry companies is correct — that is what
    `coverage` is for.
    """
    signals = list(signals if signals is not None else (_get(company, "signals") or []))
    mapping: Mapping[str, str] = _lists(config).get(
        "signal_traction",
        {"grant_award": "clinical_grant_validation", "competition_win": "community_traction"},
    )
    for kind, traction in mapping.items():
        if any(_get(s, "kind") == kind for s in signals):
            return traction

    early = tuple(_lists(config).get("pre_revenue_stages", ("pre_seed", "idea")))
    if _get(company, "stage") in early and _get(company, "has_share_issue") is False:
        return "pre_revenue_concept"
    return None  # honestly unknown


# ------------------------------------------------------------- the front door


class Derivation(BaseModel):
    """What was derived and from what — one row per attribute, so the sheet can
    always answer "where did this sector come from?" with "derived from SIC
    72190" (06-scoring §2.6)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: str
    rule: str
    evidence: str
    source_type: str = "derived"
    confidence: float = 0.6


class _Overlay:
    """A read-only view: base values with `updates` winning. Both `_get` and
    attribute access read it, so `derive_updates` can progressively feed derived
    fields back into the later derivation steps without pydantic copies."""

    __slots__ = ("_base", "_updates")

    def __init__(self, base: Any, updates: Mapping[str, Any]):
        self._base = base
        self._updates = updates

    def __getitem__(self, key: str):
        if key in self._updates:
            return self._updates[key]
        try:
            return self._base[key]
        except (TypeError, IndexError, KeyError):
            return getattr(self._base, key)

    def __getattr__(self, key: str):
        if key in self._updates:
            return self._updates[key]
        return getattr(self._base, key)


def _view(base: Any, updates: Mapping[str, Any]) -> Any:
    if isinstance(base, Mapping):
        return {**base, **updates}
    return _Overlay(base, updates)


def derive_updates(
    company: Any, config: Any = None, *, today: date | None = None
) -> tuple[dict[str, Any], list[tuple[str, str, str, str]]]:
    """The derivation decisions as plain data, in the exact order of
    `derive_attributes` — the single source both paths call.

    Returns `(updates, trace)` where each trace entry is
    `(field, value, rule, evidence)`; `derive_attributes` wraps the trace in
    `Derivation` models, the lean rescore (`pipeline.rescore_all`) applies
    `updates` to its dict and drops the trace. The order matters and is
    mirrored here verbatim: geography → sector → stage → founder → traction,
    because `sector` feeds founder and `stage` feeds traction (06-scoring §2).
    """
    updates: dict[str, Any] = {}
    trace: list[tuple[str, str, str, str]] = []

    # --- geography first: nothing reads it, but it is the cheapest to fill
    geography = _get(company, "hq_region")
    if geography is None:
        outcode = outcode_of(_get(company, "hq_postcode"))
        if outcode:
            geography = geography_from_outcode(outcode, config)
            if geography:
                trace.append(("geography", geography, "outcode_region", f"outcode {outcode}"))
        elif _get(company, "country_iso2") == "GB":
            geography = "uk_wide"
            trace.append(("geography", geography, "country_gb", "country GB"))
        if geography:
            updates["hq_region"] = geography

    sector = _get(company, "sector")
    if sector is None:
        sic_codes = _get(company, "sic_codes")
        if sic_codes:
            sector = derive_sector(sic_codes, config)
            if sector:
                updates["sector"] = sector
                trace.append(("sector", sector, "sic_sector", f"SIC {sic_codes[0]}"))

    working = _view(company, updates)

    stage = _get(company, "stage")
    if stage is None:
        stage = derive_stage(working, config, today)
        if stage:
            updates["stage"] = stage
            evidence = ("SH01 filed" if _get(working, "has_share_issue")
                        else "no share issue on a young company")
            trace.append(("stage", stage, "derive_stage", evidence))

    working = _view(company, updates)

    founder_signal = _get(company, "founder_signal")
    if founder_signal is None:
        founder_signal = derive_founder_signal(working, _get(working, "founders") or [], config)
        if founder_signal:
            updates["founder_signal"] = founder_signal
            trace.append(("founder_signal", founder_signal, "derive_founder_signal",
                          _founder_evidence(working)))

    working = _view(company, updates)

    traction_signal = _get(company, "traction_signal")
    if traction_signal is None:
        traction_signal = derive_traction_signal(working, _get(working, "signals") or [], config)
        if traction_signal:
            updates["traction_signal"] = traction_signal
            trace.append(("traction_signal", traction_signal, "derive_traction_signal",
                          _traction_evidence(working)))

    return updates, trace


def derive_attributes(
    company: Company, config: Any = None, *, today: date | None = None
) -> Company:
    """Fill in whatever the register can tell us, and nothing more.

    A value already stated by a source always wins: `SOURCE_TRUST` ranks
    `news` (40) and `company_site` (70) above a derivation, which is recorded
    as `source_type = "derived"` with trust 30 and confidence 0.6
    (06-scoring §2.1).

    Returns a **new** Company. Derivation never mutates its input, so scoring
    the same record twice cannot drift. The decision logic lives in
    `derive_updates` (plain data, shared with the bulk rescore); this wraps it
    in the pydantic model and the `Derivation` trace.
    """
    updates, trace = derive_updates(company, config, today=today)
    derivations = [
        Derivation(field=field, value=value, rule=rule, evidence=evidence)
        for field, value, rule, evidence in trace
    ]
    return company.model_copy(update={**updates, "derivations": derivations})


def _founder_evidence(company: Any) -> str:
    if _get(company, "is_university_spinout"):
        return f"university spinout ({_get(company, 'spinout_university') or 'university unnamed'})"
    if any((_get(f, "prior_appointments") or 0) >= 1 for f in (_get(company, "founders") or [])):
        return "officer with a prior UK directorship"
    if any(_get(f, "is_psc") for f in (_get(company, "founders") or [])):
        return "PSC officer in a technical sector"
    return "officers found, no standout signal"


def _traction_evidence(company: Any) -> str:
    kinds = {_get(s, "kind") for s in (_get(company, "signals") or [])}
    if "grant_award" in kinds:
        return "grant award matched"
    if "competition_win" in kinds:
        return "competition win matched"
    return "early stage, no share issue"


def validate_derived(company: Company) -> list[str]:
    """Cheap guard: derivation must only ever emit vocabulary values."""
    problems: list[str] = []
    for field, allowed in (
        ("sector", SECTORS), ("stage", STAGES), ("hq_region", GEOGRAPHIES),
        ("founder_signal", FOUNDER_SIGNALS), ("traction_signal", TRACTION_SIGNALS),
    ):
        value = getattr(company, field)
        if value is not None and canon_enum(value, allowed) is None:
            problems.append(f"{field}={value!r} is not in the vocabulary")
    return problems
