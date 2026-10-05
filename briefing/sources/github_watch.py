from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from briefing.models import Item
from briefing.textutil import strip_html

log = logging.getLogger(__name__)

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    watch = cfg["ai"]["watchlist"]
    since = now - timedelta(hours=int(watch.get("release_lookback_hours", 48)))
    repos = list(watch.get("github_repos") or [])
    items: list[Item] = []
    failures = 0
    for repo in repos:
        try:
            items.extend(_releases(client, repo, since))
        except Exception:
            failures += 1
            log.warning("GitHub releases failed for %s", repo, exc_info=True)
    if repos and failures == len(repos):
        raise RuntimeError("all GitHub release checks failed")
    return items


def _releases(client: httpx.Client, repo: str, since: datetime) -> list[Item]:
    response = client.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 5},
        headers=GITHUB_HEADERS,
    )
    response.raise_for_status()
    items: list[Item] = []
    label = repo.split("/")[-1]
    for release in response.json():
        if release.get("draft"):
            continue
        published = _parse_time(release.get("published_at"))
        if published is None or published < since:
            continue
        # Releases are newest first. One note per repo is enough; patch tags
        # would otherwise crowd out the rest of the briefing.
        name = (release.get("name") or release.get("tag_name") or "release").strip()
        url = (release.get("html_url") or "").strip()
        if not url:
            continue
        body = strip_html(release.get("body") or "")
        items.append(
            Item(
                title=f"{repo} {name}",
                url=url,
                source=f"GitHub: {repo}",
                section="ai",
                snippet=body[:1500],
                published=published,
                watchlist=[label],
            )
        )
        break
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
