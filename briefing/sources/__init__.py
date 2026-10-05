from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from briefing.models import Item
from briefing.sources import canadabuys, github_watch, hackernews, huggingface, market_scan, reddit, rss
from briefing.textutil import match_keywords, normalize_url

log = logging.getLogger(__name__)


@dataclass
class CollectResult:
    items: list[Item]
    errors: list[str]
    counts: dict[str, int]


def collect(client: httpx.Client, cfg: dict, now: datetime) -> CollectResult:
    errors: list[str] = []
    counts: dict[str, int] = {}
    gathered: list[Item] = []

    def run(name: str, fn) -> None:
        try:
            found = fn()
        except Exception:
            log.exception("%s failed", name)
            errors.append(name)
            counts[name] = 0
            return
        counts[name] = len(found)
        log.info("%s: %d items", name, len(found))
        gathered.extend(found)

    run("hackernews", lambda: hackernews.fetch(client, cfg, now))
    run("huggingface", lambda: huggingface.fetch(client, cfg, now))
    run("reddit", lambda: reddit.fetch(client, cfg, now))
    run("github", lambda: github_watch.fetch(client, cfg, now))
    run("canada", lambda: rss.fetch(client, cfg, now))
    run("canadabuys", lambda: canadabuys.fetch(client, cfg, now))
    run("market_scan", lambda: market_scan.fetch(cfg, now))

    items = _dedupe(gathered)
    _tag_watchlist(items, cfg["ai"]["watchlist"].get("keywords") or [])
    return CollectResult(items=items, errors=errors, counts=counts)


def _dedupe(items: list[Item]) -> list[Item]:
    by_url: dict[str, Item] = {}
    for item in items:
        if not item.url or not item.title:
            continue
        key = normalize_url(item.url)
        current = by_url.get(key)
        if current is None:
            by_url[key] = item
            continue
        current.watchlist = sorted(set(current.watchlist) | set(item.watchlist))
        if len(item.snippet) > len(current.snippet):
            current.snippet = item.snippet
        if item.discussion_url and not current.discussion_url:
            current.discussion_url = item.discussion_url
        if item.score is not None and (current.score is None or item.score > current.score):
            current.score = item.score
    return list(by_url.values())


def _tag_watchlist(items: list[Item], keywords: list[str]) -> None:
    for item in items:
        found = match_keywords(f"{item.title}\n{item.snippet}", keywords)
        item.watchlist = sorted(set(item.watchlist) | set(found))


def cap_candidates(items: list[Item], cfg: dict) -> list[Item]:
    limits = cfg["limits"]
    ai = [item for item in items if item.section == "ai"]
    canada = [item for item in items if item.section == "canada"]
    chosen = _select_with_quotas(
        ai,
        limit=int(limits.get("max_ai_candidates", 40)),
        watchlist_limit=int(limits.get("max_watchlist_candidates", 8)),
        quotas=limits.get("ai_quotas") or {},
        family_of=_ai_family,
    )
    chosen += _select_with_quotas(
        canada,
        limit=int(limits.get("max_canada_candidates", 25)),
        watchlist_limit=0,
        quotas=limits.get("canada_quotas") or {},
        family_of=_canada_family,
    )
    counts = {"ai": 0, "canada": 0}
    prefix = {"ai": "ai", "canada": "ca"}
    for item in chosen:
        counts[item.section] += 1
        item.item_id = f"{prefix[item.section]}-{counts[item.section]}"
    return chosen


def _select_with_quotas(items, limit, watchlist_limit, quotas, family_of) -> list[Item]:
    if limit <= 0:
        return []
    ranked_watch = _rank([item for item in items if item.watchlist])
    chosen = ranked_watch[:watchlist_limit]
    seen = {normalize_url(item.url) for item in chosen}
    room = limit - len(chosen)
    grouped: dict[str, list[Item]] = {}
    for item in items:
        if normalize_url(item.url) in seen:
            continue
        grouped.setdefault(family_of(item), []).append(item)
    for family in grouped:
        grouped[family] = _rank(grouped[family])

    for family, quota in quotas.items():
        if room <= 0:
            break
        for item in grouped.get(family, [])[: int(quota)]:
            key = normalize_url(item.url)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(item)
            room -= 1
            if room <= 0:
                break

    if room > 0:
        for item in _rank(items):
            key = normalize_url(item.url)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(item)
            room -= 1
            if room <= 0:
                break
    return chosen


def _ai_family(item: Item) -> str:
    source = item.source.lower()
    if source.startswith("hacker news"):
        return "hackernews"
    if source.startswith("hugging face"):
        return "huggingface"
    if source.startswith("r/"):
        return "reddit"
    if source.startswith("github"):
        return "github"
    return "other"


def _canada_family(item: Item) -> str:
    source = item.source.lower()
    if source == "google news":
        return "google"
    if source == "betakit":
        return "betakit"
    if source == "canadabuys":
        return "tenders"
    if source == "market scan":
        return "scan"
    if "ontario" in source:
        return "ontario"
    if "cyber" in source:
        return "cyber"
    return "other"


def _rank(items: list[Item]) -> list[Item]:
    def key(item: Item) -> tuple:
        published = item.published.timestamp() if item.published else 0.0
        return (0 if item.watchlist else 1, -(item.score or 0), -published)

    return sorted(items, key=key)
