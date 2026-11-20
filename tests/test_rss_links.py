from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.models import Source
from open_news_monitor.rss import fetch_feed
from open_news_monitor.store import ArticleStore


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _source(url: str = "https://example.test/feed/news.xml") -> Source:
    return Source("feed", "Feed", "rss", url)


def _feed(link: str, *, root_base: str = "", item_base: str = "") -> bytes:
    root_attribute = f' xml:base="{root_base}"' if root_base else ""
    item_attribute = f' xml:base="{item_base}"' if item_base else ""
    return (
        f'<?xml version="1.0"?><rss version="2.0"{root_attribute}><channel><item{item_attribute}>'
        f'<title>Title</title><link>{link}</link><pubDate>Tue, 11 Nov 2026 09:00:00 +0000</pubDate>'
        f'</item></channel></rss>'
    ).encode("utf-8")


class RssLinkTests(unittest.TestCase):
    def _fetch(self, payload: bytes, url: str = "https://example.test/feed/news.xml"):
        with patch("open_news_monitor.rss.urlopen", return_value=_Response(payload)):
            return fetch_feed(_source(url))

    def test_relative_path_is_resolved_against_document_url(self) -> None:
        articles = self._fetch(_feed("article-1"))
        self.assertEqual("https://example.test/feed/article-1", articles[0].url)

    def test_root_relative_path_is_resolved_against_origin(self) -> None:
        articles = self._fetch(_feed("/article-1"))
        self.assertEqual("https://example.test/article-1", articles[0].url)

    def test_absolute_url_is_not_changed(self) -> None:
        articles = self._fetch(_feed("https://other.example/article-1"))
        self.assertEqual("https://other.example/article-1", articles[0].url)

    def test_xml_base_is_applied_to_relative_link(self) -> None:
        articles = self._fetch(_feed("article-1", root_base="https://cdn.example/base/", item_base="news/"))
        self.assertEqual("https://cdn.example/base/news/article-1", articles[0].url)

    def test_atom_relative_href_is_resolved(self) -> None:
        payload = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://atom.example/base/">
          <entry><title>Atom</title><link href="posts/one"/><updated>2026-11-11T09:00:00Z</updated></entry>
        </feed>'''
        articles = self._fetch(payload, "https://example.test/feed.xml")
        self.assertEqual("https://atom.example/base/posts/one", articles[0].url)

    def test_malformed_or_non_http_link_is_skipped(self) -> None:
        self.assertEqual([], self._fetch(_feed("javascript:alert(1)")))
        self.assertEqual([], self._fetch(_feed("http://")))

    def test_resolved_link_keeps_deduplication_stable(self) -> None:
        articles = self._fetch(_feed("article-1"))
        with tempfile.TemporaryDirectory() as directory:
            store = ArticleStore(Path(directory) / "memory.sqlite3")
            try:
                self.assertFalse(store.has_seen("https://example.test/feed/article-1"))
                store.add(articles[0], "2026-11-11T09:00:00+00:00", "Matched")
                self.assertTrue(store.has_seen("https://example.test/feed/article-1"))
            finally:
                store.close()
