import json
import tempfile
import unittest
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.models import Article
from open_news_monitor.pipeline import collect_monitor, deliver_monitor
from open_news_monitor.store import ArticleStore
from open_news_monitor.cli import main


class FreshnessTests(unittest.TestCase):
    def test_unavailable_feed_does_not_establish_a_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            config = {
                "monitor": {"name": "test", "database_path": str(db)},
                "sources": [{"id": "feed", "name": "Feed", "url": "https://example.test/feed", "kind": "rss"}],
                "freshness": {"max_age_hours": 48},
                "outputs": {"markdown": {"enabled": False}},
            }
            now = datetime.now(timezone.utc)
            recovered = Article("feed", "Feed", "Recovered", "https://example.test/recovered", "",
                                (now - timedelta(minutes=1)).isoformat())
            with patch("open_news_monitor.pipeline.fetch_feed", side_effect=[OSError("offline"), [recovered]]):
                self.assertTrue(collect_monitor(config)[1])
                findings, errors, markdown = collect_monitor(config)
            self.assertEqual([], findings)
            self.assertEqual([], errors)
            self.assertIn("baseline: Recovered", markdown)

    def test_new_source_archive_and_legacy_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "monitor.sqlite3"
            config = {
                "monitor": {"name": "test", "database_path": str(db)},
                "sources": [{"id": "feed", "name": "Feed", "url": "https://example.test/feed", "kind": "rss"}],
                "freshness": {"max_age_hours": 48},
                "outputs": {"markdown": {"enabled": False}, "telegram": {"enabled": True}},
            }
            start = datetime(2026, 11, 25, 7, tzinfo=timezone.utc)

            def article(slug, date):
                return Article("feed", "Feed", slug, f"https://example.test/{slug}", "",
                               date.isoformat() if isinstance(date, datetime) else date)

            old = article("legacy", start - timedelta(days=50))
            store = ArticleStore(db)
            try:
                store.add_with_telegram_queue(old, start.isoformat(), "legacy", enqueue_telegram=True)
            finally:
                store.close()
            first = article("baseline", start - timedelta(minutes=5))
            fresh = article("new", start + timedelta(minutes=5))
            archive = article("new-url-for-old-story", start - timedelta(days=30))
            doubtful = article("no-date", None)
            with patch("open_news_monitor.pipeline.datetime", wraps=datetime) as clock, \
                 patch("open_news_monitor.pipeline.fetch_feed", side_effect=[[first], [fresh, archive, doubtful]]):
                clock.now.return_value = start
                self.assertEqual([], collect_monitor(config)[0])
                clock.now.return_value = start + timedelta(minutes=10)
                findings, _, markdown = collect_monitor(config)
                self.assertEqual([fresh.url], [x.article.url for x in findings])
                self.assertIn(archive.url, markdown)
                self.assertIn(doubtful.url, markdown)
                with patch("open_news_monitor.pipeline.send_telegram") as sender:
                    sender.side_effect = lambda name, queued, *, on_confirmed: on_confirmed(queued)
                    errors, status = deliver_monitor(config, channels={"telegram"})
                    self.assertEqual([], errors)
                    self.assertEqual([old.url], status["telegram"]["withheld_urls"])
                    self.assertEqual([fresh.url], [x.article.url for x in sender.call_args.args[1]])
                    deliver_monitor(config, channels={"telegram"})
                    sender.assert_called_once()
            profile = root / "profile.json"
            profile.write_text(json.dumps(config), encoding="utf-8")
            captured = io.StringIO()
            with patch.object(sys, "argv", ["open-news-monitor", "withheld", str(profile)]), redirect_stdout(captured):
                main()
            self.assertIn(doubtful.url, captured.getvalue())
            self.assertIn("uncertain", captured.getvalue())
            events = [json.loads(line) for line in
                      (root / "monitor.status.history.jsonl").read_text(encoding="utf-8").splitlines()]
            snapshots = [event for event in events if "event" not in event]
            self.assertEqual(4, len(snapshots))
            self.assertEqual(4, sum(event.get("event") == "started" for event in events))
            self.assertEqual("collection", snapshots[1]["mode"])
            self.assertEqual("delivery", snapshots[-1]["mode"])
