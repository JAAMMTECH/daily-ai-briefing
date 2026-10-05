from __future__ import annotations

import httpx

USER_AGENT = "ai-it-updates/1.0 (personal daily briefing; +https://jaammtech.com)"


def client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
