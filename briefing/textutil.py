from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
        ],
        doseq=True,
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    found: list[str] = []
    for keyword in keywords:
        if re.search(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE):
            found.append(keyword)
    return found


def safe_http_url(url: str | None) -> str:
    if not url:
        return ""
    stripped = url.strip()
    if stripped.startswith("https://") or stripped.startswith("http://"):
        return stripped
    return ""
