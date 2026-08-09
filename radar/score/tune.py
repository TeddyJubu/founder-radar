"""Threshold tuning against Aryan's own verdicts (06-scoring §8).

The `Verdict` column is labelled training data: every company Aryan marks
"worth contacting" or "not for me" is a data point the thresholds can be
measured against. `sweep()` runs the shortlist-fit threshold across a range
and reports, for each value, how many companies it would shortlist and the
precision / recall / F1 against his labels.

Aryan reads the output table, not a scatter plot: "at 70 you'd get 23
companies and like 78% of them." The same sweep is also available over the
attribute-importance weights (`--attribute`), perturbing one at a time and
reporting the F1 change, which tells him which attributes are doing work and
which are decorative.

Pure database read plus arithmetic — no network, no AI, nothing is written
here. The CLI renders the result; the sheet's Tuning tab is a view.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

WORTH_CONTACTING = "worth contacting"
NOT_FOR_ME = "not for me"
UNSURE = "unsure"

DEFAULT_EDGE_GRID = (40, 45, 50, 55, 60, 65, 70)

# A sweep is only worth a row if it would give a different answer. 30 is the
# most rows worth putting in front of a person; past that the table stops
# being read.
MAX_SWEEP_ROWS = 30


# ------------------------------------------------------------------ labels


def labelled_companies(db: Any) -> dict[str, str]:
    """`{company_id: verdict}` from Aryan's own column.

    Only the two decisive labels count; `unsure` is a label but not a signal
    in either direction.
    """
    out: dict[str, str] = {}
    for row in db.query(
        "SELECT company_id, value FROM user_field WHERE field = 'verdict' AND value != ''"
    ):
        verdict = str(row["value"]).strip().lower()
        if verdict in (WORTH_CONTACTING, NOT_FOR_ME):
            out[row["company_id"]] = verdict
    return out


# ---------------------------------------------------------------- the sweep


@dataclass
class SweepRow:
    """One threshold value and what it would have bought."""

    threshold: int
    would_shortlist: int
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "would_shortlist": self.would_shortlist,
            "precision": None if self.precision is None else round(self.precision, 2),
            "recall": None if self.recall is None else round(self.recall, 2),
            "f1": None if self.f1 is None else round(self.f1, 2),
        }


def _f1(precision: float | None, recall: float | None) -> float | None:
    if not precision or not recall:
        return None
    return 2 * precision * recall / (precision + recall)


def _score_rows(db: Any, *, fund_key: str | None = None) -> list[dict]:
    """The best (highest-priority) score row per company, optionally per fund."""
    where = ""
    params: list[Any] = []
    if fund_key:
        where = "WHERE s.fund_key = ?"
        params.append(fund_key)
    rows = db.query(
        f"""SELECT s.company_id AS company_id, s.priority AS priority,
                   s.fund_fit_pct AS fund_fit_pct, s.tier AS tier
              FROM score s
              JOIN (SELECT company_id, MAX(priority) AS best
                      FROM score {where}
                     GROUP BY company_id) best
                ON best.company_id = s.company_id AND best.best = s.priority
             ORDER BY s.company_id""",
        params,
    )
    return [dict(r) for r in rows]


def change_points(rows: Iterable[Mapping[str, Any]], *,
                  limit: int = MAX_SWEEP_ROWS) -> list[int]:
    """The integer thresholds at which the shortlist actually changes.

    `shortlist_fit` is an `int`, so there are only 101 thresholds to choose
    between and most of them are indistinguishable. A company scoring 86.8 is
    shortlisted at every threshold up to 86 and at none above it, so
    `floor(fit)` is the last threshold that keeps it — and the set of those
    floors is exactly the set of thresholds that give different answers. It is
    the complete set, not a sample: there is no threshold between two adjacent
    floors that produces a shortlist either of them doesn't.

    On the live database this returns seven values spanning 0 to 100. The
    fixed grid it replaces spent three of its seven rows on 60/65 and 80/85 —
    pairwise identical shortlists — and never showed the cliff at 91, where
    the shortlist drops from several hundred companies to a few dozen. A row
    that cannot change the outcome is a decision dressed up as a choice.

    Above `limit` distinct floors the range is sampled evenly, keeping both
    ends. That is a real loss of resolution, so `sweep` reports it.
    """
    floors = _floors(rows)
    if len(floors) <= limit:
        return floors
    step = (len(floors) - 1) / (limit - 1)
    return sorted({floors[round(index * step)] for index in range(limit)})


def _floors(rows: Iterable[Mapping[str, Any]]) -> list[int]:
    """Every distinct `floor(fund_fit_pct)`, clamped to a settable threshold."""
    return sorted({
        max(0, min(100, math.floor(float(r["fund_fit_pct"]))))
        for r in rows if r.get("fund_fit_pct") is not None
    })


def sweep(
    db: Any,
    *,
    fit_grid: Sequence[int] | None = None,
    fund_key: str | None = None,
) -> dict[str, Any]:
    """Sweep the shortlist-fit threshold and score each value against verdicts.

    A company counts as "would shortlist" if its best score's `fund_fit_pct`
    is at or above the threshold and it was not gated (tier is not reject for
    a gate reason). Labels come from `user_field`.

    With no `fit_grid` the thresholds are derived from the data — every value
    at which the shortlist actually changes, and nothing else (`change_points`).
    Pass an explicit grid to ask about particular numbers.
    """
    labels = labelled_companies(db)
    rows = _score_rows(db, fund_key=fund_key)
    derived = fit_grid is None
    # Change points must be computed over the rows the sweep actually counts.
    # A gated company's fit still has a floor, but moving the threshold across
    # it changes nothing — those produced three rows reading 792 in a row.
    eligible = [r for r in rows if r["tier"] != "reject"]
    grid = change_points(eligible) if derived else list(fit_grid)

    out: list[SweepRow] = []
    for threshold in grid:
        shortlisted = {
            r["company_id"]
            for r in rows
            if r["fund_fit_pct"] >= threshold and r["tier"] != "reject"
        }
        positives = {cid for cid, v in labels.items() if v == WORTH_CONTACTING}

        # Precision is measured over the companies Aryan has actually judged.
        # An unlabelled company is not a false positive — it is unknown, and
        # counting it as a miss drives precision toward zero and the
        # recommended threshold toward "shortlist nothing". With ~50 labels
        # against a few thousand companies, that error is the whole answer.
        judged = shortlisted & set(labels)
        tp = len(judged & positives)
        fp = len(judged - positives)
        fn = len(positives - shortlisted)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None

        out.append(SweepRow(
            threshold=int(threshold),
            would_shortlist=len(shortlisted),
            precision=precision,
            recall=recall,
            f1=_f1(precision, recall),
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        ))

    best = max((r for r in out if r.f1 is not None), key=lambda r: r.f1 or 0, default=None)
    # No silent caps: if the range was too wide to show every change point,
    # say so rather than letting the table read as complete.
    dropped = len(_floors(eligible)) - len(grid) if derived else 0
    return {
        "sweep": [r.to_dict() for r in out],
        "thresholds": "change points" if derived else "explicit grid",
        "change_points_omitted": max(dropped, 0),
        "labels": {WORTH_CONTACTING: len([v for v in labels.values() if v == WORTH_CONTACTING]),
                   NOT_FOR_ME: len([v for v in labels.values() if v == NOT_FOR_ME])},
        "scored_companies": len(rows),
        "best": best.to_dict() if best else None,
        "recommendation": (
            f"At {best.threshold} you'd shortlist {best.would_shortlist} companies "
            f"and like {best.precision:.0%} of them." if best else
            "No verdicts yet — fill in the Verdict column, then re-run tune."
        ),
    }


# ------------------------------------------------- attribute-importance sweep


def sweep_attribute(
    db: Any,
    cfg: Any,
    *,
    attribute: str,
    grid: Sequence[float] = (1, 2, 3, 4),
    fund_key: str | None = None,
) -> dict[str, Any]:
    """Perturb one attribute's importance weight and report the F1 delta.

    Answers "which attributes are doing work?" — an attribute whose weight can
    move from 1 to 4 with no change in F1 is decorative; one that moves F1
    by 0.1 is load-bearing. Requires a config object to rebuild scores, which
    is why this is the heavier sibling of `sweep`.
    """
    labels = labelled_companies(db)
    rows = _score_rows(db, fund_key=fund_key)
    positives = {cid for cid, v in labels.items() if v == WORTH_CONTACTING}

    results: list[dict[str, Any]] = []
    for weight in grid:
        modified = cfg.model_copy(deep=True)
        for fund in modified.funds:
            modified.weights.importance.setdefault(attribute, {})[fund.key] = int(weight)

        from radar.score.fund_fit import fund_fit
        from radar.score.derive import Company

        shortlisted: set[str] = set()
        for row in rows:
            company = _company_from_row(db, row["company_id"])
            if company is None:
                continue
            fit = fund_fit(company, fund_key or "northstar", modified)
            if fit.pct >= modified.settings.shortlist_fit and fit.coverage >= modified.settings.min_coverage:
                shortlisted.add(row["company_id"])

        judged = shortlisted & set(labels)          # same rule as `sweep`
        tp = len(judged & positives)
        fp = len(judged - positives)
        fn = len(positives - shortlisted)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        results.append({
            "attribute": attribute,
            "weight": int(weight),
            "would_shortlist": len(shortlisted),
            "precision": None if precision is None else round(precision, 2),
            "recall": None if recall is None else round(recall, 2),
            "f1": None if _f1(precision, recall) is None else round(_f1(precision, recall), 2),
        })

    return {"attribute": attribute, "sweep": results}


def _company_from_row(db: Any, company_id: str):
    """Rebuild a scoring `Company` from a stored row (best-effort, no network)."""
    row = db.one("SELECT * FROM company WHERE id = ?", (company_id,))
    if row is None:
        return None
    from radar.score.derive import Company

    return Company(
        id=row["id"],
        canonical_name=row["canonical_name"],
        norm_key=row["norm_key"],
        companies_house_no=row["companies_house_no"],
        domain=row["domain"],
        website_url=row["website_url"],
        incorporated_on=row["incorporated_on"],
        hq_postcode=row["hq_postcode"],
        hq_region=row["hq_region"],
        hq_city=row["hq_city"],
        country_iso2=row["country_iso2"],
        sector=row["sector"],
        stage=row["stage"],
        founder_signal=row["founder_signal"],
        traction_signal=row["traction_signal"],
        total_funding_gbp=row["total_funding_gbp"],
        on_vc_portfolio=bool(row["on_vc_portfolio"]),
        discovery_route=row["discovery_route"],
    )


__all__ = ["sweep", "sweep_attribute", "labelled_companies", "change_points",
           "SweepRow"]
