from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from open_news_monitor.models import Article, Finding
from open_news_monitor.outputs import send_telegram, telegram_message_chunks
from open_news_monitor.pipeline import initialize_monitor, run_monitor
from open_news_monitor.status import read_status
from open_news_monitor.store import ArticleStore


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class TelegramQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_token = os.environ.get("ONM_TELEGRAM_BOT_TOKEN")
        self.old_chat = os.environ.get("ONM_TELEGRAM_CHAT_ID")
        os.environ["ONM_TELEGRAM_BOT_TOKEN"] = "test-token"
        os.environ["ONM_TELEGRAM_CHAT_ID"] = "12345"

    def tearDown(self) -> None:
        self._restore_env("ONM_TELEGRAM_BOT_TOKEN", self.old_token)
        self._restore_env("ONM_TELEGRAM_CHAT_ID", self.old_chat)

    @staticmethod
    def _restore_env(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    @staticmethod
    def _article(number: int = 1) -> Article:
        return Article("source", "Source", f"News {number}", f"https://example.test/{number}", "", "2026-10-29")

    @staticmethod
    def _config(root: Path) -> dict:
        return {
            "monitor": {"name": "Telegram test", "database_path": str(root / "memory.sqlite3"), "status_path": str(root / "status.json")},
            "sources": [{"id": "source", "name": "Source", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {"markdown": {"enabled": False}, "telegram": {"enabled": True}},
        }

    def _run(self, root: Path, articles: list[Article], sender):
        with patch("open_news_monitor.pipeline.fetch_feed", return_value=articles), patch("open_news_monitor.pipeline.send_telegram", side_effect=sender):
            return run_monitor(self._config(root))

    def test_successful_delivery_marks_item_sent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def sent(_name, findings, *, on_confirmed):
                on_confirmed(findings)

            _findings, errors, _markdown = self._run(root, [self._article()], sent)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
            finally:
                store.close()
            self.assertEqual([], errors)
            self.assertEqual("sent", read_status(root / "status.json")["deliveries"]["telegram"]["status"])

    def test_missing_token_keeps_item_failed(self) -> None:
        os.environ.pop("ONM_TELEGRAM_BOT_TOKEN")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]):
                _findings, errors, _markdown = run_monitor(self._config(root))
            self.assertTrue(errors)
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["telegram"]["status"])
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"FAILED": 1}, store.telegram_queue_states())
            finally:
                store.close()

    def test_wrong_token_http_error_keeps_item_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _findings, errors, _markdown = self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPError("https://api.telegram.org", 401, "Unauthorized", {}, None)))
            self.assertTrue(errors)
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["telegram"]["status"])

    def test_wrong_chat_id_rejection_keeps_item_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _findings, errors, _markdown = self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad chat id")))
            self.assertTrue(errors)
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["telegram"]["status"])

    def test_timeout_keeps_item_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _findings, errors, _markdown = self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")))
            self.assertTrue(errors)
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["telegram"]["status"])

    def test_retry_sends_previously_failed_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
            delivered: list[str] = []

            def sent(_name, findings, *, on_confirmed):
                delivered.extend(item.article.url for item in findings)
                on_confirmed(findings)

            self._run(root, [self._article()], sent)
            self.assertEqual([self._article().url], delivered)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
            finally:
                store.close()

    def test_confirmed_item_is_not_sent_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda _name, findings, *, on_confirmed: on_confirmed(findings))
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), patch("open_news_monitor.pipeline.send_telegram") as sender:
                run_monitor(self._config(root))
            sender.assert_not_called()

    def test_long_digest_is_split_without_losing_links(self) -> None:
        findings = [
            Finding(self._article(number), "Matched monitor rules", datetime.now(timezone.utc))
            for number in range(1, 9)
        ]
        findings = [Finding(Article(item.article.source_id, item.article.source_name, "X" * 70, item.article.url, "", item.article.published_at), item.reason, item.detected_at) for item in findings]
        chunks = telegram_message_chunks("Monitor", findings, max_chars=180)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(text) <= 180 for text, _chunk in chunks))
        self.assertEqual({item.article.url for item in findings}, {item.article.url for _text, chunk in chunks for item in chunk})

    def test_partial_delivery_marks_only_unconfirmed_items_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def partially_sent(_name, findings, *, on_confirmed):
                on_confirmed(findings[:1])
                raise OSError("connection lost after first message")

            self._run(root, [self._article(1), self._article(2)], partially_sent)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1, "FAILED": 1}, store.telegram_queue_states())
            finally:
                store.close()
            delivery = read_status(root / "status.json")["deliveries"]["telegram"]
            self.assertEqual("partial", delivery["status"])
            self.assertEqual(1, delivery["failed"])

    def test_initialization_does_not_enqueue_existing_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), patch("open_news_monitor.pipeline.send_telegram") as sender:
                report = initialize_monitor(self._config(root))
            self.assertEqual(1, report.saved_articles)
            sender.assert_not_called()
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({}, store.telegram_queue_states())
            finally:
                store.close()

    def test_failed_queue_survives_program_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
            reopened = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual(1, len(reopened.pending_telegram_findings()))
                self.assertEqual({"FAILED": 1}, reopened.telegram_queue_states())
            finally:
                reopened.close()

    def test_status_records_telegram_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
            status = read_status(root / "status.json")
            self.assertEqual("failed", status["deliveries"]["telegram"]["status"])
            self.assertIn("Telegram delivery was not confirmed", status["errors"][0])

    def test_queue_is_saved_before_delivery_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fail_after_queue(_name, findings, *, on_confirmed):
                store = ArticleStore(root / "memory.sqlite3")
                try:
                    self.assertEqual({"PENDING": 1}, store.telegram_queue_states())
                finally:
                    store.close()
                raise OSError("connection interrupted")

            self._run(root, [self._article()], fail_after_queue)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"FAILED": 1}, store.telegram_queue_states())
            finally:
                store.close()

    def test_article_and_pending_queue_row_rollback_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "memory.sqlite3")
            article = self._article()
            try:
                with patch.object(store, "_insert_telegram_queue", side_effect=sqlite3.OperationalError("simulated crash")):
                    with self.assertRaises(sqlite3.OperationalError):
                        store.add_with_telegram_queue(
                            article, "2026-10-29T10:00:00+00:00", "Matched monitor rules", enqueue_telegram=True
                        )
                self.assertFalse(store.has_seen(article.url))
                self.assertEqual({}, store.telegram_queue_states())
            finally:
                store.close()

    def test_article_can_be_saved_and_queued_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            article = self._article()
            store = ArticleStore(path)
            try:
                with patch.object(store, "_insert_telegram_queue", side_effect=sqlite3.OperationalError("simulated crash")):
                    with self.assertRaises(sqlite3.OperationalError):
                        store.add_with_telegram_queue(
                            article, "2026-10-29T10:00:00+00:00", "Matched monitor rules", enqueue_telegram=True
                        )
            finally:
                store.close()
            restarted = ArticleStore(path)
            try:
                self.assertTrue(
                    restarted.add_with_telegram_queue(
                        article, "2026-10-29T10:01:00+00:00", "Matched monitor rules", enqueue_telegram=True
                    )
                )
                self.assertTrue(restarted.has_seen(article.url))
                self.assertEqual({"PENDING": 1}, restarted.telegram_queue_states())
            finally:
                restarted.close()

    def test_existing_article_database_gets_queue_without_losing_articles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE articles (
                url TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
                title TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT,
                detected_at TEXT NOT NULL, matched_reason TEXT)"""
            )
            connection.execute(
                "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("https://example.test/old", "source", "Source", "Old", "", None, "2026-10-29T09:00:00+00:00", None),
            )
            connection.commit()
            connection.close()
            store = ArticleStore(path)
            try:
                self.assertTrue(store.has_seen("https://example.test/old"))
                self.assertEqual({}, store.telegram_queue_states())
            finally:
                store.close()

    def test_send_telegram_accepts_successful_mock_response(self) -> None:
        with patch("open_news_monitor.outputs.urlopen", return_value=_Response({"ok": True})):
            send_telegram("Monitor", [Finding(self._article(), "Matched monitor rules", datetime.now(timezone.utc))])

    def test_telegram_ok_false_is_an_error(self) -> None:
        with patch("open_news_monitor.outputs.urlopen", return_value=_Response({"ok": False, "description": "chat not found"})):
            with self.assertRaises(RuntimeError):
                send_telegram("Monitor", [Finding(self._article(), "Matched monitor rules", datetime.now(timezone.utc))])

    def test_multiple_message_delivery_can_stop_after_confirmed_part(self) -> None:
        findings = [
            Finding(
                Article("source", "Source", "X" * 1500, f"https://example.test/{number}", "", "2026-10-29"),
                "Matched monitor rules",
                datetime.now(timezone.utc),
            )
            for number in range(1, 5)
        ]
        confirmed: list[str] = []
        with patch("open_news_monitor.outputs.urlopen", side_effect=[_Response({"ok": True}), OSError("connection lost")]):
            with self.assertRaises(OSError):
                send_telegram("Monitor", findings, on_confirmed=lambda chunk: confirmed.extend(item.article.url for item in chunk))
        self.assertGreater(len(confirmed), 0)
        self.assertLess(len(confirmed), len(findings))


if __name__ == "__main__":
    unittest.main()
