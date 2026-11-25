from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .models import Article, Source

USER_AGENT = "Open-News-Monitor/0.1 (+https://github.com/)"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


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

    parents = {child: parent for parent in root.iter() for child in parent}
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{*}entry")
    if not entries:
        raise SourceFetchError(f"No RSS or Atom entries were found in {source.name}.")

    articles: list[Article] = []
    for entry in entries:
        title = _text(entry, "title") or _text(entry, "{*}title")
        link = _link(entry, _effective_base(entry, parents, source.url))
        if not title or not link:
            continue
        summary = _text(entry, "description") or _text(entry, "{*}summary") or _text(entry, "{*}content") or ""
        published = _text(entry, "pubDate") or _text(entry, "{*}published")
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


def _link(entry: ET.Element, entry_base: str) -> str | None:
    rss_link = entry.find("link")
    if rss_link is not None and rss_link.text:
        return _resolve_link(rss_link.text, _with_xml_base(entry_base, rss_link))
    for link in entry.findall("{*}link"):
        href = link.attrib.get("href")
        relation = link.attrib.get("rel", "alternate")
        if href and relation in {"alternate", ""}:
            return _resolve_link(href, _with_xml_base(entry_base, link))
    return None


def _effective_base(element: ET.Element, parents: dict[ET.Element, ET.Element], document_url: str) -> str:
    chain = [element]
    current = element
    while current in parents:
        current = parents[current]
        chain.append(current)
    base = document_url
    for item in reversed(chain):
        base = _with_xml_base(base, item)
    return base


def _with_xml_base(base: str, element: ET.Element) -> str:
    xml_base = element.attrib.get(XML_BASE) or element.attrib.get("xml:base")
    return urljoin(base, xml_base.strip()) if xml_base and xml_base.strip() else base


def _resolve_link(value: str, base: str) -> str | None:
    candidate = urljoin(base, value.strip())
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


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
