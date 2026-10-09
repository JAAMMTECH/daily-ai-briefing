from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    title: str
    url: str
    source: str
    section: str
    snippet: str = ""
    score: float | None = None
    published: datetime | None = None
    discussion_url: str | None = None
    watchlist: list[str] = field(default_factory=list)
    item_id: str = ""
    story_id: str = ""


@dataclass
class DigestTerm:
    term: str
    stands_for: str
    explanation: str


@dataclass
class DigestItem:
    title: str
    url: str
    source: str
    summary: str
    why_it_matters: str
    watchlist_hit: bool
    discussion_url: str | None = None
    terms: list[DigestTerm] = field(default_factory=list)


@dataclass
class DigestSection:
    id: str
    title: str
    items: list[DigestItem]


@dataclass
class Digest:
    subject: str
    intro: str
    sections: list[DigestSection]
