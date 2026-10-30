from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.models import Article
from open_news_monitor.pipeline import initialize_monitor, run_monitor
from open_news_monitor.status import read_status
from open_news_monitor.store import ArticleStore, publication_id_for_url


class _Request:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class _Values:
    def __init__(self, rows=None, *, get_error=None, append_error=None, lose_response=False, partial_count=None):
        self.rows = rows or []
        self.get_error = get_error
        self.append_error = append_error
        self.lose_response = lose_response
        self.partial_count = partial_count
        self.append_calls = 0

    def get(self, **_kwargs):
        def callback():
            if self.get_error:
                raise self.get_error
            return {"values": self.rows}
        return _Request(callback)

    def append(self, **kwargs):
        def callback():
            self.append_calls += 1
            if self.append_error:
                raise self.append_error
            new_rows = kwargs["body"]["values"]
            if self.partial_count is not None:
                self.rows.extend(new_rows[:self.partial_count])
                self.partial_count = None
                raise OSError("response lost after partial append")
            self.rows.extend(new_rows)
            if self.lose_response:
                self.lose_response = False
                raise OSError("response lost after append")
            return {"updates": {"updatedRows": len(kwargs["body"]["values"])}}
        return _Request(callback)


class _Service:
    def __init__(self, values):
        self.values_api = values

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api


