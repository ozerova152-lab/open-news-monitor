from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.locking import DatabaseRunLock, MonitorAlreadyRunningError
from open_news_monitor.scheduling import schedule_tick


class _FakePosixLock:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 8

    def __init__(self) -> None:
        self.locked = False
        self.calls: list[int] = []

    def flock(self, _fd: int, operation: int) -> None:
        self.calls.append(operation)
        if operation & self.LOCK_UN:
            self.locked = False
            return
        if self.locked:
            raise OSError("already locked")
        self.locked = True


class _FakeWindowsLock:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[int] = []

    def locking(self, _fd: int, operation: int, _size: int) -> None:
        self.calls.append(operation)


class CrossPlatformLockTests(unittest.TestCase):
    def test_posix_lock_releases_after_normal_and_exceptional_exit(self) -> None:
        fake = _FakePosixLock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"fcntl": fake}):
            path = Path(directory) / "monitor.sqlite3"
            with DatabaseRunLock(path, platform_name="posix"):
                self.assertTrue(fake.locked)
            self.assertFalse(fake.locked)
            with self.assertRaisesRegex(RuntimeError, "test error"):
                with DatabaseRunLock(path, platform_name="posix"):
                    raise RuntimeError("test error")
            self.assertFalse(fake.locked)
            with DatabaseRunLock(path, platform_name="posix"):
                self.assertTrue(fake.locked)
        self.assertIn(fake.LOCK_UN, fake.calls)

    def test_posix_competing_lock_is_rejected(self) -> None:
        fake = _FakePosixLock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"fcntl": fake}):
            path = Path(directory) / "monitor.sqlite3"
            with DatabaseRunLock(path, platform_name="posix"):
                with self.assertRaises(MonitorAlreadyRunningError):
                    DatabaseRunLock(path, platform_name="posix").__enter__()
            with DatabaseRunLock(path, platform_name="posix"):
                self.assertTrue(fake.locked)

    def test_windows_branch_keeps_msvcrt_locking_semantics(self) -> None:
        fake = _FakeWindowsLock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"msvcrt": fake}):
            path = Path(directory) / "monitor.sqlite3"
            with DatabaseRunLock(path, platform_name="win32"):
                self.assertIn(fake.LK_NBLCK, fake.calls)
            self.assertEqual(fake.LK_UNLCK, fake.calls[-1])

    def test_schedule_tick_works_with_posix_lock_branch(self) -> None:
        fake = _FakePosixLock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"fcntl": fake}):
            root = Path(directory)
            config = {
                "monitor": {"name": "POSIX schedule", "database_path": str(root / "memory.sqlite3")},
                "sources": [],
                "filters": {"include_any": [], "exclude_any": []},
                "outputs": {"markdown": {"enabled": False}, "telegram": {"enabled": False}, "email": {"enabled": False, "recipients": []}, "google_sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": "News"}},
                "schedule": {"enabled": True, "timezone": "Europe/Moscow", "collect_every_minutes": 60, "deliver_at": {}},
            }
            with patch("open_news_monitor.scheduling.DatabaseRunLock", side_effect=lambda path: DatabaseRunLock(path, platform_name="posix")):
                report = schedule_tick(config, now=datetime(2026, 11, 12, 7, 5, tzinfo=timezone.utc))
            self.assertTrue(report.collected)
            self.assertFalse(fake.locked)
