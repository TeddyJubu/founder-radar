"""09-test-plan §2.6 — the sheet configuration.

A deliberate typo in Settings must never stop the run: the offending field
falls back to the last known-good snapshot and reports itself in the sheet in
red. And coercion is generous, because humans type `yes`, `Y`, `1` and `✓`
for the same truth value.
"""

from __future__ import annotations

import pytest

from radar.config.defaults import default_config
from radar.config.loader import coerce_value, coerce_weight, load_config, save_snapshot


def test_typo_uses_last_good_and_reports_in_sheet(db):
    good = default_config()
    good.settings.shortlist_fit = 45          # the last known-good threshold
    save_snapshot(db, good, is_last_good=True)

    raw = {
        "Settings": [
            ["Key", "Value", "Type"],
            ["shortlist_fit", "fourty five", "int"],
            ["max_company_age_months", "36", "int"],
        ],
    }
    cfg, errors = load_config(raw, db=db)
    assert cfg.settings.shortlist_fit == good.settings.shortlist_fit
    assert "not a number" in errors["shortlist_fit"]
    assert "45" in errors["shortlist_fit"]        # tells them the fallback used
    assert cfg.settings.max_company_age_months == 36


@pytest.mark.parametrize("typed,expected", [
    ("yes", True), ("Y", True), ("1", True), ("✓", True),
    ("no", False), ("", False),
    ("£1.5m", 1_500_000.0), ("1,500,000", 1_500_000.0), ("1.5M", 1_500_000.0),
    ("Pre Seed", "pre_seed"), ("pre-seed", "pre_seed"), ("PRE_SEED", "pre_seed"),
    ("GB, IE", ["GB", "IE"]), ("gb;ie", ["GB", "IE"]),
])
def test_coercion_is_generous(typed, expected):
    assert coerce_value(typed) == expected


def test_blank_and_zero_mean_different_things():
    assert coerce_weight("") is None      # use default
    assert coerce_weight("0") == 0.0      # weight at zero


def test_config_hash_changes_with_a_threshold_edit():
    """FR-4.7/NFR-6: an edit to the sheet changes scores with no code change,
    and the hash proves *why* a company dropped off the shortlist."""
    cfg = default_config()
    before = cfg.hash()
    edited = cfg.model_copy(deep=True)
    edited.settings.max_company_age_months = 12
    assert edited.hash() != before


# ------------------------------------------- the onboarding fund summary
#
# The onboarding page used to state each fund's hard rules in hand-written
# prose and twice contradicted the config: it attributed Outward's ECF backing
# to the company rather than the fund, dropped Outward's £5m round cap, and
# claimed Northstar is hard-limited to the North East when `eis_growth` is a
# SOFT preference across the north. The page now renders from this config, so
# these tests guard the two ways that generation could still go quiet or wrong.


def test_every_hard_reject_key_has_onboarding_wording():
    """A rule the config can express but the page cannot say is invisible.

    Adding a key to `HARD_REJECT_KEYS` and forgetting `_reject_phrase` would
    silently drop it from the summary — the same class of omission as the
    hand-written £5m cap, just harder to spot.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from prototype.server import _reject_phrase

    from radar.config.models import HARD_REJECT_KEYS

    sample = {
        "round_max": 5_000_000, "prior_total_max": 20_000_000,
        "valuation_max": 10_000_000, "uk_exec_pct_min": 66,
        "university_spinout_required": ["durham"],
        "beyond_research_stage": True, "requires_seis_eis": True,
    }
    missing = [k for k in HARD_REJECT_KEYS if not _reject_phrase(k, sample[k])]
    assert not missing, f"no onboarding wording for {missing}"


def test_onboarding_states_every_active_hard_rule():
    """Every hard reject on every ACTIVE vehicle reaches the page.

    Outward's £5m round cap is the specific thing the prose lost, so it is
    asserted by name as well as by the general sweep.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from prototype.server import fund_rules

    cfg = default_config()
    by_fund = {f["key"]: " ".join(f["rules"]) for f in fund_rules(cfg)}

    for fund in cfg.funds:
        if not fund.active_vehicles:
            continue
        text = by_fund[fund.key]
        for vehicle in fund.active_vehicles:
            if vehicle.max_age_years:
                assert f"{vehicle.max_age_years} years old" in text, \
                    f"{fund.key}: {vehicle.vehicle_key} age cap missing"
            for key in vehicle.hard_rejects:
                assert key in ("round_max", "prior_total_max", "valuation_max",
                               "uk_exec_pct_min", "university_spinout_required",
                               "beyond_research_stage", "requires_seis_eis"), key

    assert "£5m" in by_fund["outward"], "Outward's round cap is missing again"
    assert "£20m" in by_fund["outward"]
    assert "66%" in by_fund["outward"]
    # Client-issues plan §3.5 (D14): the exact prose that started this whole
    # section — Outward's ECF backing belongs to the fund (it takes British
    # Business Bank money), never to the company itself. The word must not
    # describe Outward.
    assert "government" not in by_fund["outward"].lower()
    # An inactive vehicle describes money that cannot be deployed.
    assert "co-investment" not in by_fund["dsw"].lower()


def test_onboarding_never_hardens_a_soft_geography():
    """Northstar's exact regression: `eis_growth` is SOFT/north_england, so the
    summary must not promise the North East without qualifying it."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from prototype.server import fund_rules

    cfg = default_config()
    by_fund = {f["key"]: " ".join(f["rules"]) for f in fund_rules(cfg)}

    for fund in cfg.funds:
        soft = [v for v in fund.active_vehicles if v.geo_rule != "HARD"]
        if soft:
            text = by_fund[fund.key]
            assert "except" in text and "prefers" in text, \
                f"{fund.key} has a SOFT vehicle but reads as an absolute rule"


def test_onboarding_page_carries_no_hand_written_fund_rules():
    """The placeholder is the contract: rules pasted back into the HTML would
    be able to drift again, which is the whole thing this replaced."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "prototype" / "onboarding.html").read_text()
    assert "<!--FUND_RULES-->" in page
    assert "fund-row" not in page.split("<style>")[-1].split("</style>")[-1], \
        "fund rules are hand-written in the page again"
