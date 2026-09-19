"""Hermes plugin: Telegram search lands on the Today page, never a company list.

Slash `/run` `/search` `/today` never reach the LLM. Natural-language cues
("Start", "search now") are rewritten to those commands before dispatch.
If a search somehow still hits the model, tools are blocked and the reply
is replaced with the dashboard ping.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

_APP = os.environ.get("RADAR_APP") or "/opt/founder-radar/app"
_ROOT = os.environ.get("RADAR_ROOT") or "/opt/founder-radar"
_PENDING: dict[str, str] = {}
_BLOCK_MSG = (
    "Founder Radar search already started. Do not use tools. "
    "Reply with only the dashboard ping you were given."
)


def _load_intent():
    if _APP not in sys.path:
        sys.path.insert(0, _APP)
    from radar.notify.search_intent import classify, fund_from, slash_rewrite

    return classify, slash_rewrite, fund_from


def _event_text(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("text") or "")
    return str(getattr(event, "text", "") or "")


def _radar(args: list[str], timeout: int = 25) -> str:
    bin_path = os.environ.get("RADAR_BIN") or f"{_ROOT}/venv/bin/founder-radar"
    argv = [bin_path, *args]
    if os.environ.get("RADAR_TELEGRAM_NO_SUDO") != "1":
        env_file = shlex.quote(f"{_ROOT}/.env")
        hermes_env = shlex.quote(f"{_ROOT}/hermes.env")
        app = shlex.quote(_APP)
        inner = (
            "set -a; "
            f"[ -f {env_file} ] && . {env_file}; "
            f"[ -f {hermes_env} ] && . {hermes_env}; "
            "set +a; "
            f"cd {app} 2>/dev/null || true; "
            + " ".join(shlex.quote(part) for part in argv)
        )
        argv = ["sudo", "-n", "-u", "radar", "bash", "-lc", inner]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("founder-radar %s failed: %s", args[:1], type(exc).__name__)
        return ""
    out = (completed.stdout or "").strip()
    if completed.returncode != 0 and not out:
        err = (completed.stderr or "").strip().splitlines()
        logger.warning(
            "founder-radar %s exited %s: %s",
            args[:1],
            completed.returncode,
            err[-1] if err else "",
        )
    return out


def _ping() -> str:
    text = _radar(["today"])
    if text:
        return text
    url = (
        os.environ.get("RADAR_WEB_PUBLIC_URL")
        or os.environ.get("RADAR_WEB_DOMAIN")
        or ""
    ).strip()
    lines = [
        "📡 UK Founder Radar",
        "",
        "Today's companies are on the dashboard — not in this chat.",
    ]
    if url:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        lines.append(url.rstrip("/") + "/")
    else:
        lines.append("Open the Today page on the review site.")
    return "\n".join(lines)


def _kickoff(fund: str = "") -> str:
    args = ["search", "--background", "--send"]
    if fund:
        args.extend(["--fund", fund])
    text = _radar(args)
    return text or (
        _ping()
        + "\n\nScan started. I'll ping this chat again when it finishes."
    )


def _fund_from_args(raw_args: str) -> str:
    try:
        _, _, fund_from = _load_intent()
    except Exception:  # noqa: BLE001
        fund_from = None
    token = (raw_args or "").strip().split()
    key = token[0].lower() if token else ""
    if key in {"northstar", "dsw", "outward", "anticus"}:
        return key
    if fund_from is not None:
        found = fund_from(raw_args or "")
        if found:
            return found
    return ""


def _cmd_run(raw_args: str) -> str:
    return _kickoff(_fund_from_args(raw_args))


def _cmd_today(_raw_args: str) -> str:
    return _ping()


def _on_pre_gateway_dispatch(event=None, **_kwargs: Any):
    try:
        _, slash_rewrite, _ = _load_intent()
    except Exception:  # noqa: BLE001 - never break inbound dispatch
        logger.exception("search_intent import failed")
        return None
    rewritten = slash_rewrite(_event_text(event))
    if not rewritten:
        return None
    return {"action": "rewrite", "text": rewritten}


def _on_pre_llm_call(user_message: str = "", session_id: str = "", **_kwargs: Any):
    try:
        classify, _, fund_from = _load_intent()
    except Exception:  # noqa: BLE001
        return None
    kind = classify(user_message or "")
    key = session_id or "default"
    if not kind:
        _PENDING.pop(key, None)
        return None
    _PENDING[key] = kind
    if kind == "search":
        ack = _kickoff(fund_from(user_message or "") or "")
    else:
        ack = _ping()
    return {
        "context": (
            "Founder Radar already handled this turn. Reply with EXACTLY "
            "the following text and nothing else. Do not call tools. Do not "
            "list companies.\n\n"
            + ack
        )
    }


def _on_pre_tool_call(session_id: str = "", **_kwargs: Any):
    if (session_id or "default") not in _PENDING:
        return None
    return {"action": "block", "message": _BLOCK_MSG}


def _on_transform_llm_output(response_text: str = "", session_id: str = "", **_kwargs: Any):
    kind = _PENDING.pop(session_id or "default", None)
    if not kind:
        return None
    return _ping() or response_text


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
    ctx.register_command(
        "run",
        handler=_cmd_run,
        description="Scan now, then the Today dashboard URL (not a company list).",
        args_hint="[fund]",
    )
    ctx.register_command(
        "search",
        handler=_cmd_run,
        description="Same as /run: scan now, then the Today dashboard URL.",
        args_hint="[fund]",
    )
    ctx.register_command(
        "today",
        handler=_cmd_today,
        description="Today dashboard URL + counts. No company list.",
    )
