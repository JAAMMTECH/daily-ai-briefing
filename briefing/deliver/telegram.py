from __future__ import annotations

import os
import re

import httpx


def telegram_chat_ids(cfg: dict | None = None) -> list[str]:
    raw: list[str] = []
    env_value = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env_value:
        raw.extend(re.split(r"[,;\s]+", env_value))
    for entry in ((cfg or {}).get("delivery") or {}).get("telegram_chat_ids") or []:
        raw.append(str(entry))
    found: set[str] = set()
    chat_ids: list[str] = []
    for value in raw:
        chat_id = value.strip()
        if not chat_id or chat_id in found:
            continue
        found.add(chat_id)
        chat_ids.append(chat_id)
    return chat_ids


def send_telegram(text: str, chat_ids: list[str] | None = None) -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    targets = chat_ids if chat_ids is not None else telegram_chat_ids()
    if not token or not targets:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and at least one chat id are required")
    failures: list[str] = []
    for chat_id in targets:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:4096],
                "disable_web_page_preview": True,
            },
            timeout=30.0,
        )
        if response.status_code >= 400 or not response.json().get("ok"):
            failures.append(f"{chat_id} ({response.status_code})")
    if failures:
        raise RuntimeError("Telegram send failed for: " + ", ".join(failures))
