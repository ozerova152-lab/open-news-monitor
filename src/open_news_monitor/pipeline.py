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
    delivery_status: dict[str, object] = {
        "telegram": {"status": "disabled", "queued": 0, "sent": 0, "failed": 0},
        "email": {"status": "disabled", "queued": 0, "sent": 0, "failed": 0},
        "google_sheets": {"status": "disabled", "queued": 0, "sent": 0, "failed": 0},
    }

    database_path = monitor.get("database_path", "runtime/open-news-monitor.sqlite3")
    store = None if dry_run else ArticleStore(database_path)
    try:
        telegram_enabled = bool(config.get("outputs", {}).get("telegram", {}).get("enabled"))
        email_enabled = bool(config.get("outputs", {}).get("email", {}).get("enabled"))
        sheets_config = config.get("outputs", {}).get("google_sheets", {})
        sheets_target = None
        if sheets_config.get("enabled") and sheets_config.get("spreadsheet_id"):
            sheets_target = (sheets_config["spreadsheet_id"], sheets_config.get("worksheet", "News"))
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
                enqueue_telegram = bool(reason and not initialize and telegram_enabled)
                enqueue_email = bool(reason and not initialize and email_enabled)
                if store and store.add_with_delivery_queues(
                    article,
                    detected_at.isoformat(),
                    reason,
                    enqueue_telegram=enqueue_telegram,
                    enqueue_email=enqueue_email,
                    google_sheets_target=sheets_target if reason and not initialize else None,
                ):
                    saved += 1
                if reason and not initialize:
                    findings.append(Finding(article=article, reason=reason, detected_at=detected_at))
            source_statuses.append({"name": source.name, "status": "ok", "articles": len(articles), "saved": saved})

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
        if telegram.get("enabled") and not initialize and store:
            queued = store.pending_telegram_findings()
            telegram_status = delivery_status["telegram"]
            telegram_status["queued"] = len(queued)
            if not queued:
                telegram_status["status"] = "not_needed"
            else:
                confirmed_urls: list[str] = []

                def confirm(chunk: list[Finding]) -> None:
                    urls = [finding.article.url for finding in chunk]
                    store.mark_telegram_sent(urls, detected_at.isoformat())
                    confirmed_urls.extend(urls)

                try:
                    send_telegram(monitor["name"], queued, on_confirmed=confirm)
                    telegram_status["status"] = "sent"
                    telegram_status["sent"] = len(confirmed_urls)
                except Exception as error:
                    message = f"Telegram delivery was not confirmed: {error}"
                    remaining_urls = [finding.article.url for finding in queued if finding.article.url not in confirmed_urls]
                    store.mark_telegram_failed(remaining_urls, message, detected_at.isoformat())
                    telegram_status["status"] = "partial" if confirmed_urls else "failed"
                    telegram_status["sent"] = len(confirmed_urls)
                    telegram_status["failed"] = len(remaining_urls)
                    errors.append(message)

        email = outputs.get("email", {})
        if email.get("enabled") and not initialize and store:
            queued = store.pending_email_findings()
            email_status = delivery_status["email"]
            email_status["queued"] = len(queued)
            if not queued:
                email_status["status"] = "not_needed"
            else:
                recipients = email.get("recipients", [])
                confirmed_urls: list[str] = []

                def confirm_email(chunk: list[Finding]) -> None:
                    urls = [finding.article.url for finding in chunk]
                    store.mark_email_sent(urls, detected_at.isoformat())
                    confirmed_urls.extend(urls)

                try:
                    if not recipients:
                        raise RuntimeError("Email recipients are missing.")
                    send_email(monitor["name"], recipients, queued, on_confirmed=confirm_email)
                    email_status["status"] = "sent"
                    email_status["sent"] = len(confirmed_urls)
                except Exception as error:
                    message = _delivery_error("Email", error)
                    remaining_urls = [finding.article.url for finding in queued if finding.article.url not in confirmed_urls]
                    store.mark_email_failed(remaining_urls, message, detected_at.isoformat())
                    email_status["status"] = "partial" if confirmed_urls else "failed"
                    email_status["sent"] = len(confirmed_urls)
                    email_status["failed"] = len(remaining_urls)
                    errors.append(message)

        sheets = outputs.get("google_sheets", {})
        if sheets.get("enabled") and not initialize and store:
            sheets_status = delivery_status["google_sheets"]
            if not sheets.get("spreadsheet_id"):
                sheets_status["status"] = "failed"
                errors.append("Google Sheets delivery was not confirmed (missing spreadsheet ID).")
            else:
                spreadsheet_id = sheets["spreadsheet_id"]
                worksheet = sheets.get("worksheet", "News")
                queued = store.pending_google_sheets_findings(spreadsheet_id, worksheet)
                sheets_status["queued"] = len(queued)
                if not queued:
                    sheets_status["status"] = "not_needed"
                else:
                    confirmed_urls: list[str] = []

                    def confirm_sheets(chunk: list[Finding]) -> None:
                        urls = [finding.article.url for finding in chunk]
                        store.mark_google_sheets_sent(
                            urls, spreadsheet_id, worksheet, detected_at.isoformat()
                        )
                        confirmed_urls.extend(urls)

                    try:
                        append_google_sheet(
                            spreadsheet_id, worksheet, queued, on_confirmed=confirm_sheets
                        )
                        sheets_status["status"] = "sent"
                        sheets_status["sent"] = len(confirmed_urls)
                    except Exception as error:
                        message = _delivery_error("Google Sheets", error)
                        remaining_urls = [
                            finding.article.url for finding in queued
                            if finding.article.url not in confirmed_urls
                        ]
                        store.mark_google_sheets_failed(
                            remaining_urls, spreadsheet_id, worksheet, message, detected_at.isoformat()
                        )
                        sheets_status["status"] = "partial" if confirmed_urls else "failed"
                        sheets_status["sent"] = len(confirmed_urls)
                        sheets_status["failed"] = len(remaining_urls)
                        errors.append(message)

        status_path = monitor.get("status_path") or str(Path(database_path).with_suffix(".status.json"))
        write_status(
            status_path,
            {
                "monitor_name": monitor["name"],
                "ran_at": detected_at.isoformat(),
                "mode": "initialization" if initialize else "ordinary",
                "new_matching_publications": len(findings),
                "sources": source_statuses,
                "deliveries": delivery_status,
                "errors": errors,
            },
        )

        return findings, errors, markdown
    finally:
        if store:
            store.close()


def _delivery_error(channel: str, error: Exception) -> str:
    """Keep diagnostics useful without copying credentials or server responses into status files."""
    return f"{channel} delivery was not confirmed ({type(error).__name__})."
