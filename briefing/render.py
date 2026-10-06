from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from briefing.config import ROOT
from briefing.models import Digest, DigestItem, DigestSection, Item
from briefing.textutil import safe_http_url

log = logging.getLogger(__name__)

SECTION_TITLES = {
    "ai": "AI",
    "canada": "Ontario and Canada",
}
SECTION_ORDER = ("ai", "canada")


def assemble(parsed, candidates: list[Item], limits: dict) -> Digest:
    by_id = {item.item_id: item for item in candidates if item.item_id}
    caps = {
        "ai": int(limits.get("ai_items", 8)),
        "canada": int(limits.get("canada_items", 6)),
    }
    returned = {str(section.id).strip().lower(): section for section in parsed.sections}
    sections: list[DigestSection] = []
    for section_id in SECTION_ORDER:
        model_section = returned.get(section_id)
        chosen: list[DigestItem] = []
        seen: set[str] = set()
        for picked in model_section.items if model_section else []:
            item_id = str(picked.id).strip()
            source = by_id.get(item_id)
            if source is None or source.section != section_id or item_id in seen:
                log.warning("Dropping briefing id %s", item_id)
                continue
            seen.add(item_id)
            summary = (picked.summary or "").strip() or "See the link."
            why = (picked.why_it_matters or "").strip()
            chosen.append(
                DigestItem(
                    title=source.title,
                    url=safe_http_url(source.url),
                    source=source.source,
                    summary=summary[:600],
                    why_it_matters=why[:400],
                    watchlist_hit=bool(source.watchlist),
                    discussion_url=safe_http_url(source.discussion_url) or None,
                )
            )
            if len(chosen) >= caps[section_id]:
                break
        sections.append(DigestSection(id=section_id, title=SECTION_TITLES[section_id], items=chosen))

    subject = " ".join((parsed.subject or "").split()) or "AI + Ontario briefing"
    intro = (parsed.intro or "").strip()
    if not intro:
        if any(section.items for section in sections):
            intro = "Today's briefing."
        else:
            intro = "Nothing in today's sources cleared the bar."
    return Digest(subject=subject, intro=intro, sections=sections)


def fallback_digest(candidates: list[Item], limits: dict, when: datetime) -> Digest:
    """Used only for a local dry run when no model key is configured."""
    day = _long_date(when)
    caps = {
        "ai": int(limits.get("ai_items", 8)),
        "canada": int(limits.get("canada_items", 6)),
    }
    sections: list[DigestSection] = []
    for section_id in SECTION_ORDER:
        items = _spread(candidates, section_id, caps[section_id])
        rendered = []
        for item in items:
            snippet = " ".join((item.snippet or "").split())
            why = ""
            rendered.append(
                DigestItem(
                    title=item.title,
                    url=safe_http_url(item.url),
                    source=item.source,
                    summary=(snippet[:320] or "No excerpt captured."),
                    why_it_matters=why,
                    watchlist_hit=bool(item.watchlist),
                    discussion_url=safe_http_url(item.discussion_url) or None,
                )
            )
        sections.append(DigestSection(id=section_id, title=SECTION_TITLES[section_id], items=rendered))
    return Digest(
        subject=f"AI + Ontario briefing — {day} (unsummarized)",
        intro=(
            "This copy was built without the model, so it is the raw candidate list "
            "rather than an edited briefing."
        ),
        sections=sections,
    )


def render_html(digest: Digest, generated_at: str, masthead: str) -> str:
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("email.html.j2")
    return template.render(digest=digest, generated_at=generated_at, masthead=masthead)


