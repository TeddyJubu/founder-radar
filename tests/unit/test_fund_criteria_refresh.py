"""The published regulated-industry thesis must be expressible and scoreable."""

import pytest

from radar.config.defaults import default_config
from radar.config.loader import parse_lists, parse_weights
from radar.score.fund_fit import calculate_fund_fit
from radar.score.derive import derive_sector, derive_updates


@pytest.mark.parametrize("sector", ["legaltech", "proptech", "cybersecurity", "hr_tech", "data_infrastructure"])
def test_supported_regulated_technology_is_not_scored_as_generic_saas(sector):
    cfg = default_config()
    specific = calculate_fund_fit({"sector": sector}, "outward", cfg)
    generic = calculate_fund_fit({"sector": "b2b_saas"}, "outward", cfg)
    unknown = calculate_fund_fit({}, "outward", cfg)
    assert specific.pct > generic.pct > unknown.pct
    assert specific.pct < 100  # a sector alone is never a complete match
    assert specific.coverage == generic.coverage


def test_new_sector_rows_are_editable_without_replacing_client_weights():
    lists = parse_lists([])
    weights, warnings, _ = parse_weights(
        [["Attribute", "Category", "DSW", "Northstar", "Outward", "Anticus"],
         ["sector", "legaltech", "1", "0", "2", "1"],
         ["sector", "b2b_saas", "4", "2", "0", "3"]],
        lists=lists,
        fund_keys={key: key for key in ("dsw", "northstar", "outward", "anticus")},
        last_good=None,
    )
    assert not warnings
    assert weights.matrix_value("sector", "legaltech", "outward") == 2
    assert weights.matrix_value("sector", "b2b_saas", "outward") == 0


@pytest.mark.parametrize("sic", ["62012", "62020", "62090"])
def test_software_registration_code_does_not_prove_subscription_product(sic):
    cfg = default_config()
    assert derive_sector([sic], cfg) is None
    updates, _ = derive_updates({"sic_codes": [sic], "sector": "legaltech"}, cfg)
    assert "sector" not in updates  # real product evidence still wins
