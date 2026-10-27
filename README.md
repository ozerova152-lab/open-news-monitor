# Open News Monitor

**Self-hosted news monitoring, RSS alerts, and source-linked digests.**

Open News Monitor watches the RSS feeds and public websites you choose, remembers what it has already seen, removes duplicates, and delivers only new matching publications to a Markdown digest, Telegram, email, or Google Sheets.

It is designed for people and teams who need a dependable source monitor, not a black-box news feed. Every finding retains its source, publication date, original link, and the rule that matched it.

## What it does

```text
Your sources -> new publications -> relevance rules -> deduplication
             -> SQLite history -> digest / Telegram / email / Google Sheets
```

- Monitor RSS, Atom, configured public HTML news pages, and public JSON endpoints; every source must expose a title, date, and direct link.
- Safely probe a public URL before adding it: direct RSS/Atom, an advertised feed on the page, or an open JSON endpoint.
- Offer a small optional catalog of verified source recipes by topic; suggestions never connect a source automatically.
- Filter by clear include and exclude phrases.
- Keep a local SQLite history so the same link is not delivered twice.
- Produce a human-readable Markdown digest on every run.
- Save a local health report with the last run time, per-source availability, and errors.
- Optionally send a digest to Telegram, email, or append records to Google Sheets.
- Run safely in `--dry-run` mode before connecting any delivery channel.

## Quick start

Requirements: Python 3.11 or later.

```bash
python -m venv .venv
.venv\\Scripts\\activate        # Windows PowerShell
pip install -e .
python -m open_news_monitor validate examples/ai-work-monitor.json
python -m open_news_monitor run examples/ai-work-monitor.json --dry-run
```

To start a real local monitor, copy the example configuration, replace the source URLs with feeds you trust, and run it without `--dry-run`:

```bash
mkdir config\\local
copy examples\\ai-work-monitor.json config\\local\\my-monitor.json
python -m open_news_monitor run config\\local\\my-monitor.json
```

The first run creates the SQLite database and Markdown digest specified in the configuration. Runtime data is deliberately ignored by Git.

Before enabling a real monitor for the first time, create a baseline. This records the current feed items without treating the entire archive as new:

```bash
python -m open_news_monitor run config\\local\\my-monitor.json --initialize
```

Keep real monitor profiles in `config/local/`. This folder is excluded from Git so a public repository never receives personal sources, delivery choices, or private work settings.

## Configure a monitor

The configuration is JSON so it works without an extra parser dependency. The key parts are:

```json
{
  "monitor": {"name": "My source monitor", "timezone": "Europe/Moscow"},
  "sources": [{"id": "trusted-feed", "name": "Trusted source", "kind": "rss", "url": "https://example.org/feed.xml", "enabled": true}],
  "filters": {"include_any": ["AI", "agent"], "exclude_any": ["vacancy"]},
  "outputs": {"markdown": {"enabled": true, "path": "runtime/latest_digest.md"}}
}
```

`include_any` is optional. When it is empty, every new item from an enabled source matches. Categories such as `grant`, `announcement`, or `case study` are output labels added later; they are not hidden search queries.

An HTML source must use selectors verified against one repeated publication card. The same card must contain title, date, and direct link:

```json
{
  "id": "example-news",
  "name": "Example news",
  "kind": "html",
  "url": "https://example.org/news",
  "selectors": {
    "card": "article.news-card",
    "title": "h2 a",
    "link": "h2 a",
    "date": "time",
    "summary": "p.summary"
  }
}
```

## Check a source before connecting it

Use the same RSS/Atom parser as the monitor to inspect a public feed without changing configuration or sending messages:

```bash
python -m open_news_monitor check-source https://example.org/feed.xml --name "Example source"
```

The report confirms transport, titles, direct links, and available dates. It also states what it cannot establish, including feed pagination and archive completeness.

## Create a monitor without hand-editing JSON

The setup interview verifies each RSS/Atom URL before adding it and asks where the user wants results: Google Sheets, Telegram, email, or more than one of these. It keeps a local Markdown digest and SQLite history in every case:

```bash
python -m open_news_monitor init-monitor my-monitor.json
```

After the wizard writes the profile, create the first-run baseline before ordinary monitoring:

```bash
python -m open_news_monitor run my-monitor.json --initialize
```

## Delivery channels

| Channel | Purpose | Status |
| --- | --- | --- |
| Markdown + SQLite | Local history and a readable digest | Included |
| Telegram | Immediate alert or digest | Included; disabled until configured |
| Email | Daily or weekly digest | Included; disabled until configured |
| Google Sheets | Shared working register | Optional dependency and credentials |

Use `.env.example` as a secret-free template. Never commit a bot token, SMTP password, chat ID, service-account key, or runtime database.

## Product scope

This project is a **source-first monitoring agent**. It does not claim to crawl the entire web, verify facts automatically, or publish content on a user's behalf. A person chooses the sources and makes the final editorial or business decision.

## Current roadmap

1. RSS/Atom collection, filters, deduplication, local history, first-run baseline, and safe outputs.
2. Source-health checks and configurable website/API adapters.
3. Setup wizard, Docker package, and optional AI-assisted summaries with explicit source links.

## Documentation

- [Russian README](README.ru.md)
- [Configuration and delivery guide](docs/SETUP_RU.md)
- [Project map](КАРТА_ПРОЕКТА.md)
- [Source-research skill](skills/news-source-research/SKILL.md) — a maintained source-onboarding standard for AI contributors.

## License

[MIT](LICENSE)
