from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.models import Article
from open_news_monitor.pipeline import collect_monitor, deliver_monitor, run_monitor
from open_news_monitor.status import read_status
from open_news_monitor.store import ArticleStore


class CollectionDeliverySplitTests(unittest.TestCase):
    @staticmethod
    def _article() -> Article:
        return Article("source", "Source", "Queued news", "https://example.test/queued", "", "2026-11-02")

    @staticmethod
    def _config(root: Path) -> dict:
        return {
            "monitor": {
                "name": "Collection delivery test",
                "database_path": str(root / "memory.sqlite3"),
                "status_path": str(root / "status.json"),
            },
            "sources": [{"id": "source", "name": "Source", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False},
                "telegram": {"enabled": True},
                "email": {"enabled": True, "recipients": ["receiver@example.test"]},
                "google_sheets": {"enabled": True, "spreadsheet_id": "test-sheet", "worksheet": "News"},
            },
        }

    def test_collect_queues_all_channels_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), \
                 patch("open_news_monitor.pipeline.send_telegram") as telegram, \
                 patch("open_news_monitor.pipeline.send_email") as email, \
                 patch("open_news_monitor.pipeline.append_google_sheet") as sheets:
                findings, errors, _markdown = collect_monitor(self._config(root))
            telegram.assert_not_called()
            email.assert_not_called()
            sheets.assert_not_called()
            self.assertEqual(1, len(findings))
            self.assertEqual([], errors)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"PENDING": 1}, store.telegram_queue_states())
                self.assertEqual({"PENDING": 1}, store.email_queue_states())
                self.assertEqual({"PENDING": 1}, store.google_sheets_queue_states("test-sheet", "News"))
            finally:
                store.close()
            self.assertEqual("collection", read_status(root / "status.json")["mode"])

    def test_deliver_uses_only_selected_queues_without_fetching_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]):
                collect_monitor(config)

            def confirm_telegram(_name, findings, *, on_confirmed):
                on_confirmed(findings)

            def confirm_sheets(_sheet_id, _worksheet, findings, *, on_confirmed):
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", side_effect=AssertionError("sources must not be fetched")) as fetch, \
                 patch("open_news_monitor.pipeline.send_telegram", side_effect=confirm_telegram) as telegram, \
                 patch("open_news_monitor.pipeline.send_email") as email, \
                 patch("open_news_monitor.pipeline.append_google_sheet", side_effect=confirm_sheets) as sheets:
                errors, status = deliver_monitor(config, channels={"telegram", "google_sheets"})
            fetch.assert_not_called()
            telegram.assert_called_once()
            sheets.assert_called_once()
            email.assert_not_called()
            self.assertEqual([], errors)
            self.assertEqual("not_selected", status["email"]["status"])
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
                self.assertEqual({"PENDING": 1}, store.email_queue_states())
                self.assertEqual({"SENT": 1}, store.google_sheets_queue_states("test-sheet", "News"))
            finally:
                store.close()
            self.assertEqual("delivery", read_status(root / "status.json")["mode"])

    def test_run_keeps_collect_then_deliver_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def confirm_telegram(_name, findings, *, on_confirmed):
                on_confirmed(findings)

            def confirm_email(_name, _recipients, findings, *, on_confirmed):
                on_confirmed(findings)

            def confirm_sheets(_sheet_id, _worksheet, findings, *, on_confirmed):
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), \
                 patch("open_news_monitor.pipeline.send_telegram", side_effect=confirm_telegram), \
                 patch("open_news_monitor.pipeline.send_email", side_effect=confirm_email), \
                 patch("open_news_monitor.pipeline.append_google_sheet", side_effect=confirm_sheets):
                findings, errors, _markdown = run_monitor(self._config(root))
            self.assertEqual(1, len(findings))
            self.assertEqual([], errors)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
                self.assertEqual({"SENT": 1}, store.email_queue_states())
                self.assertEqual({"SENT": 1}, store.google_sheets_queue_states("test-sheet", "News"))
            finally:
                store.close()
            self.assertEqual("ordinary", read_status(root / "status.json")["mode"])
