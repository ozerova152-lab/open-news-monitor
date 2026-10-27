from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import Finding


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


def send_telegram(monitor_name: str, findings: list[Finding]) -> None:
    token = os.getenv("ONM_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("ONM_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram is enabled but ONM_TELEGRAM_BOT_TOKEN or ONM_TELEGRAM_CHAT_ID is missing.")
    text = _plain_digest(monitor_name, findings)
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


def send_email(monitor_name: str, recipients: list[str], findings: list[Finding]) -> None:
    required = {key: os.getenv(key) for key in ("ONM_SMTP_HOST", "ONM_SMTP_USERNAME", "ONM_SMTP_PASSWORD", "ONM_SMTP_FROM")}
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Email is enabled but these environment variables are missing: " + ", ".join(missing))

    message = EmailMessage()
    message["Subject"] = f"{monitor_name}: {len(findings)} new matching publication(s)"
    message["From"] = required["ONM_SMTP_FROM"]
    message["To"] = ", ".join(recipients)
    message.set_content(_plain_digest(monitor_name, findings))
    port = int(os.getenv("ONM_SMTP_PORT", "587"))
    with smtplib.SMTP(required["ONM_SMTP_HOST"], port, timeout=20) as server:
        server.starttls()
        server.login(required["ONM_SMTP_USERNAME"], required["ONM_SMTP_PASSWORD"])
        server.send_message(message)


def append_google_sheet(spreadsheet_id: str, worksheet: str, findings: list[Finding]) -> None:
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
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
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
        ]
        for finding in findings
    ]
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{worksheet}'!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def _plain_digest(monitor_name: str, findings: list[Finding]) -> str:
    lines = [f"{monitor_name}: {len(findings)} new matching publication(s)"]
    for finding in findings[:10]:
        article = finding.article
        lines.extend(["", article.title, f"{article.source_name} | {article.url}"])
    if len(findings) > 10:
        lines.append(f"\nAnd {len(findings) - 10} more item(s) in the full digest.")
    return "\n".join(lines)
