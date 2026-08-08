"""Freshness gates and per-vehicle hard rules.

06-scoring §1 and §4.5. Two policies do all the work here and they are the same
policy applied twice:

* **A gate whose input is `NULL` PASSES and sets a flag.** Any flag set means
  the company cannot reach `shortlist`. Rejecting on unknown would throw away
  good early candidates; shortlisting on unknown would let old companies back
  in through the gap. Watchlist with a stated reason is the honest answer.
* **A gate whose input is known and out of range REJECTS**, with the gate key
  as the reason, so the sheet can say which rule bit.

`test_freshness_gates` is the single most important test in the suite: it is
the client's exact complaint, encoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from radar.config.models import STAGES, Settings, canon_enum

from .derive import _get, _lists, age_months, is_outside_golden_triangle, outcode_of

# 06-scoring §1. A gate not listed here has no flag: an unknown stage or an
# unknown funding total is normal, not a warning.
UNKNOWN_FLAGS: dict[str, str | None] = {
    "max_company_age_months": "age_unknown",
    "min_uk_presence": "uk_unverified",
    "max_stage": None,
    "max_total_funding_gbp": None,
    "already_on_vc_portfolio": None,
}

GATE_UNVERIFIED = "gate_unverified"

# Geographies that count as UK presence for `min_uk_presence` and for a
# vehicle's `uk_wide` rule.
UK_GEOGRAPHIES = ("london", "uk_regions", "north_east", "yorkshire", "uk_wide")


@dataclass(frozen=True)
class GateResult:
    """`reason` is the gate key that rejected, or None. `flags` are the
    unknown-input warnings, which pass but block the shortlist."""

    passed: bool
    reason: str | None = None
    flags: tuple[str, ...] = ()
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.passed


@dataclass
class VehicleGateResult:
    passed: bool
    reason: str | None = None
    flags: list[str] = field(default_factory=list)
    unverified_rules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _settings(config_or_settings: Any) -> Settings:
    """Accept a `Config` or a bare `Settings` — the tests use both."""
    inner = getattr(config_or_settings, "settings", None)
    return inner if isinstance(inner, Settings) else config_or_settings


# ------------------------------------------------------------ universal gates


def apply_freshness_gates(
    company: Any, config_or_settings: Any, *, today: date | None = None
) -> GateResult:
    """Applied before any fund is considered. Every threshold lives in Settings.

    Order matters only for which key is reported when two gates would fail, and
    it follows the table in 06-scoring §1.
    """
    settings = _settings(config_or_settings)
    config = config_or_settings if hasattr(config_or_settings, "lists") else None
    flags: list[str] = []

    # --- age: the client's exact complaint -------------------------------
    age = age_months(company, today)
    if age is None:
        flags.append(UNKNOWN_FLAGS["max_company_age_months"])
    elif age > settings.max_company_age_months:
        return GateResult(False, "max_company_age_months", tuple(flags),
                          f"{age:.0f} months old")

    # --- funding: "many have already raised" -----------------------------
    funding = _get(company, "total_funding_gbp")
    if funding is not None and funding > settings.max_total_funding_gbp:
        return GateResult(False, "max_total_funding_gbp", tuple(flags),
                          f"£{funding:,.0f} raised")

    # --- stage ------------------------------------------------------------
    stage = canon_enum(_get(company, "stage"), STAGES)
    max_stage = canon_enum(settings.max_stage, STAGES) or "series_a"
    if stage is not None and STAGES.index(stage) > STAGES.index(max_stage):
        return GateResult(False, "max_stage", tuple(flags), f"stage {stage}")

    # --- already seen: the whole point of being a scout -------------------
    if _get(company, "on_vc_portfolio"):
        return GateResult(False, "already_on_vc_portfolio", tuple(flags),
                          "found on a tracked VC portfolio page")

    # --- UK ---------------------------------------------------------------
    uk = has_uk_presence(company, config)
    if uk is False:
        return GateResult(False, "min_uk_presence", tuple(flags), "not a UK company")
    if uk is None:
        flags.append(UNKNOWN_FLAGS["min_uk_presence"])

    return GateResult(True, None, tuple(flags))


def has_uk_presence(company: Any, config: Any = None) -> bool | None:
    """True / False / unknown. A non-GB country is the only hard no — every
    other piece of UK evidence is corroborating, never contradicting."""
    country = _get(company, "country_iso2")
    if country:
        return str(country).upper() == "GB"
    if _get(company, "hq_region") in UK_GEOGRAPHIES:
        return True
    if _get(company, "hq_postcode") or _get(company, "companies_house_no"):
        return True
    return None


# -------------------------------------------------------- per-vehicle gates


def evaluate_vehicle_gates(company: Any, vehicle: Any, config: Any = None) -> VehicleGateResult:
    """A vehicle's stage range, age cap, geography rule and hard rejects.

    06-scoring §4.6: the stage range **is a hard gate**. Stage is also a scored
    attribute, and that is fine — the gate decides eligibility, the score
    decides preference within it.
    """
    result = VehicleGateResult(passed=True)

    _stage_range_gate(company, vehicle, result)
    if not result.passed:
        return result

    _age_cap_gate(company, vehicle, config, result)
    if not result.passed:
        return result

    _geography_gate(company, vehicle, config, result)
    if not result.passed:
        return result

    for key, expected in (vehicle.hard_rejects or {}).items():
        _hard_reject(company, key, expected, config, result)
        if not result.passed:
            return result

    if result.unverified_rules:
        result.flags.append(GATE_UNVERIFIED)
    return result


def _unverified(result: VehicleGateResult, rule: str) -> None:
    if rule not in result.unverified_rules:
        result.unverified_rules.append(rule)


def _stage_range_gate(company: Any, vehicle: Any, result: VehicleGateResult) -> None:
    lo = canon_enum(vehicle.stage_min, STAGES)
    hi = canon_enum(vehicle.stage_max, STAGES)
    if lo is None and hi is None:
        return
    stage = canon_enum(_get(company, "stage"), STAGES)
    if stage is None:
        _unverified(result, "stage_range")
        return
    index = STAGES.index(stage)
    if lo is not None and index < STAGES.index(lo):
        result.passed, result.reason = False, "stage_range"
    elif hi is not None and index > STAGES.index(hi):
        result.passed, result.reason = False, "stage_range"


def _age_cap_gate(company: Any, vehicle: Any, config: Any, result: VehicleGateResult) -> None:
    if not vehicle.max_age_years:
        return
    age = age_months(company)
    if age is None:
        _unverified(result, "max_age_years")
        return
    if age > vehicle.max_age_years * 12:
        result.passed, result.reason = False, "max_age_years"


def _geography_gate(company: Any, vehicle: Any, config: Any, result: VehicleGateResult) -> None:
    """SOFT rules never reject and never flag — a soft preference is not a
    mandate, and pretending otherwise is how a Yorkshire company loses a fund
    it could genuinely have fitted (06-scoring §4.7)."""
    if vehicle.geo_rule != "HARD" or not vehicle.geo_values:
        return

    verdicts = [_geo_rule_verdict(company, rule, config) for rule in vehicle.geo_values]
    if any(v is True for v in verdicts):
        return
    if any(v is None for v in verdicts):
        _unverified(result, "geography")
        return
    result.passed, result.reason = False, f"geography:{','.join(vehicle.geo_values)}"


def _geo_rule_verdict(company: Any, rule: str, config: Any) -> bool | None:
    geography = _get(company, "hq_region")
    lists = _lists(config)

    if rule == "outside_golden_triangle":
        return is_outside_golden_triangle(company, config)

    if rule == "sunderland":
        city = (_get(company, "hq_city") or "").strip().lower()
        towns = [t.lower() for t in lists.get("sunderland_city_region", ["sunderland"])]
        prefixes = tuple(lists.get("sunderland_outcodes", ["SR"]))
        outcode = outcode_of(_get(company, "hq_postcode"))
        if city:
            return city in towns
        if outcode:
            return outcode.upper().startswith(prefixes)
        return None

    if rule == "north_england":
        northern = lists.get("north_england_geographies", ["north_east", "yorkshire"])
        if geography is None:
            return None
        return geography in northern or geography == "uk_wide"

    if rule == "uk_wide":
        return has_uk_presence(company, config)

    if rule == "uk_regions":
        if geography is None:
            return None
        return geography in ("uk_regions", "north_east", "yorkshire", "uk_wide")

    # A plain vocabulary region: north_east, yorkshire, london...
    if geography is None:
        return None
    if geography == "uk_wide":
        # UK confirmed but not resolved to a region — we cannot tell.
        return None
    return geography == rule


def _hard_reject(
    company: Any, key: str, expected: Any, config: Any, result: VehicleGateResult
) -> None:
    """The mini-language from 06-scoring §4.5.

    Every input is NULL unless a source supplied it, and **a NULL input passes
    the gate and sets `gate_unverified`**. That policy choice is deliberate:
    these mandates are real, but we usually cannot verify them from public
    data, and pretending otherwise in either direction — auto-rejecting or
    silently ignoring — would be worse than telling Aryan which box to tick.
    """
    if key == "round_max":
        _max_rule(company, "last_round_gbp", expected, key, result)
    elif key == "prior_total_max":
        _max_rule(company, "prior_total_gbp", expected, key, result)
    elif key == "valuation_max":
        _max_rule(company, "valuation_gbp", expected, key, result)
    elif key == "uk_exec_pct_min":
        value = _get(company, "uk_exec_pct")
        if value is None:
            _unverified(result, key)
        elif value < expected:
            result.passed, result.reason = False, key
    elif key == "university_spinout_required":
        _spinout_rule(company, expected, key, result)
    elif key == "beyond_research_stage":
        _beyond_research_rule(company, expected, key, config, result)
    elif key == "requires_seis_eis":
        _seis_eis_rule(company, expected, key, config, result)
    else:  # unknown keys warn and are ignored, never an error (07-interfaces tab 4)
        result.notes.append(f"unknown hard-reject key {key!r} ignored")


def _max_rule(company: Any, field_name: str, cap: Any, key: str, result: VehicleGateResult) -> None:
    value = _get(company, field_name)
    if value is None:
        _unverified(result, key)
    elif value > cap:
        result.passed, result.reason = False, key


def _spinout_rule(company: Any, expected: Any, key: str, result: VehicleGateResult) -> None:
    is_spinout = _get(company, "is_university_spinout")
    if is_spinout is None:
        _unverified(result, key)
        return
    if not is_spinout:
        result.passed, result.reason = False, key
        return
    universities = [u.lower() for u in expected] if isinstance(expected, list) else []
    university = (_get(company, "spinout_university") or "").strip().lower()
    if not universities:
        return
    if not university:
        _unverified(result, key)
        return
    if not any(university.startswith(u) or u in university for u in universities):
        result.passed, result.reason = False, key


def _beyond_research_rule(
    company: Any, expected: Any, key: str, config: Any, result: VehicleGateResult
) -> None:
    if not expected:
        return
    # ponytail: there is no `beyond_research_stage` column in `company`
    # (06-scoring §4.5 lists the seven fields and this is not one of them), so
    # it is read off `stage`: `idea` is still research, anything later is not,
    # and an unknown stage is unverifiable.
    research_stages = tuple(_lists(config).get("research_stages", ("idea",)))
    stage = canon_enum(_get(company, "stage"), STAGES)
    if stage is None:
        _unverified(result, key)
    elif stage in research_stages:
        result.passed, result.reason = False, key


def _seis_eis_rule(
    company: Any, expected: Any, key: str, config: Any, result: VehicleGateResult
) -> None:
    if not expected:
        return
    excluded = _lists(config).get("seis_eis_excluded_sectors", [])
    sector = _get(company, "sector")
    if sector is not None and sector in excluded:
        # An excluded trade is a *known* disqualification, not an unknown.
        result.passed, result.reason = False, "seis_eis_excluded_trade"
        return
    qualifying = _get(company, "seis_eis_qualifying")
    if qualifying is None:
        _unverified(result, key)
    elif not qualifying:
        result.passed, result.reason = False, key
