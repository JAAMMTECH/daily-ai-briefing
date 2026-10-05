from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from briefing.config import ROOT
from briefing.textutil import normalize_url


class State:
    def __init__(self, path: Path, seen: dict[str, str], delivered: dict[str, str]) -> None:
        self.path = path
        self.seen = seen
        self.delivered = delivered

    @classmethod
    def load(cls, path: Path | None = None) -> State:
        state_path = path or ROOT / "data" / "seen.json"
        if not state_path.exists():
            return cls(state_path, {}, {})
        raw = json.loads(state_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict):
            return cls(state_path, {}, {})
        if "seen" not in raw and raw and all(isinstance(value, str) for value in raw.values()):
            return cls(state_path, raw, {})
        seen = raw.get("seen") or {}
        delivered = raw.get("delivered") or {}
        return cls(state_path, dict(seen), dict(delivered))

    def has_url(self, url: str) -> bool:
        return normalize_url(url) in self.seen

    def mark_urls(self, urls: list[str], when: datetime) -> None:
        stamp = _stamp(when)
        for url in urls:
            if url:
                self.seen[normalize_url(url)] = stamp

    def mark_delivered(self, key: str, when: datetime) -> None:
        self.delivered[key] = _stamp(when)

    def prune(self, now: datetime, days: int) -> None:
        cutoff = now.astimezone(timezone.utc) - timedelta(days=days)
        self.seen = {url: stamp for url, stamp in self.seen.items() if _is_fresh(stamp, cutoff)}
        self.delivered = {
            key: stamp for key, stamp in self.delivered.items() if _is_fresh(stamp, cutoff)
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen": self.seen, "delivered": self.delivered}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _stamp(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _is_fresh(stamp: str, cutoff: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= cutoff
