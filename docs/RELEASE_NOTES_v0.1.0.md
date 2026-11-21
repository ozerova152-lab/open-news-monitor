# Open News Monitor v0.1.0 · Черновик / Draft

## Русский

Первый публичный выпуск Новостника для локального мониторинга выбранных публичных источников. Мастер помогает проверить источники и создать профиль. Безопасная инициализация запоминает текущие публикации без отправки. SQLite хранит известные ссылки, а Markdown, Telegram, email и Google Sheets позволяют получать подборки. Для Telegram и email можно включить фрагмент исходного описания. CSV-реестр использует UTF-8 с BOM и разделитель «;».

Проверены локальный Markdown, реальные доставки Telegram и Gmail и матрица GitHub Actions на Windows и Ubuntu. У Google Sheets прошли 13 автотестов и ранее прошла реальная запись. Текущий доступ к Google повторно не проверялся.

- Windows отказала в регистрации задачи планировщика на тестовом компьютере. Перед использованием расписания проверьте его на своей системе.
- Установка и расписание Linux/macOS не проверялись на реальных системах.
- Excel 2010 импортировал CSV, сохранение CSV UTF-8 не подтверждено.
- HTML без найденной ленты требует проверенных правил. Подключение произвольных сайтов, графический интерфейс и встроенный AI-перевод отсутствуют.

[Установка и документация](../README.md)

## English

The first public release monitors public sources you choose on your own computer. The setup wizard checks sources and creates a profile. Safe initialization records current items without sending them. SQLite tracks known links. Digests can go to Markdown, Telegram, email and Google Sheets. Telegram and email can include excerpts of source descriptions. The optional CSV registry uses UTF-8 with BOM and a semicolon separator.

Local Markdown, live Telegram and Gmail delivery, and GitHub Actions on Windows and Ubuntu were checked. All 13 Google Sheets tests passed and a previous live write succeeded. Current Google access was not rechecked.

- Windows denied task registration on the test computer. Verify scheduling on your system before relying on it.
- Linux/macOS installation and scheduling have not been checked on real systems.
- Excel 2010 imported CSV successfully. Saving CSV UTF-8 remains unverified.
- HTML pages without a discovered feed need verified collection rules. Automatic connection of arbitrary websites, a graphical application interface and built-in AI translation are unavailable.

[Setup and documentation](../README.en.md)
