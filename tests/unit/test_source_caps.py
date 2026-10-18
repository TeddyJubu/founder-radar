"""Light per-source caps on Today's order (Oct 2026: Innovate UK was 55 of 63)."""
from types import SimpleNamespace

from radar.selection import _shares, cap_source_share


def _rows(spec):
    rows, meta = [], {}
    for i, (fund, family) in enumerate(spec):
        cid = f"c{i}"
        rows.append({"company_id": cid, "fund_key": fund})
        meta[cid] = {"source": family}
    return rows, meta


def _ids(rows):
    return [r["company_id"] for r in rows]


def test_one_source_cannot_fill_a_funds_list_but_nothing_is_dropped():
    rows, meta = _rows([("dsw", "grants")] * 6 + [("dsw", "press"), ("dsw", "accelerator")])
    out = cap_source_share(rows, metadata=meta, max_share=0.4, registry_share=0.2)
    assert sorted(_ids(out)) == sorted(_ids(rows))          # same cards, reordered
    # Three grants run free, then press and the accelerator jump the rest.
    assert _ids(out) == ["c0", "c1", "c2", "c6", "c7", "c3", "c4", "c5"]


def test_a_short_single_source_list_keeps_its_order():
    rows, meta = _rows([("dsw", "press")] * 3)
    out = cap_source_share(rows, metadata=meta, max_share=0.4, registry_share=0.2)
    assert _ids(out) == _ids(rows)


def test_companies_house_gets_the_stricter_share():
    rows, meta = _rows([("northstar", "registry")] * 3 + [("northstar", "press")] * 4)
    out = cap_source_share(rows, metadata=meta, max_share=0.4, registry_share=0.2)
    # Press fills its three free slots. Registry has no free slot and needs
    # four other cards before 20% buys one, so it waits behind them.
    assert _ids(out) == ["c3", "c4", "c5", "c0", "c1", "c2", "c6"]


def test_each_fund_is_capped_on_its_own_list():
    rows, meta = _rows([("dsw", "grants"), ("outward", "grants"), ("anticus", "grants")])
    out = cap_source_share(rows, metadata=meta, max_share=0.4, registry_share=0.2)
    assert _ids(out) == ["c0", "c1", "c2"]                   # one each, nothing waits


def test_a_share_of_one_leaves_the_order_alone():
    rows, meta = _rows([("dsw", "grants")] * 5)
    out = cap_source_share(rows, metadata=meta, max_share=1.0, registry_share=1.0)
    assert _ids(out) == _ids(rows)


def test_shares_come_from_settings_with_safe_defaults():
    assert _shares(None) == (0.40, 0.20)
    assert _shares(SimpleNamespace(max_source_share=0.5, max_registry_share=0.1)) == (0.5, 0.1)
