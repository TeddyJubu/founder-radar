"""Tiering — shortlist, watchlist, reject, and always a reason.

06-scoring §8, implemented as written. Two properties are worth stating
plainly because they are the whole design:

* **Any flag at all keeps a company off the shortlist.** `age_unknown`,
  `uk_unverified` and `gate_unverified` all mean "we could not check
  something that matters", and a shortlist you cannot defend is worse than a
  shorter one.
* **"Scores high but we know too little" is watchlist with an explicit
  reason, not reject.** That is a research prompt, not a dismissal.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from radar.config.models import Settings

from .fund_fit import FitScore


def _settings(config_or_settings: Any) -> Settings:
    inner = getattr(config_or_settings, "settings", None)
    return inner if isinstance(inner, Settings) else config_or_settings


def tier_of(
    fit: FitScore, edge: float, flags: Sequence[str] | Iterable[str], config: Any
) -> tuple[str, str]:
    """Returns `(tier, reason)`. `reason` is '' when there is nothing to explain.

    The returned reason is appended to `score.explanation`, not stored
    separately — `reject_reason` is reserved for gates.
    """
    settings = _settings(config)
    flags = list(flags)

    if flags:
        if fit.pct >= settings.watchlist_fit:
            return "watchlist", f"{flags[0].replace('_', ' ')} — verify before sending"
        return "reject", "below fit threshold"

    if (
        fit.pct >= settings.shortlist_fit
        and edge >= settings.shortlist_edge
        and fit.coverage >= settings.min_coverage
    ):
        return "shortlist", ""

    if fit.pct >= settings.shortlist_fit and fit.coverage < settings.min_coverage:
        return "watchlist", "strong fit but too little known — needs 10 minutes of research"

    if fit.pct >= settings.shortlist_fit and edge < settings.shortlist_edge:
        return "watchlist", "good fit but likely already on their radar"

    if fit.pct >= settings.watchlist_fit:
        return "watchlist", ""

    return "reject", "below fit threshold"


def priority_of(fund_fit_pct: float, edge: float, config: Any) -> float:
    """`weight_fit × fit + weight_edge × edge` (06-scoring §7).

    Ranking on Fund Fit alone produced the version 1 complaint — *"there's a
    good chance these funds have already come across them"* — so the edge term
    is not decoration, it is the number that encodes the job.
    """
    settings = _settings(config)
    return round(settings.weight_fit * fund_fit_pct + settings.weight_edge * edge, 1)
