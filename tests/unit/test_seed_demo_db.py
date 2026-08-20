"""The demo-DB recipe used by Cloud Agents and prototype/TESTING.md §0.2."""

from __future__ import annotations

from tests.demo_db import build


def test_seed_demo_db_scores_register_companies(tmp_path):
    """Three fixture companies, at least one qualified after the website bar.

    #18 dropped `website` as an admitting qualifier. The fixture mock still
    serves SH01 and prior appointments, so the Today demo must not go empty.
    """
    path = tmp_path / "demo.db"
    assert build(str(path)) == 3

    from radar.store.db import Db

    db = Db(str(path))
    try:
        assert db.scalar("SELECT COUNT(*) FROM company WHERE qualified = 1") >= 1
        assert db.scalar("SELECT COUNT(*) FROM score") > 0
    finally:
        db.close()
