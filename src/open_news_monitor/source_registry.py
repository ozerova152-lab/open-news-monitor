from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .html_collector import fetch_html
from .models import Source
from .rss import SourceFetchError
from .source_probe import InspectionStatus, SourceInspection

CSV_COLUMNS = (
    "id", "enabled", "group", "name", "url", "kind", "item_or_card",
    "title", "link", "date", "summary", "notes",
)
SUPPORTED_KINDS = {"rss", "atom", "html", "json"}


class SourceRegistryError(ValueError):
    """Raised when a CSV source registry cannot be used safely."""


def load_sources_registry(settings: object, profile_path: str | Path) -> list[dict[str, Any]]:
    """Load an Excel-friendly CSV registry into the existing source shape."""
    if not isinstance(settings, dict):
        raise SourceRegistryError("sources_registry должен быть объектом с путём к CSV-файлу.")
    csv_path_value = settings.get("path")
    if not isinstance(csv_path_value, str) or not csv_path_value.strip():
        raise SourceRegistryError("sources_registry.path должен указывать на CSV-файл источников.")
    encoding = settings.get("encoding", "utf-8-sig")
    delimiter = settings.get("delimiter", ";")
    if encoding != "utf-8-sig":
        raise SourceRegistryError("CSV-реестр должен использовать кодировку utf-8-sig для корректной работы с Excel и кириллицей.")
    if delimiter != ";":
        raise SourceRegistryError("CSV-реестр должен использовать разделитель ;.")

    path = Path(csv_path_value)
    if not path.is_absolute():
        path = Path(profile_path).parent / path
    path = path.resolve()
    try:
        stream = path.open("r", encoding=encoding, newline="")
    except FileNotFoundError as error:
        raise SourceRegistryError(f"CSV-реестр не найден: {path}") from error
    except OSError as error:
        raise SourceRegistryError(f"Не удалось открыть CSV-реестр: {path}. {error}") from error

    with stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        if reader.fieldnames is None:
            raise SourceRegistryError("CSV-реестр пустой: отсутствует строка заголовков.")
        headers = tuple((header or "").strip() for header in reader.fieldnames)
        missing = [column for column in CSV_COLUMNS if column not in headers]
        if missing:
            raise SourceRegistryError("В CSV-реестре отсутствуют колонки: " + ", ".join(missing) + ".")

        sources: list[dict[str, Any]] = []
        source_rows: dict[str, int] = {}
        for row_number, row in enumerate(reader, start=2):
            normalized = {key: (value or "").strip() for key, value in row.items()}
            if not any(normalized.values()):
                continue
            source = _source_from_row(normalized, row_number)
            source_id = source["id"]
            if source_id in source_rows:
                raise SourceRegistryError(
                    f"Строка CSV {row_number}: id '{source_id}' уже указан в строке {source_rows[source_id]}."
                )
            source_rows[source_id] = row_number
            sources.append(source)
    return sources


def registry_path(settings: object, profile_path: str | Path) -> Path:
    """Return the resolved CSV path for a user-facing validation report."""
    if not isinstance(settings, dict) or not isinstance(settings.get("path"), str):
        raise SourceRegistryError("sources_registry.path должен указывать на CSV-файл источников.")
    path = Path(settings["path"])
    return (Path(profile_path).parent / path).resolve() if not path.is_absolute() else path.resolve()


def check_configured_html_source(source_data: dict[str, Any]) -> SourceInspection:
    """Confirm a configured HTML recipe without storing or delivering articles."""
    source = Source(
        id=source_data["id"], name=source_data["name"], kind="html",
        url=source_data["url"], enabled=bool(source_data.get("enabled", True)),
        selectors=source_data.get("selectors"),
    )
    try:
        articles = fetch_html(source)
    except SourceFetchError as error:
        technical_reason = str(error)
        if technical_reason.startswith("Could not download"):
            return SourceInspection(
                source.name, source.url, "html", InspectionStatus.ERROR,
                "Не удалось завершить проверку HTML-источника из-за технической проблемы.",
                technical_reason=technical_reason,
            )
        return SourceInspection(
            source.name, source.url, "html", InspectionStatus.NEEDS_SETUP,
            "Правила HTML не подтвердили публикации с заголовком, датой и ссылкой.",
            technical_reason=technical_reason,
        )
    except (KeyError, TypeError) as error:
        return SourceInspection(
            source.name, source.url, "html", InspectionStatus.NEEDS_SETUP,
            "Для HTML-источника отсутствуют или некорректны правила извлечения.",
            technical_reason=str(error),
        )
    except Exception as error:
        return SourceInspection(
            source.name, source.url, "html", InspectionStatus.ERROR,
            "Не удалось завершить проверку HTML-источника из-за технической проблемы.",
            technical_reason=str(error),
        )

    confirmed = [article for article in articles if article.title and article.published_at and article.url]
    if not confirmed:
        return SourceInspection(
            source.name, source.url, "html", InspectionStatus.NEEDS_SETUP,
            "Правила HTML не подтвердили публикации с заголовком, датой и ссылкой.",
            technical_reason="HTML collector returned no complete publications.",
        )
    return SourceInspection(
        source.name, source.url, "html", InspectionStatus.READY,
        "HTML-источник проверен по заданным правилам извлечения.",
        title_confirmed=True, date_confirmed=True, link_confirmed=True,
        publication_count=len(confirmed), source=dict(source_data),
    )


def _source_from_row(row: dict[str, str], row_number: int) -> dict[str, Any]:
    for column in ("id", "enabled", "name", "url", "kind"):
        if not row.get(column):
            raise SourceRegistryError(f"Строка CSV {row_number}: не заполнена обязательная колонка '{column}'.")
    source_id = row["id"]
    enabled = _parse_enabled(row["enabled"], row_number)
    kind = row["kind"].casefold()
    if kind not in SUPPORTED_KINDS:
        raise SourceRegistryError(
            f"Строка CSV {row_number}: kind '{row['kind']}' не поддерживается. Допустимы: rss, atom, html, json."
        )
    url = row["url"]
    if not url.startswith(("https://", "http://")):
        raise SourceRegistryError(f"Строка CSV {row_number}: url должен начинаться с http:// или https://.")

    source: dict[str, Any] = {
        "id": source_id,
        "name": row["name"],
        "kind": kind,
        "url": url,
        "enabled": enabled,
    }
    if row.get("group"):
        source["group"] = row["group"]
    if row.get("notes"):
        source["notes"] = row["notes"]
    if kind in {"html", "json"}:
        fields = ("item_or_card", "title", "link", "date")
        missing = [field for field in fields if not row.get(field)]
        if missing:
            raise SourceRegistryError(
                f"Строка CSV {row_number}: для {kind} нужны правила: " + ", ".join(missing) + "."
            )
        selectors = {
            ("card" if kind == "html" else "items"): row["item_or_card"],
            "title": row["title"],
            "link": row["link"],
            "date": row["date"],
        }
        if row.get("summary"):
            selectors["summary"] = row["summary"]
        source["selectors"] = selectors
    return source


def _parse_enabled(value: str, row_number: int) -> bool:
    normalized = value.casefold()
    if normalized in {"true", "1", "yes", "да"}:
        return True
    if normalized in {"false", "0", "no", "нет"}:
        return False
    raise SourceRegistryError(
        f"Строка CSV {row_number}: enabled должен быть TRUE или FALSE."
    )
