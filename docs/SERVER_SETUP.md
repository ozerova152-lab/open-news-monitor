# Linux server deployment

Use a dedicated Linux host that remains online, with Python 3.11+ and systemd. The examples assume an administrator provisions the host and that the server clock is synchronized. Review the unit files before installation.

1. Create an unprivileged system user and group named `open-news-monitor`. Install a reviewed checkout at `/opt/open-news-monitor`. Create its virtual environment with `python3 -m venv /opt/open-news-monitor/.venv` and install with `/opt/open-news-monitor/.venv/bin/python -m pip install /opt/open-news-monitor`. Keep this directory writable only by the deployment administrator.
2. Put the profile and `.env` in `/etc/open-news-monitor`. Restrict the directory and files to the administrator and service account. Keep the credentials out of Git and the journal. Use absolute paths under `/var/lib/open-news-monitor/` for SQLite, status and Markdown. Use an absolute path for any service-account key. Enable only approved channels. Install the optional Sheets extra only if needed.
3. Add `"freshness": {"max_age_hours": 48}` to the server profile. Set its schedule with timezone `Europe/Moscow`, an enabled collection interval and the selected channel's `deliver_at` at `10:00`. The five-minute timer checks what is due. Delivery may follow the target time by the polling interval and processing time.
4. Create the state directory with owner `open-news-monitor` and mode `0700` for initial testing. From the profile directory, run `doctor` as the service account. Initialize using `run <profile> --initialize` with external channels disabled. Verify the first source baseline and review local output before enabling delivery.
5. After explicit approval, install both example unit files under `/etc/systemd/system/`, run `systemctl daemon-reload` and `systemctl enable --now open-news-monitor.timer`. Inspect `systemctl list-timers open-news-monitor.timer`, `journalctl -u open-news-monitor.service` and the local CLI `history <profile>`. Test with an isolated profile and external channels disabled first.
6. After downtime, systemd can trigger a check after restart. The application processes the current due work rather than replaying each missed interval. Old queue entries remain visible in history when withheld by freshness rules. Review receipt at the destination before repeating ambiguous deliveries.
7. To back up or update, stop the timer, wait for the service to finish, and confirm no other runner uses the database. Take a protected SQLite online backup using SQLite's backup API or copy the entire quiescent state directory, including any SQLite sidecars. Back up the profile and secrets separately with restricted access. Install the new checkout into a separate environment, verify against a copy, then resume the timer. For rollback, restore the previous software and its matching state backup before re-enabling scheduling. Reconcile externally delivered items before restoring an older queue.
8. To stop, run `systemctl disable --now open-news-monitor.timer` and check the service state. To uninstall, remove the two unit files and reload systemd. Retain configuration and state until you have verified a backup and explicitly decided to remove them.

The unit templates passed a syntax check in Ubuntu WSL using an available executable. A separate transient user timer ran a clean package installation with a temporary profile and produced Markdown and history. The persistent system unit, dedicated service account, restart recovery and delivery remain to be verified on the target server.

## Переход действующей рассылки

Сначала сделайте отдельную копию профиля и базы или создайте временный профиль на фиктивных данных. Проверьте сбор с включённой защитой от архива и отключёнными внешними каналами. Второй запуск должен сохранить только новые записи и добавить строку в историю. Не переносите старую очередь в новый профиль автоматически. Если перенос нужен, проверьте каждую отложенную публикацию и сверку доставок.

После согласования отключите старую автоматизацию Codex, подтвердите отсутствие второго запуска рабочего профиля и только затем настройте системное расписание. Сначала проверьте системную задачу с локальной подборкой. Реальную доставку тестируйте отдельно после разрешения. При недоступной машине в 10:00 отправку на эту минуту гарантировать нельзя.

### План перехода рабочего профиля

1. Согласовать окно перехода. До его начала проверить новую версию на фиктивном источнике и отдельной SQLite с выводом только в Markdown. Сверить два запуска в одном интервале по истории.
2. После явного разрешения остановить старую автоматизацию, дождаться окончания текущего процесса, затем сделать защищённую копию профиля, базы и её файлов состояния. Сохранить конфигурацию старого расписания.
3. Проверить новую версию на копии базы. Сохранить историю отправок. В копии профиля включить freshness.max_age_hours, выполнить проверку стартовых границ без доставки и просмотреть удержанные записи. Не очищать sent-историю.
4. После отдельного разрешения настроить один независимый запуск, с timezone Europe/Moscow и Telegram deliver_at в 10:00. Проверить действие, рабочую папку, Python и результат системной задачи. Первый реальный тест провести в согласованное время и место, затем сверить запись истории и получение в Telegram.
5. Для возврата сначала остановить новый запуск. Сверить, что уже было доставлено после резервной копии. Только после сверки и разрешения вернуть прежние настройки и при необходимости прежнюю базу. Бездумное восстановление старой очереди может повторно разослать материалы.

For existing profiles, enable freshness only with the user's consent. Review held items with `open-news-monitor withheld <profile>` and run history with `open-news-monitor history <profile>`. A Telegram no-news notice is optional and can be suppressed by source errors or uncertain dates. A lost delivery confirmation can still lead to an external duplicate.
