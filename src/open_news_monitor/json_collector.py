from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .models import Article, Source
from .rss import SourceFetchError, USER_AGENT


def fetch_json(source: Source, timeout_seconds: int = 20) -> list[Article]:
    """Collect publication records from a public JSON endpoint.

    The configuration maps the top-level list and its title, link, date and
    optional summary fields.  It intentionally does not support authenticated
    APIs: Open News Monitor is source-first and public by default.
    """
    selectors = source.selectors or {}
    request = Request(source.url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceFetchError(f"Could not download or parse JSON from {source.name}: {error}") from error

    records = _field(payload, selectors["items"])
    if not isinstance(records, list):
        raise SourceFetchError(
            f"JSON field '{selectors['items']}' in {source.name} is not a list of publications."
        )

    articles: list[Article] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title = _clean_text(_field(record, selectors["title"]))
        href = _field(record, selectors["link"])
        date = _clean_text(_field(record, selectors["date"]))
        if not title or not isinstance(href, str) or not href or not date:
            continue
        summary = _clean_text(_field(record, selectors.get("summary", "")))
        articles.append(
            Article(
                source_id=source.id,
                source_name=source.name,
                title=title,
                url=urljoin(source.url, href),
                summary=summary,
                published_at=date,
            )
        )
    if not articles:
        raise SourceFetchError(
            f"JSON records were found in {source.name}, but none had title, date, and direct link together."
        )
    return articles


def _field(value: object, path: str) -> object:
    current = value
    if not path:
        return ""
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part, "")
    return current


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return " ".join(html.unescape(re.sub(r"\s+", " ", text)).split())
