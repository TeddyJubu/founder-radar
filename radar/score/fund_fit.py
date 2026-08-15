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

from dataclasses import dataclass
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from .criteria import ComponentScore, attribute_label, attribute_raw, attributes_for
from .derive import _get


class FitComponent(NamedTuple):
    """Lean component data shared by the pydantic and bulk score paths."""

    key: str
    label: str
    sub_score: float | None
    weight: float
    evidence: str
    raw: int | None

    @property
    def contribution(self) -> float | None:
        return self.weight * self.sub_score if self.sub_score is not None else None


@dataclass(frozen=True, slots=True)
class FundFitCalculation:
    """The complete plain-data Fund Fit calculation.

    Keeping this independent of Pydantic is important for `rescore --all`;
    both paths still get one arithmetic implementation without forcing the
    bulk path to construct daily-path models.
    """

    components: tuple[FitComponent, ...]
    pct: float
    coverage: float
    raw_sum: float
    earned: float
    max_achievable: float
    max_all: float


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


def calculate_fund_fit(
    company: Any,
    fund_key: str,
    config: Any,
    attributes: tuple[str, ...] | None = None,
) -> FundFitCalculation:
    """Calculate Fund Fit once, without constructing a Pydantic model.

    The daily evaluator wraps this result in `FitScore`; the bulk rescore
    adapts the same result to its existing plain tuples. Unknown source values
    remain absent from the evidence coverage count, while a configured
    pessimistic policy can still contribute zero to the fit denominator.
    """
    attributes = attributes if attributes is not None else attributes_for(config)
    components: list[FitComponent] = []
    for attribute in attributes:
        sub_score, weight, evidence, raw = attribute_raw(
            company, attribute, fund_key, config)
        components.append(FitComponent(
            key=attribute,
            label=attribute_label(attribute),
            sub_score=sub_score,
            weight=weight,
            evidence=evidence,
            raw=raw,
        ))
    components = tuple(components)

    known = tuple(component for component in components if component.sub_score is not None)
    earned = sum(component.weight * component.sub_score for component in known)
    max_achievable = sum(component.weight for component in known)
    max_all = sum(component.weight for component in components)
    pct = 100.0 * earned / max_all if max_all else 0.0

    # Coverage means confirmed source evidence, not merely a score produced by
    # an unknown-value policy such as `pessimistic` or `assume`.
    covered = sum(1 for component in components if _get(company, component.key) is not None)
    coverage = covered / len(attributes) if attributes else 0.0

    return FundFitCalculation(
        components=components,
        pct=round(pct, 1),
        coverage=round(coverage, 2),
        raw_sum=round(sum((component.raw or 0) for component in known), 1),
        earned=round(earned, 4),
        max_achievable=round(max_achievable, 4),
        max_all=round(max_all, 4),
    )


def fund_fit(company: Any, fund: Any, config: Any, vehicle: Any = None) -> FitScore:
    """06-scoring §6, verbatim, with the coverage correction from §2.6.

    Fit depends on the *fund*, not the vehicle: the vehicle decides eligibility
    (a hard gate), the fund's weight column decides preference within it.
    `vehicle_key` is carried through only so the score row can name the route.
    """
    fund_key = fund if isinstance(fund, str) else fund.key
    calculation = calculate_fund_fit(company, fund_key, config)
    components = [
        ComponentScore(
            key=component.key,
            label=component.label,
            sub_score=component.sub_score,
            weight=component.weight,
            evidence=component.evidence,
            raw=component.raw,
        )
        for component in calculation.components
    ]

    return FitScore(
        fund_key=fund_key,
        vehicle_key=(vehicle.vehicle_key if vehicle is not None else None),
        pct=calculation.pct,
        coverage=calculation.coverage,
        # The raw sum is still reported because the client is used to it; it is
        # not comparable across funds, which is why it is not the headline
        # (06-scoring §5.2).
        raw_sum=calculation.raw_sum,
        earned=calculation.earned,
        max_achievable=calculation.max_achievable,
        max_all=calculation.max_all,
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
