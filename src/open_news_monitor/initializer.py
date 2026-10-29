from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .source_check import SourceCheck, inspect_rss_source
from .source_probe import InspectionStatus, SourceInspection, SourceProbe, probe_source_url
from .pipeline import InitializationReport, initialize_monitor


def create_monitor_interactively(
    path: str | Path,
    *,
    input_func=input,
    output_func=print,
    inspect_func=None,
    probe_func=probe_source_url,
    initialize_func=initialize_monitor,
) -> Path:
    """Create a safe monitor profile from a short terminal interview."""
    destination = Path(path)
    name = input_func("Название мониторинга [My news monitor]: ").strip() or "My news monitor"
    output_func("Добавьте публичные URL источников по одному. Пустая строка завершит ввод.")

    sources: list[dict] = []
    used_ids: set[str] = set()
    while True:
        url = input_func("URL источника: ").strip()
        if not url:
            break
        source_name = input_func("Название источника [по домену]: ").strip() or urlparse(url).netloc
        if inspect_func is not None:
            check: SourceCheck = inspect_func(url, source_name)
            probe: SourceInspection | SourceProbe = SourceInspection(
                source_name,
                url,
                "rss_or_atom",
                InspectionStatus.READY if check.status == "Можно подключать" and check.articles else InspectionStatus.NEEDS_SETUP,
                "Источник проверен." if check.articles else "В источнике не удалось подтвердить пригодные публикации.",
                title_confirmed=bool(check.articles),
                date_confirmed=check.dated_count == len(check.articles) and bool(check.articles),
                link_confirmed=bool(check.articles),
                publication_count=len(check.articles),
                source={"id": _source_id(url, used_ids), "name": source_name, "kind": "rss", "url": url, "enabled": True}
                if check.status == "Можно подключать" and check.articles else None,
            )
        else:
            probe: SourceProbe = probe_func(url, source_name)
        _show_probe_result(probe, output_func)
        if not _can_connect(probe):
            _show_unavailable_message(probe, output_func)
            continue
        if not _confirmed(input_func("Добавить этот источник? Да/Нет: ")):
            output_func("Источник не добавлен. Можно проверить другую ссылку.")
            continue
        source = dict(probe.source)
        source_id = _source_id(source["url"], used_ids)
        used_ids.add(source_id)
        source["id"] = source_id
        source["name"] = source_name
        sources.append(source)
        output_func(f"Источник добавлен: {source_name}")

    if not sources:
        raise ValueError("Не добавлено ни одного подтверждённого источника.")

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
    output_func("Сейчас агент может запомнить уже опубликованные новости. Они не будут отправлены вам повторно. При следующих проверках вы будете получать только новые публикации.")
    if _confirmed(input_func("Запомнить текущие публикации как отправную точку? Да/Нет: ")):
        _show_initialization_result(initialize_func(config), output_func)
    else:
        output_func("Инициализация не выполнена. При первом обычном запуске могут быть получены уже существующие публикации.")
        output_func(f"Позже выполните безопасную инициализацию: open-news-monitor run {destination} --initialize")
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


def _can_connect(probe: SourceProbe | object) -> bool:
    return bool(
        getattr(probe, "can_connect", False)
        or (getattr(probe, "status", None) == "Можно подключать" and getattr(probe, "source", None))
    )


def _show_probe_result(probe: SourceInspection | SourceProbe, output_func) -> None:
    output_func("")
    output_func(f"Проверяемый источник: {probe.name}")
    output_func(f"Адрес: {_probe_url(probe)}")
    count = getattr(probe, "publication_count", 0)
    if count:
        output_func(f"Подтверждено публикаций: {count}.")
    elif _can_connect(probe):
        output_func("Публикации подтверждены.")
    else:
        output_func("Подтверждённых публикаций нет.")
    discovered_feed_url = getattr(probe, "discovered_feed_url", None)
    if discovered_feed_url:
        output_func(f"Найдена новостная лента: {discovered_feed_url}")
        output_func("Новости будут собираться через найденную ленту.")
    if _can_connect(probe):
        output_func("Источник можно подключить.")


def _show_unavailable_message(probe: SourceInspection | SourceProbe, output_func) -> None:
    status = str(getattr(probe, "status", ""))
    message = getattr(probe, "message", "")
    if status == InspectionStatus.NEEDS_SETUP.value:
        output_func("Сайт доступен, но автоматическое подключение пока невозможно.")
        output_func(message or "Для этого сайта нужна дополнительная настройка.")
        output_func("Источник не добавлен. Попробуйте другую ссылку.")
    elif status == InspectionStatus.UNSUPPORTED.value:
        output_func("Этот формат источника пока не поддерживается агентом.")
        output_func(message or "Источник не добавлен. Попробуйте другую ссылку.")
    elif status == InspectionStatus.ERROR.value:
        output_func("Проверка не завершилась из-за технической проблемы. Источник не считается неподдерживаемым.")
        output_func(message or "Попробуйте повторить проверку позже.")
    else:
        output_func("Источник не добавлен. Агент не смог подтвердить заголовок, дату и прямую ссылку.")


def _probe_url(probe: SourceInspection | SourceProbe) -> str:
    return str(getattr(probe, "source_url", getattr(probe, "submitted_url", "")))


def _confirmed(value: str) -> bool:
    return value.strip().casefold() in {"да", "д", "yes", "y"}


def _show_initialization_result(report: InitializationReport, output_func) -> None:
    output_func(f"Инициализация: проверено источников — {report.checked_sources}; сохранено публикаций — {report.saved_articles}.")
    for source in report.sources:
        if source.get("status") == "ok":
            output_func(f"{source.get('name', 'Источник')}: сохранено {source.get('saved', 0)} из {source.get('articles', 0)} публикаций.")
        else:
            output_func(f"{source.get('name', 'Источник')}: не обработан — {source.get('error', 'техническая ошибка')}.")
    if report.errors:
        output_func("Инициализация завершилась не полностью. Обычная доставка не запускалась. Подробности сохранены в файле статуса.")
    elif report.complete:
        output_func("Отправная точка сохранена. При следующих проверках будут показаны только новые публикации.")
    else:
        output_func("Инициализация не подтверждена полностью. Обычная доставка не запускалась.")


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
