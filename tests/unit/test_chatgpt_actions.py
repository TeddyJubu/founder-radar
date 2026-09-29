"""Unit tests for ChatGPT Actions auth, allowlist, and job polling."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from radar.chatgpt_actions import auth, jobs, runner, server


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("RADAR_CHATGPT_API_KEY", "test-secret-key-32chars-xxxxxx")
    monkeypatch.setenv("RADAR_WEB_DOMAIN", "radar.example.test")
    monkeypatch.setenv("RADAR_BIN", "founder-radar")


def test_auth_fail_closed_when_key_unset(monkeypatch):
    monkeypatch.delenv("RADAR_CHATGPT_API_KEY", raising=False)
    with pytest.raises(auth.AuthError) as exc:
        auth.check_authorization({"Authorization": "Bearer anything"})
    assert exc.value.status == 503


def test_auth_rejects_missing_and_wrong_bearer():
    with pytest.raises(auth.AuthError):
        auth.check_authorization({})
    with pytest.raises(auth.AuthError):
        auth.check_authorization({"Authorization": "Basic nope"})
    with pytest.raises(auth.AuthError):
        auth.check_authorization({"Authorization": "Bearer wrong"})
    auth.check_authorization(
        {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    )


def test_auth_constant_time_accepts_case_insensitive_header():
    auth.check_authorization(
        {"authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    )


def test_require_api_key_configured_exits(monkeypatch):
    monkeypatch.delenv("RADAR_CHATGPT_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        auth.require_api_key_configured()


def test_health_no_auth():
    status, body, _ = server.handle_request("GET", "/health", {})
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_v1_requires_auth():
    status, body, _ = server.handle_request("GET", "/v1/status", {})
    assert status == 401
    assert json.loads(body)["ok"] is False


def test_allowlist_blocks_dangerous_argv():
    with pytest.raises(runner.AllowlistError):
        runner._guard_subcommand(["founder-radar", "forget", "Alice"])
    with pytest.raises(runner.AllowlistError):
        runner._guard_subcommand(["founder-radar", "--json", "db", "restore"])
    with pytest.raises(runner.AllowlistError):
        runner._guard_subcommand(["founder-radar", "run"])


def test_argv_builders_are_fixed_shapes():
    assert runner.argv_today()[-1] == "today"
    assert "--json" in runner.argv_today()
    assert runner.argv_decide("Acme", "worth contacting")[-3:] == [
        "decide",
        "Acme",
        "--verdict",
    ] or runner.argv_decide("Acme", "worth contacting")[-4:] == [
        "decide",
        "Acme",
        "--verdict",
        "worth contacting",
    ]
    decide = runner.argv_decide("Acme Ltd", "not for me")
    assert decide[decide.index("decide") + 1] == "Acme Ltd"
    assert decide[decide.index("--verdict") + 1] == "not for me"

    search = runner.argv_search(fund_key="northstar")
    assert "search" in search
    assert "--send" not in search
    assert "--fund" in search

    with pytest.raises(runner.AllowlistError):
        runner.argv_fund("madeup")
    with pytest.raises(runner.AllowlistError):
        runner.argv_decide("X", "maybe later")

    pub = runner.argv_publish(send=True)
    assert "--send" in pub
    assert runner.argv_publish(send=False).count("--send") == 0


def test_dangerous_http_paths_404():
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    for path in ("/v1/forget", "/v1/db/restore", "/v1/run", "/v1/shell", "/v1/deploy"):
        status, body, _ = server.handle_request("POST", path, headers, body={})
        assert status == 404, path
        assert "out of scope" in json.loads(body)["error"]


def test_unknown_path_404_and_wrong_method():
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    status, _, _ = server.handle_request("GET", "/v1/nope", headers)
    assert status == 404
    status, _, _ = server.handle_request("DELETE", "/v1/today", headers)
    assert status == 405


def test_sync_route_runs_allowlisted_argv(monkeypatch):
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return runner.RunResult(
            argv=list(argv),
            exit_code=0,
            stdout='{"status": "ok"}',
            stderr="",
        )

    monkeypatch.setattr(runner, "run_argv", fake_run)
    status, body, _ = server.handle_request("GET", "/v1/status", headers)
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["result"]["status"] == "ok"
    assert captured["argv"][-1] == "status"
    assert "--json" in captured["argv"]


def test_today_includes_dashboard_hint(monkeypatch):
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}

    def fake_run(argv, **kwargs):
        return runner.RunResult(
            argv=list(argv),
            exit_code=0,
            stdout="Today's companies are on the dashboard\nhttps://radar.example.test/",
            stderr="",
        )

    monkeypatch.setattr(runner, "run_argv", fake_run)
    status, body, _ = server.handle_request("GET", "/v1/today", headers)
    assert status == 200
    payload = json.loads(body)
    assert payload["dashboard_url"] == "https://radar.example.test/"
    assert "invent" in payload["hint"].lower() or "scores" in payload["hint"].lower()


def test_job_state_machine(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RADAR_CHATGPT_JOBS_DIR", str(tmp_path / "jobs"))
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}

    def fake_run(argv, **kwargs):
        time.sleep(0.05)
        return runner.RunResult(
            argv=list(argv),
            exit_code=0,
            stdout='{"shortlisted": 3}',
            stderr="",
        )

    monkeypatch.setattr(jobs, "run_argv", fake_run)

    status, body, _ = server.handle_request(
        "POST", "/v1/jobs/search", headers, body={"fund": "dsw"}
    )
    assert status == 202
    accepted = json.loads(body)
    job_id = accepted["job_id"]
    assert accepted["dashboard_url"] == "https://radar.example.test/"
    assert accepted["poll"] == f"/v1/jobs/{job_id}"

    # Poll until done (daemon thread).
    deadline = time.time() + 5
    final = None
    while time.time() < deadline:
        st, raw, _ = server.handle_request("GET", f"/v1/jobs/{job_id}", headers)
        assert st == 200
        final = json.loads(raw)["job"]
        if final["status"] in ("done", "failed"):
            break
        time.sleep(0.05)

    assert final is not None
    assert final["status"] == "done"
    assert final["exit_code"] == 0
    assert "shortlisted" in final["stdout"]
    assert "--send" not in " ".join(
        json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text())["argv"]
    )


def test_rescore_job_respects_all_flag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RADAR_CHATGPT_JOBS_DIR", str(tmp_path / "jobs"))
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    seen = {}

    def fake_runner(job):
        seen["argv"] = list(job.argv)
        job.status = "done"
        job.exit_code = 0
        job.stdout = "{}"
        jobs.save_job(job)
        return job

    monkeypatch.setattr(jobs, "spawn_job", lambda job, **k: fake_runner(job) or job)

    status, body, _ = server.handle_request(
        "POST", "/v1/jobs/rescore", headers, body={"all": True}
    )
    assert status == 202
    assert "--all" in seen["argv"]
    assert json.loads(body)["job_id"]


def test_job_not_found():
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    status, _, _ = server.handle_request(
        "GET", "/v1/jobs/" + ("a" * 32), headers
    )
    assert status == 404


def test_decide_body_validation():
    headers = {"Authorization": "Bearer test-secret-key-32chars-xxxxxx"}
    status, body, _ = server.handle_request(
        "POST", "/v1/decide", headers, body={"name": "X", "verdict": "garbage"}
    )
    assert status == 400
    assert "verdict" in json.loads(body)["error"]


def test_deploy_artifacts_exist():
    root = Path(__file__).resolve().parents[2]
    assert (root / "deploy" / "founder-radar-chatgpt-actions.service").is_file()
    assert (root / "deploy" / "chatgpt-actions.caddy").is_file()
    assert (root / "deploy" / "chatgpt-actions.openapi.yaml").is_file()
    assert (root / "docs" / "chatgpt-actions.md").is_file()
    caddy = (root / "deploy" / "Caddyfile").read_text()
    assert "import /etc/caddy/chatgpt-actions.caddy" in caddy
    fragment = (root / "deploy" / "chatgpt-actions.caddy").read_text()
    # Comment may mention basic_auth; the site block must not enable it.
    assert "\n\tbasic_auth" not in fragment and "\n  basic_auth" not in fragment
    assert "127.0.0.1:8790" in fragment
    installer = (root / "deploy" / "install.sh").read_text()
    assert "founder-radar-chatgpt-actions.service" in installer
    assert "chatgpt-actions.caddy" in installer
