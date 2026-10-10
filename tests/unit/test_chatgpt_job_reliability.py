"""Offline-only job retries, mutation leases, and interruption contracts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from radar.chatgpt_actions import jobs, runner, server

REAL_RUN_ARGV = runner.run_argv

HEADERS = {"Authorization": "Bearer offline-test-token"}


@pytest.fixture(autouse=True)
def isolated_api(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_CHATGPT_API_KEY", "offline-test-token")
    monkeypatch.setenv("RADAR_CHATGPT_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("RADAR_ROOT", str(tmp_path))
    monkeypatch.setenv("RADAR_BIN", "mock-founder-radar")

    def refuse_real_cli(*args, **kwargs):
        pytest.fail("A reliability test attempted an unmocked CLI call")

    monkeypatch.setattr(jobs, "run_argv", refuse_real_cli)
    monkeypatch.setattr(runner, "run_argv", refuse_real_cli)


def request(path, body=None, *, key=None, method="POST"):
    headers = {**HEADERS, **({"Idempotency-Key": key} if key is not None else {})}
    status, raw, _ = server.handle_request(method, path, headers, body=body)
    return status, json.loads(raw)


def wait_job(job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, data = request(f"/v1/jobs/{job_id}", method="GET")
        assert status == 200
        job = data["job"]
        if job["status"] in jobs.TERMINAL_STATUSES:
            return job
        time.sleep(0.01)
    pytest.fail(f"Job {job_id} did not finish")


def complete_run(monkeypatch, *, code=0, stdout='{"run":{"run_id":"mock-run-1"}}'):
    calls = []

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        return runner.RunResult(list(argv), code, stdout, "mock-stderr" if code else "")

    monkeypatch.setattr(jobs, "run_argv", fake)
    return calls


@pytest.mark.parametrize(("code", "expected"), [(0, "done"), (1, "partial"), (2, "failed"), (124, "failed"), (127, "failed")])
def test_search_exit_status_and_parsed_result(monkeypatch, code, expected):
    run_status = {0: "ok", 1: "partial"}.get(code, "failed")
    calls = complete_run(monkeypatch, code=code,
                         stdout=f'{{"run":{{"run_id":"mock-run-1","status":"{run_status}"}}}}')
    status, accepted = request("/v1/jobs/search", {"no_llm": True}, key="scan:one")
    assert status == 202
    job = wait_job(accepted["job_id"])
    assert job["status"] == expected
    assert job["exit_code"] == code
    assert job["result"]["run"]["run_id"] == "mock-run-1"
    assert len(calls) == 1
    assert "--no-llm" in calls[0][0] and "--send" not in calls[0][0]


@pytest.mark.parametrize("stdout", ["", "Traceback (most recent call last):\n  ...\nOSError: boom",
                                    '{"run":{"run_id":"mock-run-1","status":"ok"}}'])
def test_search_exit_1_without_partial_summary_is_failed(monkeypatch, stdout):
    # An uncaught exception also exits 1; it must not be reported as partial.
    complete_run(monkeypatch, code=1, stdout=stdout)
    status, accepted = request("/v1/jobs/search", {"no_llm": True}, key="scan:crash")
    assert status == 202
    job = wait_job(accepted["job_id"])
    assert job["status"] == "failed"
    assert job["exit_code"] == 1


@pytest.mark.parametrize("path", ["/v1/jobs/search", "/v1/jobs/rescore", "/v1/jobs/publish"])
def test_completed_retry_does_not_execute_twice(monkeypatch, path):
    calls = complete_run(monkeypatch)
    first_status, first = request(path, {}, key="execution:one")
    wait_job(first["job_id"])
    second_status, second = request(path, {}, key="execution:one")
    assert first_status == 202 and second_status == 200
    assert first["reused"] is False and second["reused"] is True
    assert first["job_id"] == second["job_id"]
    assert len(calls) == 1


def test_normalized_alias_retry_and_cross_endpoint_conflict(monkeypatch):
    calls = complete_run(monkeypatch)
    _, first = request("/v1/jobs/search", {"fund": "DSW"}, key="same")
    wait_job(first["job_id"])
    assert request("/v1/jobs/search", {"fund_key": "dsw"}, key="same")[0] == 200
    for path, body in [("/v1/jobs/search", {"fund": "northstar"}), ("/v1/jobs/publish", {})]:
        status, data = request(path, body, key="same")
        assert status == 409
        assert data["error"] == "idempotency_conflict"
    assert len(calls) == 1


def test_concurrent_duplicate_submissions_launch_once(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        started.set()
        assert release.wait(5)
        return runner.RunResult(argv, 0, "{}", "")

    monkeypatch.setattr(jobs, "run_argv", fake)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: request("/v1/jobs/search", {}, key="concurrent"), range(8)))
        assert started.wait(5)
        assert all(status == 202 for status, _ in results)
        assert len({data["job_id"] for _, data in results}) == 1
        assert sum(not data["reused"] for _, data in results) == 1
        assert len(calls) == 1
    finally:
        release.set()
    wait_job(results[0][1]["job_id"])


@pytest.mark.parametrize("path,body", [
    ("/v1/jobs/search", {}), ("/v1/jobs/rescore", {}), ("/v1/jobs/publish", {}),
    ("/v1/publish", {}), ("/v1/publish-check", {}), ("/v1/today-qa", {}),
    ("/v1/decide", {"name": "Example", "verdict": "unsure"}),
])
def test_single_mutation_lease_covers_async_and_legacy_routes(path, body):
    with jobs.mutation_guard():
        status, data = request(path, body, key="busy-operation")
    assert status == 409
    assert data["error"] == "mutation_in_progress"
    assert list(jobs.jobs_dir().glob("*.json")) == []


def test_reads_remain_available_during_mutation(monkeypatch):
    monkeypatch.setattr(runner, "run_argv", lambda argv, **kw: runner.RunResult(argv, 0, "{}", ""))
    with jobs.mutation_guard():
        assert request("/v1/status", method="GET")[0] == 200


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, [], {}])
@pytest.mark.parametrize("path,field", [
    ("/v1/jobs/publish", "send"), ("/v1/publish", "send"),
    ("/v1/jobs/search", "no_llm"), ("/v1/jobs/rescore", "all"),
    ("/v1/jobs/rescore", "all_companies"),
])
def test_mutating_boolean_fields_are_strict(path, field, bad):
    status, data = request(path, {field: bad}, key="invalid")
    assert status == 400
    assert "boolean" in data["error"]
    assert not list(jobs.jobs_dir().glob("*.json"))


@pytest.mark.parametrize("body", [{"all": False, "all_companies": True}, {"all": True, "all_companies": False}])
def test_conflicting_rescore_aliases_rejected(body):
    assert request("/v1/jobs/rescore", body)[0] == 400


@pytest.mark.parametrize("send", [False, True])
def test_publish_only_calls_existing_cli_gate(monkeypatch, send):
    calls = complete_run(monkeypatch, code=2, stdout="Gate BLOCK")
    _, data = request("/v1/jobs/publish", {"send": send}, key="publish:gate")
    job = wait_job(data["job_id"])
    assert calls[0][0] == ["mock-founder-radar", "--json", "publish"] + (["--send"] if send else [])
    assert job["status"] == "failed" and job["exit_code"] == 2
    assert "result" not in job
    assert job["meta"]["send"] is send
    assert request("/v1/jobs/publish", {"send": send}, key="publish:gate")[0] == 200
    assert len(calls) == 1


def test_publish_defaults_to_no_send_and_rejects_bypass(monkeypatch):
    calls = complete_run(monkeypatch)
    _, data = request("/v1/jobs/publish", {}, key="publish:default")
    assert wait_job(data["job_id"])["status"] == "done"
    assert "--send" not in calls[0][0]
    for field in ("skip_today_qa", "no_hermes", "no_heal", "argv", "shell"):
        assert request("/v1/jobs/publish", {field: True}, key=f"bad:{field}")[0] == 400
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["queued", "running"])
def test_restart_marks_orphan_interrupted_without_replay(monkeypatch, state):
    calls = complete_run(monkeypatch)
    _, accepted = request("/v1/jobs/publish", {}, key="restart:publish")
    wait_job(accepted["job_id"])
    job = jobs.load_job(accepted["job_id"])
    job.status, job.exit_code, job.stdout = state, None, ""
    jobs.save_job(job)
    jobs.recover_interrupted_jobs()
    status, replay = request("/v1/jobs/publish", {}, key="restart:publish")
    assert status == 200 and replay["status"] == "interrupted" and replay["reused"] is True
    public = wait_job(job.id)
    assert public["exit_code"] is None
    assert "will not be replayed" in public["error"]
    assert len(calls) == 1


def test_inherited_lease_prevents_false_restart_recovery():
    with jobs._admission_lock():
        fd = jobs._acquire_mutation()
        orphan = jobs.create_job("publish", runner.argv_publish())
        orphan.status = "running"
        jobs.save_job(orphan)
        inherited_fd = os.dup(fd)
        os.close(fd)
    try:
        jobs.recover_interrupted_jobs()
        assert jobs.load_job(orphan.id).status == "running"
        assert request("/v1/jobs/search", {}, key="blocked")[0] == 409
    finally:
        os.close(inherited_fd)
    jobs.recover_interrupted_jobs()
    assert jobs.load_job(orphan.id).status == "interrupted"


def test_worker_exception_is_interrupted_not_retried(monkeypatch):
    calls = []

    def broken(argv, **kwargs):
        calls.append(argv)
        raise RuntimeError("unknown outcome")

    monkeypatch.setattr(jobs, "run_argv", broken)
    _, data = request("/v1/jobs/publish", {}, key="broken")
    assert wait_job(data["job_id"])["status"] == "interrupted"
    assert request("/v1/jobs/publish", {}, key="broken")[0] == 200
    assert len(calls) == 1


def test_persistence_failure_never_launches(monkeypatch):
    calls = complete_run(monkeypatch)
    monkeypatch.setattr(jobs, "save_job", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    assert request("/v1/jobs/search", {}, key="disk-full")[0] == 500
    assert calls == []
    # The unsuccessful admission must release its execution lease.
    with jobs.mutation_guard():
        pass


def test_corrupt_existing_record_fails_closed(monkeypatch):
    calls = complete_run(monkeypatch)
    _, data = request("/v1/jobs/publish", {}, key="corrupt")
    wait_job(data["job_id"])
    jobs._job_path(data["job_id"]).write_text("broken json")
    status, retry = request("/v1/jobs/publish", {}, key="corrupt")
    assert status == 503 and retry["error"] == "job_store_unavailable"
    assert len(calls) == 1
    assert request("/v1/jobs/search", {}, key="new-operation")[0] == 503


@pytest.mark.parametrize("key", ["", "x" * 129, "spaces are invalid", "../oops", "é"])
def test_invalid_idempotency_keys_rejected(key):
    assert request("/v1/jobs/search", {}, key=key)[0] == 400


def test_async_publish_requires_bearer_auth():
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        status, _, _ = server.handle_request("POST", "/v1/jobs/publish", headers, body={})
        assert status == 401


def test_runner_uses_argv_no_shell_and_passes_lease(monkeypatch):
    captured = {}

    class MockProcess:
        returncode = 0

        def __init__(self, argv, **kwargs):
            captured.update(argv=argv, **kwargs)

        def communicate(self, timeout):
            captured["timeout"] = timeout
            return "{}", ""

    monkeypatch.setattr(runner.subprocess, "Popen", MockProcess)
    with jobs.mutation_guard() as fd:
        result = REAL_RUN_ARGV(runner.argv_publish(), pass_fds=(fd,))
        assert captured["pass_fds"] == (fd,)
    assert captured["shell"] is False and captured["start_new_session"] is True
    assert isinstance(captured["argv"], list)
    assert result.exit_code == 0


def test_timeout_kills_whole_process_group_before_returning(monkeypatch):
    events = []

    class MockProcess:
        pid = 987654

        def __init__(self, argv, **kwargs):
            assert kwargs["start_new_session"] is True

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            if len(events) == 1:
                raise subprocess.TimeoutExpired("mock-cli", timeout, output=b"partial")
            return "partial", "child diagnostics"

    monkeypatch.setattr(runner.subprocess, "Popen", MockProcess)
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: events.append(("killpg", pid, sig)))
    result = REAL_RUN_ARGV(runner.argv_publish(), timeout=0.01)
    assert events == [("communicate", 0.01), ("killpg", 987654, signal.SIGKILL), ("communicate", 5.0)]
    assert result.exit_code == 124 and result.stdout == "partial"
    assert "child diagnostics" in result.stderr and "process group" in result.stderr


def test_missing_executable_returns_127(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("mock missing")))
    result = REAL_RUN_ARGV(runner.argv_publish())
    assert result.exit_code == 127 and "failed to exec" in result.stderr


def test_real_mock_child_inherits_lease_until_exit():
    """Only a harmless Python mock child, never founder-radar or network."""
    import sys

    with jobs._admission_lock():
        fd = jobs._acquire_mutation()
        job = jobs.create_job("publish", runner.argv_publish())
        job.status = "running"
        jobs.save_job(job)
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        os.close(fd)
    try:
        jobs.recover_interrupted_jobs()
        assert jobs.load_job(job.id).status == "running"
        status, busy = request("/v1/jobs/search", {}, key="while-child-lives")
        assert status == 409 and busy["job_id"] == job.id
    finally:
        child.communicate("finish", timeout=5)
    jobs.recover_interrupted_jobs()
    assert jobs.load_job(job.id).status == "interrupted"


def test_direct_sync_helper_clears_recovered_error_on_success(monkeypatch):
    complete_run(monkeypatch)
    job = jobs.create_job("search", runner.argv_search())
    finished = jobs.run_job_sync(job)
    assert finished.status == "done" and finished.error is None
    assert jobs.load_job(job.id).error is None
