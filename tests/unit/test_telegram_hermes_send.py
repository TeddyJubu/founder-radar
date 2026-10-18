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
