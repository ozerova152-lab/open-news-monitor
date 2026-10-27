from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    url: str
    enabled: bool = True
    selectors: dict[str, str] | None = None


@dataclass(frozen=True)
class Article:
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str
    published_at: str | None


@dataclass(frozen=True)
class Finding:
    article: Article
    reason: str
    detected_at: datetime
