# Open News Monitor

**A self-hosted, source-first news monitoring agent.**

Open News Monitor lets a person choose public sources, checks whether they can be collected safely, remembers what it has already seen, and creates a source-linked digest of new publications. It is a universal tool: the user chooses the subject and sources.

It does not search the entire web, publish content, or replace human judgement.

## Current capabilities

- Check a public URL before adding it to a profile.
- Connect RSS/Atom feeds and some public JSON sources after real publications have been confirmed.
- Discover an RSS/Atom feed advertised by a regular web page.
- Save a local SQLite history and avoid delivering the same link twice.
- Create a safe first-run baseline: current publications are remembered but not delivered.
- Create Markdown digests and local status reports.
- Offer optional Telegram, email and Google Sheets settings. These delivery integrations are present in code but have **not** been end-to-end tested in this public project.

Plain HTML news pages without an advertised feed are not connected automatically. They need separately verified collection rules; there is no automatic HTML analyser yet.

## Windows quick start

Requirements: Python 3.11 or newer. Open PowerShell in the repository folder:

```powershell
.\scripts\setup-windows.ps1
.\scripts\create-monitor.ps1 -Name my-monitor
```

The wizard asks for source URLs one at a time, shows the check result, and asks for explicit confirmation before adding a source. Leave the delivery-choice field empty to keep the first run local: Markdown and SQLite only.

After the profile is created, the wizard offers to remember current publications as a safe starting point. Choose `Да` (or `yes`) to prevent existing feed entries from being delivered later. The profile is saved in `config/local/`, which Git ignores.

Run the monitor and view its status:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor run config\local\my-monitor.json
.\.venv\Scripts\python.exe -m open_news_monitor status config\local\my-monitor.json
```

If you decline the first-run baseline, run it later without delivery:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor run config\local\my-monitor.json --initialize
```

Some automated PowerShell input pipelines can corrupt Cyrillic confirmation text. In that case, use the safe equivalent `yes`. In normal interactive use the wizard also accepts `Да` and `д`.

## Source check results

`probe-source` and the setup wizard use four clear results:

| Result | Meaning |
| --- | --- |
| `READY` | Real publications with title, date and direct link were confirmed. The source can be added after confirmation. |
| `NEEDS_SETUP` | The site is reachable, but the current collector cannot safely connect it automatically. |
| `UNSUPPORTED` | The available format is outside the current collector capabilities. |
| `ERROR` | The check could not finish because of a technical problem. This does not mean the source is unsupported. |

Check a URL without creating a profile:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor probe-source https://example.org/news
```

## What has been verified

Local automated tests and a full temporary-profile check have verified RSS source checks, profile creation, first-run initialization, SQLite deduplication, status reporting, and partial failure handling. The real-source integration check used public GitHub Blog and Python Insider RSS feeds.

Still in development: a graphical interface, built-in scheduling, automatic analysis of arbitrary HTML pages, and end-to-end verification of Telegram, email and Google Sheets delivery.

## Privacy and safety

Never commit bot tokens, chat IDs, SMTP passwords, Google service-account keys, `.env` files, personal profiles, or runtime databases. The included `.gitignore` excludes these local files.

## Documentation

- [Russian README](README.ru.md)
- [Russian Windows setup guide](docs/SETUP_RU.md)
- [Project map](КАРТА_ПРОЕКТА.md)
- [Source research standard](skills/news-source-research/SKILL.md)

## License

[MIT](LICENSE)
