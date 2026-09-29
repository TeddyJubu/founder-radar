"""H-04 — a mistyped value in a HARD geography rule must fail closed.

The Fund Criteria tab is edited by hand. The loader drops an unrecognised
`Geo values` entry with a warning ("never an error", 07-interfaces tab 4), and
until now that could leave a HARD rule with an empty list — which the gate
reads as "no rule" — and then save the result as last-good. A typo in
Northstar's `north_east` would have let a Yorkshire company through.
"""

from __future__ import annotations

from radar.config.defaults import default_config
from radar.config.loader import load_config, load_runtime_config, save_snapshot
from tests.factories import C
from tests.fakes import FakeSheetGateway
from tests.unit.test_runtime_config import _seeded_fund_criteria_grid
GEO_VALUES_COL = 10
GEO_RULE_COL = 9


def _grid_with_geo(fund_key, vehicle_key, geo_values=None, geo_rule=None):
    grid = _seeded_fund_criteria_grid()
    for row in grid[1:]:
        if row[0] == fund_key and row[1] == vehicle_key:
            if geo_values is not None:
                row[GEO_VALUES_COL] = geo_values
            if geo_rule is not None:
                row[GEO_RULE_COL] = geo_rule
            return grid
    raise AssertionError(f"{fund_key}.{vehicle_key} not in the seeded grid")


def _vehicle(cfg, fund_key, vehicle_key):
    return next(v for v in cfg.all_vehicles()
                if v.fund_key == fund_key and v.vehicle_key == vehicle_key)


def _hash_of_last_good(db):
    return db.one(
        "SELECT config_hash FROM config_snapshot WHERE is_last_good = 1"
    )["config_hash"]


def test_invalid_hard_geography_value_holds_the_vehicle_at_last_good(db):
    from radar.score.gates import evaluate_vehicle_gates

    save_snapshot(db, default_config(), is_last_good=True)
    prior = _hash_of_last_good(db)

    grid = _grid_with_geo("northstar", "spinout_inspire", "north_eest")
    result = load_config({"Fund Criteria": grid}, db=db)

    held = _vehicle(result.config, "northstar", "spinout_inspire")
    assert held.active is True
    assert held.geo_rule == "HARD"
    assert held.geo_values == ["north_east"], "typo must not empty a HARD rule"

    # Loud and specific, in every place the loader reports to.
    key = "northstar.spinout_inspire"
    assert "north_eest" in result.warnings[key]
    assert "north_east" in result.warnings[key]          # did-you-mean hint
    assert "last valid" in result.warnings[key].lower()
    assert any("north_eest" in text and "spinout_inspire" in text
               for text in result.errors.values())
    cell = next(c for c in result.status_cells
                if c.tab == "Fund Criteria" and "north_eest" in c.text)
    assert cell.is_error is True
    assert result.used_last_good is True

    # The config with a rejected HARD value is never promoted to last-good.
    assert _hash_of_last_good(db) == prior

    # And the rule still bites: a Yorkshire company is not a North East fit.
    yorkshire = C(geography="yorkshire")
    verdict = evaluate_vehicle_gates(yorkshire, held, result.config)
    assert verdict.passed is False
    assert verdict.reason == "geography:north_east"


def test_one_bad_value_among_good_ones_still_holds_the_vehicle(db):
    save_snapshot(db, default_config(), is_last_good=True)

    grid = _grid_with_geo("anticus", "fy_seedcorn", "yorkshire, north_eest")
    result = load_config({"Fund Criteria": grid}, db=db)

    held = _vehicle(result.config, "anticus", "fy_seedcorn")
    assert held.geo_values == ["yorkshire"]
    assert held.warnings == _vehicle(default_config(), "anticus",
                                     "fy_seedcorn").warnings
    assert "north_eest" in result.warnings["anticus.fy_seedcorn"]


