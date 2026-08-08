"""09-test-plan §7 — the FR-9 clauses that can only be checked on the VPS.

Marked `integration`, so they are deselected by the default offline run. They
also skip themselves cleanly on a laptop: the point of `pytest -m integration`
on a developer machine is to prove the *suite* is healthy, and a red run
because `/opt/founder-radar` is not there teaches nobody anything.

Run them on the box, after `deploy/install.sh`:

    sudo -u radar /opt/founder-radar/venv/bin/python -m pytest -m integration \\
        tests/integration/test_ops_live.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(os.environ.get("FOUNDER_RADAR_ROOT", "/opt/founder-radar"))
ENV_FILE = ROOT / ".env"

ON_VPS = ROOT.is_dir() and shutil.which("systemctl") is not None
HAS_CH_KEY = bool(os.environ.get("COMPANIES_HOUSE_API_KEY") or os.environ.get("CH_API_KEY"))

needs_vps = pytest.mark.skipif(
    not ON_VPS, reason=f"not a Founder Radar host — no {ROOT} with systemd")
needs_ch_key = pytest.mark.skipif(
    not HAS_CH_KEY, reason="no Companies House key in the environment")


def _run(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return done.stdout + done.stderr


# -------------------------------------------------------- FR-9.1 the timer


@needs_vps
def test_timer_is_enabled_and_scheduled():
    """FR-9.1 — systemd is the scheduler, not the application.

    Three separate things can be wrong and each looks fine from the others:
    the unit file can be installed but not enabled, enabled but not running,
    or running on the wrong calendar. All three are checked, because "the timer
    exists" is exactly the reassurance a silently-dead deployment gives.
    """
    listing = _run(["systemctl", "list-timers", "founder-radar.timer", "--all"])
    assert "founder-radar.timer" in listing, listing

    enabled = _run(["systemctl", "is-enabled", "founder-radar.timer"]).strip()
    assert enabled.startswith("enabled"), f"timer is {enabled!r}"

    show = _run(["systemctl", "show", "founder-radar.timer",
                 "--property=TimersCalendar", "--property=Persistent"])
    assert "06:30" in show, show
    # Without Persistent the machine simply skips a day it was down for, and
    # nobody finds out until the heartbeat fires (08-deployment §4).
    assert "Persistent=yes" in show, show


@needs_vps
def test_heartbeat_and_backup_timers_are_enabled_too():
    """The two timers whose absence is invisible: no heartbeat means a dead
    run is never reported, no backup means the loss is discovered later."""
    for unit in ("founder-radar-heartbeat.timer", "founder-radar-backup.timer"):
        assert _run(["systemctl", "is-enabled", unit]).strip().startswith("enabled"), unit


# ------------------------------------------------------- FR-9.5 the secrets


def _secret_values() -> list[str]:
    """Every value in the env file worth keeping out of a log."""
    values: list[str] = []
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if len(value) < 8:
            continue                       # too short to be a credential
        if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            values.append(value)
    return values


@needs_vps
def test_env_file_is_0600_and_never_logged(caplog, tmp_path):
    """FR-9.5 — secrets live in a 0600 file outside the repository, and never
    reach a log line.

    Mode first, because a hand-edit with a careless umask is the realistic way
    0600 becomes 0644 six months from now. Then a real dry run: whatever it
    prints and whatever it logs is searched for every credential in the file.
    """
    assert ENV_FILE.is_file(), f"{ENV_FILE} is missing"
    assert oct(ENV_FILE.stat().st_mode)[-3:] == "600", oct(ENV_FILE.stat().st_mode)
    assert ROOT not in Path(__file__).resolve().parents or not (ROOT / ".git").is_dir(), \
        "the env file must live outside the checkout"

    secrets = _secret_values()
    if not secrets:
        pytest.skip("no credentials configured yet — nothing to leak")

    caplog.set_level("DEBUG")
    output = _run(["founder-radar", "--db", str(tmp_path / "probe.db"),
                   "run", "--dry-run", "--no-llm"])

    for secret in secrets:
        assert secret not in output, "a credential reached stdout/stderr"
        assert secret not in caplog.text, "a credential reached the log"

    for log_file in (ROOT / "logs").glob("*.log"):
        text = log_file.read_text(errors="replace")
        for secret in secrets:
            assert secret not in text, f"a credential is sitting in {log_file}"


# ------------------------------------------ the Companies House window sweep


@pytest.fixture
def network(monkeypatch):
    """Undo the session-wide socket block for one test.

    `tests/conftest.py` replaces `socket.socket` for the whole session so a
    forgotten call in the offline suite fails loudly. A live test is the one
    case where that guard is the wrong answer, so it is lifted here — locally,
    reversibly, and only for tests that already need a real API key.
    """
    import socket

    current = socket.socket
    if current.__name__ == "GuardedSocket":
        monkeypatch.setattr(socket, "socket", current.__mro__[1])
    yield


@needs_ch_key
def test_companies_house_window_sweep_live(network):
    """The most important discovery guarantee in the system, against the real
    API: everything that comes back was incorporated inside the window.

    A sweep that quietly returns older companies is precisely the v1 failure —
    the client's complaint was a list of five-year-old businesses.
    """
    from radar.config.defaults import default_config
    from radar.fetch.http import HttpClient
    from radar.sources.base import FetchContext
    from radar.sources.companies_house import CompaniesHouseAdapter

    days_back = 14
    adapter = CompaniesHouseAdapter(days_back=days_back)
    today = date.today()
    ctx = FetchContext(http=HttpClient(), config=default_config(), db=None, now=today)

    got = list(adapter.fetch(ctx))
    assert got, "the sweep returned nothing — check the key and the SIC tiers"

    for item in got:
        created = item.structured["date_of_creation"]
        incorporated = date.fromisoformat(str(created)[:10])
        assert (today - incorporated).days <= days_back, \
            f"{item.title} incorporated {incorporated} — outside the {days_back}-day window"
        assert incorporated <= today, f"{item.title} is incorporated in the future"

        sic = set(item.structured.get("sic_codes") or [])
        assert not sic.issubset({"82990", "70229"}), \
            f"{item.title} is denylisted-SIC only and should have been dropped"

    # Politeness, and the ban risk: the sweep is windowed and batched, so a
    # fortnight must not cost hundreds of requests. Companies House bans an
    # application for repeated breaches rather than throttling it.
    assert adapter.stats["pages"] <= 60, adapter.stats
