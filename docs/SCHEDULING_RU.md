# Автоматический запуск Open News Monitor

Внешний планировщик не ищет новости и не решает, что отправлять. Он только регулярно запускает:

```text
open-news-monitor schedule-tick <абсолютный-путь-к-профилю>
```

Дальше сам профиль определяет часовой пояс, частоту сбора, время доставки и включённые каналы. SQLite сохраняет успешные интервалы и не позволяет повторному tick повторно обработать тот же слот.

Перед настройкой выполните локальную проверку:

```text
open-news-monitor doctor <абсолютный-путь-к-профилю>
```

Все примеры используют абсолютные пути и рабочую папку проекта. Это важно: локальный `.env` загружается из текущей рабочей папки. Не помещайте токены, пароли или ключи в аргументы планировщика, журналы или файлы, попадающие в Git.

## Формат расписания в профиле

```json
"schedule": {
  "enabled": true,
  "timezone": "Europe/Moscow",
  "collect_every_minutes": 180,
  "deliver_at": {
    "telegram": ["10:00", "18:00"],
    "google_sheets": ["20:00"]
  }
}
```

`timezone` — IANA-часовой пояс. Время из `deliver_at` интерпретируется именно в нём. Внешний планировщик можно запускать каждые пять минут: `schedule-tick` сам решит, наступило ли действие.

Если компьютер был выключен, публикации не собираются в выключенное время. После следующего tick агент делает один актуальный сбор, но не воспроизводит цепочку старых интервалов. Неуспешный сбор или доставка не отмечаются как завершённые и остаются доступными для следующей попытки.

## Windows: ручная задача Task Scheduler

Используйте этот вариант, только если ваша учётная запись имеет право создавать обычные пользовательские задачи.

### Параметры задачи

Замените пути на свои, сохраняя их абсолютными.

| Поле Task Scheduler | Значение |
| --- | --- |
| Program/script | `C:\\Users\\YourName\\Open News Monitor\\.venv\\Scripts\\python.exe` |
| Add arguments | `-m open_news_monitor.cli schedule-tick "C:\\Users\\YourName\\Open News Monitor\\config\\local\\my-monitor.json"` |
| Start in | `C:\\Users\\YourName\\Open News Monitor` |
| Trigger | At log on, then repeat every 5 minutes |
| User | Текущий пользователь, без повышения прав |

Создайте задачу в интерфейсе Task Scheduler вручную. Не выбирайте запуск от `SYSTEM`, не сохраняйте пароль в аргументах и не обходите системные ограничения безопасности.

### Проверка и остановка

Проверьте последний запуск через интерфейс Task Scheduler и локальный статус:

```powershell
& "C:\\Users\\YourName\\Open News Monitor\\.venv\\Scripts\\python.exe" -m open_news_monitor status "C:\\Users\\YourName\\Open News Monitor\\config\\local\\my-monitor.json"
```

Отключите или удалите вручную созданную задачу через Task Scheduler. Команды `schedule disable`, `schedule enable`, `schedule remove` применимы только к задаче, созданной самой командой `schedule install`: вручную созданная задача не содержит её служебную метку.

### Ограничение прав

На части компьютеров встроенная команда `schedule install` может завершиться с `HRESULT 0x80070005 (Access denied)`. Это означает отказ Windows в регистрации задачи для текущей учётной записи. Не запускайте проект от администратора и не меняйте политики безопасности без отдельного решения пользователя или администратора.

## Linux: systemd user timer

После установки проекта создайте два файла в `~/.config/systemd/user/`.

`open-news-monitor.service`:

```ini
[Unit]
Description=Open News Monitor schedule tick

[Service]
Type=oneshot
WorkingDirectory=/home/yourname/open-news-monitor
Environment=PYTHONIOENCODING=utf-8
ExecStart=/home/yourname/open-news-monitor/.venv/bin/python -m open_news_monitor.cli schedule-tick /home/yourname/open-news-monitor/config/local/my-monitor.json
```

`open-news-monitor.timer`:

```ini
[Unit]
Description=Run Open News Monitor every five minutes

[Timer]
OnStartupSec=1min
OnUnitActiveSec=5min
AccuracySec=1min
Unit=open-news-monitor.service

[Install]
WantedBy=timers.target
```

Установка и проверка:

```bash
systemctl --user daemon-reload
systemctl --user enable --now open-news-monitor.timer
systemctl --user list-timers open-news-monitor.timer
journalctl --user -u open-news-monitor.service
```

Остановка и удаление:

```bash
systemctl --user disable --now open-news-monitor.timer
rm ~/.config/systemd/user/open-news-monitor.service ~/.config/systemd/user/open-news-monitor.timer
systemctl --user daemon-reload
```

Пользовательский systemd-manager обычно запускается после входа пользователя. Для работы без активного входа нужны отдельные настройки системы, которые не входят в Open News Monitor.

### Linux: cron как резервный вариант

Если systemd user timer недоступен, добавьте в пользовательский crontab строку с абсолютными путями:

```cron
CRON_TZ=Europe/Moscow
*/5 * * * * cd /home/yourname/open-news-monitor && /home/yourname/open-news-monitor/.venv/bin/python -m open_news_monitor.cli schedule-tick /home/yourname/open-news-monitor/config/local/my-monitor.json >> /home/yourname/open-news-monitor/runtime/schedule.log 2>&1
```

Cron не запускает задачу, пока компьютер выключен. В переходы летнего/зимнего времени cron может пропустить несуществующий час или запустить совпавший час дважды; Open News Monitor дополнительно защищает уже обработанные интервалы через SQLite.

## macOS: пользовательский LaunchAgent

Создайте `~/Library/LaunchAgents/com.opennewsmonitor.schedule.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.opennewsmonitor.schedule</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/yourname/open-news-monitor/.venv/bin/python</string>
    <string>-m</string>
    <string>open_news_monitor.cli</string>
    <string>schedule-tick</string>
    <string>/Users/yourname/open-news-monitor/config/local/my-monitor.json</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/yourname/open-news-monitor</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONIOENCODING</key>
    <string>utf-8</string>
  </dict>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/yourname/open-news-monitor/runtime/schedule.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/yourname/open-news-monitor/runtime/schedule-error.log</string>
</dict>
</plist>
```

Создайте папку `runtime` заранее. Установка, проверка и остановка:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.opennewsmonitor.schedule.plist
launchctl print gui/$(id -u)/com.opennewsmonitor.schedule
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.opennewsmonitor.schedule.plist
rm ~/Library/LaunchAgents/com.opennewsmonitor.schedule.plist
```

LaunchAgent работает в пользовательской сессии. Если Mac выключен, задания не выполняются до следующего доступного запуска.

## Проверка состояния без изменения данных

Команда статуса только читает последний локальный файл состояния:

```text
open-news-monitor status <абсолютный-путь-к-профилю>
```

Для технической проверки интервалов можно открыть SQLite в любом просмотрщике SQLite и посмотреть таблицу `schedule_runs`. Не редактируйте её вручную. В ней видны завершённые сборы и доставки; сами публикации и очереди остаются в отдельных таблицах.

## Что проверять после настройки

1. Внешний планировщик вызвал `schedule-tick`.
2. В `schedule_runs` появилась новая запись.
3. `status` показывает актуальное время и отсутствие неожиданной ошибки.
4. При отключённых каналах не возникло внешней отправки.
5. Повторный tick в том же интервале не повторил сбор или доставку.