class GoogleSheetsQueueTests(unittest.TestCase):
    spreadsheet_id = "test-sheet"
    worksheet = "News"

    @staticmethod
    def _article(number: int = 1) -> Article:
        return Article("source", "Source", f"Sheet news {number}", f"https://example.test/sheet/{number}", "", "2026-10-29")

    def _config(self, root: Path, *, telegram=False, email=False) -> dict:
        return {
            "monitor": {"name": "Sheets test", "database_path": str(root / "memory.sqlite3"), "status_path": str(root / "status.json")},
            "sources": [{"id": "source", "name": "Source", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False},
                "telegram": {"enabled": telegram},
                "email": {"enabled": email, "recipients": ["test@example.test"]},
                "google_sheets": {"enabled": True, "spreadsheet_id": self.spreadsheet_id, "worksheet": self.worksheet},
            },
        }

    def _run(self, root: Path, articles: list[Article], values: _Values, *, telegram=False, email=False):
        with patch("open_news_monitor.pipeline.fetch_feed", return_value=articles), patch("open_news_monitor.outputs._google_service", return_value=_Service(values)):
            return run_monitor(self._config(root, telegram=telegram, email=email))

    def _states(self, root: Path) -> dict[str, int]:
        store = ArticleStore(root / "memory.sqlite3")
        try:
            return store.google_sheets_queue_states(self.spreadsheet_id, self.worksheet)
        finally:
            store.close()

    def test_successful_write_marks_items_sent_and_adds_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = _Values()
            _findings, errors, _markdown = self._run(root, [self._article(1), self._article(2)], values)
            self.assertEqual([], errors)
            self.assertEqual({"SENT": 2}, self._states(root))
            self.assertEqual(2, len(values.rows))
            self.assertEqual(9, len(values.rows[0]))
            self.assertEqual(publication_id_for_url(self._article(1).url), values.rows[0][8])

    def test_access_error_keeps_items_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], _Values(get_error=PermissionError("access denied")))
            self.assertEqual({"FAILED": 1}, self._states(root))
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["google_sheets"]["status"])

    def test_network_error_keeps_items_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], _Values(append_error=OSError("network down")))
            self.assertEqual({"FAILED": 1}, self._states(root))

    def test_retry_after_error_writes_item_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = _Values(append_error=OSError("offline"))
            self._run(root, [self._article()], failed)
            working = _Values()
            self._run(root, [self._article()], working)
            self.assertEqual({"SENT": 1}, self._states(root))
            self.assertEqual(1, len(working.rows))

    def test_sent_item_is_not_written_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = _Values()
            self._run(root, [self._article()], values)
            self._run(root, [self._article()], values)
            self.assertEqual(1, values.append_calls)
            self.assertEqual(1, len(values.rows))

    def test_lost_response_is_reconciled_without_duplicate_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = _Values(lose_response=True)
            self._run(root, [self._article()], values)
            self.assertEqual({"FAILED": 1}, self._states(root))
            self._run(root, [self._article()], values)
            self.assertEqual({"SENT": 1}, self._states(root))
            self.assertEqual(1, values.append_calls)
            self.assertEqual(1, len(values.rows))

    def test_partial_append_is_reconciled_without_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = _Values(partial_count=1)
            self._run(root, [self._article(1), self._article(2)], values)
            self.assertEqual({"FAILED": 2}, self._states(root))
            self._run(root, [self._article(1), self._article(2)], values)
            self.assertEqual({"SENT": 2}, self._states(root))
            self.assertEqual(2, values.append_calls)
            self.assertEqual(2, len(values.rows))
            self.assertEqual(
                {self._article(1).url, self._article(2).url}, {row[4] for row in values.rows}
            )

    def test_legacy_url_row_prevents_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article = self._article()
            values = _Values(rows=[["NEW", "old", "Source", "Old title", article.url, "", "", ""]])
            self._run(root, [article], values)
            self.assertEqual({"SENT": 1}, self._states(root))
            self.assertEqual(0, values.append_calls)

    def test_article_and_sheets_queue_rollback_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "memory.sqlite3")
            article = self._article()
            try:
                with patch.object(store, "_insert_google_sheets_queue", side_effect=sqlite3.OperationalError("simulated crash")):
                    with self.assertRaises(sqlite3.OperationalError):
                        store.add_with_delivery_queues(article, "2026-10-29T10:00:00+00:00", "Matched", enqueue_telegram=True, enqueue_email=True, google_sheets_target=(self.spreadsheet_id, self.worksheet))
                self.assertFalse(store.has_seen(article.url))
                self.assertEqual({}, store.telegram_queue_states())
                self.assertEqual({}, store.email_queue_states())
                self.assertEqual({}, store.google_sheets_queue_states())
            finally:
                store.close()

    def test_existing_database_gets_sheets_queue_without_losing_article(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE articles (url TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT, detected_at TEXT NOT NULL, matched_reason TEXT)")
            connection.execute("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("https://example.test/old", "source", "Source", "Old", "", None, "2026-10-29T09:00:00+00:00", None))
            connection.commit()
            connection.close()
            store = ArticleStore(path)
            try:
                self.assertTrue(store.has_seen("https://example.test/old"))
                self.assertEqual({}, store.google_sheets_queue_states())
            finally:
                store.close()

    def test_same_publication_can_have_queues_for_two_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "memory.sqlite3")
            article = self._article()
            try:
                self.assertTrue(store.add_with_delivery_queues(
                    article, "2026-10-29T10:00:00+00:00", "Matched",
                    enqueue_telegram=False, enqueue_email=False,
                    google_sheets_target=("sheet-one", self.worksheet),
                ))
                self.assertFalse(store.add_with_delivery_queues(
                    article, "2026-10-29T10:01:00+00:00", "Matched",
                    enqueue_telegram=False, enqueue_email=False,
                    google_sheets_target=("sheet-two", self.worksheet),
                ))
                self.assertEqual({"PENDING": 1}, store.google_sheets_queue_states("sheet-one", self.worksheet))
                self.assertEqual({"PENDING": 1}, store.google_sheets_queue_states("sheet-two", self.worksheet))
            finally:
                store.close()

    def test_initialization_does_not_enqueue_old_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]):
                report = initialize_monitor(self._config(root))
            self.assertEqual(1, report.saved_articles)
            self.assertEqual({}, self._states(root))

    def test_sheets_failure_does_not_block_telegram_or_email(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = _Values(append_error=OSError("offline"))

            def telegram(_name, findings, *, on_confirmed):
                on_confirmed(findings)

            def email(_name, _recipients, findings, *, on_confirmed):
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), \
                 patch("open_news_monitor.pipeline.send_telegram", side_effect=telegram), \
                 patch("open_news_monitor.pipeline.send_email", side_effect=email), \
                 patch("open_news_monitor.outputs._google_service", return_value=_Service(values)):
                _findings, errors, _markdown = run_monitor(self._config(root, telegram=True, email=True))
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
                self.assertEqual({"SENT": 1}, store.email_queue_states())
                self.assertEqual({"FAILED": 1}, store.google_sheets_queue_states())
            finally:
                store.close()
            self.assertIn("Google Sheets delivery was not confirmed", errors[0])


if __name__ == "__main__":
    unittest.main()
