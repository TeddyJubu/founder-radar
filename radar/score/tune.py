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
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

WORTH_CONTACTING = "worth contacting"
NOT_FOR_ME = "not for me"
UNSURE = "unsure"

DEFAULT_FIT_GRID = (55, 60, 65, 70, 75, 80, 85)
DEFAULT_EDGE_GRID = (40, 45, 50, 55, 60, 65, 70)


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


def sweep(
    db: Any,
    *,
    fit_grid: Sequence[int] = DEFAULT_FIT_GRID,
    fund_key: str | None = None,
) -> dict[str, Any]:
    """Sweep the shortlist-fit threshold and score each value against verdicts.

    A company counts as "would shortlist" if its best score's `fund_fit_pct`
    is at or above the threshold and it was not gated (tier is not reject for
    a gate reason). Labels come from `user_field`.
    """
    labels = labelled_companies(db)
    rows = _score_rows(db, fund_key=fund_key)

    out: list[SweepRow] = []
    for threshold in fit_grid:
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
    return {
        "sweep": [r.to_dict() for r in out],
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


__all__ = ["sweep", "sweep_attribute", "labelled_companies", "SweepRow"]
