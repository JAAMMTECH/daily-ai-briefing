from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def select_slot(
    now: datetime,
    schedule: dict,
    delivered: dict[str, str],
    *,
    force: bool,
) -> str | None:
    """Return the briefing slot to send, or None when this run should exit.

    GitHub's cron is UTC and does not follow daylight saving time, so the
    workflow starts at both UTC hours that can equal the configured Toronto
    hour. A slot is due during that local hour, and during the following hour
    if it has not been delivered yet (scheduled jobs often start late). The
    delivered flag stops the second UTC start from sending a duplicate.
    """
    if force:
        return "manual"

    local = _local(now, schedule)
    for name, hour in _slots(schedule):
        key = delivery_key(local, name)
        if key in delivered:
            continue
        if local.hour == hour or local.hour == (hour + 1) % 24:
            return name
    return None


def delivery_key(now: datetime, slot: str, schedule: dict | None = None) -> str:
    local = _local(now, schedule) if schedule is not None else now
    return f"{local.date().isoformat()}-{slot}"


def local_now(now: datetime, schedule: dict) -> datetime:
    return _local(now, schedule)


def _slots(schedule: dict) -> list[tuple[str, int]]:
    slots = [("morning", int(schedule["local_hour"]))]
    if schedule.get("afternoon_enabled"):
        slots.append(("afternoon", int(schedule["afternoon_hour"])))
    return slots


def _local(now: datetime, schedule: dict | None) -> datetime:
    if schedule is None:
        return now
    return now.astimezone(ZoneInfo(schedule["timezone"]))
