from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import enabled_sources
from .filtering import match_article
from .models import Finding
from .outputs import append_google_sheet, render_markdown, send_email, send_telegram, write_markdown
from .html_collector import fetch_html
from .json_collector import fetch_json
from .rss import SourceFetchError, fetch_feed
from .store import ArticleStore
from .status import write_status


@dataclass
class InitializationReport:
    """Result of a no-delivery first-run baseline."""

    sources: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def checked_sources(self) -> int:
        return len(self.sources)

    @property
    def saved_articles(self) -> int:
        return sum(int(source.get("saved", 0)) for source in self.sources)

    @property
    def complete(self) -> bool:
        return bool(self.sources) and not self.errors and all(source.get("status") == "ok" for source in self.sources)


def initialize_monitor(config: dict) -> InitializationReport:
    """Record current items using the normal pipeline, with all delivery disabled."""
    report = InitializationReport()
    try:
        run_monitor(config, initialize=True, initialization_report=report)
    except Exception as error:
        message = f"Ошибка инициализации: {error}"
        report.errors.append(message)
        monitor = config.get("monitor", {})
        database_path = monitor.get("database_path", "runtime/open-news-monitor.sqlite3")
        status_path = monitor.get("status_path") or str(Path(database_path).with_suffix(".status.json"))
        try:
            write_status(status_path, {
                "monitor_name": monitor.get("name", "Open News Monitor"),
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "mode": "initialization",
                "new_matching_publications": 0,
                "sources": report.sources,
                "errors": report.errors,
            })
        except OSError:
            pass
    return report


def run_monitor(
    config: dict, *, dry_run: bool = False, initialize: bool = False,
    initialization_report: InitializationReport | None = None,
) -> tuple[list[Finding], list[str], str]:
    """Run one monitoring cycle and return findings, source/output errors, and Markdown."""
    monitor = config["monitor"]
    filters = config.get("filters", {})
    include_any = filters.get("include_any", [])
    exclude_any = filters.get("exclude_any", [])
    detected_at = datetime.now(timezone.utc)
    errors: list[str] = []
    findings: list[Finding] = []
    source_statuses: list[dict[str, object]] = []

    database_path = monitor.get("database_path", "runtime/open-news-monitor.sqlite3")
    store = None if dry_run else ArticleStore(database_path)
    try:
        for source in enabled_sources(config):
            saved = 0
            try:
                if source.kind == "html":
                    articles = fetch_html(source)
                elif source.kind == "json":
                    articles = fetch_json(source)
                else:
                    articles = fetch_feed(source)
            except Exception as error:
                message = str(error)
                errors.append(message)
                source_statuses.append({"name": source.name, "status": "error", "error": message, "saved": 0})
                continue
            for article in articles:
                if store and store.has_seen(article.url):
                    continue
                reason = match_article(article, include_any, exclude_any)
                if store and store.add(article, detected_at.isoformat(), reason):
                    saved += 1
                if reason and not initialize:
                    findings.append(Finding(article=article, reason=reason, detected_at=detected_at))
            source_statuses.append({"name": source.name, "status": "ok", "articles": len(articles), "saved": saved})
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

    if initialization_report is not None:
        initialization_report.sources.extend(source_statuses)
        initialization_report.errors.extend(errors)

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

    status_path = monitor.get("status_path") or str(Path(database_path).with_suffix(".status.json"))
    write_status(
        status_path,
        {
            "monitor_name": monitor["name"],
            "ran_at": detected_at.isoformat(),
            "mode": "initialization" if initialize else "ordinary",
            "new_matching_publications": len(findings),
            "sources": source_statuses,
            "errors": errors,
        },
    )

    return findings, errors, markdown
