from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .json_collector import fetch_json
from .models import Article, Source
from .rss import USER_AGENT
from .source_check import SourceCheck, inspect_rss_source


class InspectionStatus(StrEnum):
    READY = "READY"
    NEEDS_SETUP = "NEEDS_SETUP"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class SourceInspection:
    """One safe, structured result for any public source URL."""

    name: str
    source_url: str
    source_type: str | None
    status: InspectionStatus
    message: str
    technical_reason: str | None = None
    discovered_feed_url: str | None = None
    title_confirmed: bool = False
    date_confirmed: bool = False
    link_confirmed: bool = False
    publication_count: int = 0
    source: dict | None = None

    @property
    def can_connect(self) -> bool:
        return self.status is InspectionStatus.READY and self.source is not None


@dataclass(frozen=True)
class SourceProbe:
    """Legacy helper shape retained for existing wizard integrations."""

    name: str
    submitted_url: str
    status: str
    message: str
    source: dict | None = None

    @property
    def can_connect(self) -> bool:
        return self.status in {"READY", "Можно подключать"} and self.source is not None


def probe_source_url(url: str, name: str | None = None, timeout_seconds: int = 20) -> SourceInspection:
    """Inspect a URL as RSS/Atom, advertised feed, JSON, or HTML.

    A result is READY only when the collector has extracted real publications
    containing title, date and direct link. HTML selector guessing is excluded.
    """
    source_name = name or _default_name(url)
    direct_feed = inspect_rss_source(url, source_name)
    if _is_confirmed_feed(direct_feed):
        return _ready_feed(source_name, url, direct_feed, discovered_feed_url=url)

    try:
        payload, content_type = _download(url, timeout_seconds)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        return SourceInspection(
            source_name, url, None, InspectionStatus.ERROR,
            "Не удалось завершить проверку: источник временно недоступен или сервер вернул ошибку.",
            technical_reason=str(error),
        )

    if _looks_like_feed(payload):
        return SourceInspection(
            source_name, url, _feed_type(payload), InspectionStatus.NEEDS_SETUP,
            "Лента обнаружена, но в ней пока нет публикаций с подтверждёнными заголовком, датой и ссылкой.",
            technical_reason=direct_feed.error or "No usable RSS/Atom publications were found.",
        )

    json_result = _probe_json(url, source_name, payload, content_type)
    if json_result is not None:
        return json_result

    if _looks_like_html(payload, content_type):
        soup = BeautifulSoup(payload, "html.parser")
        for feed_url in _advertised_feeds(soup, url):
            check = inspect_rss_source(feed_url, source_name)
            if _is_confirmed_feed(check):
                return _ready_feed(source_name, url, check, discovered_feed_url=feed_url)
            if _feed_appears_empty(check):
                return SourceInspection(
                    source_name, url, "rss", InspectionStatus.NEEDS_SETUP,
                    "На странице найдена лента, но в ней пока нет публикаций с подтверждёнными полями.",
                    technical_reason=check.error, discovered_feed_url=feed_url,
                )
        return SourceInspection(
            source_name, url, "html", InspectionStatus.NEEDS_SETUP,
            "Страница доступна, но для неё ещё не настроен безопасный способ извлечения новостей.",
            technical_reason="No confirmed RSS/Atom feed or configured HTML card recipe.",
        )

    return SourceInspection(
        source_name, url, None, InspectionStatus.UNSUPPORTED,
        "Источник доступен, но его формат не поддерживается текущими механизмами.",
        technical_reason=f"Unsupported content type: {content_type or 'not provided'}.",
    )


def probe_source_report(url: str, name: str | None = None) -> str:
    result = probe_source_url(url, name)
    lines = [
        f"# Проверка источника: {result.name}", "",
        f"**Статус: {result.status.value}**", "",
        f"- Адрес: {result.source_url}",
        f"- Тип: {result.source_type or 'не определён'}",
        f"- Объяснение: {result.message}",
        f"- Заголовок подтверждён: {'да' if result.title_confirmed else 'нет'}",
        f"- Дата подтверждена: {'да' if result.date_confirmed else 'нет'}",
        f"- Прямая ссылка подтверждена: {'да' if result.link_confirmed else 'нет'}",
    ]
    if result.discovered_feed_url:
        lines.append(f"- Найденная RSS/Atom-лента: {result.discovered_feed_url}")
    if result.technical_reason:
        lines.append(f"- Техническая причина: {result.technical_reason}")
    if result.can_connect:
        lines.append("- Источник можно добавить в профиль. Перед обычным запуском создайте стартовую границу через `run ... --initialize`.")
    else:
        lines.append("- Источник не добавлен, сообщения не отправлялись.")
    return "\n".join(lines)


def _ready_feed(name: str, submitted_url: str, check: SourceCheck, discovered_feed_url: str | None = None) -> SourceInspection:
    source_url = discovered_feed_url or submitted_url
    return SourceInspection(
        name, submitted_url, "rss_or_atom", InspectionStatus.READY,
        "Источник проверен: реальные публикации содержат заголовок, дату и прямую ссылку.",
        discovered_feed_url=discovered_feed_url, title_confirmed=True,
        date_confirmed=True, link_confirmed=True, publication_count=len(check.articles),
        source={"id": _source_id(source_url), "name": name, "kind": "rss", "url": source_url, "enabled": True},
    )


