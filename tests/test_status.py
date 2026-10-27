from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from open_news_monitor.status import read_status, render_status, write_status


class StatusTests(unittest.TestCase):
    def test_writes_reads_and_renders_a_health_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime" / "status.json"
            write_status(
                path,
                {
                    "monitor_name": "Test monitor",
                    "ran_at": "2026-10-27T10:00:00+03:00",
                    "mode": "ordinary",
                    "new_matching_publications": 2,
                    "sources": [{"name": "Official feed", "status": "ok", "articles": 5}],
                    "errors": [],
                },
            )
            report = render_status(read_status(path))
        self.assertIn("Test monitor", report)
        self.assertIn("получено 5 публикаций", report)
