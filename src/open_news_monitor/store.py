from __future__ import annotations

import sqlite3
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
        self.connection.commit()

    def has_seen(self, url: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
        return row is not None

    def add(self, article: Article, detected_at: str, reason: str | None) -> bool:
        return self.add_with_telegram_queue(article, detected_at, reason, enqueue_telegram=False)

    def add_with_telegram_queue(
        self, article: Article, detected_at: str, reason: str | None, *, enqueue_telegram: bool
    ) -> bool:
        """Save a new article and its Telegram queue row atomically when needed."""
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

    def pending_telegram_findings(self) -> list[Finding]:
        rows = self.connection.execute(
            """
            SELECT a.source_id, a.source_name, a.title, a.url, a.summary, a.published_at,
                   a.detected_at, a.matched_reason
            FROM telegram_queue AS q
            JOIN articles AS a ON a.url = q.url
            WHERE q.state IN ('PENDING', 'FAILED')
            ORDER BY a.detected_at, a.url
            """
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
        if not urls:
            return
        self.connection.executemany(
            """
            UPDATE telegram_queue
            SET state = 'SENT', attempts = attempts + 1, last_error = NULL,
                updated_at = ?, sent_at = ?
            WHERE url = ?
            """,
            [(updated_at, updated_at, url) for url in urls],
        )
        self.connection.commit()

    def mark_telegram_failed(self, urls: list[str], error: str, updated_at: str) -> None:
        if not urls:
            return
        self.connection.executemany(
            """
            UPDATE telegram_queue
            SET state = 'FAILED', attempts = attempts + 1, last_error = ?, updated_at = ?
            WHERE url = ? AND state != 'SENT'
            """,
            [(error, updated_at, url) for url in urls],
        )
        self.connection.commit()

    def telegram_queue_states(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) FROM telegram_queue GROUP BY state"
        ).fetchall()
        return {state: count for state, count in rows}

    def close(self) -> None:
        self.connection.close()
