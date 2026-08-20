"""Qualification — Track B needs a reason to exist.

06-scoring §3. Roughly 60,000 companies are incorporated in the UK every
month. Most are dormant, holding vehicles, or one-person consultancies.
Scoring all of them would be noise.

A registry-sourced company enters scoring only once it has at least one
qualifying signal. Companies with none are **not rejected** — they stay in the
candidate pool with `qualified = 0` and are re-checked on every run, because a
company incorporated today may file an SH01 next month. They simply never
reach the sheet until they earn it.

This is also the honest answer to *"why isn't every new company on my list?"* —
because a company with nothing but a SIC code genuinely is not worth Aryan's
time yet.

**Companies House verifies and enriches; it does not drive discovery** (client
feedback, 18 Aug 2026). A live website is true of almost every registered Ltd —
a corner shop has one — so it is *not* a venture signal. A prior Companies
House appointment is not one either: formation agents and accountants sit on
thousands of new Ltds, which is how names like `4DCONSTRUCTIONPLANNING LTD`
kept filling Today. What admits a Track B company is a real venture signal: a
share allotment (SH01), a grant, a university spinout match, or press in a
tracked source. Repeat-founder evidence still *scores*; it does not open the
door on its own. The admitting set is read from the Lists tab
(`lists["qualifiers"]`) — add `website` or `repeat_founder` back there to
loosen, or drop `press` to tighten.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .derive import _get

# The full qualifier vocabulary — everything the record can prove. Which of
# these actually *admits* a registry company to scoring is configured in
# `lists["qualifiers"]` (see `_admitting_qualifiers`); by default `website`
# and `repeat_founder` are proven but not admitting. A website alone is a
# small business; a prior CH appointment is usually a formation agent.
QUALIFIER_KINDS: tuple[str, ...] = (
    "share_issue",     # SH01 filed since incorporation
    "grant",           # matched to a UKRI or Innovate UK award
    "spinout",         # matched to a university spinout announcement
    "press",           # matched to any news article
    "repeat_founder",  # an officer with a prior UK directorship
    "website",         # a live company website resolved and reachable
)

# Only Track B (register sweep) needs to earn its way in. A company that came
# from a news article, a spinout page or a grant record arrived *because* of a
# signal — asking it to produce one again would be circular.
REGISTRY_ROUTES = ("registry",)

_SIGNAL_QUALIFIERS = {
    "share_issue": "share_issue",
    "grant_award": "grant",
    "spinout": "spinout",
    "press": "press",
    "news": "press",
    "competition_win": "press",
}


def derive_qualifiers(company: Any) -> list[str]:
    """Everything the record can currently prove, in a stable order."""
    found: set[str] = set()

    for stated in (_get(company, "qualifiers") or []):
        if stated in QUALIFIER_KINDS:
            found.add(stated)

    if _get(company, "has_share_issue"):
        found.add("share_issue")
    if _get(company, "is_university_spinout"):
        found.add("spinout")
    if (_get(company, "news_mention_count") or 0) > 0:
        found.add("press")
    if _get(company, "website_url") or _get(company, "domain"):
        found.add("website")

    for founder in (_get(company, "founders") or []):
        if (_get(founder, "prior_appointments") or 0) >= 1:
            found.add("repeat_founder")
            break

    for signal in (_get(company, "signals") or []):
        mapped = _SIGNAL_QUALIFIERS.get(_get(signal, "kind", ""))
        if mapped:
            found.add(mapped)

    return [q for q in QUALIFIER_KINDS if q in found]


def _admitting_qualifiers(config: Any) -> tuple[str, ...]:
    """The qualifiers that actually admit a registry company, from the sheet.

    Reads `lists["qualifiers"]` so the bar is editable without a code change.
    When the list is absent or matches nothing, fall back to the seeded
    admitting set (no `website`, no `repeat_founder`), not `QUALIFIER_KINDS`.
    Those two are proven signals but not admitting ones (client J25).
    """
    lists = getattr(config, "lists", None) or {}
    configured = lists.get("qualifiers") if isinstance(lists, Mapping) else None
    if not configured:
        from radar.config.defaults import LISTS
        configured = LISTS.get("qualifiers") or QUALIFIER_KINDS
    allowed = tuple(q for q in QUALIFIER_KINDS if q in set(configured))
    # A sheet typo that matches nothing must not silently admit everything or
    # admit nothing. Fall back to the seeded admitting set, not the full
    # vocabulary — QUALIFIER_KINDS includes `website`, which is the J25 leak.
    if allowed:
        return allowed
    from radar.config.defaults import LISTS
    seeded = tuple(q for q in QUALIFIER_KINDS if q in set(LISTS.get("qualifiers") or ()))
    return seeded or QUALIFIER_KINDS


def admitting_qualifiers(company: Any, config: Any) -> list[str]:
    """The proven qualifiers that count towards admission, in stable order."""
    allowed = set(_admitting_qualifiers(config))
    return [q for q in derive_qualifiers(company) if q in allowed]


def is_qualified(company: Any, config: Any) -> bool:
    """`min_qualifiers` is a Setting, default 1. Raise it to 2 if the noise is
    still too high. Only *admitting* qualifiers count (see the module docstring):
    a registry company with nothing but a website stays in the pool, unscored."""
    minimum = config.settings.min_qualifiers
    if minimum <= 0:
        return True
    route = _get(company, "discovery_route")
    if route is not None and route not in REGISTRY_ROUTES:
        return True
    return len(admitting_qualifiers(company, config)) >= minimum


def qualification_reason(company: Any, config: Any) -> str:
    qualifiers = admitting_qualifiers(company, config)
    if is_qualified(company, config):
        return "qualified by " + (", ".join(qualifiers) or "discovery route")
    return (
        f"no qualifying signal yet — needs {config.settings.min_qualifiers} of "
        + ", ".join(_admitting_qualifiers(config))
    )
