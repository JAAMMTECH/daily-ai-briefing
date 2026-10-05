from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
import trafilatura

from briefing.models import Item
from briefing.textutil import match_keywords, strip_html

log = logging.getLogger(__name__)

HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    settings = cfg["ai"]["hackernews"]
    if not settings.get("enabled", True):
        return []

    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in cfg["ai"].get("enrich_title_patterns", [])]
    front_page = _front_page(client, int(settings.get("front_page_limit", 30)))
    keywords = cfg["ai"]["watchlist"].get("keywords", [])
    lookback = int(cfg["ai"]["watchlist"].get("keyword_lookback_hours", 24))
    per_term = int(cfg["ai"]["watchlist"].get("keyword_hits_per_term", 5))
    try:
        watched = _keyword_stories(client, keywords, now - timedelta(hours=lookback), per_term)
    except Exception:
        log.warning("Hacker News watchlist search failed", exc_info=True)
        watched = []

    enrich_targets = _enrich_targets(
        front_page + watched,
        patterns,
        int(cfg["limits"].get("enrich_top", 8)),
    )
    comments_limit = int(settings.get("comments_per_story", 5))
    article_chars = int(cfg["limits"].get("article_chars", 4000))
    comment_chars = int(cfg["limits"].get("comment_chars", 1500))
    for item in enrich_targets:
        _enrich(client, item, comments_limit, article_chars, comment_chars)
    return front_page + watched


def _front_page(client: httpx.Client, limit: int) -> list[Item]:
    response = client.get(HN_SEARCH, params={"tags": "front_page", "hitsPerPage": limit})
    response.raise_for_status()
    items: list[Item] = []
    for hit in response.json().get("hits") or []:
        item = _story_item(hit, source="Hacker News")
        if item:
            items.append(item)
    return items


def _keyword_stories(
    client: httpx.Client,
    keywords: list[str],
    since: datetime,
    per_term: int,
) -> list[Item]:
    cutoff = int(since.timestamp())
    items: list[Item] = []
    for keyword in keywords:
        response = client.get(
            HN_SEARCH,
            params={
                "query": keyword,
                "tags": "story",
                "hitsPerPage": per_term,
                "numericFilters": f"created_at_i>{cutoff}",
                # Algolia treats "ROCm" as a typo for "rock" or "room" unless this is off.
                "typoTolerance": "false",
                "restrictSearchableAttributes": "title,story_text,url",
            },
        )
        response.raise_for_status()
        for hit in response.json().get("hits") or []:
            item = _story_item(hit, source="Hacker News (watchlist)", watchlist=[keyword])
            if item is None:
                continue
            haystack = f"{item.title}\n{item.snippet}\n{item.url}"
            if not match_keywords(haystack, [keyword]):
                continue
            items.append(item)
    return items


def _story_item(hit: dict, source: str, watchlist: list[str] | None = None) -> Item | None:
    title = (hit.get("title") or "").strip()
    story_id = str(hit.get("objectID") or "")
    if not title or not story_id:
        return None
    discussion = f"https://news.ycombinator.com/item?id={story_id}"
    url = (hit.get("url") or "").strip() or discussion
    published = _parse_time(hit.get("created_at"))
    story_text = strip_html(hit.get("story_text") or "")
    points = hit.get("points")
    comments = hit.get("num_comments")
    meta = []
    if points is not None:
        meta.append(f"{points} points")
    if comments is not None:
        meta.append(f"{comments} comments")
    snippet = story_text
    if meta:
        snippet = (snippet + "\n" if snippet else "") + " · ".join(meta)
    return Item(
        title=title,
        url=url,
        source=source,
        section="ai",
        snippet=snippet,
        score=float(points) if isinstance(points, (int, float)) else None,
        published=published,
        discussion_url=discussion,
        watchlist=list(watchlist or []),
        story_id=story_id,
    )


def _enrich_targets(items: list[Item], patterns: list[re.Pattern[str]], limit: int) -> list[Item]:
    matched = [item for item in items if item.watchlist or any(pattern.search(item.title) for pattern in patterns)]
    matched.sort(key=lambda item: (-(1 if item.watchlist else 0), -(item.score or 0)))
    seen: set[str] = set()
    chosen: list[Item] = []
    for item in matched:
        if not item.story_id or item.story_id in seen:
            continue
        seen.add(item.story_id)
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _enrich(
    client: httpx.Client,
    item: Item,
    comments_limit: int,
    article_chars: int,
    comment_chars: int,
) -> None:
    parts = [item.snippet] if item.snippet else []
    article = _article_text(client, item.url, article_chars)
    if article:
        parts.append(article)
    comments = _comments(client, item.story_id, comments_limit, comment_chars)
    if comments:
        parts.append("Top comments:\n" + comments)
    item.snippet = "\n\n".join(part for part in parts if part).strip()


def _article_text(client: httpx.Client, url: str, limit: int) -> str:
    if not url.startswith("http"):
        return ""
    host = url.split("/")[2].lower()
    if host.endswith("news.ycombinator.com") or "reddit.com" in host:
        return ""
    try:
        response = client.get(url, timeout=12.0)
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400 or "html" not in content_type.lower():
            return ""
        if len(response.content) > 2_000_000:
            return ""
        extracted = trafilatura.extract(
            response.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception:
        log.info("Article fetch failed for %s", url, exc_info=True)
        return ""
    if not extracted:
        return ""
    collapsed = re.sub(r"\s+", " ", extracted).strip()
    return collapsed[:limit]


def _comments(client: httpx.Client, story_id: str, limit: int, char_limit: int) -> str:
    if not story_id:
        return ""
    try:
        response = client.get(
            HN_SEARCH,
            params={"tags": f"comment,story_{story_id}", "hitsPerPage": limit},
        )
        response.raise_for_status()
        hits = response.json().get("hits") or []
    except Exception:
        log.info("Comment fetch failed for story %s", story_id, exc_info=True)
        return ""
    chunks: list[str] = []
    for hit in hits:
        text = strip_html(hit.get("comment_text") or "")
        if text:
            chunks.append(text)
    return "\n".join(chunks)[:char_limit]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
