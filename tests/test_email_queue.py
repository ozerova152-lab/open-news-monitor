from __future__ import annotations

import os
import sqlite3
import smtplib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.models import Article
from open_news_monitor.outputs import send_email
from open_news_monitor.pipeline import initialize_monitor, run_monitor
from open_news_monitor.status import read_status
from open_news_monitor.store import ArticleStore


class _SMTP:
    def __init__(self, refused=None):
        self.refused = refused or {}
        self.started_tls = False
        self.logged_in = False
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, *_args):
        self.logged_in = True

    def send_message(self, message):
        self.message = message
        return self.refused


class EmailQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {name: os.environ.get(name) for name in (
            "ONM_SMTP_HOST", "ONM_SMTP_PORT", "ONM_SMTP_USERNAME", "ONM_SMTP_PASSWORD", "ONM_SMTP_FROM"
        )}
        os.environ.update({
            "ONM_SMTP_HOST": "smtp.test",
            "ONM_SMTP_PORT": "587",
            "ONM_SMTP_USERNAME": "test-user",
            "ONM_SMTP_PASSWORD": "test-password",
            "ONM_SMTP_FROM": "sender@example.test",
        })

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @staticmethod
    def _article(number: int = 1) -> Article:
        return Article("source", "Source", f"Email news {number}", f"https://example.test/email/{number}", "", "2026-10-29")

    @staticmethod
    def _config(root: Path, *, telegram: bool = False, sheets: bool = False) -> dict:
        return {
            "monitor": {"name": "Email test", "database_path": str(root / "memory.sqlite3"), "status_path": str(root / "status.json")},
            "sources": [{"id": "source", "name": "Source", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False},
                "telegram": {"enabled": telegram},
                "email": {"enabled": True, "recipients": ["receiver@example.test"]},
                "google_sheets": {"enabled": sheets, "spreadsheet_id": "test-sheet", "worksheet": "News"},
            },
        }

    def _run(self, root: Path, articles: list[Article], email_sender, *, telegram=False, sheets=False):
        with patch("open_news_monitor.pipeline.fetch_feed", return_value=articles), patch("open_news_monitor.pipeline.send_email", side_effect=email_sender):
            return run_monitor(self._config(root, telegram=telegram, sheets=sheets))

    def test_successful_smtp_send_marks_all_items_sent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def sent(_name, _recipients, findings, *, on_confirmed):
                on_confirmed(findings)

            _findings, errors, _markdown = self._run(root, [self._article(1), self._article(2)], sent)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 2}, store.email_queue_states())
            finally:
                store.close()
            self.assertEqual([], errors)
            self.assertEqual("sent", read_status(root / "status.json")["deliveries"]["email"]["status"])

    def test_send_email_uses_tls_and_confirms_all_findings(self) -> None:
        smtp = _SMTP()
        confirmed = []
        with patch("open_news_monitor.outputs.smtplib.SMTP", return_value=smtp):
            from open_news_monitor.models import Finding
            finding = Finding(self._article(), "Matched monitor rules", datetime.now(timezone.utc))
            send_email("Email test", ["receiver@example.test"], [finding], on_confirmed=confirmed.extend)
        self.assertTrue(smtp.started_tls)
        self.assertTrue(smtp.logged_in)
        self.assertIn("Email news 1", smtp.message.get_content())
        self.assertEqual(1, len(confirmed))

    def test_refused_recipient_does_not_confirm_email(self) -> None:
        smtp = _SMTP(refused={"receiver@example.test": (550, b"rejected")})
        with patch("open_news_monitor.outputs.smtplib.SMTP", return_value=smtp):
            from open_news_monitor.models import Finding
            finding = Finding(self._article(), "Matched monitor rules", datetime.now(timezone.utc))
            with self.assertRaises(RuntimeError):
                send_email("Email test", ["receiver@example.test"], [finding])

    def test_authentication_error_keeps_items_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _findings, errors, _markdown = self._run(
                root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(535, b"bad credentials"))
            )
            self.assertIn("SMTPAuthenticationError", errors[0])
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"FAILED": 1}, store.email_queue_states())
            finally:
                store.close()

    def test_network_error_keeps_items_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _findings, errors, _markdown = self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network down")))
            self.assertIn("OSError", errors[0])
            self.assertEqual("failed", read_status(root / "status.json")["deliveries"]["email"]["status"])

    def test_missing_smtp_settings_keep_items_failed(self) -> None:
        os.environ.pop("ONM_SMTP_HOST")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]):
                _findings, errors, _markdown = run_monitor(self._config(root))
            self.assertIn("RuntimeError", errors[0])
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"FAILED": 1}, store.email_queue_states())
            finally:
                store.close()

    def test_retry_sends_failed_email_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
            delivered = []

            def sent(_name, _recipients, findings, *, on_confirmed):
                delivered.extend(item.article.url for item in findings)
                on_confirmed(findings)

            self._run(root, [self._article()], sent)
            self.assertEqual([self._article().url], delivered)

    def test_sent_email_item_is_not_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, [self._article()], lambda _n, _r, findings, *, on_confirmed: on_confirmed(findings))
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), patch("open_news_monitor.pipeline.send_email") as sender:
                run_monitor(self._config(root))
            sender.assert_not_called()

    def test_article_and_email_queue_rollback_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "memory.sqlite3")
            article = self._article()
            try:
                with patch.object(store, "_insert_email_queue", side_effect=sqlite3.OperationalError("simulated crash")):
                    with self.assertRaises(sqlite3.OperationalError):
                        store.add_with_delivery_queues(article, "2026-10-29T10:00:00+00:00", "Matched", enqueue_telegram=False, enqueue_email=True)
                self.assertFalse(store.has_seen(article.url))
                self.assertEqual({}, store.email_queue_states())
            finally:
                store.close()

    def test_existing_database_gets_email_queue_without_losing_article(self) -> None:
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
                self.assertEqual({}, store.email_queue_states())
            finally:
                store.close()

    def test_initialization_does_not_enqueue_old_items_for_email(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), patch("open_news_monitor.pipeline.send_email") as sender:
                report = initialize_monitor(self._config(root))
            self.assertEqual(1, report.saved_articles)
            sender.assert_not_called()
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({}, store.email_queue_states())
            finally:
                store.close()

    def test_email_failure_does_not_block_telegram_or_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def telegram(_name, findings, *, on_confirmed):
                calls.append("telegram")
                on_confirmed(findings)

            def sheets(_id, _worksheet, findings, *, on_confirmed):
                calls.append("sheets")
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[self._article()]), \
                 patch("open_news_monitor.pipeline.send_telegram", side_effect=telegram), \
                 patch("open_news_monitor.pipeline.send_email", side_effect=OSError("offline")), \
                 patch("open_news_monitor.pipeline.append_google_sheet", side_effect=sheets):
                _findings, errors, _markdown = run_monitor(self._config(root, telegram=True, sheets=True))
            self.assertEqual(["telegram", "sheets"], calls)
            self.assertIn("Email delivery was not confirmed", errors[0])


if __name__ == "__main__":
    unittest.main()
