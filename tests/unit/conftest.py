"""Fixtures for `tests/unit/`.

The one thing this file exists for: the weekly live check (09-test-plan §4).
`tests/conftest.py` replaces `socket.socket` with `GuardedSocket` for the whole
session, which is what keeps the offline suite honest — but `pytest -m live`
is documented to hit real source websites, so the live-marked tests must be
the one deliberate exception. This mirrors `tests/integration/conftest.py`'s
`_network_for_integration`: the guard is lifted only while a `live`-marked
test runs and restored afterwards, so a `live` run can never leak real
sockets into the offline suite.
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _network_for_live(request):
    """Lift the session-wide socket guard for `live`-marked tests only."""
    if "live" not in {m.name for m in request.node.iter_markers()}:
        yield
        return
    guard = socket.socket
    real = guard.__bases__[0] if guard.__name__ == "GuardedSocket" else guard
    socket.socket = real                              # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = guard                         # type: ignore[misc]
