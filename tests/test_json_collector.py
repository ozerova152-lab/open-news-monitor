from __future__ import annotations

import unittest
from unittest.mock import patch

from open_news_monitor.json_collector import fetch_json
from open_news_monitor.models import Source


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class JsonCollectorTests(unittest.TestCase):
    def test_collects_title_date_and_direct_link(self) -> None:
        source = Source(
            id="runway",
            name="Runway",
            kind="json",
            url="https://help.example.test/api/articles.json",
            selectors={"items": "articles", "title": "title", "link": "html_url", "date": "updated_at", "summary": "body"},
        )
        payload = b'{"articles":[{"title":"New model","html_url":"/article","updated_at":"2026-10-27T10:00:00Z","body":"<p>Update</p>"}]}'
        with patch("open_news_monitor.json_collector.urlopen", return_value=_Response(payload)):
            articles = fetch_json(source)
        self.assertEqual(articles[0].title, "New model")
        self.assertEqual(articles[0].url, "https://help.example.test/article")
        self.assertEqual(articles[0].published_at, "2026-10-27T10:00:00Z")
        self.assertEqual(articles[0].summary, "Update")
