"""Fixtures for the Today-screen browser suite (prototype/TESTING.md).

Everything here exists to make the suite hermetic. Three properties matter:

* **A disposable database per session.** Verdicts are persistent writes, so a
  suite that reused one file would pass on the first run and drift on the
  second. Each session rebuilds the register-derived demo (TESTING.md §0.2)
  into a temp path and never opens `/tmp/demo.db`.
* **A free port, chosen at run time.** Port 8787 is usually serving a live
  preview and 8788 is what `TESTING.md` tells a human to use; CI must collide
  with neither, so the port is whatever the OS hands out.
* **A real server process.** The suite drives the actual `prototype/server.py`
  over HTTP rather than a stub, because half the value is the API contract.

The session-wide socket guard in `tests/conftest.py` allows loopback, which is
all Playwright and this server need.
"""

from __future__ import annotations

import json
import sqlite3
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "prototype" / "server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_demo_db(path: Path) -> None:
    """TESTING.md §0.2 — the register-derived dataset, no key and no network.

    Every company here has `coverage = 0.8`, so no card is `thin`. The tests
    that need the amber state skip rather than fail; `test_d7_*` still runs and
    asserts that nothing claims to be thin.
    """
    sys.path.insert(0, str(REPO))
    from radar.config.defaults import default_config
    from radar.pipeline import enrich_stage, resolve_item, score_company
    from radar.sources.base import FetchContext
    from radar.sources.companies_house import CompaniesHouseAdapter
    from radar.store.db import Db

    from tests.unit.test_track_b_end_to_end import TODAY, MockCompaniesHouse

    db = Db(str(path))
    db.migrate()
    cfg = default_config()
    http = MockCompaniesHouse()
    adapter = CompaniesHouseAdapter(api_key="demo", days_back=90, window_days=90)
    for item in adapter.fetch(FetchContext(http=http, config=cfg, db=db, now=TODAY)):
        resolve_item(db, item, cfg)
    enrich_stage(db, cfg, http, api_key="demo")
    for row in db.query("SELECT id FROM company"):
        score_company(db, row["id"], cfg, today=TODAY)
    db.close()


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory) -> Path:
    """A disposable register-derived database for the suite (TESTING.md §0.2).

    Always rebuilt — never a copy of `/tmp/demo.db`. The live demo can carry
    hundreds of shortlist rows and leftover `user_field` verdicts; copying it
    makes layout and Kept-count tests depend on whoever last ran a pipeline.
    """
    target = tmp_path_factory.mktemp("today") / "test-run.db"
    _build_demo_db(target)
    return target


@pytest.fixture(autouse=True)
def fresh_daily_review(demo_db: Path):
    """Keep browser tests isolated without clearing lasting verdicts."""
    from prototype.server import reset_daily_review

    conn = sqlite3.connect(str(demo_db))
    try:
        reset_daily_review(conn)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def server(demo_db: Path):
    """The real prototype server. Yields its base URL."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--db", str(demo_db), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(REPO),
    )
    base = f"http://127.0.0.1:{port}"

    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode()[-800:]
            pytest.fail(f"prototype server exited early:\n{out}")
        try:
            urllib.request.urlopen(base + "/", timeout=1).read()
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.15)
    else:
        proc.kill()
        pytest.fail(f"prototype server never came up on {base}")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def api(server: str) -> dict:
    """`/api/today` once. The DB does not change under the read-only suites."""
    with urllib.request.urlopen(server + "/api/today", timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture
def today(page, server: str):
    """A freshly loaded Today screen with the first card rendered."""
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector('[data-testid="card"]')
    return page


def tid(name: str) -> str:
    """The only way this suite addresses the DOM (TESTING.md §1)."""
    return f'[data-testid="{name}"]'
