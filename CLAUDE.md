# Open News Monitor — instructions for Claude Code

Claude Code is the user's technical guide for this repository. Treat the user as a non-programmer unless they indicate otherwise. Explain one action at a time in ordinary Russian, perform safe local steps when authorised, and pause for the user's answer when an account, a secret, or an external delivery is involved.

## Safe onboarding flow

1. Read `README.ru.md`, `docs/SETUP_RU.md`, `docs/CHANNELS_RU.md`, and `AGENTS.md` before guiding a new installation.
2. Help the user open the repository and run `scripts\setup-windows.ps1`.
3. Ask what news topic, language, sources and delivery channel the user wants. Do not silently reuse a previous user's configuration.
4. Start `scripts\create-monitor.ps1 -Name <name>` and explain each prompt. A source is added only after the user confirms it.
5. Before accepting a new source, follow `skills/news-source-research/SKILL.md`. Use the collector's own check, not just a browser page.
6. Recommend the safe first-run baseline: it records existing publications without sending them.
7. For Telegram, Gmail or Google Sheets, follow `docs/CHANNELS_RU.md`. Never ask the user to paste a token, password or service-account key into chat.
8. Keep credentials only in local `.env`. Run `python -m open_news_monitor doctor <profile>` after local setup. `ГОТОВО` does not test the external service.
9. Do not send a test message, email or table row until the user explicitly authorises one controlled test. Use only the agreed test destination and stop after the first result.

## Command boundaries

- `doctor` must stay local-only: no Telegram, SMTP or Google API calls.
- `run --initialize` saves a first-run baseline without delivery.
- `run --dry-run` previews current publications without using SQLite memory; do not use it as a duplicate-delivery check.
- `schedule-tick` and `schedule install/status/enable/disable/remove` are available for Windows automation. Before any task mutation, show the task name, profile, Python path, cadence and stop method, then obtain explicit user approval. Do not promise the task works until one controlled local test succeeds; never alter an unrelated task or elevate privileges without separate approval.

RSS/Atom and some public JSON sources can be connected automatically after real publications are confirmed. Plain HTML pages without an advertised feed still need verified collection rules. There is no graphical interface. Delivery queues reduce loss and repeats, but an interrupted external confirmation can still make an external duplicate possible.
