"""Tell Aryan when a scheduled job dies — the systemd `OnFailure=` payload.

The heartbeat (`radar.notify.heartbeat`) is the second clock for "the daily
scan stopped": it fires once a day, at 09:00. That is too late and too narrow
for everything else that runs on a timer. The auto-deploy runs every five
minutes as root and can stay broken for days (a diverged checkout, a failing
doctor) with nobody the wiser; the nightly backup can fail for weeks; the scan
itself can exit fatally at 06:30 and not be mentioned until 09:00.

`founder-radar-alert@.service` is a template unit; the three services that
matter name it in `OnFailure=founder-radar-alert@%n.service`, and `%n` arrives
here as `argv[1]`. The heartbeat is deliberately not wired to it — a non-zero
heartbeat already *is* an alert, and alerting about the alert is noise.

Failures repeat: the update timer retries every five minutes, so a broken
deploy would otherwise send twelve messages an hour. Each unit is announced
once and then not again for `REPEAT_AFTER`; a fix that holds and a later
regression are still announced, because the quiet period is time-based and
short. The state lives in `_meta` beside the heartbeat's, and losing it costs
at most one repeat message.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

REPEAT_AFTER = timedelta(hours=6)
_KEY_PREFIX = "alert:failed:"
_UNIT = re.compile(r"^[A-Za-z0-9@_.:\-\\]{1,120}$")

# What a person reading the message on their phone should do next.
_HINTS = {
    "founder-radar-update.service": (
        "The auto-deploy is not applying new commits. Check the working tree "
        "under /opt/founder-radar/app (`git status`) and /opt/founder-radar/logs/update.log."
    ),
    "founder-radar-backup.service": (
        "Tonight's database backup did not complete. Check /opt/founder-radar/logs/backup.log."
    ),
    "founder-radar.service": (
        "The daily scan exited fatally, so no digest was produced. "
        "Check /opt/founder-radar/logs/run.log and error.log."
    ),
}


def build_message(unit: str) -> str:
    hint = _HINTS.get(unit, "See the journal for details.")
    return (
        f"❌ UK Founder Radar — {unit} failed\n\n{hint}\n"
        f"On the server: journalctl -u {unit} -n 50 --no-pager"
    )


def _due(db, key: str, now: datetime, repeat: timedelta) -> bool:
    if db is None:
        return True
    try:
        raw = db.get_meta(_KEY_PREFIX + key)
        if not raw:
            return True
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001 - no usable state means "say it"
        return True
    return now - last >= repeat


def _stamp(db, key: str, now: datetime) -> None:
    if db is None:
        return
    try:
        db.set_meta(_KEY_PREFIX + key, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001 - costs one repeat message at worst
        log.warning("could not record alert state for %s", key)


def notify(unit: str, *, db=None, sender=None, now: datetime | None = None,
           repeat: timedelta = REPEAT_AFTER) -> bool:
    """Send the failure message unless this unit was announced recently.

    Returns True when a message was delivered, False when suppressed or when
    delivery failed (a failed delivery is not stamped, so it is retried).
    """
    if not _UNIT.match(unit):
        raise ValueError(f"not a unit name: {unit!r}")
    moment = now or datetime.now(timezone.utc)
    if not _due(db, unit, moment, repeat):
        return False
    if sender is None:
        from radar.notify.telegram import send_message as sender  # noqa: PLC0415
    try:
        delivered = bool(sender(build_message(unit)))
    except Exception as exc:  # noqa: BLE001 - an unsent alert is not a crash
        log.error("failure alert for %s could not be delivered: %s", unit, type(exc).__name__)
        return False
    if delivered:
        _stamp(db, unit, moment)
    return delivered


def main(argv: list[str] | None = None) -> int:
    """`python -m radar.notify.alert <unit>` — exit 0 unless the unit is malformed."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m radar.notify.alert <unit-name>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    from radar.store.db import Db, default_db_path

    try:
        db = Db(str(default_db_path()))
    except Exception:  # noqa: BLE001 - the alert must go out even with no database
        db = None
    try:
        sent = notify(args[0], db=db)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if db is not None:
            db.close()
    print("alert sent" if sent else "alert suppressed or not delivered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
