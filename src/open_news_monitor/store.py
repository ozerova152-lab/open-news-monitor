from __future__ import annotations

import sqlite3
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

from .models import Article, Finding


class ArticleStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                published_at TEXT,
                detected_at TEXT NOT NULL,
                matched_reason TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS google_sheets_queue (
                url TEXT NOT NULL,
                spreadsheet_id TEXT NOT NULL,
                worksheet TEXT NOT NULL,
                publication_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'SENT', 'FAILED')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                PRIMARY KEY (url, spreadsheet_id, worksheet),
                FOREIGN KEY (url) REFERENCES articles(url)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_queue (
                url TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'SENT', 'FAILED')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY (url) REFERENCES articles(url)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS email_queue (
                url TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'SENT', 'FAILED')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY (url) REFERENCES articles(url)
            )
            """
        )
        self.connection.commit()

    def has_seen(self, url: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
        return row is not None

    def add(self, article: Article, detected_at: str, reason: str | None) -> bool:
        return self.add_with_delivery_queues(
            article, detected_at, reason, enqueue_telegram=False, enqueue_email=False
        )

    def add_with_telegram_queue(
        self, article: Article, detected_at: str, reason: str | None, *, enqueue_telegram: bool
    ) -> bool:
        """Backward-compatible helper for Telegram-only callers."""
        return self.add_with_delivery_queues(
            article, detected_at, reason, enqueue_telegram=enqueue_telegram, enqueue_email=False
        )

    def add_with_delivery_queues(
        self,
        article: Article,
        detected_at: str,
        reason: str | None,
        *,
        enqueue_telegram: bool,
        enqueue_email: bool,
        google_sheets_target: tuple[str, str] | None = None,
    ) -> bool:
        """Save a new article and selected delivery queue rows in one transaction."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO articles
                (url, source_id, source_name, title, summary, published_at, detected_at, matched_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.url,
                    article.source_id,
                    article.source_name,
                    article.title,
                    article.summary,
                    article.published_at,
                    detected_at,
                    reason,
                ),
            )
            added = cursor.rowcount == 1
            if added and enqueue_telegram:
                self._insert_telegram_queue(article.url, detected_at)
            if added and enqueue_email:
                self._insert_email_queue(article.url, detected_at)
            # Unlike the other channels, Sheets has a destination-specific queue:
            # the same publication may legitimately be written to another table.
            # The caller in the normal pipeline only reaches this method for a new
            # article, so this does not re-queue historical publications.
            if google_sheets_target:
                self._insert_google_sheets_queue(article.url, google_sheets_target, detected_at)
        return added

    def enqueue_telegram(self, url: str, updated_at: str) -> None:
        """Keep a matching publication available until Telegram confirms delivery."""
        with self.connection:
            self._insert_telegram_queue(url, updated_at)

    def _insert_telegram_queue(self, url: str, updated_at: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO telegram_queue (url, state, attempts, updated_at)
            VALUES (?, 'PENDING', 0, ?)
            """,
            (url, updated_at),
        )

    def _insert_email_queue(self, url: str, updated_at: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO email_queue (url, state, attempts, updated_at)
            VALUES (?, 'PENDING', 0, ?)
            """,
            (url, updated_at),
        )

    def _insert_google_sheets_queue(
        self, url: str, target: tuple[str, str], updated_at: str
    ) -> None:
        spreadsheet_id, worksheet = target
        self.connection.execute(
            """
            INSERT OR IGNORE INTO google_sheets_queue
            (url, spreadsheet_id, worksheet, publication_id, state, attempts, updated_at)
            VALUES (?, ?, ?, ?, 'PENDING', 0, ?)
            """,
            (url, spreadsheet_id, worksheet, publication_id_for_url(url), updated_at),
        )

    def pending_telegram_findings(self) -> list[Finding]:
        return self._pending_findings("telegram_queue")

    def pending_email_findings(self) -> list[Finding]:
        return self._pending_findings("email_queue")

    def pending_google_sheets_findings(
        self, spreadsheet_id: str, worksheet: str
    ) -> list[Finding]:
        return self._pending_findings(
            "google_sheets_queue", "q.spreadsheet_id = ? AND q.worksheet = ?", (spreadsheet_id, worksheet)
        )

    def _pending_findings(
        self, queue_table: str, extra_where: str = "", values: tuple[str, ...] = ()
    ) -> list[Finding]:
        where = "q.state IN ('PENDING', 'FAILED')"
        if extra_where:
            where += f" AND {extra_where}"
        rows = self.connection.execute(
            f"""
            SELECT a.source_id, a.source_name, a.title, a.url, a.summary, a.published_at,
                   a.detected_at, a.matched_reason
            FROM {queue_table} AS q
            JOIN articles AS a ON a.url = q.url
            WHERE {where}
            ORDER BY a.detected_at, a.url
            """,
            values,
        ).fetchall()
        findings: list[Finding] = []
        for row in rows:
            try:
                detected_at = datetime.fromisoformat(row[6])
            except (TypeError, ValueError):
                detected_at = datetime.now(timezone.utc)
            findings.append(
                Finding(
                    article=Article(*row[:6]),
                    reason=row[7] or "Matched monitor rules",
                    detected_at=detected_at,
                )
            )
        return findings

    def mark_telegram_sent(self, urls: list[str], updated_at: str) -> None:
        self._mark_sent("telegram_queue", urls, updated_at)

    def mark_email_sent(self, urls: list[str], updated_at: str) -> None:
        self._mark_sent("email_queue", urls, updated_at)

    def mark_google_sheets_sent(
        self, urls: list[str], spreadsheet_id: str, worksheet: str, updated_at: str
    ) -> None:
        self._mark_sent(
            "google_sheets_queue", urls, updated_at,
            "spreadsheet_id = ? AND worksheet = ?", (spreadsheet_id, worksheet),
        )

    def _mark_sent(
        self, queue_table: str, urls: list[str], updated_at: str,
        extra_where: str = "", extra_values: tuple[str, ...] = (),
    ) -> None:
        if not urls:
            return
        self.connection.executemany(
            f"""
            UPDATE {queue_table}
            SET state = 'SENT', attempts = attempts + 1, last_error = NULL,
                updated_at = ?, sent_at = ?
            WHERE url = ? {f'AND {extra_where}' if extra_where else ''}
            """,
            [(updated_at, updated_at, url, *extra_values) for url in urls],
        )
        self.connection.commit()

    def mark_telegram_failed(self, urls: list[str], error: str, updated_at: str) -> None:
        self._mark_failed("telegram_queue", urls, error, updated_at)

    def mark_email_failed(self, urls: list[str], error: str, updated_at: str) -> None:
        self._mark_failed("email_queue", urls, error, updated_at)

    def mark_google_sheets_failed(
        self, urls: list[str], spreadsheet_id: str, worksheet: str, error: str, updated_at: str
    ) -> None:
        self._mark_failed(
            "google_sheets_queue", urls, error, updated_at,
            "spreadsheet_id = ? AND worksheet = ?", (spreadsheet_id, worksheet),
        )

    def _mark_failed(
        self, queue_table: str, urls: list[str], error: str, updated_at: str,
        extra_where: str = "", extra_values: tuple[str, ...] = (),
    ) -> None:
        if not urls:
            return
        self.connection.executemany(
            f"""
            UPDATE {queue_table}
            SET state = 'FAILED', attempts = attempts + 1, last_error = ?, updated_at = ?
            WHERE url = ? AND state != 'SENT' {f'AND {extra_where}' if extra_where else ''}
            """,
            [(error, updated_at, url, *extra_values) for url in urls],
        )
        self.connection.commit()

    def telegram_queue_states(self) -> dict[str, int]:
        return self._queue_states("telegram_queue")

    def email_queue_states(self) -> dict[str, int]:
        return self._queue_states("email_queue")

    def google_sheets_queue_states(self, spreadsheet_id: str | None = None, worksheet: str | None = None) -> dict[str, int]:
        if spreadsheet_id is None or worksheet is None:
            return self._queue_states("google_sheets_queue")
        rows = self.connection.execute(
            "SELECT state, COUNT(*) FROM google_sheets_queue WHERE spreadsheet_id = ? AND worksheet = ? GROUP BY state",
            (spreadsheet_id, worksheet),
        ).fetchall()
        return {state: count for state, count in rows}

    def _queue_states(self, queue_table: str) -> dict[str, int]:
        rows = self.connection.execute(
            f"SELECT state, COUNT(*) FROM {queue_table} GROUP BY state"
        ).fetchall()
        return {state: count for state, count in rows}

    def close(self) -> None:
        self.connection.close()


def publication_id_for_url(url: str) -> str:
    return "onm-" + sha256(url.encode("utf-8")).hexdigest()
