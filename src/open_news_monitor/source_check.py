from __future__ import annotations

from dataclasses import dataclass

from .models import Article, Source
from .rss import SourceFetchError, fetch_feed


@dataclass(frozen=True)
class SourceCheck:
    name: str
    url: str
    articles: list[Article]
    error: str | None = None

    @property
    def dated_count(self) -> int:
        return sum(article.published_at is not None for article in self.articles)

    @property
    def status(self) -> str:
        if self.error:
            return "Подключение следует отложить"
        if not self.articles:
            return "Нужна доработка"
        if self.dated_count == len(self.articles):
            return "Можно подключать"
        return "Нужна доработка"


def inspect_rss_source(url: str, name: str = "Checked source") -> SourceCheck:
    """Inspect a public RSS/Atom source without adding it to a monitor."""
    source = Source(id="source-check", name=name, kind="rss", url=url)
    try:
        articles = fetch_feed(source)
    except SourceFetchError as error:
        return SourceCheck(name=name, url=url, articles=[], error=str(error))
    return SourceCheck(name=name, url=url, articles=articles)


def check_rss_source(url: str, name: str = "Checked source") -> str:
    check = inspect_rss_source(url, name)
    if check.error:
        return "\n".join(
            [
                f"# Проверка источника: {check.name}",
                "",
                "**Статус: Подключение следует отложить**",
                "",
                f"Причина: {check.error}",
                "Источник не добавлен и никаких сообщений не отправлено.",
            ]
        )

    if not check.articles:
        return "\n".join(
            [
                f"# Проверка источника: {check.name}",
                "",
                "**Статус: Нужна доработка**",
                "",
                "Причина: RSS/Atom-лента доступна, но в ней нет пригодных публикаций с заголовком и прямой ссылкой.",
                "Источник не добавлен и никаких сообщений не отправлено.",
            ]
        )

    first = check.articles[0]
    lines = [
        f"# Проверка источника: {check.name}",
        "",
        f"**Статус: {check.status}**",
        "",
        "| Поле | Результат |",
        "| --- | --- |",
        f"| URL | {check.url} |",
        "| Метод | RSS/Atom |",
        f"| Доступ через парсер агента | Получено {len(check.articles)} публикаций |",
        f"| Заголовки | Подтверждены у {len(check.articles)} публикаций |",
        f"| Прямые ссылки | Подтверждены у {len(check.articles)} публикаций |",
        f"| Даты | Подтверждены у {check.dated_count} из {len(check.articles)} публикаций |",
        "| Пагинация и полнота | Не подтверждены этой проверкой; RSS отражает доступное окно ленты |",
        "| Первая инициализация | Перед подключением выполните `run ... --initialize`, чтобы архив не был доставлен как новый |",
        "",
        "## Самая новая публикация в доступном окне",
        "",
        f"- **Заголовок:** {first.title}",
        f"- **Дата:** {first.published_at or 'не предоставлена источником'}",
        f"- **Ссылка:** {first.url}",
        "",
        "Источник не добавлен в конфигурацию и никаких сообщений не отправлено.",
    ]
    return "\n".join(lines)
