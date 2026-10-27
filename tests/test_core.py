from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.filtering import match_article
from open_news_monitor.html_collector import fetch_html
from open_news_monitor.initializer import create_monitor_interactively
from open_news_monitor.local_env import load_local_env
from open_news_monitor.models import Article, Source
from open_news_monitor.rss import fetch_feed
from open_news_monitor.source_check import SourceCheck, check_rss_source
from open_news_monitor.store import ArticleStore


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.article = Article(
            source_id="source",
            source_name="Source",
            title="AI agent improves a workflow",
            url="https://example.org/a",
            summary="Automation guidance.",
            published_at=None,
        )

    def test_match_returns_a_human_reason(self) -> None:
        reason = match_article(self.article, ["AI", "agent"], ["vacancy"])
        self.assertEqual(reason, "Matched include rule: AI, agent")

    def test_exclude_rule_wins(self) -> None:
        excluded = Article(**{**self.article.__dict__, "title": "AI agent vacancy"})
        self.assertIsNone(match_article(excluded, ["AI"], ["vacancy"]))


class StoreTests(unittest.TestCase):
    def test_store_remembers_seen_url(self) -> None:
        article = Article(
            source_id="source",
            source_name="Source",
            title="Stored article",
            url="https://example.org/stored",
            summary="",
            published_at=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "monitor.sqlite3")
            try:
                self.assertFalse(store.has_seen(article.url))
                store.add(article, "2026-10-27T07:00:00+00:00", "Matched include rule: AI")
                self.assertTrue(store.has_seen(article.url))
            finally:
                store.close()


class InitializerTests(unittest.TestCase):
    def test_initializer_writes_only_a_confirmed_source(self) -> None:
        answers = iter(["My AI monitor", "https://example.org/feed.xml", "Example", "", "agent, MCP", "vacancy", ""])
        article = Article("source", "Example", "Title", "https://example.org/article", "Summary", "2026-10-27")
        approved = SourceCheck("Example", "https://example.org/feed.xml", [article])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            create_monitor_interactively(
                target,
                input_func=lambda _prompt: next(answers),
                output_func=messages.append,
                inspect_func=lambda _url, _name: approved,
            )
            config = target.read_text(encoding="utf-8")

        self.assertIn('"name": "My AI monitor"', config)
        self.assertIn('"id": "example-org"', config)
        self.assertIn('"enabled": false', config)

    def test_initializer_saves_selected_delivery_channels(self) -> None:
        answers = iter([
            "Delivery monitor", "https://example.org/feed.xml", "Example", "", "", "", "1,2,3",
            "https://docs.google.com/spreadsheets/d/sheet-123/edit", "Signals", "reader@example.org, editor@example.org",
        ])
        article = Article("source", "Example", "Title", "https://example.org/article", "Summary", "2026-10-27")
        approved = SourceCheck("Example", "https://example.org/feed.xml", [article])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            create_monitor_interactively(
                target,
                input_func=lambda _prompt: next(answers),
                output_func=lambda _message: None,
                inspect_func=lambda _url, _name: approved,
            )
            config = target.read_text(encoding="utf-8")

        self.assertIn('"spreadsheet_id": "sheet-123"', config)
        self.assertIn('"telegram": {\n      "enabled": true', config)
        self.assertIn('"reader@example.org"', config)


class LocalEnvTests(unittest.TestCase):
    def test_loader_sets_missing_values_without_overwriting_existing_environment(self) -> None:
        previous = os.environ.get("ONM_TEST_EXISTING")
        os.environ["ONM_TEST_EXISTING"] = "already-set"
        try:
            with tempfile.TemporaryDirectory() as directory:
                env_path = Path(directory) / ".env"
                env_path.write_text("ONM_TEST_NEW=from-file\nONM_TEST_EXISTING=replace-me\n", encoding="utf-8")
                self.assertTrue(load_local_env(env_path))
            self.assertEqual(os.environ["ONM_TEST_NEW"], "from-file")
            self.assertEqual(os.environ["ONM_TEST_EXISTING"], "already-set")
        finally:
            os.environ.pop("ONM_TEST_NEW", None)
            if previous is None:
                os.environ.pop("ONM_TEST_EXISTING", None)
            else:
                os.environ["ONM_TEST_EXISTING"] = previous


class RssTests(unittest.TestCase):
    def test_feed_reader_extracts_titles_links_and_dates(self) -> None:
        payload = (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        source = Source("example", "Example source", "rss", "https://example.org/feed.xml")
        with patch("open_news_monitor.rss.urlopen", return_value=Response()):
            articles = fetch_feed(source)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].title, "AI agent improves a research workflow")
        self.assertEqual(articles[0].url, "https://example.org/articles/ai-agent")
        self.assertEqual(articles[0].published_at, "2026-10-27T07:00:00+00:00")

    def test_source_check_reports_confirmed_fields(self) -> None:
        payload = (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        with patch("open_news_monitor.rss.urlopen", return_value=Response()):
            report = check_rss_source("https://example.org/feed.xml", "Example source")

        self.assertIn("Статус: Можно подключать", report)
        self.assertIn("Получено 2 публикаций", report)


class HtmlCollectorTests(unittest.TestCase):
    def test_html_collector_extracts_fields_from_one_card(self) -> None:
        payload = (Path(__file__).parent / "fixtures" / "sample_news.html").read_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        source = Source(
            "example-html",
            "Example news",
            "html",
            "https://example.org/news",
            selectors={"card": "article.news-card", "title": "h2 a", "link": "h2 a", "date": "time", "summary": "p.summary"},
        )
        with patch("open_news_monitor.html_collector.urlopen", return_value=Response()):
            articles = fetch_html(source)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].title, "AI agent update")
        self.assertEqual(articles[0].url, "https://example.org/news/agent")
        self.assertEqual(articles[0].published_at, "2026-10-27")


if __name__ == "__main__":
    unittest.main()
