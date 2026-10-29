from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from open_news_monitor.models import Article
from open_news_monitor.source_check import SourceCheck
from open_news_monitor.source_probe import InspectionStatus, probe_source_url


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


def _article() -> Article:
    return Article("id", "Example", "Title", "https://example.org/post", "", "2026-10-27")


class SourceProbeTests(unittest.TestCase):
    def test_correct_rss_is_ready_with_confirmed_fields(self) -> None:
        approved = SourceCheck("Example", "https://example.org/feed", [_article()])
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=approved):
            result = probe_source_url("https://example.org/feed", "Example")
        self.assertEqual(result.status, InspectionStatus.READY)
        self.assertTrue(result.can_connect)
        self.assertTrue(result.title_confirmed)
        self.assertTrue(result.date_confirmed)
        self.assertTrue(result.link_confirmed)
        self.assertEqual(result.discovered_feed_url, "https://example.org/feed")

    def test_correct_json_is_ready_only_after_collector_confirms_articles(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/updates", [], "Not a feed")
        payload = b'{"articles":[{"title":"Launch","html_url":"https://example.org/launch","updated_at":"2026-10-27T10:00:00Z"}]}'
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(payload, "application/json")
        ), patch("open_news_monitor.source_probe.fetch_json", return_value=[_article()]):
            result = probe_source_url("https://example.org/updates", "Example")
        self.assertEqual(result.status, InspectionStatus.READY)
        self.assertEqual(result.source_type, "json")
        self.assertTrue(result.can_connect)

    def test_html_page_with_advertised_feed_is_ready(self) -> None:
        direct = SourceCheck("Example", "https://example.org/news", [], "Not a feed")
        advertised = SourceCheck("Example", "https://example.org/feed.xml", [_article()])
        html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
        with patch("open_news_monitor.source_probe.inspect_rss_source", side_effect=[direct, advertised]), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(html, "text/html")
        ):
            result = probe_source_url("https://example.org/news", "Example")
        self.assertEqual(result.status, InspectionStatus.READY)
        self.assertEqual(result.discovered_feed_url, "https://example.org/feed.xml")

    def test_html_page_without_recipe_needs_setup(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/news", [], "Not a feed")
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(b"<html><body>News</body></html>", "text/html")
        ):
            result = probe_source_url("https://example.org/news", "Example")
        self.assertEqual(result.status, InspectionStatus.NEEDS_SETUP)
        self.assertEqual(result.source_type, "html")
        self.assertFalse(result.can_connect)

    def test_empty_rss_is_not_ready_and_does_not_crash(self) -> None:
        empty = SourceCheck("Example", "https://example.org/feed", [], "No RSS or Atom entries were found in Example.")
        xml = b"<?xml version='1.0'?><rss><channel><title>Empty</title></channel></rss>"
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=empty), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(xml, "application/rss+xml")
        ):
            result = probe_source_url("https://example.org/feed", "Example")
        self.assertEqual(result.status, InspectionStatus.NEEDS_SETUP)
        self.assertEqual(result.source_type, "rss")

    def test_unavailable_source_is_error_not_unsupported(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/news", [], "Not a feed")
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", side_effect=URLError("temporary outage")
        ):
            result = probe_source_url("https://example.org/news", "Example")
        self.assertEqual(result.status, InspectionStatus.ERROR)
        self.assertIsNotNone(result.technical_reason)

    def test_invalid_json_response_is_error(self) -> None:
        rejected = SourceCheck("Example", "https://example.org/api", [], "Not a feed")
        with patch("open_news_monitor.source_probe.inspect_rss_source", return_value=rejected), patch(
            "open_news_monitor.source_probe.urlopen", return_value=_Response(b"{not-json", "application/json")
        ):
            result = probe_source_url("https://example.org/api", "Example")
        self.assertEqual(result.status, InspectionStatus.ERROR)
        self.assertEqual(result.source_type, "json")
