"""Empty-Today diagnosis — poison, config_hash drift, and the why-today CLI."""

from __future__ import annotations

import json
from datetime import date

from click.testing import CliRunner

from radar.cli import cli
from radar.config.defaults import default_config
from radar.config.loader import funds_are_poisoned, save_snapshot
from radar.config.models import Fund, Vehicle
from radar.render.today_diagnose import diagnose_today, format_today_diagnosis
from radar.store.db import now_iso
from tests.factories import C, store_company

TODAY = date(2026, 8, 26)


def _poison_config():
    cfg = default_config().model_copy(deep=True)
    cfg.funds = [
        Fund(
            key="outward",
            name="UK-based founders at entry",
            vehicles=[
                Vehicle(
                    fund_key="outward",
                    vehicle_key="yes",
                    fund_name="UK-based founders at entry",
                    vehicle_name="Pre-Series A",
                    active=True,
                ),
            ],
        ),
    ]
    return cfg


def test_diagnose_today_flags_poisoned_fund_criteria(db):
    save_snapshot(db, _poison_config(), is_last_good=True)
    report = diagnose_today(db)
    assert report["poisoned_fund_criteria"] is True
    assert any("poisoned" in c.lower() for c in report["likely_causes"])
    text = format_today_diagnosis(report)
    assert "POISONED" in text


def test_diagnose_today_flags_config_hash_mismatch(db):
    """Healed last-good with only old-hash scores → Today sees zero cards."""
    good = default_config()
    save_snapshot(db, good, is_last_good=True)
    cid = store_company(db, C())
    db.execute(
        """INSERT INTO score
             (company_id, fund_key, vehicle_key, fund_fit_pct, coverage,
              discovery_edge, priority, tier, reject_reason, explanation,
              flags, config_hash, scorer_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, "dsw", "seis_fund", 80.0, 0.7, 70.0, 76.0, "shortlist",
         None, "ok", None, "stale-hash-from-before-heal", "test", now_iso()),
    )
    report = diagnose_today(db)
    assert report["scored_for_active_hash"] == 0
    assert report["scores_on_other_hashes"] == 1
    assert any("config_hash" in c for c in report["likely_causes"])


def test_why_today_cli_reports_poison(tmp_path, monkeypatch):
    from radar.store.db import Db

    path = tmp_path / "radar.db"
    conn = Db(str(path))
    conn.migrate()
    save_snapshot(conn, _poison_config(), is_last_good=True)
    conn.close()

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(path), "why-today"])
    assert result.exit_code in (0, 1)
    assert "POISONED" in result.output or "poisoned" in result.output.lower()


def test_doctor_fails_on_poisoned_fund_criteria(tmp_path, monkeypatch):
    from radar.store.db import Db

    path = tmp_path / "radar.db"
    conn = Db(str(path))
    conn.migrate()
    save_snapshot(conn, _poison_config(), is_last_good=True)
    conn.close()

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(path), "doctor"])
    assert result.exit_code == 1
    assert "Fund Criteria last-good" in result.output
    assert "POISONED" in result.output
