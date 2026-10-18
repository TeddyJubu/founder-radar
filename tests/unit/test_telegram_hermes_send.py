"""`hermes send` must launch like Today QA, or pings miss Hermes's home channel."""
from types import SimpleNamespace

from radar.notify import telegram


def test_hermes_send_uses_the_operator_env_and_heals_acls(monkeypatch):
    events, seen = [], {}
    monkeypatch.setattr("radar.qa.today.resolve_hermes_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr("radar.qa.publish._ensure_hermes_acl", lambda: events.append("heal"))
    monkeypatch.setenv("HERMES_HOME", "/home/operator")

    def run(argv, **kw):
        events.append("send")
        seen.update(argv=argv, env=kw["env"])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(telegram.subprocess, "run", run)
    assert telegram.hermes_transport("hello") is True
    assert seen["argv"] == ["/usr/bin/hermes", "send", "--to", "telegram", "hello"]
    assert seen["env"]["HOME"] == "/home/operator"
    assert events == ["heal", "send", "heal"]


def test_a_failed_hermes_send_logs_why(monkeypatch, caplog):
    monkeypatch.setattr("radar.qa.today.resolve_hermes_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr("radar.qa.publish._ensure_hermes_acl", lambda: None)
    monkeypatch.setattr(telegram.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"Traceback...\nPermission denied: hermes"))
    assert telegram.hermes_transport("hello") is False
    assert "Permission denied: hermes" in caplog.text


def test_ops_alerts_go_to_the_ops_chat_when_one_is_set(monkeypatch):
    sent = []
    monkeypatch.setenv("RADAR_OPS_CHAT_ID", "111")
    monkeypatch.setattr(telegram, "hermes_transport",
                        lambda text: (_ for _ in ()).throw(AssertionError("no hermes")))
    monkeypatch.setattr(telegram, "bot_api_transport",
                        lambda text, chat_id=None: sent.append(chat_id) or True)
    assert telegram.send_ops_message("❌ deploy failed") is True
    assert sent == ["111"]


def test_ops_alerts_use_the_normal_route_without_an_ops_chat(monkeypatch):
    monkeypatch.delenv("RADAR_OPS_CHAT_ID", raising=False)
    seen = []
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: seen.append(text) or True)
    assert telegram.send_ops_message("hello") is True and seen == ["hello"]
