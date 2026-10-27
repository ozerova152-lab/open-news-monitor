from __future__ import annotations

import unittest
from unittest.mock import patch

from open_news_monitor.models import Article
from open_news_monitor.source_check import SourceCheck
from open_news_monitor.source_probe import probe_source_url


class _Response:
    def __init__(self, payload: bytes, content_type: str) -> None:
        self.payload = payload
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class SourceProbeTests(unittest.TestCase):
    def test_confirms_direct_rss_without_downloading_page(self) -> None:
        article = Article("id", "Example", "Title", "https://example.org/post", "", "2026-10-27")
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=SourceCheck("Example", "https://example.org/feed", [article])):
            result = probe_source_url("https://example.org/feed", "Example")
        self.assertEqual(result.status, "Можно подключать")
        self.assertEqual(result.source["kind"], "rss")

    def test_confirms_public_json_with_known_fields(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/updates", [], "Not a feed")
        payload = b'{"articles":[{"title":"Launch","html_url":"https://example.org/launch","updated_at":"2026-10-27T10:00:00Z","body":"Details"}]}'
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(payload, "application/json")
        ):
            result = probe_source_url("https://example.org/updates", "Example")
        self.assertEqual(result.status, "Можно подключать")
        self.assertEqual(result.source["kind"], "json")
        self.assertEqual(result.source["selectors"]["items"], "articles")

    def test_reports_html_that_needs_a_recipe(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/news", [], "Not a feed")
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(b"<html><body>News</body></html>", "text/html")
        ):
            result = probe_source_url("https://example.org/news", "Example")
        self.assertEqual(result.status, "Нужна доработка")
        self.assertIsNone(result.source)
