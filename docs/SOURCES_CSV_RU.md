# CSV-реестр источников

CSV-реестр позволяет редактировать список источников в Excel, не меняя сложный JSON-профиль. Реестр не содержит токены, пароли или настройки доставки.

## Подключение

Добавьте в JSON-профиль необязательный раздел:

```json
"sources_registry": {
  "path": "my-sources.csv",
  "encoding": "utf-8-sig",
  "delimiter": ";"
}
```

Если раздел отсутствует, Open News Monitor продолжает использовать прежний массив `sources` из JSON. Если раздел указан, источники берутся только из CSV: два списка автоматически не объединяются.

## Колонки

```text
id;enabled;group;name;url;kind;item_or_card;title;link;date;summary;notes
```

- `id`, `enabled`, `name`, `url`, `kind` — обязательные.
- `enabled` — `TRUE` или `FALSE`.
- `group` и `notes` нужны для организации таблицы и пока не меняют фильтры или доставку.
- `kind`: `rss`, `atom`, `html` или `json`.
- Для `rss` и `atom` поля правил извлечения остаются пустыми.
- Для `html` заполните `item_or_card`, `title`, `link`, `date`. Поле `item_or_card` станет правилом `card`.
- Для `json` заполните `item_or_card`, `title`, `link`, `date`. Поле `item_or_card` станет правилом `items`.
- `summary` необязательно.

Используйте кодировку **UTF-8 с BOM** (`utf-8-sig`) и разделитель `;`. Так кириллица корректно открывается в Excel. Готовый выключенный шаблон: [sources-registry-example.csv](../examples/sources-registry-example.csv).

Готовый безопасный профиль, который использует этот шаблон: [csv-monitor-profile-example.json](../examples/csv-monitor-profile-example.json). Скопируйте оба файла в `config/local/`, не меняя имена, или укажите своё имя CSV в `sources_registry.path`.

## Проверка

Локальная проверка структуры без сетевых запросов:

```powershell
.\.venv\Scripts\open-news-monitor.exe sources validate config\local\my-monitor.json
```

Проверка включённых источников существующим механизмом probe, без изменения CSV, SQLite или профиля:

```powershell
.\.venv\Scripts\open-news-monitor.exe sources check config\local\my-monitor.json
```

`check` показывает `READY`, `NEEDS_SETUP`, `UNSUPPORTED` или `ERROR`. Для HTML-строки с заполненными правилами проверка использует эти правила и подтверждает извлечение публикаций без сохранения в SQLite. Не включайте новый или изменённый сайт в рабочий мониторинг, пока проверка не подтвердила реальные заголовок, дату и прямую ссылку.
