"""Allowlisted founder-radar argv builders and sync runner.

ChatGPT never gets an open shell — every call maps to a fixed argv family.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from radar.render.digest import review_url

ALLOWED_FUNDS = frozenset({"northstar", "dsw", "outward", "anticus"})
ALLOWED_VERDICTS = frozenset({"worth contacting", "not for me", "unsure"})

# Commands that must never be reachable via this service (defense in depth).
BLOCKED_SUBCOMMANDS = frozenset({
    "forget",
    "db",
    "run",  # daily systemd path only
})


class AllowlistError(ValueError):
    """Request parameters are outside the allowlist."""


@dataclass(frozen=True)
class RunResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str

    def as_payload(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": self.exit_code == 0,
            "exit_code": self.exit_code,
            "argv": list(self.argv),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "dashboard_url": review_url() or None,
        }
        parsed = _try_parse_json(self.stdout)
        if parsed is not None:
            body["result"] = parsed
        if extra:
            body.update(extra)
        return body


def radar_bin() -> str:
    env = (os.environ.get("RADAR_BIN") or "").strip()
    if env:
        return env
    wrap = "/usr/local/bin/founder-radar"
    if os.path.isfile(wrap) and os.access(wrap, os.X_OK):
        return wrap
    root = (os.environ.get("RADAR_ROOT") or "/opt/founder-radar").rstrip("/")
    venv = f"{root}/venv/bin/founder-radar"
    if os.path.isfile(venv):
        return venv
    found = shutil.which("founder-radar")
    if found:
        return found
    return "founder-radar"


def _base(*, as_json: bool = True) -> list[str]:
    argv = [radar_bin()]
    if as_json:
        argv.append("--json")
    return argv


def _guard_subcommand(argv: list[str]) -> list[str]:
    # After optional --json / --db flags, first non-flag token is the command.
    for part in argv[1:]:
        if part.startswith("-"):
            continue
        if part in BLOCKED_SUBCOMMANDS:
            raise AllowlistError(f"subcommand not allowed: {part}")
        break
    return argv


def argv_today() -> list[str]:
    return _guard_subcommand(_base() + ["today"])


def argv_status() -> list[str]:
    return _guard_subcommand(_base() + ["status"])


def argv_doctor() -> list[str]:
    return _guard_subcommand(_base() + ["doctor"])


def argv_why_today() -> list[str]:
    return _guard_subcommand(_base() + ["why-today"])


def argv_show(name: str) -> list[str]:
    cleaned = unquote((name or "").strip())
    if not cleaned or len(cleaned) > 200:
        raise AllowlistError("company name required (max 200 chars)")
    return _guard_subcommand(_base() + ["show", cleaned])


def argv_fund(fund_key: str, *, top: int = 10) -> list[str]:
    key = (fund_key or "").strip().lower()
    if key not in ALLOWED_FUNDS:
        raise AllowlistError(
            f"fund must be one of: {', '.join(sorted(ALLOWED_FUNDS))}"
        )
    if top < 1 or top > 50:
        raise AllowlistError("top must be 1..50")
    return _guard_subcommand(_base() + ["fund", key, "--top", str(top)])


def argv_decide(name: str, verdict: str) -> list[str]:
    cleaned = (name or "").strip()
    v = (verdict or "").strip().lower()
    if not cleaned or len(cleaned) > 200:
        raise AllowlistError("name required (max 200 chars)")
    if v not in ALLOWED_VERDICTS:
        raise AllowlistError(
            "verdict must be one of: worth contacting | not for me | unsure"
        )
    return _guard_subcommand(
        _base() + ["decide", cleaned, "--verdict", v]
    )


def argv_publish_check() -> list[str]:
    return _guard_subcommand(_base() + ["publish-check"])


def argv_publish(*, send: bool = False) -> list[str]:
    if type(send) is not bool:
        raise AllowlistError("send must be a JSON boolean")
    argv = _base() + ["publish"]
    if send:
        argv.append("--send")
    return _guard_subcommand(argv)


def argv_today_qa() -> list[str]:
    return _guard_subcommand(_base() + ["today-qa"])


def argv_search(
    *,
    fund_key: str | None = None,
    source_key: str | None = None,
    since: str | None = None,
    no_llm: bool = False,
) -> list[str]:
    """Build search argv. Never adds ``--send`` (publish is the only Telegram path)."""
    if type(no_llm) is not bool:
        raise AllowlistError("no_llm must be a JSON boolean")
    argv = _base() + ["search"]
    if fund_key:
        key = fund_key.strip().lower()
        if key not in ALLOWED_FUNDS:
            raise AllowlistError(
                f"fund must be one of: {', '.join(sorted(ALLOWED_FUNDS))}"
            )
        argv.extend(["--fund", key])
    if source_key:
        src = source_key.strip()
        if not src or len(src) > 64 or any(c in src for c in " \t\n;/|&"):
            raise AllowlistError("invalid source_key")
        argv.extend(["--source", src])
    if since:
        s = since.strip()
        if not s or len(s) > 32 or any(c in s for c in " \t\n;/|&"):
            raise AllowlistError("invalid since")
        argv.extend(["--since", s])
    if no_llm:
        argv.append("--no-llm")
    return _guard_subcommand(argv)


def argv_rescore(*, all_companies: bool = False) -> list[str]:
    if type(all_companies) is not bool:
        raise AllowlistError("all_companies must be a JSON boolean")
    argv = _base() + ["rescore"]
    if all_companies:
        argv.append("--all")
    return _guard_subcommand(argv)


def run_argv(
    argv: list[str],
    *,
    timeout: float | None = 120.0,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> RunResult:
    """Run an allowlisted argv synchronously; never invent scores."""
    _guard_subcommand(argv)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=merged, stdin=subprocess.DEVNULL, shell=False, pass_fds=pass_fds,
            # The CLI can spawn Hermes. A timeout must stop its whole process
            # group before the API lets another mutation start.
            start_new_session=True,
        )
    except OSError as exc:
        return RunResult(list(argv), 127, "", f"failed to exec: {exc}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        cleanup = "timeout; CLI process group killed; review side effects before retrying"
        try:
            stdout, stderr = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            # A deliberately detached descendant can retain an output pipe.
            # Do not wait forever; the allowlisted CLI must not daemonize.
            stdout, stderr = _output_text(exc.stdout), _output_text(exc.stderr)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass  # A surviving CLI still holds its inherited lease.
            cleanup += "; cleanup incomplete, operator review required"
        return RunResult(list(argv), 124, stdout or "", f"{stderr or ''}\n{cleanup}".strip())
    return RunResult(list(argv), int(proc.returncode), stdout or "", stderr or "")


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _try_parse_json(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw or raw[0] not in "{[":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
