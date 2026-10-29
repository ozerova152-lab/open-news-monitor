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
from open_news_monitor.pipeline import InitializationReport, initialize_monitor, run_monitor
from open_news_monitor.rss import fetch_feed
from open_news_monitor.source_check import SourceCheck, check_rss_source
from open_news_monitor.source_probe import InspectionStatus, SourceInspection, SourceProbe
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
    @staticmethod
    def _ready_probe(url: str, name: str = "Example", discovered_feed_url: str | None = None) -> SourceInspection:
        return SourceInspection(
            name=name,
            source_url=url,
            source_type="rss_or_atom",
            status=InspectionStatus.READY,
            message="Источник проверен.",
            discovered_feed_url=discovered_feed_url,
            title_confirmed=True,
            date_confirmed=True,
            link_confirmed=True,
            publication_count=3,
            source={"id": "example", "name": name, "kind": "rss", "url": discovered_feed_url or url, "enabled": True},
        )

    def test_ready_source_is_added_only_after_yes(self) -> None:
        answers = iter(["Monitor", "https://example.org/feed", "Example", "да", "", "", "", "", "нет"])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            create_monitor_interactively(
                target,
                input_func=lambda _prompt: next(answers),
                output_func=messages.append,
                probe_func=lambda url, name: self._ready_probe(url, name),
            )
            config = target.read_text(encoding="utf-8")

        self.assertIn('"https://example.org/feed"', config)
        self.assertIn("Подтверждено публикаций: 3.", messages)
        self.assertIn("Источник можно подключить.", messages)

    def test_ready_source_is_not_added_after_no(self) -> None:
        answers = iter(["Monitor", "https://example.org/feed", "Example", "нет", ""])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            with self.assertRaisesRegex(ValueError, "Не добавлено"):
                create_monitor_interactively(
                    target,
                    input_func=lambda _prompt: next(answers),
                    output_func=lambda _message: None,
                    probe_func=lambda url, name: self._ready_probe(url, name),
                )
            self.assertFalse(target.exists())

    def test_needs_setup_source_is_not_added(self) -> None:
        probe = SourceInspection("Example", "https://example.org/news", "html", InspectionStatus.NEEDS_SETUP, "Страница требует дополнительной настройки.")
        answers = iter(["Monitor", "https://example.org/news", "Example", ""])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Не добавлено"):
                create_monitor_interactively(Path(directory) / "monitor.json", input_func=lambda _prompt: next(answers), output_func=messages.append, probe_func=lambda _url, _name: probe)
        self.assertIn("Сайт доступен, но автоматическое подключение пока невозможно.", messages)

    def test_unsupported_source_is_not_added(self) -> None:
        probe = SourceInspection("Example", "https://example.org/file", None, InspectionStatus.UNSUPPORTED, "Формат не поддерживается.")
        answers = iter(["Monitor", "https://example.org/file", "Example", ""])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Не добавлено"):
                create_monitor_interactively(Path(directory) / "monitor.json", input_func=lambda _prompt: next(answers), output_func=messages.append, probe_func=lambda _url, _name: probe)
        self.assertIn("Этот формат источника пока не поддерживается агентом.", messages)

    def test_error_source_is_not_added(self) -> None:
        probe = SourceInspection("Example", "https://example.org/news", None, InspectionStatus.ERROR, "Сервер временно недоступен.")
        answers = iter(["Monitor", "https://example.org/news", "Example", ""])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Не добавлено"):
                create_monitor_interactively(Path(directory) / "monitor.json", input_func=lambda _prompt: next(answers), output_func=messages.append, probe_func=lambda _url, _name: probe)
        self.assertIn("Проверка не завершилась из-за технической проблемы. Источник не считается неподдерживаемым.", messages)

    def test_discovered_feed_is_shown_to_user(self) -> None:
        feed_url = "https://example.org/news.xml"
        answers = iter(["Monitor", "https://example.org/news", "Example", "да", "", "", "", "", "нет"])
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            create_monitor_interactively(
                Path(directory) / "monitor.json",
                input_func=lambda _prompt: next(answers),
                output_func=messages.append,
                probe_func=lambda url, name: self._ready_probe(url, name, feed_url),
            )
        self.assertIn(f"Найдена новостная лента: {feed_url}", messages)
        self.assertIn("Новости будут собираться через найденную ленту.", messages)

    def test_multiple_confirmed_sources_are_saved(self) -> None:
        answers = iter([
            "Monitor", "https://one.example/feed", "One", "да",
            "https://two.example/feed", "Two", "да", "", "", "", "", "нет",
        ])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            create_monitor_interactively(
                target,
                input_func=lambda _prompt: next(answers),
                output_func=lambda _message: None,
                probe_func=lambda url, name: self._ready_probe(url, name),
            )
            config = target.read_text(encoding="utf-8")
        self.assertIn('"https://one.example/feed"', config)
        self.assertIn('"https://two.example/feed"', config)

    def test_initializer_accepts_a_confirmed_json_source(self) -> None:
        answers = iter(["JSON monitor", "https://example.org/api/articles", "Example", "да", "", "", "", "", "нет"])
        probe = SourceProbe(
            "Example",
            "https://example.org/api/articles",
            "Можно подключать",
            "Confirmed JSON",
            {
                "id": "example-org",
                "name": "Example",
                "kind": "json",
                "url": "https://example.org/api/articles",
                "enabled": True,
                "selectors": {"items": "articles", "title": "title", "link": "url", "date": "published_at"},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "monitor.json"
            create_monitor_interactively(
                target,
                input_func=lambda _prompt: next(answers),
                output_func=lambda _message: None,
                probe_func=lambda _url, _name: probe,
            )
            config = target.read_text(encoding="utf-8")

        self.assertIn('"kind": "json"', config)
        self.assertIn('"items": "articles"', config)

    def test_initializer_writes_only_a_confirmed_source(self) -> None:
        answers = iter(["My AI monitor", "https://example.org/feed.xml", "Example", "да", "", "agent, MCP", "vacancy", "", "нет"])
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
            "Delivery monitor", "https://example.org/feed.xml", "Example", "да", "", "", "", "1,2,3",
            "https://docs.google.com/spreadsheets/d/sheet-123/edit", "Signals", "reader@example.org, editor@example.org", "нет",
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


class InitializationTests(unittest.TestCase):
    @staticmethod
    def _config(directory: Path, sources: list[dict] | None = None) -> dict:
        return {
            "monitor": {"name": "Test monitor", "database_path": str(directory / "memory.sqlite3"), "status_path": str(directory / "status.json")},
            "sources": sources or [{"id": "one", "name": "One", "kind": "rss", "url": "https://example.org/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {"markdown": {"enabled": False}, "telegram": {"enabled": True}, "email": {"enabled": True, "recipients": ["test@example.org"]}, "google_sheets": {"enabled": True, "spreadsheet_id": "sheet"}},
        }

    @staticmethod
    def _article(source_id: str = "one", source_name: str = "One") -> Article:
        return Article(source_id, source_name, "Existing", f"https://example.org/{source_id}/old", "", "2026-10-27")

    def test_wizard_can_confirm_or_decline_initialization(self) -> None:
        report = InitializationReport(sources=[{"name": "Example", "status": "ok", "articles": 1, "saved": 1}])
        for answer, expected_calls in (("да", 1), ("нет", 0)):
            answers = iter(["Monitor", "https://example.org/feed", "Example", "да", "", "", "", "", answer])
            calls: list[dict] = []
            with tempfile.TemporaryDirectory() as directory:
                create_monitor_interactively(
                    Path(directory) / "monitor.json",
                    input_func=lambda _prompt: next(answers),
                    output_func=lambda _message: None,
                    probe_func=lambda url, name: InitializerTests._ready_probe(url, name),
                    initialize_func=lambda config: calls.append(config) or report,
                )
            self.assertEqual(expected_calls, len(calls))

    def test_initialization_saves_old_items_without_delivery_and_repeat_keeps_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._config(Path(directory))
            article = self._article()
            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[article]), \
                 patch("open_news_monitor.pipeline.send_telegram") as telegram, \
                 patch("open_news_monitor.pipeline.send_email") as email, \
                 patch("open_news_monitor.pipeline.append_google_sheet") as sheets:
                first = initialize_monitor(config)
                second = initialize_monitor(config)
                findings, errors, _markdown = run_monitor(config)
            self.assertEqual(1, first.saved_articles)
            self.assertEqual(0, second.saved_articles)
            self.assertEqual([], findings)
            self.assertEqual([], errors)
            telegram.assert_not_called()
            email.assert_not_called()
            sheets.assert_not_called()

    def test_partial_initialization_reports_source_error_and_keeps_other_profile_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [
                {"id": "good", "name": "Good", "kind": "rss", "url": "https://example.org/good", "enabled": True},
                {"id": "bad", "name": "Bad", "kind": "rss", "url": "https://example.org/bad", "enabled": True},
            ]
            config = self._config(root, sources)
            other = self._config(root / "other")
            def fetch(source):
                if source.id == "bad":
                    raise RuntimeError("source temporarily unavailable")
                return [self._article("good", "Good")]
            with patch("open_news_monitor.pipeline.fetch_feed", side_effect=fetch):
                report = initialize_monitor(config)
            self.assertEqual(2, report.checked_sources)
            self.assertEqual(1, report.saved_articles)
            self.assertFalse(report.complete)
            self.assertTrue(report.errors)
            self.assertFalse(Path(other["monitor"]["database_path"]).exists())


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

    def test_source_check_reports_empty_feed_without_crashing(self) -> None:
        empty = SourceCheck("Empty source", "https://example.org/empty.xml", [])
        with patch("open_news_monitor.source_check.inspect_rss_source", return_value=empty):
            report = check_rss_source("https://example.org/empty.xml", "Empty source")

        self.assertIn("Статус: Нужна доработка", report)
        self.assertIn("нет пригодных публикаций", report)


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
