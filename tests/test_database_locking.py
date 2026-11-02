from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.locking import MonitorAlreadyRunningError
from open_news_monitor.models import Article
from open_news_monitor.pipeline import collect_monitor, deliver_monitor, run_monitor
from open_news_monitor.store import ArticleStore


class DatabaseLockingTests(unittest.TestCase):
    @staticmethod
    def _config(root: Path, *, name: str = "memory.sqlite3", telegram: bool = False) -> dict:
        return {
            "monitor": {
                "name": "Lock test",
                "database_path": str(root / name),
                "status_path": str(root / f"{name}.status.json"),
            },
            "sources": [],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False},
                "telegram": {"enabled": telegram},
                "email": {"enabled": False, "recipients": []},
                "google_sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": "News"},
            },
        }

    @staticmethod
    def _hold_lock(
        database_path: Path, *, crash: bool = False, exit_normally: bool = False,
    ) -> subprocess.Popen[str]:
        action = "os._exit(0)" if crash else ("pass" if exit_normally else "time.sleep(30)")
        code = (
            "from open_news_monitor.locking import DatabaseRunLock\n"
            "import os\n"
            "import time\n"
            f"with DatabaseRunLock({str(database_path)!r}):\n"
            "    print('LOCKED', flush=True)\n"
            f"    {action}\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        assert process.stdout is not None
        if process.stdout.readline().strip() != "LOCKED":
            stderr = process.stderr.read() if process.stderr else ""
            process.wait(timeout=5)
            raise AssertionError(f"lock holder did not start: {stderr}")
        return process

    @staticmethod
    def _stop(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    def test_collect_deliver_and_run_reject_another_process_with_same_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            process = self._hold_lock(root / "memory.sqlite3")
            try:
                for operation in (
                    lambda: collect_monitor(config),
                    lambda: deliver_monitor(config),
                    lambda: run_monitor(config),
                ):
                    with self.assertRaises(MonitorAlreadyRunningError):
                        operation()
            finally:
                self._stop(process)

    def test_different_databases_can_run_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._config(root, name="first.sqlite3")
            second = self._config(root, name="second.sqlite3")
            process = self._hold_lock(root / "first.sqlite3")
            try:
                findings, errors, _markdown = collect_monitor(second)
            finally:
                self._stop(process)
            self.assertEqual([], findings)
            self.assertEqual([], errors)

    def test_lock_releases_after_normal_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = self._hold_lock(root / "memory.sqlite3", exit_normally=True)
            process.wait(timeout=5)
            self._stop(process)
            findings, errors, _markdown = collect_monitor(self._config(root))
            self.assertEqual([], findings)
            self.assertEqual([], errors)

    def test_lock_releases_after_unexpected_process_termination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = self._hold_lock(root / "memory.sqlite3", crash=True)
            process.wait(timeout=5)
            self._stop(process)
            findings, errors, _markdown = collect_monitor(self._config(root))
            self.assertEqual([], findings)
            self.assertEqual([], errors)

    def test_lock_prevents_parallel_delivery_and_preserves_sqlite_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, telegram=True)
            database = root / "memory.sqlite3"
            store = ArticleStore(database)
            try:
                article = Article("source", "Source", "Queued", "https://example.test/lock", "", "2026-11-02")
                store.add_with_delivery_queues(
                    article, "2026-11-02T10:00:00+00:00", "Matched",
                    enqueue_telegram=True, enqueue_email=False,
                )
            finally:
                store.close()
            process = self._hold_lock(database)
            try:
                with patch("open_news_monitor.pipeline.send_telegram") as sender:
                    with self.assertRaises(MonitorAlreadyRunningError):
                        deliver_monitor(config)
                sender.assert_not_called()
            finally:
                self._stop(process)

            def confirm(_name, findings, *, on_confirmed):
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.send_telegram", side_effect=confirm) as sender:
                errors, _status = deliver_monitor(config)
            sender.assert_called_once()
            self.assertEqual([], errors)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertEqual([("SENT", 1)], connection.execute("SELECT state, COUNT(*) FROM telegram_queue GROUP BY state").fetchall())
            finally:
                connection.close()
