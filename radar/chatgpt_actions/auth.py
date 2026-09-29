"""Bearer auth for the ChatGPT Actions service."""

from __future__ import annotations

import hmac
import os
from typing import Mapping


class AuthError(Exception):
    """Missing or invalid Authorization header."""

    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def configured_api_key() -> str:
    """Return the configured key, or empty if unset."""
    return (os.environ.get("RADAR_CHATGPT_API_KEY") or "").strip()


def require_api_key_configured() -> str:
    """Fail closed: refuse to bind when the key is missing."""
    key = configured_api_key()
    if not key:
        raise SystemExit(
            "RADAR_CHATGPT_API_KEY is unset — refusing to start ChatGPT Actions "
            "(set it in /opt/founder-radar/.env, then restart the unit)"
        )
    return key


def extract_bearer(headers: Mapping[str, str]) -> str | None:
    """Pull the Bearer token from an Authorization header (case-insensitive)."""
    raw = ""
    for name, value in headers.items():
        if name.lower() == "authorization":
            raw = (value or "").strip()
            break
    if not raw:
        return None
    kind, _, token = raw.partition(" ")
    if kind.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def check_authorization(headers: Mapping[str, str], *, expected: str | None = None) -> None:
    """Raise AuthError unless the Bearer token matches the configured key."""
    key = (expected if expected is not None else configured_api_key()).strip()
    if not key:
        raise AuthError("API key not configured", status=503)
    token = extract_bearer(headers)
    if token is None:
        raise AuthError("Authorization: Bearer <key> required")
    if not hmac.compare_digest(token, key):
        raise AuthError("Invalid API key")
