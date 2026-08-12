"""The scored types and the attribute evaluator.

One `ComponentScore` type is used by Fund Fit and by Discovery Edge, with the
same normalisation and the same explanation generator, so the arithmetic a
sceptical investor checks is the same arithmetic in both halves of the score.

The single invariant this module exists to protect: **`sub_score is None` means
unknown, and unknown is never `0`.** There is a unit test for it; do not "fix"
it.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from radar.config.models import SCORED_ATTRIBUTES

from .derive import _get, _lists

SCORER_VERSION = "2.0.0"

# The matrix is 0-4; every sub-score in the system is 0-1.
MATRIX_MAX = 4.0

UNKNOWN_EVIDENCE = "unknown"

_ATTRIBUTE_LABELS = {
    "stage": "Stage",
    "sector": "Sector",
    "geography": "Geography",
    "founder_signal": "Founder signal",
    "traction_signal": "Traction signal",
}


class Criterion(BaseModel):
    """One scored attribute for one fund: what it is worth and what it means."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    weight: float
    unknown_policy: str = "neutral"


class ComponentScore(BaseModel):
    """One line of the arithmetic. Mirrors the `score_component` table."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    sub_score: float | None = None  # NULL = unknown. Never coerce to 0.
    weight: float = 0.0
    evidence: str = UNKNOWN_EVIDENCE
    raw: int | None = None  # the 0-4 matrix cell, for the cell note

    @property
    def known(self) -> bool:
        return self.sub_score is not None

    @property
    def contribution(self) -> float | None:
        """weight × sub_score, or None. Written to `score_component`."""
        if self.sub_score is None:
            return None
        return self.weight * self.sub_score


class Score(BaseModel):
    """One company against one fund, routed to one vehicle."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = ""
    company_name: str = ""
    fund_key: str = ""
    vehicle_key: str | None = None
    vehicle_name: str | None = None

    fund_fit_pct: float = 0.0
    raw_sum: float = 0.0  # the client's original column (06-scoring §5.2)
    coverage: float = 0.0
    discovery_edge: float = 0.0
    priority: float = 0.0

    tier: str = "reject"
    reject_reason: str | None = None
    explanation: str = ""
    flags: list[str] = Field(default_factory=list)

    components: list[ComponentScore] = Field(default_factory=list)
    edge_components: list[ComponentScore] = Field(default_factory=list)

    config_hash: str = ""
    scorer_version: str = SCORER_VERSION


# ------------------------------------------------------------------- labels


def attribute_label(attribute: str) -> str:
    return _ATTRIBUTE_LABELS.get(attribute, attribute.replace("_", " ").capitalize())


def label_of(value: str | None, config: Any = None) -> str:
    """Vocabulary value → the words Aryan reads in the explanation."""
    if value is None:
        return UNKNOWN_EVIDENCE
    overrides: Mapping[str, str] = _lists(config).get("value_labels", {})
    if value in overrides:
        return overrides[value]
    return str(value).replace("_", " ").title()


# --------------------------------------------------------------- attributes


def attributes_for(config: Any) -> tuple[str, ...]:
    """Which attributes are scored.

    Read from config rather than hard-coded so that adding a criterion is a
    sheet edit — and so the fit denominator regression test can prove that
    adding one does not inflate every existing score.
    """
    configured = _lists(config).get("scored_attributes")
    return tuple(configured) if configured else tuple(SCORED_ATTRIBUTES)


def attribute_raw(
    company: Any, attribute: str, fund_key: str, config: Any
) -> tuple[float | None, float, str, int | None]:
    """06-scoring §6, one attribute, as plain data.

    Returns `(sub_score, weight, evidence, raw)`. This is the single source of
    per-attribute arithmetic; `calculate_fund_fit` aggregates these values once
    for both the pydantic daily path and the lean bulk rescore
    (`pipeline.rescore_all`), so the two paths cannot drift (09-test-plan §8).

    A known value that is missing from the matrix scores 0, not unknown: a
    blank cell in the *matrix* means "worth nothing to this fund", which is a
    different statement from a blank cell in the *importance* table, where a
    blank means 1. Confusing the two is the easiest way to get every score
    wrong (06-scoring §5.1). The three unknown policies are §6:

    | policy       | numerator     | denominator |
    | neutral      | excluded      | excluded    |
    | pessimistic  | 0             | included    |
    | assume       | assumed value | included    |
    """
    weight = float(config.attribute_weight(attribute, fund_key))
    value = _get(company, attribute)

    if value is None:
        policy = config.weights.policy(attribute)
        if policy == "pessimistic":
            return 0.0, weight, "unknown (counted against)", None
        if policy == "assume":
            assumed = _lists(config).get("assume_values", {}).get(attribute)
            if assumed is not None:
                raw = config.matrix_value(attribute, assumed, fund_key)
                raw = 0 if raw is None else int(raw)
                return raw / MATRIX_MAX, weight, f"assumed {label_of(assumed, config)}", raw
        return None, weight, UNKNOWN_EVIDENCE, None

    raw = config.matrix_value(attribute, value, fund_key)
    raw = 0 if raw is None else int(raw)
    return raw / MATRIX_MAX, weight, label_of(value, config), raw


def attribute_component(
    company: Any, attribute: str, fund_key: str, config: Any
) -> ComponentScore:
    """`attribute_raw` wrapped in the pydantic model the daily path persists."""
    sub_score, weight, evidence, raw = attribute_raw(company, attribute, fund_key, config)
    return ComponentScore(
        key=attribute,
        label=attribute_label(attribute),
        sub_score=sub_score,
        weight=weight,
        evidence=evidence,
        raw=raw,
    )


# ------------------------------------------------------------------- banding


def band_score(value: float | int | None, bands: list, unknown: float) -> float:
    """`bands` is `[[upper_inclusive, sub_score], ...]`, evaluated in order.

    Anything above the last band scores 0. An unknown input scores whatever the
    config says — 0.5 for age and funding, because collapsing NULL into "known
    zero" would violate the one invariant this whole system holds to.
    """
    if value is None:
        return unknown
    for upper, sub in bands:
        if value <= upper:
            return float(sub)
    return 0.0
