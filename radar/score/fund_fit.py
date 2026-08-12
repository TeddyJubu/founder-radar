"""Fund Fit — the weighted matrix, evidence-aware percentage, and coverage.

06-scoring §6. Two tables are multiplied together:

* `cfg.matrix_value(attr, value, fund)` — *how good is this value for this
  fund?*, 0-4, the client's own model preserved verbatim.
* `cfg.attribute_weight(attr, fund)` — *how much does this attribute matter?*,
  0-10, the smaller second table.

They are different functions over different tables and must not be confused.

The headline is a **percentage of the configured maximum across all
attributes**. Only confirmed attributes earn points; unknown attributes stay
unknown in their component record but remain in the headline denominator. That
means a sparse two-criterion match cannot look like a perfect match. The
separate `coverage` value reports how many attributes were confirmed, and the
coverage floor still controls shortlist eligibility.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .criteria import ComponentScore, attribute_component, attributes_for
from .derive import _get


class FitScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_key: str
    vehicle_key: str | None = None
    pct: float = 0.0
    coverage: float = 0.0
    raw_sum: float = 0.0
    earned: float = 0.0
    max_achievable: float = 0.0
    max_all: float = 0.0
    components: list[ComponentScore] = Field(default_factory=list)

    @property
    def known(self) -> list[ComponentScore]:
        return [c for c in self.components if c.sub_score is not None]


def fund_fit(company: Any, fund: Any, config: Any, vehicle: Any = None) -> FitScore:
    """06-scoring §6, verbatim, with the coverage correction from §2.6.

    Fit depends on the *fund*, not the vehicle: the vehicle decides eligibility
    (a hard gate), the fund's weight column decides preference within it.
    `vehicle_key` is carried through only so the score row can name the route.
    """
    fund_key = fund if isinstance(fund, str) else fund.key
    attributes = attributes_for(config)

    components = [attribute_component(company, attr, fund_key, config) for attr in attributes]

    known = [c for c in components if c.sub_score is not None]
    earned = sum(c.weight * c.sub_score for c in known)
    # Keep both maxima: `max_achievable` describes the evidence available for
    # dominance diagnostics, while `max_all` is the stable headline baseline.
    max_all = sum(c.weight for c in components)
    max_achievable = sum(c.weight for c in known)

    pct = 100.0 * earned / max_all if max_all else 0.0

    # Coverage is intentionally a plain attribute count, independent of the
    # weighted headline: it answers "how much did we find out?" rather than
    # "how much of the weight did we find out?".
    covered = sum(1 for attr in attributes if _get(company, attr) is not None)
    coverage = covered / len(attributes) if attributes else 0.0

    return FitScore(
        fund_key=fund_key,
        vehicle_key=(vehicle.vehicle_key if vehicle is not None else None),
        pct=round(pct, 1),
        coverage=round(coverage, 2),
        # The raw sum is still reported because the client is used to it; it is
        # not comparable across funds, which is why it is not the headline
        # (06-scoring §5.2).
        raw_sum=round(sum((c.raw or 0) for c in known), 1),
        earned=round(earned, 4),
        max_achievable=round(max_achievable, 4),
        max_all=round(max_all, 4),
        components=components,
    )


def effective_share(component: ComponentScore, fit: FitScore) -> float:
    """Single-attribute dominance, measured on the **effective** share
    (weight ÷ max_achievable), not the configured share — for a company with
    two known attributes the effective share is 50% each (06-scoring §10)."""
    if not fit.max_achievable:
        return 0.0
    return component.weight / fit.max_achievable


def dominant_components(fit: FitScore, threshold: float = 0.5) -> list[str]:
    """Config-status warning: any attribute above ~50% effective share."""
    return [c.key for c in fit.known if effective_share(c, fit) > threshold]
