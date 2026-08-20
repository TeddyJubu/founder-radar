"""Telegram delivery and command routing.

Two jobs, deliberately kept apart:

**Delivery** — `send_message(text)` tries `hermes send` and falls back to a
direct Bot API call. 02-architecture §7 lists "Hermes gateway down" as a
survivable failure: *the digest goes out, only chat commands stop working*.
That promise is only true if the fallback is unconditional, so the fallback
fires on a missing binary, a non-zero exit, a timeout and an exception alike.
`test_digest_delivered_when_hermes_is_down` is the test that holds it.

**Routing** — the Telegram commands from 07-interfaces §2 mapped to CLI argv, plus
the `TELEGRAM_ALLOWED_USERS` check. `route()` and `handle()` are pure: they
return a plan, they never execute one unless a runner is injected. That is what
lets the allow-list test assert `ran_pipeline is False` without a subprocess.

Both transports are injectable, because the test suite blocks every non-loopback
socket and a delivery test that needs a real bot token is not a test.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
HERMES_TIMEOUT_S = 30
BOT_API_TIMEOUT_S = 20

# Telegram hard-caps a message at 4096 characters. A digest that silently
# vanishes because it was one company too long is exactly the failure this
# system exists to avoid, so long messages are split rather than truncated.
MAX_MESSAGE_CHARS = 4000


class DeliveryError(RuntimeError):
    """Neither Hermes nor the Bot API could deliver. Callers decide what that means."""


# --------------------------------------------------------------- transports


def hermes_transport(text: str) -> bool:
    """`hermes send --to telegram <text>`. False for every kind of failure."""
    import shutil

    binary = shutil.which("hermes")
    if binary is None:
        log.debug("hermes binary not on PATH — using the direct Bot API")
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, "send", "--to", "telegram", text],
            capture_output=True,
            timeout=HERMES_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("hermes send failed (%s) — falling back to the Bot API", type(exc).__name__)
        return False
    if completed.returncode != 0:
        log.warning("hermes send exited %s — falling back to the Bot API", completed.returncode)
        return False
    return True


def bot_api_transport(text: str) -> bool:
    """POST sendMessage directly. The path that must work when Hermes is dead."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # Never log the token, and never log what we were about to send.
        log.error("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — cannot deliver")
        return False

    import httpx  # imported here: the digest path must not pay for it

    try:
        response = httpx.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=BOT_API_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - any transport error is just "failed"
        log.error("Telegram Bot API call failed: %s", type(exc).__name__)
        return False
    if response.status_code >= 400:
        log.error("Telegram Bot API returned %s", response.status_code)
        return False
    return True


# ----------------------------------------------------------------- delivery


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on blank lines, then lines, then hard characters. Never truncates."""
    text = str(text)
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return chunks


def send_message(
    text: str,
    *,
    hermes: Callable[[str], bool] | None = None,
    bot_api: Callable[[str], bool] | None = None,
    raise_on_failure: bool = False,
) -> bool:
    """Deliver `text` to Telegram. Hermes first, direct Bot API as the fallback.

    Returns True if every chunk landed. `hermes`/`bot_api` exist so tests — and
    anyone wiring a different gateway — can inject a fake without a socket.
    """
    via_hermes = hermes_transport if hermes is None else hermes
    via_bot_api = bot_api_transport if bot_api is None else bot_api

    delivered = True
    for chunk in split_message(text):
        if via_hermes(chunk):
            continue
        if via_bot_api(chunk):
            continue
        delivered = False

    if not delivered:
        log.error("Telegram delivery failed on both transports")
        if raise_on_failure:
            raise DeliveryError("neither hermes nor the Telegram Bot API accepted the message")
    return delivered


# 08-deployment §5 names the delivery helper `send_digest`, and so does
# `test_digest_delivered_when_hermes_is_down`. Same function, both names.
send_digest = send_message


# ------------------------------------------------------------------ routing

#: The commands of 07-interfaces §2 plus `/fix` (FR-9.7). Value is the CLI
#: argv, or None for the ones answered from a constant and never shell out.
COMMANDS: dict[str, list[str] | None] = {
    "/today": ["digest", "--today"],
    "/run": ["run"],
    "/fund": ["fund", "{arg}"],
    "/why": ["show", "{arg}"],
    "/status": ["status"],
    "/sheet": None,
    "/week": ["digest", "--week"],
    "/fix": ["repair", "--apply"],
    "/help": None,
    "/start": None,
}

FUND_KEYS = ("northstar", "dsw", "outward", "anticus")

SHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sheet_id}"

HELP_TEXT = """📡 UK Founder Radar

