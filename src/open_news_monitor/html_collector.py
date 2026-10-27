from __future__ import annotations

import html
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .models import Article, Source
from .rss import SourceFetchError, USER_AGENT


def fetch_html(source: Source, timeout_seconds: int = 20) -> list[Article]:
    """Collect repeated publication cards from a configured public HTML page."""
    selectors = source.selectors or {}
    request = Request(source.url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
    except OSError as error:
        raise SourceFetchError(f"Could not download {source.name}: {error}") from error

    soup = BeautifulSoup(payload, "html.parser")
    cards = soup.select(selectors["card"])
    if not cards:
        raise SourceFetchError(f"No publication cards matched selector '{selectors['card']}' in {source.name}.")

    articles: list[Article] = []
    for card in cards:
        title_node = card.select_one(selectors["title"])
        link_node = card.select_one(selectors["link"])
        date_node = card.select_one(selectors["date"])
        if not title_node or not link_node or not date_node:
            continue
        href = link_node.get("href")
        title = _clean_text(title_node.get_text(" ", strip=True))
        date = _clean_text(date_node.get_text(" ", strip=True))
        if not href or not title or not date:
            continue
        summary_node = card.select_one(selectors.get("summary", "")) if selectors.get("summary") else None
        articles.append(
            Article(
                source_id=source.id,
                source_name=source.name,
                title=title,
                url=urljoin(source.url, href),
                summary=_clean_text(summary_node.get_text(" ", strip=True)) if summary_node else "",
                published_at=date,
            )
        )
    if not articles:
        raise SourceFetchError(f"Cards were found in {source.name}, but none had title, date, and direct link together.")
    return articles


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"\s+", " ", value)).split())
