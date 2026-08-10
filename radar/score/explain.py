"""The explanation — deterministic, template-written, no AI.

06-scoring §9. Aryan asked on 9 July for the system to *"explain why it
surfaced a startup, rather than only giving it a score"*.

Why a template beats a model here:

* **free, instant, byte-identical for identical inputs**, so it can be
  asserted in a plain string test;
* **it cannot hallucinate** — every clause comes from a computed number;
* **the totals reconcile.** The `— X of Y total` clause shows the top-three
  contribution *and* the headline together, so the two can never appear to
  contradict. `test_explanation_arithmetic_reconciles` asserts against that
  clause, not against a loose tolerance.

That last property matters because Aryan will be showing these to real
investors.
"""

from __future__ import annotations

from typing import Any, Sequence

from .criteria import ComponentScore
from .derive import _get
from .fund_fit import FitScore

POSITIVE_AT = 0.6
NEGATIVE_AT = 0.34
LOW_VISIBILITY_EDGE = 70
COVERAGE_NOTE_BELOW = 0.5


def format_cheque(vehicle: Any) -> str:
    """`£200k–£750k`. Northstar's EIS Growth Fund cheque size is not published
    anywhere, so it is left blank and said so — never guessed."""
    if vehicle is None:
        return ""
    low, high = vehicle.cheque_min, vehicle.cheque_max
    if low is None and high is None:
        return "cheque size unpublished"
    if low is None:
        return f"up to {_money(high)}"
    if high is None:
        return f"from {_money(low)}"
    return f"{_money(low)}–{_money(high)}"


def _money(amount: float) -> str:
    if amount >= 1_000_000:
        value = amount / 1_000_000
        return f"£{value:.1f}m".replace(".0m", "m")
    if amount >= 1_000:
        return f"£{amount / 1_000:.0f}k"
    return f"£{amount:,.0f}"


def explain(
    fit: FitScore,
    edge: float,
    signals: Sequence[Any] = (),
    vehicle: Any = None,
    flags: Sequence[str] = (),
    *,
    tier_reason: str = "",
    reject_reason: str | None = None,
) -> str:
    """06-scoring §9, with two additions the tests require: the tiering reason
    (§8 says it is appended to the explanation) and the gate reject reason."""
    known = [c for c in fit.components if c.sub_score is not None]
    total_weight = sum(c.weight for c in known) or 1.0

    def pts(component: ComponentScore) -> float:
        return 100 * component.weight * component.sub_score / total_weight

    positives = sorted((c for c in known if c.sub_score >= POSITIVE_AT), key=pts, reverse=True)[:3]
    negatives = sorted(
        (c for c in known if c.sub_score <= NEGATIVE_AT),
        key=lambda c: c.weight * (1 - c.sub_score),
        reverse=True,
    )[:2]
    unknowns = sorted(
        (c for c in fit.components if c.sub_score is None), key=lambda c: c.weight, reverse=True
    )[:2]

    parts: list[str] = []

    if signals:
        parts.append("Found via " + "; ".join(_signal_phrase(s) for s in signals[:2]))

    if positives:
        parts.append(
            "Matches on "
            + "; ".join(f"{c.label.lower()} ({c.evidence}, +{pts(c):.0f}pts)" for c in positives)
            + f" — {sum(pts(c) for c in positives):.0f} of {fit.pct:.0f} total"
        )

    if negatives:
        parts.append("Against: " + "; ".join(f"{c.label.lower()} ({c.evidence})" for c in negatives))

    if unknowns:
        parts.append("Unknown: " + ", ".join(c.label.lower() for c in unknowns))

    if vehicle is not None:
        cheque = format_cheque(vehicle)
        parts.append(
            f"Route to {vehicle.vehicle_name}" + (f" ({cheque})" if cheque else "")
        )

    if edge >= LOW_VISIBILITY_EDGE:
        parts.append("Low visibility — no coverage found in our tracked sources")

    if fit.coverage < COVERAGE_NOTE_BELOW:
        parts.append(f"Only {fit.coverage:.0%} of criteria could be assessed")

    if reject_reason:
        parts.append(f"Rejected: {reject_reason.replace('_', ' ')}")

    if tier_reason:
        parts.append(tier_reason)

    # One clause, and only for caveats the sentence has not already made.
    #
    # This used to emit `⚠ age unknown. ⚠ gate unverified. ⚠ uk unverified.`
    # as three sentences, directly after a tier reason that had already said
    # "age unknown — verify before sending". Aryan read one problem three
    # times, and the repetition made a routine unknown look like an alarm.
    if flags:
        said = " ".join(parts).lower()
        unsaid = [f.replace("_", " ") for f in flags
                  if f.replace("_", " ").lower() not in said]
        if unsaid:
            parts.append("⚠ " + ", ".join(unsaid))

    return ". ".join(parts) + "." if parts else ""


def _signal_phrase(signal: Any) -> str:
    """`headline (2026-07-30)`, or just the headline when the date is unknown.

    A signal with no `occurred_on` was rendering literally as
    `Found via Acme Robotics (None)`. `None` is not a date, and printing it
    tells the reader the system is broken rather than that a fact is missing.
    """
    headline = _get(signal, "headline") or ""
    when = _get(signal, "occurred_on")
    return f"{headline} ({when})" if when else headline


def eligibility_note(unverified_rules: Sequence[str]) -> str:
    """06-scoring §4.5: a company with `gate_unverified` reaches watchlist with
    the reason *"eligibility unconfirmed — check <rule> manually"*."""
    if not unverified_rules:
        return ""
    rules = ", ".join(r.replace("_", " ") for r in unverified_rules)
    return f"eligibility unconfirmed — check {rules} manually"
