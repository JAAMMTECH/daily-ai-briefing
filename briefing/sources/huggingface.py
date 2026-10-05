from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from briefing.models import Item


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    settings = cfg["ai"]["huggingface"]
    if not settings.get("enabled", True):
        return []
    items = _trending(client, int(settings.get("trending_limit", 15)))
    items.extend(_papers(client, int(settings.get("papers_limit", 10)), now))
    return items


def _trending(client: httpx.Client, limit: int) -> list[Item]:
    response = client.get(
        "https://huggingface.co/api/models",
        params={"sort": "trendingScore", "direction": "-1", "limit": limit},
    )
    response.raise_for_status()
    items: list[Item] = []
    for model in response.json():
        model_id = (model.get("id") or model.get("modelId") or "").strip()
        if not model_id:
            continue
        pipeline = model.get("pipeline_tag") or "model"
        likes = model.get("likes")
        downloads = model.get("downloads")
        snippet = f"{pipeline}"
        if likes is not None:
            snippet += f" · {likes} likes"
        if downloads is not None:
            snippet += f" · {downloads} downloads"
        items.append(
            Item(
                title=model_id,
                url=f"https://huggingface.co/{model_id}",
                source="Hugging Face trending",
                section="ai",
                snippet=snippet,
                score=float(model["trendingScore"]) if model.get("trendingScore") is not None else None,
                published=_parse_time(model.get("createdAt")),
            )
        )
    return items


def _papers(client: httpx.Client, limit: int, now: datetime) -> list[Item]:
    # publishedAt on this API is the paper's original date, which lags the day
    # it was featured. Ask for today and yesterday by the daily-papers date.
    items: list[Item] = []
    seen: set[str] = set()
    utc_now = now.astimezone(timezone.utc)
    for days_ago in (0, 1):
        day = (utc_now - timedelta(days=days_ago)).date().isoformat()
        response = client.get(
            "https://huggingface.co/api/daily_papers",
            params={"date": day, "limit": limit},
        )
        response.raise_for_status()
        for entry in response.json():
            paper = entry.get("paper") or {}
            paper_id = (paper.get("id") or "").strip()
            title = (entry.get("title") or paper.get("title") or "").strip()
            if not paper_id or not title or paper_id in seen:
                continue
            seen.add(paper_id)
            summary = (entry.get("summary") or paper.get("summary") or "").strip()
            upvotes = paper.get("upvotes")
            featured = _parse_time(entry.get("publishedAt") or paper.get("publishedAt"))
            items.append(
                Item(
                    title=title,
                    url=f"https://huggingface.co/papers/{paper_id}",
                    source="Hugging Face papers",
                    section="ai",
                    snippet=summary[:1200],
                    score=float(upvotes) if isinstance(upvotes, (int, float)) else None,
                    published=featured,
                )
            )
            if len(items) >= limit:
                return items
    return items


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
