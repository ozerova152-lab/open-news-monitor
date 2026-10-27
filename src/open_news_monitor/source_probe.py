from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .rss import USER_AGENT
from .source_check import SourceCheck, inspect_rss_source


@dataclass(frozen=True)
class SourceProbe:
    """Safe result of examining one user-supplied public URL.

    A probe never changes a monitor.  `source` is present only when the same
    transport used by the monitor has confirmed title, date and direct link.
    """

    name: str
    submitted_url: str
    status: str
    message: str
    source: dict | None = None


def probe_source_url(url: str, name: str | None = None, timeout_seconds: int = 20) -> SourceProbe:
    """Try a public URL as RSS/Atom, an advertised feed, then open JSON.

    HTML is deliberately not guessed: a page is reported as needing a recipe
    until title, date and direct link can be verified from one repeated card.
    """
    source_name = name or _default_name(url)
    direct_feed = inspect_rss_source(url, source_name)
    if _is_confirmed_feed(direct_feed):
        return SourceProbe(
            source_name,
            url,
            "Можно подключать",
            "Найдена RSS/Atom-лента. Заголовок, дата и прямая ссылка подтверждены.",
            {"id": _source_id(url), "name": source_name, "kind": "rss", "url": url, "enabled": True},
        )

    try:
        payload, content_type = _download(url, timeout_seconds)
    except OSError as error:
        return SourceProbe(
            source_name,
            url,
            "Подключение следует отложить",
            f"Источник недоступен через сборщик: {error}",
        )

    json_result = _probe_json(url, source_name, payload, content_type)
    if json_result is not None:
        return json_result

    soup = BeautifulSoup(payload, "html.parser")
    for feed_url in _advertised_feeds(soup, url):
        check = inspect_rss_source(feed_url, source_name)
        if _is_confirmed_feed(check):
            return SourceProbe(
                source_name,
                url,
                "Можно подключать",
                "На странице найдена RSS/Atom-лента. Заголовок, дата и прямая ссылка подтверждены.",
                {"id": _source_id(feed_url), "name": source_name, "kind": "rss", "url": feed_url, "enabled": True},
            )

    if "html" in content_type.lower() or b"<html" in payload[:500].lower():
        return SourceProbe(
            source_name,
            url,
            "Нужна доработка",
            "Страница доступна, но безопасные селекторы карточки новости ещё не подтверждены. "
            "Нужна проверка одной повторяющейся карточки с заголовком, датой и прямой ссылкой.",
        )
    return SourceProbe(
        source_name,
        url,
        "Подключение следует отложить",
        "Не удалось подтвердить RSS/Atom, открытый JSON или безопасную HTML-структуру публикаций.",
    )


def probe_source_report(url: str, name: str | None = None) -> str:
    result = probe_source_url(url, name)
    lines = [
        f"# Проверка источника: {result.name}",
        "",
        f"**Статус: {result.status}**",
        "",
        f"- Исходный URL: {result.submitted_url}",
        f"- Результат: {result.message}",
    ]
    if result.source:
        lines.extend(
            [
                f"- Способ подключения: {result.source['kind'].upper()}",
                f"- Рабочий URL: {result.source['url']}",
                "- Первая инициализация: после подтверждения выполните `run ... --initialize`; старый архив не будет отправлен.",
            ]
        )
    else:
        lines.append("- Источник не добавлен, сообщения не отправлялись.")
    return "\n".join(lines)


def _download(url: str, timeout_seconds: int) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html,application/xhtml+xml"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read(), response.headers.get("Content-Type", "")


def _probe_json(url: str, name: str, payload: bytes, content_type: str) -> SourceProbe | None:
    if "json" not in content_type.lower() and not payload.lstrip().startswith((b"{", b"[")):
        return None
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SourceProbe(name, url, "Нужна доработка", "Ответ похож на JSON, но не читается как корректный публичный JSON.")

    mapping = _infer_json_mapping(document)
    if mapping is None:
        return SourceProbe(
            name,
            url,
            "Нужна доработка",
            "Открытый JSON доступен, но в нём не удалось безопасно распознать список публикаций с заголовком, датой и прямой ссылкой.",
        )
    return SourceProbe(
        name,
        url,
        "Можно подключать",
        "Открытый JSON подтверждён: найдены список публикаций, заголовок, дата и прямая ссылка.",
        {"id": _source_id(url), "name": name, "kind": "json", "url": url, "enabled": True, "selectors": mapping},
    )


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


def _default_name(url: str) -> str:
    return urlparse(url).netloc or "Checked source"


def _source_id(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return "".join(character if character.isalnum() else "-" for character in domain).strip("-") or "source"