/today          today's shortlist
/run            run a scan now (takes a few minutes)
/run northstar  scan scoped to one fund
/fund northstar top 10 current matches for a fund
/why <company>  the full score breakdown
/status         last run, source health, this month's cost
/week           this week's new shortlist entries
/sheet          link to the spreadsheet
/fix            diagnose and repair this box
/help           this list

Fund keys: northstar · dsw · outward · anticus"""


@dataclass
class Reply:
    """What the router decided. `argv` is a plan, not a side effect."""

    status: str                      # ok | denied | unknown
    argv: list[str] = field(default_factory=list)
    text: str = ""
    ran_pipeline: bool = False


def allowed_users() -> set[str]:
    """`TELEGRAM_ALLOWED_USERS` — comma or space separated numeric IDs."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return {part for part in raw.replace(",", " ").split() if part}


def is_allowed(user_id) -> bool:
    """Closed by default. An empty allow-list admits nobody, not everybody.

    Getting this backwards is the classic way an allow-list becomes decorative:
    a misconfigured server would silently accept `/run` from any stranger who
    found the bot.
    """
    allowed = allowed_users()
    if not allowed:
        return False
    return str(user_id).strip() in allowed


def route(text: str) -> tuple[str, list[str] | None, str]:
    """`(command, argv, argument)` for a raw message. argv is None for local replies."""
    words = str(text or "").strip().split()
    if not words:
        return "", None, ""
    command = words[0].lower().split("@", 1)[0]     # strip @botname in group chats
    argument = " ".join(words[1:]).strip()

    if command not in COMMANDS:
        return command, None, argument

    template = COMMANDS[command]
    if template is None:
        return command, None, argument

    # `/run northstar` — the client's 24 July request. The flag already exists.
    if command == "/run" and argument:
        key = argument.lower()
        if key in FUND_KEYS:
            return command, ["run", "--fund", key], key
        return command, ["run"], ""

    argv = [argument if part == "{arg}" else part for part in template]
    return command, argv, argument


def handle(user_id, text: str, *, runner: Callable[[Sequence[str]], str] | None = None) -> Reply:
    """Allow-list, then route. Executes only when a `runner` is supplied.

    Hermes enforces its own allow-list (`telegram.allowed_users`); this is the
    second lock, on our side of the boundary, so the check survives Hermes being
    reconfigured or replaced.
    """
    if not is_allowed(user_id):
        log.warning("rejected Telegram command from unlisted user %s", user_id)
        return Reply(status="denied", text="Not authorised.")

    command, argv, argument = route(text)

    if command == "/help" or command == "/start":
        return Reply(status="ok", text=HELP_TEXT)
    if command == "/sheet":
        sheet_id = os.environ.get("RADAR_SHEET_ID") or os.environ.get("SHEET_ID", "")
        return Reply(
            status="ok",
            text=SHEET_URL_TEMPLATE.format(sheet_id=sheet_id) if sheet_id
            else "No spreadsheet configured (set RADAR_SHEET_ID).",
        )
    if argv is None:
        return Reply(status="unknown", text=f"Unknown command {command or '(empty)'}.\n\n{HELP_TEXT}")
    if any(part == "" for part in argv):
        return Reply(status="unknown", text=f"{command} needs an argument.\n\n{HELP_TEXT}")

    if runner is None:
        return Reply(status="ok", argv=argv)
    return Reply(status="ok", argv=argv, text=runner(argv), ran_pipeline=argv[0] == "run")


def skill_commands(path) -> list[list[str]]:
    """Every `founder-radar ...` command named in the Hermes skill file.

    Backs `test_every_telegram_command_maps_to_a_cli_command` (FR-9.6): the chat
    layer may not reach anything the CLI cannot do.
    """
    import re
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    found: list[list[str]] = []
    for match in re.finditer(r"`founder-radar ([^`]+)`", text):
        parts = [p for p in match.group(1).split() if not p.startswith("<")]
        if parts:
            found.append(parts)
    return found


def commands_used() -> set[str]:
    """The CLI verbs the Telegram commands actually invoke."""
    return {argv[0] for argv in COMMANDS.values() if argv}


__all__ = [
    "send_message", "send_digest", "split_message", "DeliveryError",
    "hermes_transport", "bot_api_transport",
    "COMMANDS", "HELP_TEXT", "Reply", "route", "handle", "is_allowed",
    "allowed_users", "skill_commands", "commands_used",
]
