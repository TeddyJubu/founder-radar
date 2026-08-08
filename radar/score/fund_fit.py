"""Fund Fit — the weighted matrix, percentage-of-known, and coverage.

06-scoring §6. Two tables are multiplied together:

* `cfg.matrix_value(attr, value, fund)` — *how good is this value for this
  fund?*, 0-4, the client's own model preserved verbatim.
* `cfg.attribute_weight(attr, fund)` — *how much does this attribute matter?*,
  0-10, the smaller second table.

They are different functions over different tables and must not be confused.

The headline is a **percentage of the maximum achievable over known
attributes**, which is comparable across funds with different criteria counts
and across companies with different amounts of known information. That creates
one trap — a company with one known attribute scoring 1.0 gets 100% — and
`coverage` plus a coverage floor is the fix.
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
    max_achievable = sum(c.weight for c in known)  # denominator: KNOWN only
    max_all = sum(c.weight for c in components)

    pct = 100.0 * earned / max_achievable if max_achievable else 0.0

    # ponytail: 06-scoring §6 writes `coverage = max_ach / max_all`, but the
    # worked table in §2.6 gives 0.40 / 0.60 / 0.80 / 1.00 for two, three, four
    # and five known attributes, which is a *count*, not a weighted share — and
    # `test_one_known_attribute_cannot_shortlist` needs coverage < 0.5 for a
    # company whose two known attributes happen to carry half the weight. The
    # count reading is the one that satisfies both, so it is what is
    # implemented. It is also the more honest number: coverage answers "how
    # much did we find out?", not "how much of the weight did we find out?".
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
