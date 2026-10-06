from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from briefing.config import ROOT, load_config
from briefing.deliver.email import email_recipients, send_email
from briefing.deliver.telegram import send_telegram, telegram_chat_ids
from briefing.http import client
from briefing.render import (
    archive_name,
    assemble,
    digest_from_markdown,
    fallback_digest,
    render_html,
    render_markdown,
    render_telegram,
    write_digest,
)
from briefing.schedule import delivery_key, local_now, select_slot
from briefing.sources import cap_candidates, collect
from briefing.state import State

log = logging.getLogger("briefing")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    if args.resend:
        return _resend(cfg, args.resend)
    now = datetime.now().astimezone()
    local = local_now(now, cfg["schedule"])
    state = State.load()
    force = args.force or _env("BRIEFING_FORCE") == "1"
    slot = select_slot(now, cfg["schedule"], state.delivered, force=force)
    if slot is None:
        log.info("Not due at %s %s; exiting", local.strftime("%Y-%m-%d %H:%M"), cfg["schedule"]["timezone"])
        return 0

    log.info("Running %s slot for %s", slot, local.strftime("%Y-%m-%d %H:%M %Z"))
    if not args.dry_run:
        missing = _missing_secrets(cfg)
        if missing:
            log.error("Missing required settings: %s", ", ".join(missing))
            return 1

    with client() as http:
        result = collect(http, cfg, now)
    if result.errors and len(result.errors) == len(result.counts) and not result.items:
        log.error("Every source failed: %s", ", ".join(result.errors))
        return 1
    if result.errors:
        log.warning("Continuing after source failures: %s", ", ".join(result.errors))

    fresh = [item for item in result.items if item.url and not state.has_url(item.url)]
    log.info("Collected %d items, %d new", len(result.items), len(fresh))
    if not fresh:
        log.info("No new items")
        if not args.dry_run and slot != "manual":
            state.mark_delivered(delivery_key(now, slot, cfg["schedule"]), now)
            state.prune(now, int(cfg["limits"].get("seen_days", 30)))
            state.save()
        return 0

    candidates = cap_candidates(fresh, cfg)
    when_label = local.strftime("%B %d, %Y").replace(" 0", " ")
    if _env("ANTHROPIC_API_KEY"):
        from briefing.summarize import summarize

        digest = assemble(summarize(cfg, candidates, when_label), candidates, cfg["limits"])
    elif args.dry_run:
        log.warning("ANTHROPIC_API_KEY is not set; writing an unsummarized dry run")
        digest = fallback_digest(candidates, cfg["limits"], local)
    else:
        log.error("ANTHROPIC_API_KEY is not set")
        return 1

    generated_at = local.strftime("%Y-%m-%d %H:%M %Z")
    masthead = ((cfg.get("briefing") or {}).get("title") or "Daily briefing").strip()
    html = render_html(digest, generated_at, masthead)
    markdown = render_markdown(digest)
    filename = archive_name(local, slot)
    telegram = render_telegram(digest, int(cfg["limits"].get("telegram_top", 5)), _archive_url(filename))

    if args.dry_run:
        out = ROOT / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "digest.html").write_text(html, encoding="utf-8")
        (out / "digest.md").write_text(markdown, encoding="utf-8")
        (out / "telegram.txt").write_text(telegram + "\n", encoding="utf-8")
        log.info("Dry run wrote %s", out)
        return 0

    email_error: Exception | None = None
    telegram_error: Exception | None = None
    try:
        recipients = email_recipients(cfg)
        send_email(digest.subject, html, markdown, recipients)
        log.info("Email sent to %s", ", ".join(recipients))
    except Exception as exc:
        email_error = exc
        log.exception("Email failed")
    try:
        chats = telegram_chat_ids(cfg)
        send_telegram(telegram, chats)
        log.info("Telegram message sent to %d chat(s)", len(chats))
    except Exception as exc:
        telegram_error = exc
        log.exception("Telegram failed")

    archive_path = ROOT / "digests" / filename
    write_digest(archive_path, markdown)
    log.info("Wrote %s", archive_path)

    if email_error is None:
        state.mark_urls([item.url for item in candidates], now)
        if slot != "manual":
            state.mark_delivered(delivery_key(now, slot, cfg["schedule"]), now)
        state.prune(now, int(cfg["limits"].get("seen_days", 30)))
        state.save()
        log.info("Updated %s", state.path)
    else:
        log.error("State was not updated because email failed, so the next run can retry")

    if email_error or telegram_error:
        return 1
    return 0


def _missing_secrets(cfg: dict) -> list[str]:
    required = [
        "ANTHROPIC_API_KEY",
        "SMTP_USER",
        "SMTP_APP_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
    ]
    missing = [name for name in required if not _env(name)]
    if not email_recipients(cfg):
        missing.append("EMAIL_TO or delivery.email_to")
    if not telegram_chat_ids(cfg):
        missing.append("TELEGRAM_CHAT_ID or delivery.telegram_chat_ids")
    return missing


def _resend(cfg: dict, path: Path) -> int:
    """Email a saved digest again. Does not rebuild it or change seen state."""
    archive = path if path.is_absolute() else ROOT / path
    if not archive.is_file():
        log.error("No saved digest at %s", archive)
        return 1
    recipients = email_recipients(cfg)
    if not recipients or not _env("SMTP_USER") or not _env("SMTP_APP_PASSWORD"):
        log.error("SMTP_USER, SMTP_APP_PASSWORD, and at least one recipient are required")
        return 1
    digest = digest_from_markdown(archive.read_text(encoding="utf-8"))
    masthead = ((cfg.get("briefing") or {}).get("title") or "Daily briefing").strip()
    html = render_html(digest, _generated_at(archive, cfg), masthead)
    send_email(digest.subject, html, archive.read_text(encoding="utf-8"), recipients)
    log.info("Resent %s to %s", archive.name, ", ".join(recipients))
    return 0


def _generated_at(archive: Path, cfg: dict) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:-manual-(\d{2})(\d{2}))?", archive.stem)
    zone = ZoneInfo((cfg.get("schedule") or {}).get("timezone") or "America/Toronto")
    if match is None:
        return datetime.now(zone).strftime("%Y-%m-%d %H:%M %Z")
    year, month, day, hour, minute = match.groups()
    when = datetime(
        int(year),
        int(month),
        int(day),
        int(hour or (cfg.get("schedule") or {}).get("local_hour") or 7),
        int(minute or 0),
        tzinfo=zone,
    )
    return when.strftime("%Y-%m-%d %H:%M %Z")


def _archive_url(filename: str) -> str | None:
    repo = _env("GITHUB_REPOSITORY")
    if not repo:
        return None
    ref = _env("GITHUB_REF_NAME") or "main"
    return f"https://github.com/{repo}/blob/{ref}/digests/{filename}"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and send the daily AI and Ontario briefing.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write out/digest.html without sending or updating state.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if it is outside the scheduled local hour.",
    )
    parser.add_argument(
        "--resend",
        type=Path,
        default=None,
        help="Email a saved digest again, without rebuilding it or updating seen state.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
