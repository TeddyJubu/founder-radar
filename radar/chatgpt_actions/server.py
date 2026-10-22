"""Loopback HTTP server for ChatGPT Custom GPT Actions."""

from __future__ import annotations

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from radar.chatgpt_actions import auth
from radar.chatgpt_actions import jobs as jobstore
from radar.chatgpt_actions import runner
from radar.render.digest import review_url

log = logging.getLogger(__name__)

DEFAULT_PORT = 8790
DEFAULT_HOST = "127.0.0.1"

_COMPANY_RE = re.compile(r"^/v1/companies/(.+)$")
_FUND_RE = re.compile(r"^/v1/funds/([a-zA-Z0-9_-]+)$")
_JOB_RE = re.compile(r"^/v1/jobs/([a-f0-9]{32})$")


def listen_port() -> int:
    raw = (os.environ.get("RADAR_CHATGPT_ACTIONS_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid RADAR_CHATGPT_ACTIONS_PORT: {raw!r}") from exc
    if not (1 <= port <= 65535):
        raise SystemExit(f"RADAR_CHATGPT_ACTIONS_PORT out of range: {port}")
    return port


def _json_bytes(payload: Any, *, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, indent=2, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _read_json_body(handler: BaseHTTPRequestHandler, *, max_bytes: int = 64_000) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > max_bytes:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _dashboard_hint() -> dict[str, Any]:
    url = review_url() or None
    return {
        "dashboard_url": url,
        "hint": (
            "Open the Today dashboard for the company list. "
            "Do not invent Fit/Edge scores — only report CLI output."
        ),
    }


def handle_request(
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
) -> tuple[int, bytes, str]:
    """Pure request router — used by the HTTP handler and unit tests."""
    method = method.upper()
    path = path.split("?", 1)[0]
    body = body or {}
    query = query or {}

    if method == "GET" and path == "/health":
        return _json_bytes({"ok": True, "service": "chatgpt-actions"})

    # Everything under /v1 requires Bearer auth.
    if path.startswith("/v1"):
        try:
            auth.check_authorization(headers)
        except auth.AuthError as exc:
            return _json_bytes({"ok": False, "error": str(exc)}, status=exc.status)

    try:
        return _route(method, path, body=body, query=query, headers=headers)
    except jobstore.IdempotencyConflict as exc:
        return _json_bytes({"ok": False, "error": "idempotency_conflict", "message": str(exc)}, status=409)
    except jobstore.MutationBusy as exc:
        payload = {"ok": False, "error": "mutation_in_progress", "message": str(exc)}
        if exc.job_id:
            payload.update(job_id=exc.job_id, poll=f"/v1/jobs/{exc.job_id}")
        return _json_bytes(payload, status=409)
    except jobstore.JobStoreError as exc:
        return _json_bytes({"ok": False, "error": "job_store_unavailable", "message": str(exc)}, status=503)
    except runner.AllowlistError as exc:
        return _json_bytes({"ok": False, "error": str(exc)}, status=400)
    except ValueError as exc:
        return _json_bytes({"ok": False, "error": str(exc)}, status=400)
    except Exception:  # noqa: BLE001
        log.exception("chatgpt actions handler error")
        return _json_bytes({"ok": False, "error": "internal error"}, status=500)


def _boolean(body: dict[str, Any], name: str, *, alias: str | None = None) -> bool:
    names = [name, alias] if alias else [name]
    for key in names:
        if key in body and type(body[key]) is not bool:
            raise runner.AllowlistError(f"{key} must be a JSON boolean")
    if alias and name in body and alias in body and body[name] != body[alias]:
        raise runner.AllowlistError(f"{name} and {alias} must agree")
    return body.get(name, body.get(alias, False)) if alias else body.get(name, False)


def _text(body: dict[str, Any], name: str, *, alias: str | None = None) -> str | None:
    names = [name, alias] if alias else [name]
    for key in names:
        if key in body and body[key] is not None and not isinstance(body[key], str):
            raise runner.AllowlistError(f"{key} must be a string")
    if alias and name in body and alias in body and body[name] != body[alias]:
        raise runner.AllowlistError(f"{name} and {alias} must agree")
    return body.get(name, body.get(alias)) if alias else body.get(name)


def _fields(body: dict[str, Any], allowed: set[str]) -> None:
    extra = set(body) - allowed
    if extra:
        raise runner.AllowlistError(f"unknown fields: {', '.join(sorted(extra))}")


def _run_mutation(argv: list[str], *, timeout: float = 120.0) -> runner.RunResult:
    with jobstore.mutation_guard() as fd:
        return runner.run_argv(argv, timeout=timeout, pass_fds=(fd,))


def _submit_job(command: str, argv: list[str], headers: dict[str, str], *,
                meta: dict[str, Any], timeout: float = 3600.0) -> tuple[int, bytes, str]:
    key = next((value for name, value in headers.items() if name.lower() == "idempotency-key"), None)
    job, reused = jobstore.submit_job(command, argv, idempotency_key=key, meta=meta, timeout=timeout)
    return _json_bytes({
        "ok": True, "accepted": True, "job_id": job.id, "status": job.status,
        "reused": reused, "poll": f"/v1/jobs/{job.id}", **_dashboard_hint(),
    }, status=200 if reused and job.status in jobstore.TERMINAL_STATUSES else 202)


def _route(
    method: str,
    path: str,
    *,
    body: dict[str, Any],
    query: dict[str, list[str]],
    headers: dict[str, str],
) -> tuple[int, bytes, str]:
    if method == "GET" and path == "/v1/today":
        result = runner.run_argv(runner.argv_today())
        payload = result.as_payload(extra=_dashboard_hint())
        return _json_bytes(payload, status=200 if result.exit_code == 0 else 502)

    if method == "GET" and path == "/v1/status":
        result = runner.run_argv(runner.argv_status())
        return _json_bytes(result.as_payload(), status=200 if result.exit_code == 0 else 502)

    if method == "GET" and path == "/v1/doctor":
        result = runner.run_argv(runner.argv_doctor(), timeout=180.0)
        return _json_bytes(result.as_payload(), status=200 if result.exit_code == 0 else 502)

    if method == "GET" and path == "/v1/why-today":
        result = runner.run_argv(runner.argv_why_today())
        # exit 1 (partial) is still useful diagnosis
        return _json_bytes(
            result.as_payload(extra=_dashboard_hint()),
            status=200 if result.exit_code in (0, 1) else 502,
        )

    m = _COMPANY_RE.match(path)
    if m and method == "GET":
        result = runner.run_argv(runner.argv_show(m.group(1)))
        return _json_bytes(result.as_payload(), status=200 if result.exit_code == 0 else 502)

    m = _FUND_RE.match(path)
    if m and method == "GET":
        top_raw = (query.get("top") or ["10"])[0]
        try:
            top = int(top_raw)
        except ValueError as exc:
            raise runner.AllowlistError("top must be an integer") from exc
        result = runner.run_argv(runner.argv_fund(m.group(1), top=top))
        return _json_bytes(
            result.as_payload(extra=_dashboard_hint()),
            status=200 if result.exit_code == 0 else 502,
        )

    if method == "POST" and path == "/v1/decide":
        result = _run_mutation(
            runner.argv_decide(str(body.get("name") or ""), str(body.get("verdict") or ""))
        )
        # exit 1 = ambiguous name — still return body
        return _json_bytes(
            result.as_payload(),
            status=200 if result.exit_code in (0, 1) else 502,
        )

    if method == "POST" and path == "/v1/publish-check":
        result = _run_mutation(runner.argv_publish_check(), timeout=300.0)
        return _json_bytes(
            result.as_payload(),
            status=200 if result.exit_code in (0, 2) else 502,
        )

    if method == "POST" and path == "/v1/publish":
        _fields(body, {"send"})
        send = _boolean(body, "send")
        result = _run_mutation(runner.argv_publish(send=send), timeout=600.0)
        return _json_bytes(
            result.as_payload(extra=_dashboard_hint()),
            status=200 if result.exit_code == 0 else 502,
        )

    if method == "POST" and path == "/v1/today-qa":
        result = _run_mutation(runner.argv_today_qa(), timeout=600.0)
        return _json_bytes(result.as_payload(), status=200 if result.exit_code == 0 else 502)

    if method == "POST" and path == "/v1/jobs/search":
        _fields(body, {"fund", "fund_key", "source", "source_key", "since", "no_llm"})
        argv = runner.argv_search(
            fund_key=_text(body, "fund", alias="fund_key"),
            source_key=_text(body, "source", alias="source_key"),
            since=_text(body, "since"), no_llm=_boolean(body, "no_llm"),
        )
        return _submit_job("search", argv, headers, meta={"kind": "search"})

    if method == "POST" and path == "/v1/jobs/rescore":
        _fields(body, {"all", "all_companies"})
        all_companies = _boolean(body, "all", alias="all_companies")
        return _submit_job("rescore", runner.argv_rescore(all_companies=all_companies), headers,
                           meta={"kind": "rescore", "all": all_companies})

    if method == "POST" and path == "/v1/jobs/publish":
        _fields(body, {"send"})
        send = _boolean(body, "send")
        # Only the existing CLI runs the publish gate and Today QA. There is
        # deliberately no API flag to skip a gate, heal, or replace a verdict.
        return _submit_job("publish", runner.argv_publish(send=send), headers,
                           meta={"kind": "publish", "send": send}, timeout=600.0)

    m = _JOB_RE.match(path)
    if m and method == "GET":
        jobstore.recover_interrupted_jobs()
        job = jobstore.load_job(m.group(1))
        if job is None:
            return _json_bytes({"ok": False, "error": "job not found"}, status=404)
        return _json_bytes({"ok": True, "job": job.to_public()})

    # Explicit denials for dangerous-looking paths (clearer than bare 404).
    denied = (
        "/v1/forget",
        "/v1/db",
        "/v1/db/restore",
        "/v1/run",
        "/v1/shell",
        "/v1/deploy",
    )
    if path in denied or path.startswith("/v1/db/") or path.startswith("/v1/forget"):
        return _json_bytes(
            {"ok": False, "error": "endpoint not available (out of scope)"},
            status=404,
        )

    if path.startswith("/v1"):
        if method not in ("GET", "POST", "HEAD"):
            return _json_bytes({"ok": False, "error": "method not allowed"}, status=405)
        return _json_bytes({"ok": False, "error": "not found"}, status=404)

    return _json_bytes({"ok": False, "error": "not found"}, status=404)


class ActionsHandler(BaseHTTPRequestHandler):
    server_version = "FounderRadarChatGPTActions/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        headers = {k: v for k, v in self.headers.items()}
        body: dict[str, Any] = {}
        if method in ("POST", "PUT", "PATCH"):
            try:
                body = _read_json_body(self)
            except (ValueError, json.JSONDecodeError) as exc:
                status, payload, ctype = _json_bytes(
                    {"ok": False, "error": str(exc)}, status=400
                )
                self._write(status, payload, ctype)
                return
        status, payload, ctype = handle_request(
            method, parsed.path, headers, body=body, query=query
        )
        self._write(status, payload, ctype)

    def _write(self, status: int, payload: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        self.end_headers()


def make_server(
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    auth.require_api_key_configured()
    jobstore.recover_interrupted_jobs()
    bind_host = host or DEFAULT_HOST
    bind_port = DEFAULT_PORT if port is None else port
    return ThreadingHTTPServer((bind_host, bind_port), ActionsHandler)


def serve_forever(host: str | None = None, port: int | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = make_server(host=host, port=port if port is not None else listen_port())
    host_s, port_s = server.server_address[:2]
    log.info("ChatGPT Actions listening on http://%s:%s", host_s, port_s)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
