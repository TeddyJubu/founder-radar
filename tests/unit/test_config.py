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