def test_invalid_hard_geography_without_last_good_blocks_the_vehicle(db):
    grid = _grid_with_geo("northstar", "spinout_inspire", "north_eest")
    result = load_config({"Fund Criteria": grid}, db=db)   # db has no last-good

    blocked = _vehicle(result.config, "northstar", "spinout_inspire")
    assert blocked.active is False, "no last valid config: fail closed, not open"
    assert blocked not in result.config.vehicles("northstar")
    assert "BLOCKED" in result.warnings["northstar.spinout_inspire"]
    assert any("BLOCKED" in text for text in result.errors.values())
    assert db.one("SELECT 1 FROM config_snapshot WHERE is_last_good = 1") is None

    # Every other vehicle is untouched, so one typo does not stop the run.
    assert _vehicle(result.config, "northstar", "ne_innovation_fund").active
    assert _vehicle(result.config, "dsw", "seis_fund").active
    # ...and no active HARD rule was left with an empty value list.
    assert not [v for v in result.config.vehicles()
                if v.geo_rule == "HARD" and not v.geo_values]


def test_new_vehicle_with_a_typo_is_blocked_even_when_last_good_exists(db):
    """Last-good exists but never contained this vehicle: nothing to hold."""
    from radar.config.models import Fund

    previous = default_config().model_copy(deep=True)
    previous.funds = [
        Fund(key=f.key, name=f.name,
             vehicles=[v for v in f.vehicles if v.vehicle_key != "spinout_inspire"])
        for f in previous.funds
    ]
    save_snapshot(db, previous, is_last_good=True)

    grid = _grid_with_geo("northstar", "spinout_inspire", "nrth_east")
    result = load_config({"Fund Criteria": grid}, db=db)
    assert _vehicle(result.config, "northstar", "spinout_inspire").active is False


def test_invalid_soft_geography_value_is_still_only_a_warning(db):
    """SOFT rules never reject, so warn-and-ignore stays (07-interfaces)."""
    grid = _grid_with_geo("northstar", "eis_growth", "north_england, nrth")
    result = load_config({"Fund Criteria": grid}, db=db)

    soft = _vehicle(result.config, "northstar", "eis_growth")
    assert soft.active is True
    assert soft.geo_values == ["north_england"]
    assert "nrth" in result.warnings["northstar.eis_growth"]
    assert not result.errors
    assert db.one("SELECT 1 FROM config_snapshot WHERE is_last_good = 1") is not None


def test_intentionally_blank_hard_geography_is_not_blocked(db):
    """Blank / '(agnostic)' means no geographic restriction, not a typo."""
    for blank in ("", "*(agnostic)*"):
        grid = _grid_with_geo("northstar", "ne_innovation_fund", blank)
        result = load_config({"Fund Criteria": grid}, db=db)
        vehicle = _vehicle(result.config, "northstar", "ne_innovation_fund")
        assert vehicle.active is True
        assert vehicle.geo_values == []
        assert not result.errors


def test_clean_fund_criteria_still_becomes_last_good(db):
    result = load_config({"Fund Criteria": _seeded_fund_criteria_grid()}, db=db)
    assert not result.errors
    assert not result.used_last_good
    assert db.one("SELECT 1 FROM config_snapshot WHERE is_last_good = 1") is not None


def test_run_warnings_carry_the_invalid_hard_geography_note(db):
    """Stage ① hands the note to the run result / Run Log."""
    save_snapshot(db, default_config(), is_last_good=True)
    sheet = FakeSheetGateway(tabs=["Fund Criteria"])
    sheet.grids["Fund Criteria"] = _grid_with_geo(
        "northstar", "spinout_inspire", "north_eest")

    cfg, _gateway, warnings = load_runtime_config(db, gateway=sheet)
    assert _vehicle(cfg, "northstar", "spinout_inspire").geo_values == ["north_east"]
    assert any("north_eest" in w and "spinout_inspire" in w for w in warnings)