def _probe_json(url: str, name: str, payload: bytes, content_type: str) -> SourceInspection | None:
    if "json" not in content_type.lower() and not payload.lstrip().startswith((b"{", b"[")):
        return None
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return SourceInspection(name, url, "json", InspectionStatus.ERROR, "Сервер вернул некорректный JSON, поэтому проверка не завершена.", technical_reason=str(error))

    mapping = _infer_json_mapping(document)
    if mapping is None:
        return SourceInspection(name, url, "json", InspectionStatus.NEEDS_SETUP, "Открытый JSON найден, но агенту нужна дополнительная настройка полей публикации.", technical_reason="Could not safely identify publication list, title, date and direct link fields.")
    source = Source(id=_source_id(url), name=name, kind="json", url=url, selectors=mapping)
    try:
        articles = fetch_json(source)
    except Exception as error:
        return SourceInspection(name, url, "json", InspectionStatus.ERROR, "JSON найден, но реальные публикации не удалось подтвердить рабочим сборщиком.", technical_reason=str(error))
    title, date, link = _article_fields_confirmed(articles)
    if not all((title, date, link)):
        return SourceInspection(name, url, "json", InspectionStatus.NEEDS_SETUP, "JSON доступен, но не все обязательные поля публикаций подтверждены.", title_confirmed=title, date_confirmed=date, link_confirmed=link, publication_count=len(articles))
    return SourceInspection(
        name, url, "json", InspectionStatus.READY,
        "Источник проверен: реальные JSON-публикации содержат заголовок, дату и прямую ссылку.",
        title_confirmed=True, date_confirmed=True, link_confirmed=True, publication_count=len(articles),
        source={"id": _source_id(url), "name": name, "kind": "json", "url": url, "enabled": True, "selectors": mapping},
    )


def _download(url: str, timeout_seconds: int) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html,application/xhtml+xml,application/rss+xml,application/atom+xml"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read(), response.headers.get("Content-Type", "")


def _infer_json_mapping(document: object) -> dict[str, str] | None:
    containers = ("articles", "items", "results", "data")
    title_keys = ("title", "name", "headline")
    link_keys = ("html_url", "url", "link", "permalink")
    date_keys = ("published_at", "pubDate", "published", "created_at", "updated_at", "date")
    summary_keys = ("summary", "description", "excerpt", "body")
    if isinstance(document, list):
        records, items_key = document, ""
    elif isinstance(document, dict):
        items_key = next((key for key in containers if isinstance(document.get(key), list)), "")
        records = document.get(items_key) if items_key else None
    else:
        return None
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None
    sample = records[0]
    title = next((key for key in title_keys if isinstance(sample.get(key), str) and sample[key]), "")
    link = next((key for key in link_keys if isinstance(sample.get(key), str) and sample[key]), "")
    date = next((key for key in date_keys if isinstance(sample.get(key), str) and sample[key]), "")
    if not (title and link and date):
        return None
    mapping = {"items": items_key, "title": title, "link": link, "date": date}
    summary = next((key for key in summary_keys if isinstance(sample.get(key), str) and sample[key]), "")
    if summary:
        mapping["summary"] = summary
    return mapping


def _advertised_feeds(soup: BeautifulSoup, page_url: str) -> list[str]:
    urls: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = {value.lower() for value in (link.get("rel") or [])}
        type_ = str(link.get("type", "")).lower()
        if "alternate" in rel and type_ in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}:
            candidate = urljoin(page_url, str(link["href"]))
            if candidate not in urls:
                urls.append(candidate)
    return urls


def _is_confirmed_feed(check: SourceCheck) -> bool:
    return check.status == "Можно подключать" and bool(check.articles)


def _feed_appears_empty(check: SourceCheck) -> bool:
    return bool(check.error and "No RSS or Atom entries" in check.error)


def _article_fields_confirmed(articles: list[Article]) -> tuple[bool, bool, bool]:
    return (
        bool(articles) and all(bool(article.title) for article in articles),
        bool(articles) and all(bool(article.published_at) for article in articles),
        bool(articles) and all(bool(article.url) for article in articles),
    )


def _looks_like_feed(payload: bytes) -> bool:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return False
    return root.tag.rsplit("}", 1)[-1].lower() in {"rss", "feed", "rdf"}


def _feed_type(payload: bytes) -> str:
    root = ET.fromstring(payload)
    return "atom" if root.tag.rsplit("}", 1)[-1].lower() == "feed" else "rss"


def _looks_like_html(payload: bytes, content_type: str) -> bool:
    return "html" in content_type.lower() or b"<html" in payload[:500].lower()


def _default_name(url: str) -> str:
    return urlparse(url).netloc or "Checked source"


def _source_id(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return "".join(character if character.isalnum() else "-" for character in domain).strip("-") or "source"
