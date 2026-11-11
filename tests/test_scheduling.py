from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.doctor import ERROR, READY, inspect_profile
from open_news_monitor.locking import DatabaseRunLock, MonitorAlreadyRunningError
from open_news_monitor.scheduling import schedule_configuration_problem, schedule_tick
from open_news_monitor.store import ArticleStore


class SchedulingTests(unittest.TestCase):
    def _config(self, root: Path, **schedule: object) -> dict:
        return {
            "monitor": {"name": "Schedule test", "database_path": str(root / "memory.sqlite3")},
            "sources": [],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False},
                "telegram": {"enabled": True},
                "email": {"enabled": True, "recipients": ["receiver@example.test"]},
                "google_sheets": {"enabled": True, "spreadsheet_id": "test-sheet", "worksheet": "News"},
            },
            "schedule": {
                "enabled": True,
                "timezone": "Europe/Moscow",
                "collect_every_minutes": 180,
                "deliver_at": {},
                **schedule,
            },
        }

    @staticmethod
    def _successful_collection(*_args, **_kwargs):
        return [], [], ""

    @staticmethod
    def _successful_delivery(*_args, **_kwargs):
        return [], {}

    def test_collects_once_in_the_current_interval_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection) as collect:
                first = schedule_tick(config, now=now)
                second = schedule_tick(config, now=now)
            self.assertTrue(first.collected)
            self.assertFalse(second.collected)
            self.assertEqual(1, collect.call_count)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual(1, store.connection.execute("SELECT COUNT(*) FROM schedule_runs WHERE action = 'collect'").fetchone()[0])
            finally:
                store.close()

    def test_delivery_runs_only_at_the_latest_due_time_per_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, deliver_at={"telegram": ["10:00", "18:00"], "google_sheets": ["09:30"]})
            # 10:05 Moscow: Telegram 10:00 and Sheets 09:30 are due; 18:00 is not.
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                report = schedule_tick(config, now=now)
            self.assertEqual(["telegram", "google_sheets"], report.delivered_channels)
            self.assertEqual([{"telegram"}, {"google_sheets"}], [call.kwargs["channels"] for call in deliver.call_args_list])

    def test_repeat_tick_does_not_repeat_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, deliver_at={"telegram": ["10:00"]})
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                schedule_tick(config, now=now)
                report = schedule_tick(config, now=now)
            self.assertFalse(report.did_work)
            self.assertEqual(1, deliver.call_count)

    def test_restart_keeps_completed_delivery_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, deliver_at={"email": ["10:00"]})
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                schedule_tick(config, now=now)
                # A new call creates new SQLite connections, simulating a restart.
                schedule_tick(config, now=now)
            self.assertEqual(1, deliver.call_count)

    def test_missed_collection_intervals_are_not_replayed_one_by_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection) as collect:
                schedule_tick(config, now=now)
            self.assertEqual(1, collect.call_count)

    def test_crossing_midnight_uses_a_new_local_delivery_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, deliver_at={"telegram": ["00:00"]})
            # 00:02 in Moscow on the following local day.
            now = datetime(2026, 11, 2, 21, 2, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                report = schedule_tick(config, now=now)
            self.assertEqual(["telegram"], report.delivered_channels)
            self.assertEqual(1, deliver.call_count)

    def test_dst_repeated_clock_time_does_not_duplicate_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, timezone="Europe/Berlin", deliver_at={"telegram": ["02:30"]})
            first_0235 = datetime(2026, 10, 25, 0, 35, tzinfo=timezone.utc)
            repeated_0235 = datetime(2026, 10, 25, 1, 35, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                schedule_tick(config, now=first_0235)
                report = schedule_tick(config, now=repeated_0235)
            self.assertFalse(report.delivered_channels)
            self.assertEqual(1, deliver.call_count)

    def test_dst_missing_clock_time_runs_at_the_next_available_tick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, timezone="Europe/Berlin", deliver_at={"telegram": ["02:30"]})
            # On this date Berlin jumps from 01:59 to 03:00 local time.
            after_jump = datetime(2026, 3, 29, 1, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                report = schedule_tick(config, now=after_jump)
            self.assertEqual(["telegram"], report.delivered_channels)
            self.assertEqual(1, deliver.call_count)

    def test_failed_collection_and_delivery_are_retried_without_marking_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, deliver_at={"telegram": ["10:00"]})
            now = datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc)
            with patch("open_news_monitor.scheduling._run_monitor_locked", return_value=([], ["source unavailable"], "")), \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", return_value=(["temporary delivery error"], {})):
                failed = schedule_tick(config, now=now)
            self.assertEqual(2, len(failed.errors))
            with patch("open_news_monitor.scheduling._run_monitor_locked", side_effect=self._successful_collection) as collect, \
                 patch("open_news_monitor.scheduling._deliver_monitor_locked", side_effect=self._successful_delivery) as deliver:
                recovered = schedule_tick(config, now=now)
            self.assertTrue(recovered.collected)
            self.assertEqual(["telegram"], recovered.delivered_channels)
            self.assertEqual(1, collect.call_count)
            self.assertEqual(1, deliver.call_count)

    def test_tick_uses_the_existing_database_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            with DatabaseRunLock(root / "memory.sqlite3"):
                with self.assertRaises(MonitorAlreadyRunningError):
                    schedule_tick(config, now=datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc))

    def test_old_profile_without_schedule_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            del config["schedule"]
            report = schedule_tick(config, now=datetime(2026, 11, 2, 7, 5, tzinfo=timezone.utc))
            self.assertFalse(report.did_work)
            self.assertEqual([], report.errors)
            self.assertFalse((root / "memory.sqlite3").exists())

    def test_schedule_validation_covers_timezone_time_interval_and_channels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [
                ({"timezone": "Not/AZone"}, "часовой пояс"),
                ({"collect_every_minutes": 0}, "Периодичность"),
                ({"deliver_at": {"telegram": ["25:00"]}}, "Время доставки"),
                ({"deliver_at": {"missing": ["10:00"]}}, "Неизвестный канал"),
                ({"deliver_at": {"telegram": ["10:00"]}, "outputs": {"telegram": {"enabled": False}}}, "не включён"),
            ]
            for overrides, expected in cases:
                config = self._config(root)
                outputs = overrides.pop("outputs", None)
                config["schedule"].update(overrides)
                if outputs:
                    config["outputs"].update(outputs)
                self.assertIn(expected, schedule_configuration_problem(config))
    def test_doctor_reports_schedule_errors_without_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = self._config(root, timezone="Not/AZone")
            valid = self._config(root, deliver_at={"telegram": ["10:00"]})
            self.assertEqual(ERROR, inspect_profile(invalid)[-1].status)
            self.assertEqual(READY, inspect_profile(valid)[-1].status)

