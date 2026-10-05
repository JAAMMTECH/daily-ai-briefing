from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone

import httpx

from briefing.models import Item
from briefing.textutil import match_keywords

_NEW_NOTICES = "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv"
_REFERENCE = "referenceNumber-numeroReference"
_TITLE = "title-titre-eng"
_PUBLISHED = "publicationDate-datePublication"
_CLOSING = "tenderClosingDate-appelOffresDateCloture"
_NOTICE = "noticeType-avisType-eng"
_ENTITY = "contractingEntityName-nomEntitContractante-eng"
_PROVINCE = "contractingEntityAddressProvince-entiteContractanteAdresseProvince-eng"
_DELIVERY = "regionsOfDelivery-regionsLivraison-eng"
_GSIN = "gsinDescription-nibsDescription-eng"
_UNSPSC = "unspscDescription-eng"
_DESCRIPTION = "tenderDescription-descriptionAppelOffres-eng"
_URL = "noticeURL-URLavis-eng"
_NOTICE_PAGE = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{reference}"


def fetch(client: httpx.Client, cfg: dict, now: datetime) -> list[Item]:
    settings = (cfg.get("canada") or {}).get("canadabuys") or {}
    if not settings.get("enabled", True):
        return []
    response = client.get(settings.get("url") or _NEW_NOTICES, timeout=60.0)
    response.raise_for_status()
    rows = _read_csv(response.content)
    since = now - timedelta(days=int(settings.get("lookback_days", 14)))
    return select_notices(
        rows,
        keywords=list(settings.get("keywords") or []),
        since=since,
        limit=int(settings.get("limit", 12)),
    )


def select_notices(
    rows: list[dict[str, str]],
    keywords: list[str],
    since: datetime,
    limit: int,
) -> list[Item]:
    matched: list[Item] = []
    for row in rows:
        published = _published(row.get(_PUBLISHED) or "")
        if published is None or published < since:
            continue
        title = _clean(row.get(_TITLE) or "")
        url = _notice_url(row)
        if not title or not url.startswith("http"):
            continue
        description = _clean(row.get(_DESCRIPTION) or "")
        gsin = _clean(row.get(_GSIN) or "")
        unspsc = _clean(row.get(_UNSPSC) or "")
        # Match the title and commodity class, not a passing mention of
        # "software" in an otherwise unrelated goods description.
        haystack = "\n".join((title, gsin, unspsc))
        if keywords and not match_keywords(haystack, keywords):
            continue
        entity = _clean(row.get(_ENTITY) or "")
        notice = _clean(row.get(_NOTICE) or "")
        closing = (row.get(_CLOSING) or "").strip()
        ontario = _in_ontario(row)
        snippet = " · ".join(part for part in (notice, entity, f"closes {closing}" if closing else "") if part)
        if description:
            snippet = f"{snippet}\n{description[:700]}".strip()
        matched.append(
            Item(
                title=title,
                url=url,
                source="CanadaBuys",
                section="canada",
                snippet=snippet,
                score=2.0 if ontario else 1.0,
                published=published,
            )
        )
    matched.sort(key=lambda item: (-(item.score or 0), -(item.published.timestamp() if item.published else 0)))
    return matched[:limit]


def _read_csv(payload: bytes) -> list[dict[str, str]]:
    text = _decode(payload)
    return list(csv.DictReader(io.StringIO(text)))


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _published(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean(value: str) -> str:
    value = value.replace("*", " ")
    return re.sub(r"\s+", " ", value).strip()


def _notice_url(row: dict[str, str]) -> str:
    direct = (row.get(_URL) or "").strip()
    if direct.startswith("http"):
        return direct
    # The open-data file often leaves noticeURL blank. The public page is the reference number.
    reference = (row.get(_REFERENCE) or "").strip()
    if not reference:
        return ""
    return _NOTICE_PAGE.format(reference=reference)


def _in_ontario(row: dict[str, str]) -> bool:
    province = _clean(row.get(_PROVINCE) or "").lower()
    if province in {"on", "ontario"}:
        return True
    delivery = _clean(row.get(_DELIVERY) or "").lower()
    return "ontario" in delivery
