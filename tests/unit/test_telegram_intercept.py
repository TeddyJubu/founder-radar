"""Background Telegram search acks the dashboard URL and does not dump companies."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from radar.notify import telegram_intercept as intercept
from radar.cli import cli


def test_kickoff_spawns_search_send_and_returns_dashboard_url(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_TELEGRAM_NO_SUDO", "1")
    monkeypatch.setenv("RADAR_SEARCH_LOCK", str(tmp_path / "search.lock"))
    monkeypatch.setenv("RADAR_BIN", "founder-radar")
    monkeypatch.setattr(
        intercept,
        "dashboard_ping",
        lambda: "Today's companies are on the dashboard — not in this chat.\nhttps://radar.example.test/",
    )

    spawned = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, **kwargs):
        spawned["argv"] = list(argv)
        spawned["kwargs"] = kwargs
        return FakeProc()

    started, text = intercept.kickoff_search(fund_key="northstar", popen=fake_popen)
    assert started is True
    assert "https://radar.example.test/" in text
    assert "Scan started" in text
    assert "WULL" not in text
    assert spawned["argv"][:2] == ["founder-radar", "search"]
    assert "--send" in spawned["argv"]
    assert "--background" not in spawned["argv"]
    assert spawned["argv"][spawned["argv"].index("--fund") + 1] == "northstar"
    assert spawned["kwargs"]["start_new_session"] is True
    lock = tmp_path / "search.lock"
    assert "4242" in lock.read_text()


def test_kickoff_refuses_a_second_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_SEARCH_LOCK", str(tmp_path / "search.lock"))
    monkeypatch.setattr(intercept, "dashboard_ping", lambda: "https://radar.example.test/")
    monkeypatch.setattr(intercept, "search_in_progress", lambda: True)
    started, text = intercept.kickoff_search(popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert started is False
    assert "already running" in text
    assert "https://radar.example.test/" in text


def test_search_background_cli_prints_ack_not_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "radar.notify.telegram_intercept.kickoff_search",
        lambda **k: (True, "Today's companies are on the dashboard — not in this chat.\nhttps://radar.example.test/\n\nScan started."),
    )
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "r.db"), "search", "--background", "--send"], obj={},
    )
    assert result.exit_code == 0, result.output
    assert "https://radar.example.test/" in result.output
    assert "companies_new" not in result.output


def test_search_send_calls_telegram(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(
        "radar.pipeline.run_pipeline",
        lambda *a, **k: SimpleNamespace(status="ok", summary=lambda: {"shortlisted": 1}),
    )
    monkeypatch.setattr(
        "radar.render.digest.render_today_ping",
        lambda _db: "Today's companies are on the dashboard — not in this chat.\nhttps://radar.example.test/",
    )
    monkeypatch.setattr("radar.notify.telegram.send_message", lambda text: sent.append(text) or True)
    monkeypatch.setattr("radar.notify.telegram_intercept.mark_search_done", lambda: None)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "r.db"), "search", "--send", "--no-llm"], obj={},
    )
    assert result.exit_code == 0, result.output
    assert sent
    assert "https://radar.example.test/" in sent[0]
    assert "https://radar.example.test/" in result.output
