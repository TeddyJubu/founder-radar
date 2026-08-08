"""Shared fixtures. The suite runs offline, with no credentials, in seconds.

Two guarantees are enforced here rather than by convention:

* **No network.** `socket.socket` is replaced for the whole session, so a
  forgotten real call fails loudly instead of making the suite slow and flaky.
* **No cache misses.** `offline_llm` hard-fails when an article has no recorded
  response, so nobody can accidentally add a fixture that only passes with an
  API key in the environment.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(scope="session", autouse=True)
def _no_network():
    """Block real sockets for the entire session. Loopback stays open so an
    in-process test server (if one is ever needed) still works."""
    real_socket = socket.socket

    class GuardedSocket(real_socket):
        def connect(self, address):  # type: ignore[override]
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in {"127.0.0.1", "::1", "localhost"}:
                raise NetworkBlocked(f"network access attempted: {address}")
            return super().connect(address)

        def connect_ex(self, address):  # type: ignore[override]
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in {"127.0.0.1", "::1", "localhost"}:
                raise NetworkBlocked(f"network access attempted: {address}")
            return super().connect_ex(address)

    socket.socket = GuardedSocket  # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = real_socket  # type: ignore[misc]


@pytest.fixture
def db():
    """A migrated in-memory database. Fast enough to use per-test."""
    from radar.store.db import Db

    conn = Db(":memory:")
    conn.migrate()
    yield conn
    conn.close()


@pytest.fixture
def config():
    """The seeded default configuration — four funds, eleven vehicles."""
    from radar.config.defaults import default_config

    return default_config()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def _load_json(path: Path):
    return json.loads(path.read_text()) if path.is_file() else {}


@pytest.fixture
def offline_llm():
    """An `LLMClient` that replays `tests/fixtures/llm_cache` and nothing else.

    A cache miss is a test failure, not a network call. Re-record with
    `REFRESH_LLM=1 pytest`, which is the only path that talks to a provider.
    """
    from radar.extract.llm import ReplayLLM

    return ReplayLLM(FIXTURES / "llm_cache")
