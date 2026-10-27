from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .source_check import SourceCheck, inspect_rss_source


def create_monitor_interactively(
    path: str | Path,
    *,
    input_func=input,
    output_func=print,
    inspect_func=inspect_rss_source,
) -> Path:
    """Create a safe RSS monitor profile from a short terminal interview."""
    destination = Path(path)
    name = input_func("Название мониторинга [My news monitor]: ").strip() or "My news monitor"
    output_func("Добавьте публичные RSS/Atom URL по одному. Пустая строка завершит ввод.")

    sources: list[dict] = []
    used_ids: set[str] = set()
    while True:
        url = input_func("RSS/Atom URL: ").strip()
        if not url:
            break
        source_name = input_func("Название источника [по домену]: ").strip() or urlparse(url).netloc
        check: SourceCheck = inspect_func(url, source_name)
        output_func(f"{source_name}: {check.status}")
        if check.status != "Можно подключать":
            output_func("Источник не добавлен. Сначала изучите отчёт команды check-source.")
            continue
        source_id = _source_id(url, used_ids)
        used_ids.add(source_id)
        sources.append(
            {"id": source_id, "name": source_name, "kind": "rss", "url": url, "enabled": True}
        )
        output_func(f"Источник добавлен: {source_name}")

    if not sources:
        raise ValueError("Не добавлено ни одного подтверждённого RSS/Atom-источника.")

    include_any = _phrases(input_func("Слова для включения через запятую [всё из источников]: "))
    exclude_any = _phrases(input_func("Слова для исключения через запятую [нет]: "))
    outputs = _choose_outputs(input_func, output_func)
    slug = _slug(name)
    config = {
        "monitor": {
            "name": name,
            "timezone": "Europe/Moscow",
            "database_path": f"runtime/{slug}.sqlite3",
        },
        "sources": sources,
        "filters": {"include_any": include_any, "exclude_any": exclude_any},
        "outputs": {"markdown": {"enabled": True, "path": f"runtime/{slug}-digest.md"}, **outputs},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_func(f"Профиль создан: {destination}")
    output_func(f"Сначала создайте безопасную границу: open-news-monitor run {destination} --initialize")
    return destination


def _choose_outputs(input_func, output_func) -> dict:
    output_func("Куда отправлять новые публикации? Можно выбрать несколько вариантов.")
    output_func("1 — Google Sheets; 2 — Telegram-бот; 3 — email; Enter — пока только локальный Markdown.")
    choices = {value.strip() for value in input_func("Выбор через запятую: ").split(",") if value.strip()}
    unknown = choices - {"1", "2", "3"}
    if unknown:
        output_func("Неизвестные варианты проигнорированы: " + ", ".join(sorted(unknown)))

    sheets_enabled = "1" in choices
    spreadsheet_id = ""
    worksheet = "News"
    if sheets_enabled:
        spreadsheet_id = input_func("ID или URL Google Sheets: ").strip()
        worksheet = input_func("Название листа [News]: ").strip() or "News"
        if not spreadsheet_id:
            output_func("Google Sheets выбран, но ID не указан: канал останется выключенным до заполнения конфигурации.")
            sheets_enabled = False
        else:
            spreadsheet_id = _sheet_id(spreadsheet_id)
            output_func("После создания профиля укажите путь к ключу сервисного аккаунта в .env.")

    telegram_enabled = "2" in choices
    if telegram_enabled:
        output_func("После создания профиля укажите токен бота и ID чата в .env.")

    email_enabled = "3" in choices
    recipients: list[str] = []
    if email_enabled:
        recipients = _phrases(input_func("Email получателей через запятую: "))
        if not recipients:
            output_func("Email выбран, но получатели не указаны: канал останется выключенным.")
            email_enabled = False
        else:
            output_func("После создания профиля укажите SMTP-настройки в .env.")

    return {
        "telegram": {"enabled": telegram_enabled},
        "email": {"enabled": email_enabled, "recipients": recipients},
        "google_sheets": {
            "enabled": sheets_enabled,
            "spreadsheet_id": spreadsheet_id,
            "worksheet": worksheet,
        },
    }


def _phrases(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "news-monitor"


def _source_id(url: str, used_ids: set[str]) -> str:
    domain = re.sub(r"[^a-z0-9]+", "-", urlparse(url).netloc.lower()).strip("-") or "source"
    candidate, index = domain, 2
    while candidate in used_ids:
        candidate = f"{domain}-{index}"
        index += 1
    return candidate


def _sheet_id(value: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
    return match.group(1) if match else value
