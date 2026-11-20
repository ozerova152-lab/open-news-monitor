# Первый запуск Open News Monitor на Linux и macOS

Эта инструкция основана на коде и автоматических тестах проекта. Установка на реальных Linux и macOS пока не проходила отдельную контролируемую проверку, поэтому после установки сначала используйте только локальный Markdown-вывод.

## 1. Клонирование и виртуальное окружение

В Terminal на macOS или в shell на Linux:

```bash
git clone https://github.com/ozerova152-lab/open-news-monitor.git
cd open-news-monitor
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e .
./.venv/bin/open-news-monitor --help
```

Путь к репозиторию в последующих командах должен быть абсолютным, если запуск будет выполняться внешним планировщиком.

## 2. Два способа настроить источники

### Вариант A: интерактивный мастер

Подходит, когда источников немного и вы хотите добавлять их по одному с проверкой:

```bash
mkdir -p config/local runtime
./.venv/bin/open-news-monitor init-monitor config/local/my-monitor.json
```

Мастер добавляет источник только после вашего подтверждения и предлагает безопасную первую инициализацию.

### Вариант B: CSV-реестр

Подходит, когда нужно редактировать много источников в Excel или другой таблице. Скопируйте оба примера в локальную папку и затем замените демонстрационные строки своими:

```bash
mkdir -p config/local runtime
cp examples/csv-monitor-profile-example.json config/local/my-csv-monitor.json
cp examples/sources-registry-example.csv config/local/sources-registry-example.csv
```

CSV использует UTF-8 BOM и разделитель `;`. Все строки в примере выключены, поэтому он безопасен до настройки. Подробности: [SOURCES_CSV_RU.md](SOURCES_CSV_RU.md).

## 3. Локальная проверка и первый сбор

Для JSON-профиля:

```bash
./.venv/bin/open-news-monitor doctor config/local/my-monitor.json
./.venv/bin/open-news-monitor collect config/local/my-monitor.json
./.venv/bin/open-news-monitor status config/local/my-monitor.json
```

Для CSV-профиля сначала проверьте таблицу и источники:

```bash
./.venv/bin/open-news-monitor doctor config/local/my-csv-monitor.json
./.venv/bin/open-news-monitor sources validate config/local/my-csv-monitor.json
./.venv/bin/open-news-monitor sources check config/local/my-csv-monitor.json
./.venv/bin/open-news-monitor collect config/local/my-csv-monitor.json
./.venv/bin/open-news-monitor status config/local/my-csv-monitor.json
```

`doctor` и `sources validate` не делают сетевых запросов. `sources check` проверяет включённые источники через сборщик, но не меняет CSV, профиль или SQLite. `collect` записывает новые публикации в SQLite и, в отличие от `run`, ничего не отправляет во внешние каналы.

## 4. Первая инициализация

Если вы хотите сначала запомнить уже опубликованные новости без доставки:

```bash
./.venv/bin/open-news-monitor run config/local/my-monitor.json --initialize
```

Для CSV-профиля замените путь к профилю. Не включайте Telegram, email или Google Sheets до отдельного контролируемого теста.

## 5. Расписание

После ручной проверки используйте руководство [SCHEDULING_RU.md](SCHEDULING_RU.md):

- Linux: user systemd timer, cron как резервный вариант;
- macOS: пользовательский LaunchAgent;
- Windows: Task Scheduler.

`schedule-tick` запускается внешним планировщиком, но сам принимает решение о сборе и доставке по разделу `schedule` профиля.

## 6. Секреты

Создайте `.env` только в корне своего локального репозитория. Не добавляйте в Git токены, пароли, ключи сервисных аккаунтов, `config/local/`, `runtime/` и SQLite-базы.
