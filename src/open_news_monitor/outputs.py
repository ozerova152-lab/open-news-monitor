from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import Finding
from .store import publication_id_for_url


def render_markdown(monitor_name: str, findings: list[Finding], errors: list[str]) -> str:
    lines = [f"# {monitor_name}", "", f"New matching publications: **{len(findings)}**", ""]
    if findings:
        for index, finding in enumerate(findings, start=1):
            article = finding.article
            lines.extend(
                [
                    f"## {index}. {article.title}",
                    "",
                    f"- **Source:** {article.source_name}",
                    f"- **Published:** {article.published_at or 'Not provided by source'}",
                    f"- **Why included:** {finding.reason}",
                    f"- **Original link:** {article.url}",
                ]
            )
            if article.summary:
                lines.extend(["", article.summary])
            lines.append("")
    else:
        lines.extend(["No new matching publications were found.", ""])

    if errors:
        lines.extend(["## Source errors", ""])
        lines.extend(f"- {error}" for error in errors)
        lines.append("")
    return "\n".join(lines)


def write_markdown(path: str | Path, content: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


TELEGRAM_MESSAGE_LIMIT = 4096


def send_telegram(monitor_name: str, findings: list[Finding], *, on_confirmed=None) -> None:
    token = os.getenv("ONM_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("ONM_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram is enabled but ONM_TELEGRAM_BOT_TOKEN or ONM_TELEGRAM_CHAT_ID is missing.")
    for text, chunk in telegram_message_chunks(monitor_name, findings):
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=f"chat_id={quote(chat_id)}&text={quote(text)}&disable_web_page_preview=true".encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram rejected the message: {payload}")
        if on_confirmed is not None:
            on_confirmed(chunk)


def send_email(monitor_name: str, recipients: list[str], findings: list[Finding], *, on_confirmed=None) -> None:
    required = {key: os.getenv(key) for key in ("ONM_SMTP_HOST", "ONM_SMTP_USERNAME", "ONM_SMTP_PASSWORD", "ONM_SMTP_FROM")}
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Email is enabled but these environment variables are missing: " + ", ".join(missing))

    message = EmailMessage()
    message["Subject"] = f"{monitor_name}: {len(findings)} new matching publication(s)"
    message["From"] = required["ONM_SMTP_FROM"]
    message["To"] = ", ".join(recipients)
    message.set_content(_plain_digest(monitor_name, findings, limit=None))
    port = int(os.getenv("ONM_SMTP_PORT", "587"))
    with smtplib.SMTP(required["ONM_SMTP_HOST"], port, timeout=20) as server:
        server.starttls()
        server.login(required["ONM_SMTP_USERNAME"], required["ONM_SMTP_PASSWORD"])
        refused = server.send_message(message)
    if refused:
        raise RuntimeError("SMTP server refused one or more recipients.")
    if on_confirmed is not None:
        on_confirmed(findings)


def append_google_sheet(
    spreadsheet_id: str, worksheet: str, findings: list[Finding], *, on_confirmed=None
) -> None:
    service = _google_service()
    values = service.spreadsheets().values()
    existing = values.get(
        spreadsheetId=spreadsheet_id, range=f"'{worksheet}'!A:I"
    ).execute().get("values", [])
    existing_ids = {row[8] for row in existing if len(row) > 8 and row[8]}
    existing_urls = {row[4] for row in existing if len(row) > 4 and row[4]}
    already_written = [
        finding for finding in findings
        if publication_id_for_url(finding.article.url) in existing_ids or finding.article.url in existing_urls
    ]
    pending = [finding for finding in findings if finding not in already_written]
    if pending:
        rows = [
            [
                "NEW",
                finding.detected_at.isoformat(),
                finding.article.source_name,
                finding.article.title,
                finding.article.url,
                finding.article.published_at or "",
                finding.article.summary,
                finding.reason,
                publication_id_for_url(finding.article.url),
            ]
            for finding in pending
        ]
        values.append(
            spreadsheetId=spreadsheet_id,
            range=f"'{worksheet}'!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    if on_confirmed is not None:
        on_confirmed([*already_written, *pending])


def _google_service():
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError('Google Sheets support needs: pip install -e ".[sheets]"') from error

    key_path = os.getenv("ONM_GOOGLE_SERVICE_ACCOUNT_FILE")
    if not key_path:
        raise RuntimeError("Google Sheets is enabled but ONM_GOOGLE_SERVICE_ACCOUNT_FILE is missing.")
    credentials = Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _plain_digest(monitor_name: str, findings: list[Finding], *, limit: int | None = 10) -> str:
    lines = [f"{monitor_name}: {len(findings)} new matching publication(s)"]
    selected = findings if limit is None else findings[:limit]
    for finding in selected:
        article = finding.article
        lines.extend(["", article.title, f"{article.source_name} | {article.url}"])
    if limit is not None and len(findings) > limit:
        lines.append(f"\nAnd {len(findings) - limit} more item(s) in the full digest.")
    return "\n".join(lines)


def telegram_message_chunks(
    monitor_name: str, findings: list[Finding], *, max_chars: int = TELEGRAM_MESSAGE_LIMIT
) -> list[tuple[str, list[Finding]]]:
    """Split a Telegram digest without splitting or silently dropping publications."""
    if max_chars < 64:
        raise ValueError("Telegram message limit is too small.")
    header = f"{monitor_name}: {len(findings)} new matching publication(s)"
    chunks: list[tuple[str, list[Finding]]] = []
    current_lines = [header]
    current_findings: list[Finding] = []

    for finding in findings:
        entry = _telegram_entry(finding)
        available = max_chars - len(header) - 2
        if len(entry) > available:
            entry = _shorten_telegram_entry(finding, available)
        candidate = "\n".join([*current_lines, "", entry])
        if len(candidate) <= max_chars:
            current_lines.extend(["", entry])
            current_findings.append(finding)
            continue
        if current_findings:
            chunks.append(("\n".join(current_lines), current_findings))
            current_lines = [header, "", entry]
            current_findings = [finding]
        else:
            raise ValueError("A Telegram publication cannot fit into one message while preserving its direct link.")

    if current_findings:
        chunks.append(("\n".join(current_lines), current_findings))
    return chunks


def _telegram_entry(finding: Finding) -> str:
    article = finding.article
    return f"{article.title}\n{article.source_name} | {article.url}"


def _shorten_telegram_entry(finding: Finding, available: int) -> str:
    article = finding.article
    fixed = f"\n{article.source_name} | {article.url}"
    title_space = available - len(fixed)
    if title_space < 1:
        raise ValueError("A Telegram publication cannot fit into one message while preserving its direct link.")
    if len(article.title) <= title_space:
        return _telegram_entry(finding)
    return f"{article.title[:max(1, title_space - 1)]}…{fixed}"
