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
    through the morning and the first run between local_hour and latest_hour
    sends. The delivered flag stops later runs from sending a second copy. A
    forced run inside an undelivered morning counts as the morning briefing.
    """
    local = _local(now, schedule)
    start = int(schedule["local_hour"])
    end = int(schedule.get("latest_hour", 12))
    if delivery_key(local, "morning") not in delivered and start <= local.hour < end:
        return "morning"
    return "manual" if force else None


def delivery_key(now: datetime, slot: str, schedule: dict | None = None) -> str:
    local = _local(now, schedule) if schedule is not None else now
    return f"{local.date().isoformat()}-{slot}"


def local_now(now: datetime, schedule: dict) -> datetime:
    return _local(now, schedule)


def _local(now: datetime, schedule: dict | None) -> datetime:
    if schedule is None:
        return now
    return now.astimezone(ZoneInfo(schedule["timezone"]))
