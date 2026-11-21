![Open News Monitor](assets/cover-news.jpg)

# Open News Monitor

**A self-hosted, source-first news monitoring agent.**

Open News Monitor lets a person choose public sources, checks whether they can be collected safely, remembers what it has already seen, and creates a source-linked digest of new publications. It is a universal tool: the user chooses the subject and sources.

It does not search the entire web, publish content, or replace human judgement.

## Start with Codex or Claude Code

1. Copy this repository URL and open the project in Codex or Claude Code.
2. Ask: “Help me install and configure Open News Monitor step by step.”
3. Answer the assistant's short questions about your topic, sources and delivery channel.
4. Confirm any action involving an external account or a real test delivery.

The assistant can run safe local steps and explain the rest. It must never ask you to paste tokens, passwords or service-account keys into chat.

## Current capabilities

- Check a public URL before adding it to a profile.
- Connect RSS/Atom feeds and some public JSON sources after real publications have been confirmed.
- Discover an RSS/Atom feed advertised by a regular web page.
- Save a local SQLite history and avoid delivering the same link twice.
- Create a safe first-run baseline: current publications are remembered but not delivered.
- Create Markdown digests and local status reports.
- Deliver through optional Telegram, email (including Gmail SMTP), and Google Sheets queues.
- Run `doctor` to check local channel readiness without contacting external services.
- Separate collection from delivery with `collect`, `deliver`, and `run`.
- Optionally define a profile schedule and run `schedule-tick` from a Windows task.
- Optionally manage sources in an Excel-friendly CSV registry.

Telegram and Gmail have passed controlled real-delivery checks. Google Sheets passed a real delivery and no-repeat check previously; current Google access has not been freshly confirmed. Local Markdown output has been checked. GitHub Actions passed on Windows (Python 3.11–3.13) and Ubuntu (Python 3.11–3.12). External delivery cannot be guaranteed in every network-failure scenario.

Plain HTML news pages without an advertised feed are not connected automatically. They need separately verified collection rules; there is no automatic HTML analyser yet. A graphical interface is not implemented.

## Windows quick start

Requirements: Python 3.11 or newer. Open PowerShell in the repository folder:

```powershell
.\scripts\setup-windows.ps1
.\scripts\create-monitor.ps1 -Name my-monitor
```

The wizard asks for source URLs one at a time, shows the check result, and asks for explicit confirmation before adding a source. Leave the delivery-choice field empty to keep the first run local: Markdown and SQLite only.

For a larger editable source list, use the [CSV registry guide (Russian)](docs/SOURCES_CSV_RU.md) and the safe [CSV profile example](examples/csv-monitor-profile-example.json). The interactive wizard is best for a few sources; the CSV registry is best for a maintained Excel list.

For an optional delivery channel, follow the [channel setup guide](docs/CHANNELS_RU.md), run `doctor`, and separately approve the first real test delivery. You can start with local Markdown output.

After the profile is created, the wizard offers to remember current publications as a safe starting point. Choose `Да` (or `yes`) to prevent existing feed entries from being delivered later. The profile is saved in `config/local/`, which Git ignores.

Run the monitor and view its status:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor run config\local\my-monitor.json
.\.venv\Scripts\python.exe -m open_news_monitor status config\local\my-monitor.json
```

Before the first ordinary run, check local configuration:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor doctor config\local\my-monitor.json
```

`doctor` sends nothing and makes no network requests. `READY` means the required local settings exist; it does not confirm access to an external service.

## Short descriptions in Telegram and email

Older profiles keep showing only the title, source and link. To include a short excerpt of the publication's existing description in Telegram and email, add this optional section:

```json
"presentation": {
  "summary_mode": "excerpt",
  "summary_max_chars": 400
}
```

`excerpt` normalizes and shortens the source description without AI, translation, or added facts. Set `summary_mode` to `none` to hide excerpts.

If you decline the first-run baseline, run it later without delivery:

```powershell
.\.venv\Scripts\python.exe -m open_news_monitor run config\local\my-monitor.json --initialize
```

Some automated PowerShell input pipelines can corrupt Cyrillic confirmation text. In that case, use the safe equivalent `yes`. In normal interactive use the wizard also accepts `Да` and `д`.

## Optional Windows scheduling

Profiles can include an optional `schedule` section. `schedule-tick` performs only the collection and deliveries due at that moment; it is not a background process. A Windows task can call it after sign-in and then approximately every five minutes.

Before creating a task, run `doctor` and inspect the profile. The task commands always require an explicit confirmation for changes:

```powershell
.\.venv\Scripts\open-news-monitor.exe schedule install config\local\my-monitor.json
.\.venv\Scripts\open-news-monitor.exe schedule status config\local\my-monitor.json
```

Use `schedule disable`, `schedule enable`, or `schedule remove` with the same profile to control only that profile's task. Task creation may still be restricted by local Windows policy; never run it as administrator unless the user has explicitly agreed.

Registration was denied on the tested Windows computer, so automatic execution there has not been confirmed. Verify scheduling on the target computer before relying on it. Linux/macOS setup and scheduler instructions have not been separately checked on real systems.

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

Still in development: a graphical interface and automatic analysis of arbitrary HTML pages. Windows task creation is locally validated through the Task Scheduler COM API, but must be checked on each user's computer before it is treated as working.

## Privacy and safety

Never commit bot tokens, chat IDs, SMTP passwords, Google service-account keys, `.env` files, personal profiles, or runtime databases. The included `.gitignore` excludes these local files.

## Documentation

- [Russian README](README.ru.md)
- [Russian Windows setup guide](docs/SETUP_RU.md)
- [Linux and macOS first-run guide (Russian)](docs/SETUP_UNIX_RU.md)
- [Automatic scheduling guide (Russian)](docs/SCHEDULING_RU.md)
- [Telegram, Gmail, and Google Sheets setup](docs/CHANNELS_RU.md)
- [CSV source registry guide (Russian)](docs/SOURCES_CSV_RU.md)
- [Instructions for Codex](AGENTS.md)
- [Instructions for Claude Code](CLAUDE.md)
- [Project map](КАРТА_ПРОЕКТА.md)
- [Source research standard](skills/news-source-research/SKILL.md)

## License

[MIT](LICENSE)
