"""Outbound messaging: the daily digest and the three alerts.

Deliberately import-free at package level. `radar.notify.telegram` pulls in
`httpx` only when the Bot API fallback actually fires, and nothing here should
make `import radar.notify` more expensive than that (NFR-2: 700 MB alongside
Hermes on a 4 GB box).
"""

from __future__ import annotations

__all__ = ["send_message", "send_digest", "heartbeat"]


def __getattr__(name: str):
    if name in ("send_message", "send_digest"):
        from radar.notify import telegram

        return getattr(telegram, name)
    if name == "heartbeat":
        from radar.notify import heartbeat as module

        return module
    raise AttributeError(name)
