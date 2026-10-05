from __future__ import annotations

import logging
import os

from briefing.models import Item
from briefing.textutil import safe_http_url

log = logging.getLogger(__name__)

_PROMPT = """Search the web for what a curious reader in Ontario should know from the last week. Look for things the usual news feeds miss:

- school board or college technology RFPs and tenders in Ontario
- Ontario government IT, digital, or cybersecurity procurement
- a concrete move by a Canadian technology or AI company (funding, product, policy), not a recycled press-release headline

Run several distinct searches. Prefer primary pages (the tender, the board, the company) over aggregators. Skip anything you cannot tie to a search result. Do not invent links."""


def fetch(cfg: dict, now) -> list[Item]:
    del now
    settings = (cfg.get("canada") or {}).get("market_scan") or {}
    if not settings.get("enabled", True):
        return []
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        log.info("Market scan skipped; ANTHROPIC_API_KEY is not set")
        return []
    import anthropic

    client = anthropic.Anthropic(timeout=300.0)
    tool = {
        "type": "web_search_20260318",
        "name": "web_search",
        "max_uses": int(settings.get("max_searches", 5)),
        "user_location": {
            "type": "approximate",
            "country": "CA",
            "region": "Ontario",
            "timezone": cfg.get("schedule", {}).get("timezone") or "America/Toronto",
        },
    }
    kwargs = {
        "model": cfg["model"],
        "max_tokens": int(settings.get("max_tokens", 4000)),
        "system": (
            "You research the Ontario and Canadian technology landscape. "
            "Search before you answer. Every claim must come from a search result."
        ),
        "messages": [{"role": "user", "content": _PROMPT}],
        "tools": [tool],
    }
    effort = (cfg.get("effort") or "").strip()
    if effort:
        kwargs["output_config"] = {"effort": effort}

    response = client.messages.create(**kwargs)
    # A long search can pause mid-turn. Continue once with the same blocks.
    if getattr(response, "stop_reason", None) == "pause_turn":
        response = client.messages.create(
            **{**kwargs, "messages": kwargs["messages"] + [{"role": "assistant", "content": response.content}]}
        )
    usage = getattr(response, "usage", None)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", None)
    if searches is not None:
        log.info("Market scan used %s web searches", searches)
    return items_from_search_response(response, int(settings.get("max_items", 8)))


def items_from_search_response(response, limit: int) -> list[Item]:
    """Keep only URLs the web search tool actually returned."""
    found: dict[str, Item] = {}
    for block in getattr(response, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            for citation in getattr(block, "citations", None) or []:
                if getattr(citation, "type", None) != "web_search_result_location":
                    continue
                _remember(
                    found,
                    url=getattr(citation, "url", "") or "",
                    title=(getattr(citation, "title", None) or "").strip(),
                    snippet=(getattr(citation, "cited_text", None) or "").strip(),
                )
        elif block_type == "web_search_tool_result":
            content = getattr(block, "content", None)
            if not isinstance(content, list):
                continue
            for result in content:
                if getattr(result, "type", None) != "web_search_result":
                    continue
                _remember(
                    found,
                    url=getattr(result, "url", "") or "",
                    title=(getattr(result, "title", None) or "").strip(),
                    snippet=(getattr(result, "page_age", None) or "").strip(),
                )
    return list(found.values())[:limit]


def _remember(found: dict[str, Item], url: str, title: str, snippet: str) -> None:
    safe = safe_http_url(url)
    if not safe:
        return
    current = found.get(safe)
    if current is None:
        found[safe] = Item(
            title=title or safe,
            url=safe,
            source="Market scan",
            section="canada",
            snippet=snippet[:700],
        )
        return
    if len(snippet) > len(current.snippet):
        current.snippet = snippet[:700]
    if title and current.title == current.url:
        current.title = title
