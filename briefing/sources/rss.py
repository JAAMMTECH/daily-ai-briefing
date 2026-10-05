from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from briefing.models import Item
from briefing.textutil import match_keywords, strip_html

log = logging.getLogger(__name__)


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    canada = cfg["canada"]
    default_lookback = int(canada.get("lookback_hours", 36))
    per_feed = int(canada.get("per_feed_limit", 15))
    keywords = canada.get("keep_keywords") or []
    items: list[Item] = []
    attempts = 0
    failures = 0

    def take(label: str, fn) -> list[Item]:
        nonlocal attempts, failures
        attempts += 1
        try:
            found = fn()
        except Exception:
            failures += 1
            log.warning("%s feed failed", label, exc_info=True)
            return []
        log.info("%s: %d items", label, len(found))
        return found

    for query in canada.get("google_news_queries") or []:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=en-CA&gl=CA&ceid=CA:en"
        )
        news_hours = int(canada.get("google_news_lookback_hours", default_lookback))
        items.extend(
            take(
                f"Google News ({query})",
                lambda url=url, news_hours=news_hours: _feed(
                    client,
                    url,
                    source="Google News",
                    since=now - timedelta(hours=news_hours),
                    limit=per_feed,
                ),
            )
        )
    for feed in canada.get("feeds") or []:
        hours = int(feed.get("lookback_hours", default_lookback))

        def load(feed=feed, hours=hours) -> list[Item]:
            entries = _feed(
                client,
                feed["url"],
                source=feed["name"],
                since=now - timedelta(hours=hours),
                limit=per_feed,
            )
            if feed.get("require_keywords"):
                entries = [
                    item
                    for item in entries
                    if match_keywords(f"{item.title}\n{item.snippet}", keywords)
                ]
            return entries

        items.extend(take(feed["name"], load))
    if attempts and failures == attempts:
        raise RuntimeError("all Canada feeds failed")
    return items


def _feed(
    client: httpx.Client,
    url: str,
    source: str,
    since: datetime,
    limit: int,
) -> list[Item]:
    response = client.get(url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(getattr(parsed, "bozo_exception", "unreadable feed"))
    items: list[Item] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title") or "")
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        published = _published(entry)
        if published is not None and published < since:
            continue
        summary = strip_html(entry.get("summary") or entry.get("description") or "")
        items.append(
            Item(
                title=title,
                url=link,
                source=source,
                section="canada",
                snippet=summary[:800],
                published=published,
            )
        )
        if len(items) >= limit:
            break
    return items


def _published(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None
