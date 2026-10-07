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

    GitHub starts scheduled jobs hours late, so the workflow starts every hour
    and the first run inside a slot's window sends it. The morning window runs
    from local_hour until afternoon_hour; the afternoon window runs from
    afternoon_hour to midnight. The delivered flag stops later runs from
    sending the same slot twice. A forced run inside an undelivered window
    counts as that slot, so the schedule does not send a second copy.
    """
    local = _local(now, schedule)
    for name, start, end in _windows(schedule):
        if delivery_key(local, name) in delivered:
            continue
        if start <= local.hour < end:
            return name
    return "manual" if force else None


def delivery_key(now: datetime, slot: str, schedule: dict | None = None) -> str:
    local = _local(now, schedule) if schedule is not None else now
    return f"{local.date().isoformat()}-{slot}"


def local_now(now: datetime, schedule: dict) -> datetime:
    return _local(now, schedule)


def _windows(schedule: dict) -> list[tuple[str, int, int]]:
    morning = int(schedule["local_hour"])
    afternoon = int(schedule["afternoon_hour"])
    windows = [("morning", morning, afternoon)]
    if schedule.get("afternoon_enabled"):
        windows.append(("afternoon", afternoon, 24))
    return windows


def _local(now: datetime, schedule: dict | None) -> datetime:
    if schedule is None:
        return now
    return now.astimezone(ZoneInfo(schedule["timezone"]))
