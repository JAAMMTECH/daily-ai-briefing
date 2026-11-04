"""Two short commands for the Cursor automation.

prepare fetches the feeds and writes a capped candidate file. finish turns the
model's edit into the email, the digest, and seen.json. Neither command calls
Anthropic, and neither command asks a model to read the repository.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from briefing.config import ROOT, load_config
from briefing.deliver.email import email_recipients, send_email
from briefing.http import client
from briefing.models import Item
from briefing.render import archive_name, assemble, render_html, render_markdown, write_digest
from briefing.schedule import delivery_key, local_now
from briefing.sources import cap_candidates, collect
from briefing.state import State
from briefing.summarize import BriefingModel

log = logging.getLogger("briefing.cursor_job")

CANDIDATES_PATH = ROOT / "out" / "candidates.json"
EDIT_PATH = ROOT / "out" / "edit.json"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    command = (argv if argv is not None else sys.argv[1:])[:1]
    if command == ["prepare"]:
        return prepare()
    if command == ["finish"]:
        return finish()
    print("Usage: python -m briefing.cursor_job prepare|finish", file=sys.stderr)
    return 2


def prepare() -> int:
    cfg = load_config()
    now = datetime.now().astimezone()
    local = local_now(now, cfg["schedule"])
    state = State.load()
    with client() as http:
        result = collect(http, cfg, now, skip={"market_scan"})
    if result.errors:
        log.warning("Continuing after source failures: %s", ", ".join(result.errors))
    fresh = [item for item in result.items if item.url and not state.has_url(item.url)]
    chosen = cap_candidates(fresh, cfg)
    payload = candidate_payload(cfg, chosen, _long_date(local))
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not chosen:
        print("NO_NEW_ITEMS")
        return 0
    print(f"CANDIDATES n={len(chosen)} path={CANDIDATES_PATH.relative_to(ROOT).as_posix()}")
    return 0


def finish() -> int:
    if not CANDIDATES_PATH.is_file() or not EDIT_PATH.is_file():
        log.error("finish needs out/candidates.json and out/edit.json")
        return 1
    cfg = load_config()
    now = datetime.now().astimezone()
    local = local_now(now, cfg["schedule"])
    payload = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    candidates = [_item(raw) for raw in payload.get("candidates") or []]
    if not candidates:
        print("NOTHING_TO_SEND")
        return 0
    parsed = BriefingModel.model_validate(json.loads(EDIT_PATH.read_text(encoding="utf-8")))
    digest = assemble(parsed, candidates, cfg["limits"])
    masthead = ((cfg.get("briefing") or {}).get("title") or "Daily briefing").strip()
    generated_at = local.strftime("%Y-%m-%d %H:%M %Z")
    html = render_html(digest, generated_at, masthead)
    markdown = render_markdown(digest)
    recipients = email_recipients(cfg)
    try:
        send_email(digest.subject, html, markdown, recipients)
    except Exception:
        log.exception("Email failed")
        print("EMAIL_FAILED")
        return 1

    filename = archive_name(local, "morning")
    archive_path = ROOT / "digests" / filename
    write_digest(archive_path, markdown)
    state = State.load()
    state.mark_urls([item.url for item in candidates], now)
    state.mark_delivered(delivery_key(now, "morning", cfg["schedule"]), now)
    state.prune(now, int(cfg["limits"].get("seen_days", 30)))
    state.save()
    print(f"SENT path={archive_path.relative_to(ROOT).as_posix()}")
    return 0


def candidate_payload(cfg: dict, items: list[Item], when_label: str) -> dict:
    briefing = cfg.get("briefing") or {}
    limits = cfg["limits"]
    cap = int(limits.get("article_chars", 1500))
    return {
        "date": when_label,
        "audience": (briefing.get("audience") or "").strip(),
        "limits": {
            "ai_items": int(limits["ai_items"]),
            "canada_items": int(limits["canada_items"]),
        },
        "candidates": [_candidate(item, cap) for item in items],
    }


def _candidate(item: Item, cap: int) -> dict:
    return {
        "id": item.item_id,
        "section": item.section,
        "source": item.source,
        "title": item.title,
        "url": item.url,
        "score": item.score,
        "published": item.published.isoformat() if item.published else None,
        "watchlist": item.watchlist,
        "discussion_url": item.discussion_url,
        "snippet": (item.snippet or "")[:cap],
    }


def _item(raw: dict) -> Item:
    published = raw.get("published")
    when = datetime.fromisoformat(published) if isinstance(published, str) and published else None
    return Item(
        title=str(raw.get("title") or ""),
        url=str(raw.get("url") or ""),
        source=str(raw.get("source") or ""),
        section=str(raw.get("section") or ""),
        snippet=str(raw.get("snippet") or ""),
        score=raw.get("score"),
        published=when,
        discussion_url=raw.get("discussion_url"),
        watchlist=list(raw.get("watchlist") or []),
        item_id=str(raw.get("id") or ""),
    )


def _long_date(when: datetime) -> str:
    return when.strftime("%B %d, %Y").replace(" 0", " ")


if __name__ == "__main__":
    sys.exit(main())
