from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser
import httpx

from briefing.models import Item
from briefing.textutil import strip_html

log = logging.getLogger(__name__)


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    del now
    settings = cfg["ai"]["reddit"]
    if not settings.get("enabled", True):
        return []
    subreddit = settings.get("subreddit", "LocalLLM")
    limit = int(settings.get("limit", 25))
    try:
        return _from_json(client, subreddit, limit)
    except Exception as exc:
        log.warning("Reddit JSON failed for r/%s (%s); trying RSS", subreddit, exc)
    return _from_rss(client, subreddit)


def _from_json(client: httpx.Client, subreddit: str, limit: int) -> list[Item]:
    response = client.get(
        f"https://old.reddit.com/r/{subreddit}/top.json",
        params={"t": "day", "limit": limit, "raw_json": 1},
    )
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(f"Reddit returned HTTP {response.status_code}")
    response.raise_for_status()
    if "json" not in response.headers.get("content-type", ""):
        raise RuntimeError("Reddit did not return JSON")
    children = response.json().get("data", {}).get("children") or []
    items: list[Item] = []
    for child in children:
        post = child.get("data") or {}
        if post.get("stickied"):
            continue
        title = (post.get("title") or "").strip()
        permalink = post.get("permalink") or ""
        discussion = f"https://old.reddit.com{permalink}" if permalink else ""
        outbound = (post.get("url_overridden_by_dest") or post.get("url") or "").strip()
        is_self = bool(post.get("is_self")) or not outbound
        url = discussion if is_self else outbound
        if not title or not url:
            continue
        created = post.get("created_utc")
        published = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
        selftext = strip_html(post.get("selftext") or "")
        score = post.get("score")
        items.append(
            Item(
                title=title,
                url=url,
                source=f"r/{subreddit}",
                section="ai",
                snippet=selftext[:800],
                score=float(score) if isinstance(score, (int, float)) else None,
                published=published,
                discussion_url=discussion or None,
            )
        )
    return items


def _from_rss(client: httpx.Client, subreddit: str) -> list[Item]:
    response = client.get(f"https://www.reddit.com/r/{subreddit}/top/.rss", params={"t": "day"})
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(parsed.bozo_exception)
    items: list[Item] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title") or "")
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = strip_html(entry.get("summary") or "")
        items.append(
            Item(
                title=title,
                url=link,
                source=f"r/{subreddit}",
                section="ai",
                snippet=summary[:800],
                discussion_url=link,
            )
        )
    return items