def digest_from_markdown(markdown: str) -> Digest:
    """Read a digest archive back into the structure the email template uses."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    if not lines or not lines[0].startswith("# "):
        raise ValueError("Digest markdown must start with a # heading")
    subject = lines[0][2:].strip()
    index = 1
    while index < len(lines) and lines[index] == "":
        index += 1
    intro_lines: list[str] = []
    while index < len(lines) and not lines[index].startswith("## "):
        intro_lines.append(lines[index])
        index += 1
    sections: list[DigestSection] = []
    title_to_id = {title: section_id for section_id, title in SECTION_TITLES.items()}
    while index < len(lines):
        if not lines[index].startswith("## "):
            index += 1
            continue
        title = lines[index][3:].strip()
        index += 1
        while index < len(lines) and lines[index] == "":
            index += 1
        items: list[DigestItem] = []
        if index < len(lines) and lines[index] == "Nothing worth flagging.":
            index += 1
        else:
            while index < len(lines) and not lines[index].startswith("## "):
                if lines[index].startswith("### "):
                    item, index = _item_from_markdown(lines, index)
                    items.append(item)
                else:
                    index += 1
        sections.append(
            DigestSection(id=title_to_id.get(title, title.lower()), title=title, items=items)
        )
    return Digest(subject=subject, intro="\n".join(intro_lines).strip(), sections=sections)


def _item_from_markdown(lines: list[str], index: int) -> tuple[DigestItem, int]:
    title = lines[index][4:].strip()
    index += 1
    index = _skip_blank(lines, index)
    source = lines[index].strip() if index < len(lines) else ""
    index += 1
    index = _skip_blank(lines, index)
    summary_lines: list[str] = []
    while index < len(lines) and lines[index] not in ("",) and not lines[index].startswith("#"):
        summary_lines.append(lines[index])
        index += 1
    index = _skip_blank(lines, index)
    why = ""
    if index < len(lines) and lines[index].startswith("Why it's worth knowing: "):
        why = lines[index].removeprefix("Why it's worth knowing: ").strip()
        index += 1
        index = _skip_blank(lines, index)
    url = ""
    if index < len(lines) and lines[index].startswith("http"):
        url = lines[index].strip()
        index += 1
    discussion = None
    if index < len(lines) and lines[index].startswith("Discussion: "):
        discussion = lines[index].removeprefix("Discussion: ").strip()
        index += 1
    watchlist = False
    ahead = _skip_blank(lines, index)
    if ahead < len(lines) and lines[ahead] == "Watchlist match.":
        watchlist = True
        index = ahead + 1
    return (
        DigestItem(
            title=title,
            url=url,
            source=source,
            summary="\n".join(summary_lines).strip(),
            why_it_matters=why,
            watchlist_hit=watchlist,
            discussion_url=discussion,
        ),
        index,
    )


def _skip_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and lines[index] == "":
        index += 1
    return index


def render_markdown(digest: Digest) -> str:
    lines = [f"# {digest.subject}", "", digest.intro, ""]
    for section in digest.sections:
        lines.extend([f"## {section.title}", ""])
        if not section.items:
            lines.extend(["Nothing worth flagging.", ""])
            continue
        for item in section.items:
            lines.extend([f"### {item.title}", "", item.source, "", item.summary])
            if item.why_it_matters:
                lines.extend(["", f"Why it's worth knowing: {item.why_it_matters}"])
            lines.extend(["", item.url])
            if item.discussion_url and item.discussion_url != item.url:
                lines.append(f"Discussion: {item.discussion_url}")
            if item.watchlist_hit:
                lines.extend(["", "Watchlist match."])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_telegram(digest: Digest, limit: int, archive_url: str | None) -> str:
    lines = [digest.subject, "", digest.intro, ""]
    count = 0
    for section in digest.sections:
        if not section.items or count >= limit:
            continue
        lines.append(section.title)
        for item in section.items:
            if count >= limit:
                break
            count += 1
            lines.append(f"{count}. {item.title}")
            lines.append(item.summary)
            if item.why_it_matters:
                lines.append(item.why_it_matters)
            if item.url:
                lines.append(item.url)
            lines.append("")
    if count == 0:
        lines.append("Nothing worth flagging today.")
        lines.append("")
    if archive_url:
        lines.append(f"Full digest: {archive_url}")
    else:
        lines.append("The full digest is in your email.")
    text = "\n".join(lines).strip()
    if len(text) <= 4000:
        return text
    clipped = text[:3900].rsplit("\n", 1)[0].rstrip()
    trailer = f"\n\nFull digest: {archive_url}" if archive_url else "\n\nThe full digest is in your email."
    return clipped + trailer


def _spread(candidates: list[Item], section_id: str, limit: int) -> list[Item]:
    grouped: dict[str, list[Item]] = {}
    for item in candidates:
        if item.section != section_id:
            continue
        grouped.setdefault(item.source, []).append(item)
    chosen: list[Item] = []
    while len(chosen) < limit and any(grouped.values()):
        for source in list(grouped):
            bucket = grouped[source]
            if not bucket:
                continue
            chosen.append(bucket.pop(0))
            if len(chosen) >= limit:
                break
    return chosen


def archive_name(local: datetime, slot: str) -> str:
    day = local.date().isoformat()
    if slot == "morning":
        return f"{day}.md"
    if slot == "manual":
        return f"{day}-manual-{local.strftime('%H%M')}.md"
    return f"{day}-{slot}.md"


def write_digest(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _long_date(when: datetime) -> str:
    return when.strftime("%B %d, %Y").replace(" 0", " ")
