from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .models import Article, Source

USER_AGENT = "Open-News-Monitor/0.1 (+https://github.com/)"


class SourceFetchError(RuntimeError):
    """Raised when a source cannot be downloaded or parsed."""


def fetch_feed(source: Source, timeout_seconds: int = 20) -> list[Article]:
    request = Request(source.url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
    except OSError as error:
        raise SourceFetchError(f"Could not download {source.name}: {error}") from error

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise SourceFetchError(f"Could not parse RSS/Atom from {source.name}: {error}") from error

    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{*}entry")
    if not entries:
        raise SourceFetchError(f"No RSS or Atom entries were found in {source.name}.")

    articles: list[Article] = []
    for entry in entries:
        title = _text(entry, "title") or _text(entry, "{*}title")
        link = _link(entry)
        if not title or not link:
            continue
        summary = _text(entry, "description") or _text(entry, "{*}summary") or _text(entry, "{*}content") or ""
        published = _text(entry, "pubDate") or _text(entry, "{*}published") or _text(entry, "{*}updated")
        articles.append(
            Article(
                source_id=source.id,
                source_name=source.name,
                title=_clean_text(title),
                url=link.strip(),
                summary=_clean_text(summary),
                published_at=_normalise_date(published),
            )
        )
    return articles


def _text(element: ET.Element, selector: str) -> str | None:
    found = element.find(selector)
    return found.text if found is not None and found.text else None


def _link(entry: ET.Element) -> str | None:
    rss_link = _text(entry, "link")
    if rss_link:
        return rss_link
    for link in entry.findall("{*}link"):
        href = link.attrib.get("href")
        relation = link.attrib.get("rel", "alternate")
        if href and relation in {"alternate", ""}:
            return href
    return None


def _clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _normalise_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value.strip()
