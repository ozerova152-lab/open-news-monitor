from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Article


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
        self.connection.commit()

    def has_seen(self, url: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
        return row is not None

    def add(self, article: Article, detected_at: str, reason: str | None) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
