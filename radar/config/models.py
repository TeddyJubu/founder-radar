"""Pydantic models for everything the Google Sheet configures.

One model defines both the config schema and its validation, so drift between
"what the sheet says" and "what the code expects" is structurally impossible
(02-architecture §9). Nothing here has a hard-coded threshold: defaults live in
`radar.config.defaults` and are overridden by the Settings tab.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ------------------------------------------------------------- vocabularies
# 03-data-model §4. These are the *shapes*; the live values are read from the
# Lists tab at runtime, which is why they are plain tuples and not Enums.

STAGES: tuple[str, ...] = ("idea", "pre_seed", "seed", "series_a", "series_b_plus", "growth")

SECTORS: tuple[str, ...] = (
    "fintech", "insurtech", "wealthtech", "lending", "regtech", "b2b_saas",
    "vertical_saas", "ai_data", "climate_tech", "healthy_ageing", "life_sciences",
    "healthcare", "deeptech", "developer_tools", "consumer", "marketplace",
    "industrial_tech", "other",
)

GEOGRAPHIES: tuple[str, ...] = (
    "london", "uk_regions", "north_east", "yorkshire", "uk_wide",
    "europe_ex_uk", "global", "us", "other",
)

FOUNDER_SIGNALS: tuple[str, ...] = (
    "repeat_founder", "technical_founder", "domain_expert", "research_spinout",
    "operator_led", "student_founder", "generalist_unclear",
)

TRACTION_SIGNALS: tuple[str, ...] = (
    "pre_revenue_concept", "pilot_customers", "paying_customers",
    "rapid_usage_growth", "strong_revenue_growth", "enterprise_contracts",
    "clinical_grant_validation", "community_traction",
)

TIERS: tuple[str, ...] = ("shortlist", "watchlist", "reject")

VERDICTS: tuple[str, ...] = ("worth contacting", "not for me", "unsure")

SCORED_ATTRIBUTES: tuple[str, ...] = (
    "stage", "sector", "geography", "founder_signal", "traction_signal",
)

# Gate-only region rules (07-interfaces tab 4). Legal in `geo_values`, but NOT
# values the `geography` attribute can take.
GATE_ONLY_GEOS: tuple[str, ...] = ("sunderland", "north_england", "outside_golden_triangle")

# The hard-reject mini-language (07-interfaces tab 4). Unknown keys warn and are
# ignored — never an error.
HARD_REJECT_KEYS: tuple[str, ...] = (
    "round_max", "prior_total_max", "valuation_max", "uk_exec_pct_min",
    "university_spinout_required", "beyond_research_stage", "requires_seis_eis",
)

_SEP = re.compile(r"[\s\-]+")


def canon_enum(value: Any, allowed: tuple[str, ...]) -> str | None:
    """Case- and separator-insensitive enum matching (03-data-model §4).

    "Pre Seed", "pre-seed" and "PRE_SEED" all resolve to `pre_seed`. Returns
    None for no match so the caller can decide between warn and reject —
    unknown must never silently become a valid value.
    """
    if value is None:
        return None
    key = _SEP.sub("_", str(value).strip().lower())
    for candidate in allowed:
        if key == candidate:
            return candidate
    return None


def suggest_enum(value: Any, allowed: tuple[str, ...]) -> str | None:
    """Nearest legal value, for the 'did you mean?' half of an error message."""
    from rapidfuzz import fuzz, process

    if value is None:
        return None
    # token_sort_ratio only. token_set_ratio/partial_ratio/WRatio score a subset
    # at 100 and are banned repo-wide (02-architecture §9).
    match = process.extractOne(
        _SEP.sub("_", str(value).strip().lower()), allowed, scorer=fuzz.token_sort_ratio
    )
    return match[0] if match and match[1] >= 60 else None


# ------------------------------------------------------------------ settings


class Settings(BaseModel):
    """The Settings tab. Every threshold the client may want to change."""

    model_config = ConfigDict(extra="allow")

    max_company_age_months: int = Field(36, ge=1, le=120)
    max_total_funding_gbp: float = Field(3_000_000, ge=0)
    max_stage: str = "series_a"

    shortlist_fit: int = Field(70, ge=0, le=100)
    shortlist_edge: int = Field(55, ge=0, le=100)
    min_coverage: float = Field(0.50, ge=0, le=1)
    watchlist_fit: int = Field(45, ge=0, le=100)
    min_qualifiers: int = Field(1, ge=0, le=5)

    weight_fit: float = Field(0.60, ge=0, le=1)
    weight_edge: float = Field(0.40, ge=0, le=1)

    regions_enabled: list[str] = Field(default_factory=lambda: ["north_east", "yorkshire", "uk_wide"])

    ch_backfill_days: int = Field(90, ge=1)
    ch_daily_window_days: int = Field(10, ge=1)
    max_enrichment_requests_per_run: int = Field(500, ge=0)
    daily_digest_max: int = Field(10, ge=1)

    llm_model: str = "claude-haiku-4-5-20251001"
    llm_enabled: bool = True

    @field_validator("max_stage")
    @classmethod
    def _stage_is_known(cls, v: str) -> str:
        canon = canon_enum(v, STAGES)
        if canon is None:
            hint = suggest_enum(v, STAGES)
            raise ValueError(f"{v!r} is not a stage" + (f" — did you mean {hint!r}?" if hint else ""))
        return canon

    @field_validator("regions_enabled", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


# ------------------------------------------------------------------ vehicles


class Vehicle(BaseModel):
    """One row of the Fund Criteria tab — a specific pot with its own rules."""

    fund_key: str
    vehicle_key: str
    fund_name: str
    vehicle_name: str
    active: bool = True

    stage_min: str | None = None
    stage_max: str | None = None
    cheque_min: float | None = None
    cheque_max: float | None = None

    geo_rule: Literal["HARD", "SOFT"] = "HARD"
    geo_values: list[str] = Field(default_factory=list)
    max_age_years: int | None = None

    hard_rejects: dict[str, Any] = Field(default_factory=dict)
    sectors_plus: list[str] = Field(default_factory=list)
    sectors_minus: list[str] = Field(default_factory=list)
    one_liner: str = ""

    # Populated by the loader when a hard-reject key is not in HARD_REJECT_KEYS.
    warnings: list[str] = Field(default_factory=list)

    @field_validator("hard_rejects", mode="before")
    @classmethod
    def _parse_hard_rejects(cls, v: Any) -> Any:
        """`key:value` pairs separated by ' · '. Unknown keys are dropped, not fatal."""
        if not isinstance(v, str):
            return v or {}
        out: dict[str, Any] = {}
        for part in re.split(r"\s*·\s*|\s*;\s*", v):
            part = part.strip()
            if not part or ":" not in part:
                continue
            key, _, raw = part.partition(":")
            key = key.strip()
            raw = raw.strip()
            if key not in HARD_REJECT_KEYS:
                continue
            if raw.lower() in {"true", "false"}:
                out[key] = raw.lower() == "true"
            elif re.fullmatch(r"-?\d+(\.\d+)?", raw):
                out[key] = float(raw) if "." in raw else int(raw)
            else:
                out[key] = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return out

    @field_validator("geo_values", "sectors_plus", "sectors_minus", mode="before")
    @classmethod
    def _split_list(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [p.strip().lower().replace(" ", "_") for p in v.split(",") if p.strip()]
        return v or []


class Fund(BaseModel):
    key: str
    name: str
    vehicles: list[Vehicle] = Field(default_factory=list)

    @property
    def active_vehicles(self) -> list[Vehicle]:
        return [v for v in self.vehicles if v.active]


# ------------------------------------------------------------------- weights


class Weights(BaseModel):
    """The Scoring Weights tab — two different tables, read by two functions.

    `matrix` answers "how good is this value for this fund?" (0-4).
    `importance` answers "how much does this attribute matter?" (0-10).
    Confusing them is the easiest way to get every score wrong
    (07-interfaces tab 5).
    """

    # matrix[attribute][value][fund_key] -> 0..4
    matrix: dict[str, dict[str, dict[str, int]]] = Field(default_factory=dict)
    # importance[attribute][fund_key] -> 0..10
    importance: dict[str, dict[str, int]] = Field(default_factory=dict)
    # unknown_policy[attribute] -> neutral | pessimistic | assume
    unknown_policy: dict[str, str] = Field(default_factory=dict)

    def matrix_value(self, attribute: str, value: str | None, fund_key: str) -> int | None:
        """None means unknown — the caller must not turn that into 0."""
        if value is None:
            return None
        return self.matrix.get(attribute, {}).get(value, {}).get(fund_key)

    def attribute_weight(self, attribute: str, fund_key: str) -> int:
        """A blank cell means 1, not 0 (07-interfaces tab 5)."""
        return self.importance.get(attribute, {}).get(fund_key, 1)

    def policy(self, attribute: str) -> str:
        return self.unknown_policy.get(attribute, "neutral")


# -------------------------------------------------------------------- config


class SourceConfig(BaseModel):
    key: str
    track: Literal["A", "B"] = "A"
    enabled: bool = True
    note: str = ""


class Config(BaseModel):
    """The whole validated configuration, hashed for reproducibility."""

    settings: Settings = Field(default_factory=Settings)
    funds: list[Fund] = Field(default_factory=list)
    weights: Weights = Field(default_factory=Weights)
    sources: list[SourceConfig] = Field(default_factory=list)
    lists: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    # ---- lookups

    def fund(self, key: str) -> Fund | None:
        return next((f for f in self.funds if f.key == key), None)

    def vehicles(self, fund_key: str | None = None) -> list[Vehicle]:
        out: list[Vehicle] = []
        for f in self.funds:
            if fund_key and f.key != fund_key:
                continue
            out.extend(f.active_vehicles)
        return out

    def all_vehicles(self) -> list[Vehicle]:
        return [v for f in self.funds for v in f.vehicles]

    def matrix_value(self, attribute: str, value: str | None, fund_key: str) -> int | None:
        return self.weights.matrix_value(attribute, value, fund_key)

    def attribute_weight(self, attribute: str, fund_key: str) -> int:
        return self.weights.attribute_weight(attribute, fund_key)

    # ---- reproducibility

    def hash(self) -> str:
        """Stable hash over everything that can change a score.

        `errors` is excluded: a transient sheet typo must not silently
        re-partition the score table.
        """
        payload = self.model_dump(mode="json", exclude={"errors"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
