from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import enabled_sources
from .filtering import match_article
from .models import Finding
from .outputs import append_google_sheet, render_markdown, send_email, send_telegram, write_markdown
from .html_collector import fetch_html
from .rss import SourceFetchError, fetch_feed
from .store import ArticleStore


def run_monitor(
    config: dict, *, dry_run: bool = False, initialize: bool = False
) -> tuple[list[Finding], list[str], str]:
    """Run one monitoring cycle and return findings, source/output errors, and Markdown."""
    monitor = config["monitor"]
    filters = config.get("filters", {})
    include_any = filters.get("include_any", [])
    exclude_any = filters.get("exclude_any", [])
    detected_at = datetime.now(timezone.utc)
    errors: list[str] = []
    findings: list[Finding] = []

    database_path = monitor.get("database_path", "runtime/open-news-monitor.sqlite3")
    store = None if dry_run else ArticleStore(database_path)
    try:
        for source in enabled_sources(config):
            try:
                articles = fetch_html(source) if source.kind == "html" else fetch_feed(source)
            except SourceFetchError as error:
                errors.append(str(error))
                continue
            for article in articles:
                if store and store.has_seen(article.url):
                    continue
                reason = match_article(article, include_any, exclude_any)
                if store:
                    store.add(article, detected_at.isoformat(), reason)
                if reason and not initialize:
                    findings.append(Finding(article=article, reason=reason, detected_at=detected_at))
    finally:
        if store:
            store.close()

    markdown = render_markdown(monitor["name"], findings, errors)
    if initialize:
        markdown = markdown.replace(
            "New matching publications: **0**",
            "Initial baseline saved. Existing feed items were recorded without delivery.",
        ).replace("No new matching publications were found.", "The next ordinary run will deliver only publications that appear after this baseline.")
    if dry_run:
        return findings, errors, markdown

    outputs = config.get("outputs", {})
    markdown_output = outputs.get("markdown", {})
    if markdown_output.get("enabled", True):
        write_markdown(markdown_output.get("path", "runtime/latest_digest.md"), markdown)

    telegram = outputs.get("telegram", {})
    if telegram.get("enabled") and findings and not initialize:
        try:
            send_telegram(monitor["name"], findings)
        except RuntimeError as error:
            errors.append(str(error))

    email = outputs.get("email", {})
    if email.get("enabled") and findings and not initialize:
        recipients = email.get("recipients", [])
        if not recipients:
            errors.append("Email is enabled but outputs.email.recipients is empty.")
        else:
            try:
                send_email(monitor["name"], recipients, findings)
            except RuntimeError as error:
                errors.append(str(error))

    sheets = outputs.get("google_sheets", {})
    if sheets.get("enabled") and findings and not initialize:
        if not sheets.get("spreadsheet_id"):
            errors.append("Google Sheets is enabled but outputs.google_sheets.spreadsheet_id is empty.")
        else:
            try:
                append_google_sheet(sheets["spreadsheet_id"], sheets.get("worksheet", "News"), findings)
            except RuntimeError as error:
                errors.append(str(error))

    return findings, errors, markdown
