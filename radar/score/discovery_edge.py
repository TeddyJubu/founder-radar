"""Discovery Edge (UI: Fresh) — how likely is it that this fund has *not*
already seen the company?

06-scoring §7. 0-100, deterministic, evidence-backed, scored with the same
`ComponentScore` machinery as Fund Fit (UI: Match).

| component      | weight | sub-score                                            |
|----------------|--------|------------------------------------------------------|
| company age    | 30     | continuous curve 0→36 months (younger = fresher)     |
| press coverage | 30     | 0 → 1.0 · 1 → 0.7 · 2-4 → 0.4 · ≥5 → 0.0             |
| funding        | 20     | known none → 1.0 · **unknown → 0.5** · <£500k → 0.6  |
| route          | 20     | register-first → 1.0 · spinout page → 0.6 · news 0.2 |

Four things this model deliberately does **not** do:

1. **No "VC portfolio presence" component.** Being on a tracked portfolio is a
   hard gate evaluated before scoring, so every company that reaches Discovery
   Edge would score identically on it — a constant dressed as a variable.
2. **Press coverage is named honestly.** It counts articles found in *our
   tracked sources*, not global fame.
3. **Unknown funding scores 0.5, not 1.0.** Collapsing NULL into "known zero"
   would violate the one invariant this whole system holds to.
4. **Route rewards the register.** A company found by sweeping Companies House
   with no press attached is, by construction, the least-visible thing the
   system can produce.

Age used to be four coarse step bands (≤6 / ≤18 / ≤30 / ≤36). Every young
registry company landed on the same sub-score, so the Fresh tile looked
identical across a morning's cards. The curve is piecewise-linear now so
companies of different ages separate when evidence differs — without inventing
press or funding we do not have.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .criteria import ComponentScore, band_score
from .derive import _get, _lists, age_months

EDGE_KEYS = ("age", "press_coverage", "disclosed_funding", "discovery_route")

# `test_unknown_funding_is_not_known_zero` asks for "funding"; the component
# key is "disclosed_funding". Accept both rather than make the caller guess.
_ALIASES = {
    "funding": "disclosed_funding",
    "press": "press_coverage",
    "route": "discovery_route",
    "company_age": "age",
}

_LABELS = {
    "age": "Company age",
    "press_coverage": "Press coverage in tracked sources",
    "disclosed_funding": "Disclosed funding",
    "discovery_route": "Discovery route",
}

# Piecewise-linear age curve: [months, sub_score], evaluated between knots.
# Declines steadily so same-route candidates still separate on age alone.
_DEFAULT_AGE_CURVE = [[0, 1.0], [12, 0.85], [24, 0.55], [36, 0.15]]

_FALLBACK = {
    "weights": {"age": 30, "press_coverage": 30, "disclosed_funding": 20, "discovery_route": 20},
    "age_curve": _DEFAULT_AGE_CURVE,
    # Kept for back-compat if a config snapshot only has step bands.
    "age_bands": [[6, 1.0], [18, 0.8], [30, 0.5], [36, 0.2]],
    "age_unknown": 0.5,
    "press_bands": [[0, 1.0], [1, 0.7], [4, 0.4]],
    "press_unknown": 0.5,
    "funding_bands": [[0, 1.0], [499_999.99, 0.6], [1_500_000, 0.3]],
    "funding_unknown": 0.5,
    "route_scores": {"registry": 1.0, "spinout": 0.6, "accelerator": 0.6,
                     "grant": 0.6, "news": 0.2, "portfolio": 0.2},
    "route_unknown": 0.5,
    "route_registry_with_press": 0.6,
}


def _cfg(config: Any) -> dict:
    merged = dict(_FALLBACK)
    merged.update(_lists(config).get("discovery_edge", {}) or {})
    return merged


def curve_score(value: float | int | None, curve: list, unknown: float) -> float:
    """Piecewise-linear score along `[[x, sub], ...]` knots (x ascending).

    Below the first knot → first sub-score. Above the last → 0.0 (same as
    `band_score` past the final band). Unknown → configured mid/neutral.
    """
    if value is None:
        return float(unknown)
    if not curve:
        return float(unknown)
    x = float(value)
    points = [(float(px), float(ps)) for px, ps in curve]
    if x <= points[0][0]:
        return points[0][1]
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if x <= x1:
            if x1 == x0:
                return s1
            t = (x - x0) / (x1 - x0)
            return s0 + t * (s1 - s0)
    return 0.0


def _age_sub(age: float | None, settings: dict) -> float:
    """Prefer a continuous `age_curve`; fall back to legacy step `age_bands`."""
    curve = settings.get("age_curve")
    if curve:
        return curve_score(age, curve, settings["age_unknown"])
    return band_score(age, settings["age_bands"], settings["age_unknown"])


def _edge_parts(
    company: Any, config: Any = None, *, today: date | None = None
) -> tuple[float, list[tuple[str, str, float, float, str]]]:
    """The four components as plain tuples `(key, label, sub, weight, evidence)`.

    This is the single source of the edge arithmetic: the pydantic path
    (`discovery_edge_components`) wraps it, the lean rescore
    (`pipeline.rescore_all`) consumes it directly, so the two cannot drift
    (09-test-plan §8). Returns `(edge, parts)` with the same 0-100 number
    `discovery_edge` computes.
    """
    settings = _cfg(config)
    weights = settings["weights"]

    age = age_months(company, today)
    age_sub = _age_sub(age, settings)

    press = _get(company, "news_mention_count")
    press_sub = band_score(press, settings["press_bands"], settings["press_unknown"])

    funding = _get(company, "total_funding_gbp")
    funding_sub = band_score(funding, settings["funding_bands"], settings["funding_unknown"])

    route = _get(company, "discovery_route")
    route_sub, route_evidence = _route(route, press, settings)

    parts = [
        ("age", _LABELS["age"], age_sub, float(weights["age"]),
         "age unknown" if age is None
         else f"{age:.0f} month{'' if f'{age:.0f}' == '1' else 's'} old"),
        ("press_coverage", _LABELS["press_coverage"], press_sub, float(weights["press_coverage"]),
         ("press count unknown" if press is None else f"{int(press)} tracked article(s)")),
        ("disclosed_funding", _LABELS["disclosed_funding"], funding_sub,
         float(weights["disclosed_funding"]),
         ("funding unknown" if funding is None else f"£{funding:,.0f} disclosed")),
        ("discovery_route", _LABELS["discovery_route"], route_sub,
         float(weights["discovery_route"]), route_evidence),
    ]

    # Every Discovery Edge band resolves to a number — unknown age and unknown
    # funding have their own explicit 0.5, they are not skipped — so there is
    # no None to coerce here and none is coerced.
    scored = [(s, w) for _, _, s, w, _ in parts if s is not None]
    total_weight = sum(w for _, w in scored)
    if not total_weight:
        edge = 0.0
    else:
        edge = round(100.0 * sum(s * w for s, w in scored) / total_weight, 1)
    return edge, parts


def discovery_edge_components(
    company: Any, config: Any = None, *, today: date | None = None
) -> list[ComponentScore]:
    """The edge components as pydantic models, built from `_edge_parts`."""
    _, parts = _edge_parts(company, config, today=today)
    return [ComponentScore(key=key, label=label, sub_score=sub, weight=weight,
                           evidence=evidence)
            for key, label, sub, weight, evidence in parts]


def _route(route: str | None, press: int | None, settings: dict) -> tuple[float, str]:
    if route is None:
        return float(settings["route_unknown"]), "route unknown"
    key = str(route).strip().lower()
    # ponytail: §7 says "register-first, **no press** → 1.0". A register find
    # that also has press is no longer register-first, so it drops to the
    # middle band rather than keeping the top one.
    if key == "registry" and (press or 0) > 0:
        return float(settings["route_registry_with_press"]), "found on the register, but has press"
    sub = settings["route_scores"].get(key)
    if sub is None:
        return float(settings["route_unknown"]), f"route {key}"
    return float(sub), f"found via {key}"


def discovery_edge_component(
    company: Any, key: str, config: Any = None, *, today: date | None = None
) -> ComponentScore:
    wanted = _ALIASES.get(key, key)
    for component in discovery_edge_components(company, config, today=today):
        if component.key == wanted:
            return component
    raise KeyError(f"{key!r} is not a Discovery Edge component; expected one of {EDGE_KEYS}")


def discovery_edge(
    company: Any, config: Any = None, *, today: date | None = None
) -> float:
    """0-100. The component weights sum to 100, so the total is the sum of
    `weight × sub_score` — no second normalisation to get wrong."""
    edge, _ = _edge_parts(company, config, today=today)
    return edge
