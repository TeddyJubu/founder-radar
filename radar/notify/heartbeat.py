"""The staleness alert — "the whole run dies" row of 02-architecture §7.

A daily job that stops running is invisible: no error arrives, because nothing
runs to produce one. The only defence is a *second* clock that checks the first
one fired. This module is that clock's payload; `founder-radar-heartbeat.timer`
is the clock (08-deployment §4).

Three alerts and only three (08-deployment §9) — stale run, source down, low
disk. More than three and they get ignored.

Two entry points, one implementation. `founder-radar status --alert-if-stale
26h` is the one 08-deployment §4 specifies and the one the systemd unit runs;
`python -m radar.notify.heartbeat --alert-if-stale 26h` is the same `check()`
without the CLI, kept because a heartbeat that depends on the console-script
shim being installed is a heartbeat with an extra way to be silently absent.
Both parse the same durations, apply the same threshold and send the same
single Telegram message.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_STALE_AFTER = timedelta(hours=26)
DEFAULT_MIN_FREE_GB = 5

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "": "hours"}


def parse_duration(value) -> timedelta:
    """`26h`, `90m`, `2d`, or a bare number meaning hours."""
    if isinstance(value, timedelta):
        return value
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"not a duration: {value!r}")
    amount, unit = match.groups()
    return timedelta(**{_UNITS[unit.lower()]: float(amount)})


@dataclass
class Heartbeat:
    """The verdict. Truthy `alerts` means something needs saying."""

    stale: bool
    last_success: datetime | None
    age: timedelta | None
    alerts: list[str]
    sent: bool = False

    @property
    def ok(self) -> bool:
        return not self.alerts


# --------------------------------------------------------------------- reads


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_successful_run(db) -> datetime | None:
    """When a run last *finished* successfully.

    `status = 'ok'` only. A `partial` run means sources failed, and the run row
    exists, but treating it as proof of life would mask a pipeline that fails
    every source every day. `running` is likewise not success — a hung run must
    still trip the alert.
    """
    stamp = db.scalar(
        "SELECT MAX(COALESCE(finished_at, started_at)) FROM run "
        "WHERE status = 'ok' AND finished_at IS NOT NULL"
    )
    return _parse_stamp(stamp)


def free_gb(path: str | None = None) -> float | None:
    target = path or os.environ.get("RADAR_DB") or "."
    for candidate in (target, os.path.dirname(target) or "."):
        try:
            return shutil.disk_usage(candidate).free / (1024 ** 3)
        except OSError:
            continue
    return None


def stale_sources(db, *, days: int = 7, min_avg: float = 2.0) -> list[str]:
    """Sources that went quiet: zero items for `days`, having averaged > `min_avg`.

    This is the dangerous failure from the runbook — a 200 OK with an empty list
    looks exactly like a quiet week.
    """
    rows = db.query(
        """SELECT source_key,
                  SUM(CASE WHEN observed_on >= date('now', ?) THEN items ELSE 0 END) AS recent,
                  AVG(items) AS overall,
                  COUNT(*)   AS days
             FROM source_health
            GROUP BY source_key""",
        (f"-{int(days)} days",),
    )
    return sorted(
        row["source_key"]
        for row in rows
        if int(row["days"] or 0) > days
        and int(row["recent"] or 0) == 0
        and float(row["overall"] or 0) > min_avg
    )


# -------------------------------------------------------------------- check


def check(
    db,
    *,
    stale_after=DEFAULT_STALE_AFTER,
    now: datetime | None = None,
    sender: Callable[[str], bool] | None = None,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    check_disk: bool = True,
    check_sources: bool = True,
) -> Heartbeat:
    """Look for the three alert conditions and send one message if any fired.

    One message, not three — a heartbeat that fans out into separate alerts is
    a heartbeat that gets muted. `sender` is injectable so the test suite never
    touches a socket; it defaults to the Hermes-then-Bot-API path.
    """
    stale_after = parse_duration(stale_after)
    moment = now or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    last = last_successful_run(db)
    age = (moment - last) if last else None
    stale = last is None or age > stale_after

    alerts: list[str] = []
    if stale:
        hours = int(stale_after.total_seconds() // 3600)
        if last is None:
            alerts.append(f"⚠️ No successful run has ever been recorded (threshold {hours}h).")
        else:
            alerts.append(
                f"⚠️ Stale: no successful run in {int(age.total_seconds() // 3600)}h "
                f"(threshold {hours}h). Last good run {last:%a %d %b %H:%M} UTC."
            )

    if check_sources:
        try:
            quiet = stale_sources(db)
        except Exception:  # noqa: BLE001 - an alerting path must not itself crash
            quiet = []
        if quiet:
            alerts.append(f"⚠️ Source down: {', '.join(quiet)} — zero items for 7 days.")

    if check_disk:
        free = free_gb()
        if free is not None and free < min_free_gb:
            alerts.append(f"⚠️ Disk: {free:.1f} GB free (below {min_free_gb} GB).")

    result = Heartbeat(stale=stale, last_success=last, age=age, alerts=alerts)
    if not alerts:
        return result

    body = "📡 UK Founder Radar — alert\n\n" + "\n".join(alerts)
    if sender is None:
        from radar.notify.telegram import send_message as sender  # noqa: PLC0415
    try:
        result.sent = bool(sender(body))
    except Exception as exc:  # noqa: BLE001 - an unsent alert is not a crash
        log.error("heartbeat alert could not be delivered: %s", type(exc).__name__)
        result.sent = False
    return result


# --------------------------------------------------------------------- entry


def main(argv: list[str] | None = None) -> int:
    """`python -m radar.notify.heartbeat --alert-if-stale 26h`.

    Exit 0 when healthy, 1 when an alert fired. systemd shows a failed unit for
    a non-zero exit, which is the second place the problem becomes visible.
    """
    import argparse

    from radar.store.db import Db, default_db_path

    parser = argparse.ArgumentParser(prog="founder-radar-heartbeat")
    parser.add_argument("--alert-if-stale", default="26h", metavar="DURATION")
    parser.add_argument("--db", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    db = Db(args.db or str(default_db_path()))
    try:
        result = check(db, stale_after=parse_duration(args.alert_if_stale))
    finally:
        db.close()

    if not args.quiet:
        for line in (result.alerts or ["✅ Founder Radar heartbeat: healthy."]):
            print(line)
    return 1 if result.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
