from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.config import ConfigurationError, validate_config
from open_news_monitor.models import Article, Finding
from open_news_monitor.outputs import (
    _plain_digest,
    append_google_sheet,
    finding_excerpt,
    render_markdown,
    summary_excerpt,
    telegram_message_chunks,
)
from open_news_monitor.pipeline import run_monitor
from open_news_monitor.store import ArticleStore


class _Values:
    def __init__(self) -> None:
        self.appended: list[list[str]] = []

    def get(self, **_kwargs):
        return self

    def append(self, *, body, **_kwargs):
        self.appended.extend(body["values"])
        return self

    def execute(self):
        return {"values": []}


class _SheetsService:
    def __init__(self, values: _Values) -> None:
        self._values = values

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


class PresentationTests(unittest.TestCase):
    @staticmethod
    def _finding(number: int = 1, summary: str = "Первое предложение. Второе предложение.") -> Finding:
        return Finding(
            Article(
                source_id=f"source-{number}", source_name=f"Источник {number}",
                title=f"Заголовок {number}", url=f"https://example.test/{number}",
                summary=summary, published_at="2026-11-12",
            ),
            "Matched", datetime.now(timezone.utc),
        )

    @staticmethod
    def _config(root: Path, *, presentation=None) -> dict:
        config = {
            "monitor": {"name": "Presentation test", "database_path": str(root / "memory.sqlite3"), "status_path": str(root / "status.json")},
            "sources": [{"id": "source", "name": "Source", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {"markdown": {"enabled": False}, "telegram": {"enabled": True}, "email": {"enabled": False, "recipients": []}, "google_sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": "News"}},
        }
        if presentation is not None:
            config["presentation"] = presentation
        return config

    def test_none_and_old_profile_do_not_render_excerpt(self) -> None:
        finding = self._finding(summary="Описание для проверки.")
        self.assertEqual("", finding_excerpt(finding))
        self.assertEqual("", finding_excerpt(finding, {"summary_mode": "none"}))
        self.assertNotIn("Описание для проверки", _plain_digest("Monitor", [finding], presentation=None))

    def test_excerpt_normalizes_cyrillic_and_special_characters(self) -> None:
        finding = self._finding(summary="  Привет,   мир!\nНовая строка & <тег>.  ")
        self.assertEqual("Привет, мир! Новая строка & <тег>.", finding_excerpt(finding, {"summary_mode": "excerpt", "summary_max_chars": 100}))

    def test_empty_and_long_summary_are_handled(self) -> None:
        self.assertEqual("", summary_excerpt("   \n", 20))
        self.assertEqual("Первое предложение.", summary_excerpt("Первое предложение. Второе предложение очень длинное.", 25))
        self.assertEqual("abcdefgh…", summary_excerpt("abcdefghijklmnopqrstuvwxyz", 9))

    def test_telegram_chunks_include_excerpt_and_preserve_links(self) -> None:
        findings = [self._finding(number, "Описание новости. " * 80) for number in range(1, 7)]
        chunks = telegram_message_chunks(
            "Monitor", findings, max_chars=360,
            presentation={"summary_mode": "excerpt", "summary_max_chars": 120},
        )
        text = "\n".join(message for message, _chunk in chunks)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(message) <= 360 for message, _chunk in chunks))
        for finding in findings:
            self.assertIn(finding.article.title, text)
            self.assertIn(finding.article.source_name, text)
            self.assertIn(finding.article.url, text)
        self.assertIn("Описание новости.", text)

    def test_email_excerpt_and_none_modes(self) -> None:
        finding = self._finding(summary="Краткое описание новости.")
        with_excerpt = _plain_digest("Monitor", [finding], limit=None, presentation={"summary_mode": "excerpt", "summary_max_chars": 100})
        without_excerpt = _plain_digest("Monitor", [finding], limit=None, presentation={"summary_mode": "none", "summary_max_chars": 100})
        self.assertIn("Краткое описание новости.", with_excerpt)
        self.assertNotIn("Краткое описание новости.", without_excerpt)
        self.assertIn(finding.article.url, with_excerpt)

    def test_markdown_and_sheets_keep_full_summary(self) -> None:
        full_summary = "Полное описание. " * 100
        finding = self._finding(summary=full_summary)
        self.assertIn(full_summary, render_markdown("Monitor", [finding], []))
        values = _Values()
        with patch("open_news_monitor.outputs._google_service", return_value=_SheetsService(values)):
            append_google_sheet("sheet", "News", [finding])
        self.assertEqual(full_summary, values.appended[0][6])

    def test_pipeline_old_sender_signature_and_no_repeat_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            article = self._finding(summary="Описание").article
            calls: list[str] = []

            def legacy_sender(_name, findings, *, on_confirmed):
                calls.append(findings[0].article.url)
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[article]), patch("open_news_monitor.pipeline.send_telegram", side_effect=legacy_sender):
                run_monitor(config)
                run_monitor(config)
            self.assertEqual([article.url], calls)
            store = ArticleStore(root / "memory.sqlite3")
            try:
                self.assertEqual({"SENT": 1}, store.telegram_queue_states())
            finally:
                store.close()

    def test_pipeline_passes_presentation_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            presentation = {"summary_mode": "excerpt", "summary_max_chars": 42}
            config = self._config(root, presentation=presentation)
            article = self._finding(summary="Описание").article
            received: list[dict] = []

            def sender(_name, findings, *, on_confirmed, presentation):
                received.append(presentation)
                on_confirmed(findings)

            with patch("open_news_monitor.pipeline.fetch_feed", return_value=[article]), patch("open_news_monitor.pipeline.send_telegram", side_effect=sender):
                run_monitor(config)
            self.assertEqual([presentation], received)

    def test_presentation_validation(self) -> None:
        base = {"monitor": {"name": "Test"}, "sources": [], "filters": {"include_any": [], "exclude_any": []}, "outputs": {}}
        validate_config({**base, "presentation": {"summary_mode": "none", "summary_max_chars": 1}})
        validate_config({**base, "presentation": {"summary_mode": "excerpt", "summary_max_chars": 400}})
        for presentation in (
            {"summary_mode": "translate", "summary_max_chars": 10},
            {"summary_mode": "excerpt", "summary_max_chars": 0},
            {"summary_mode": "excerpt", "summary_max_chars": True},
        ):
            with self.assertRaises(ConfigurationError):
                validate_config({**base, "presentation": presentation})
