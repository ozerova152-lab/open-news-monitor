from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.cli import main
from open_news_monitor.config import ConfigurationError, load_config
from open_news_monitor.source_probe import InspectionStatus, SourceInspection
from open_news_monitor.source_registry import (
    SourceRegistryError,
    check_configured_html_source,
    load_sources_registry,
)
from open_news_monitor.models import Article


HEADER = "id;enabled;group;name;url;kind;item_or_card;title;link;date;summary;notes\n"


class SourceRegistryTests(unittest.TestCase):
    def _write_csv(self, root: Path, body: str) -> Path:
        path = root / "источники.csv"
        path.write_text(HEADER + body, encoding="utf-8-sig", newline="")
        return path

    @staticmethod
    def _profile(root: Path, registry_path: str = "источники.csv") -> Path:
        profile = root / "profile.json"
        profile.write_text(json.dumps({
            "monitor": {"name": "CSV registry test"},
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {},
            "sources_registry": {"path": registry_path, "encoding": "utf-8-sig", "delimiter": ";"},
        }, ensure_ascii=False), encoding="utf-8")
        return profile

    def test_utf8_bom_cyrillic_and_semicolon_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(root, "новости;TRUE;Работа;Новости ИИ;https://example.test/feed;rss;;;;;;Кириллица\n")
            profile = self._profile(root)
            config = load_config(profile)
            self.assertEqual("Новости ИИ", config["sources"][0]["name"])
            self.assertEqual("Работа", config["sources"][0]["group"])
            self.assertTrue(config["sources"][0]["enabled"])

    def test_all_supported_source_kinds_are_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(root, "\n".join([
                "rss;TRUE;Feeds;RSS;https://example.test/rss;rss;;;;;;",
                "atom;TRUE;Feeds;Atom;https://example.test/atom;atom;;;;;;",
                "html;TRUE;Sites;HTML;https://example.test/news;html;article;h2;a;time;.summary;",
                "json;TRUE;APIs;JSON;https://example.test/api;json;items;title;url;published_at;description;",
            ]) + "\n")
            config = load_config(self._profile(root))
            sources = {source["id"]: source for source in config["sources"]}
            self.assertEqual({"rss", "atom", "html", "json"}, {source["kind"] for source in sources.values()})
            self.assertEqual("article", sources["html"]["selectors"]["card"])
            self.assertEqual("items", sources["json"]["selectors"]["items"])

    def test_disabled_row_is_preserved_but_not_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(root, "paused;FALSE;Archive;Paused;https://example.test/feed;rss;;;;;;\n")
            config = load_config(self._profile(root))
            self.assertFalse(config["sources"][0]["enabled"])

    def test_row_errors_are_russian_and_identify_csv_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_csv(root, "bad;MAYBE;Группа;Name;ftp://example.test;html;;;;;;\n")
            with self.assertRaisesRegex(SourceRegistryError, r"Строка CSV 2: enabled"):
                load_sources_registry({"path": str(path), "encoding": "utf-8-sig", "delimiter": ";"}, root / "profile.json")

    def test_duplicate_id_is_reported_with_both_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_csv(root, "\n".join([
                "same;TRUE;One;One;https://example.test/one;rss;;;;;;",
                "same;TRUE;Two;Two;https://example.test/two;atom;;;;;;",
            ]) + "\n")
            with self.assertRaisesRegex(SourceRegistryError, r"Строка CSV 3: id 'same' уже указан в строке 2"):
                load_sources_registry({"path": str(path), "encoding": "utf-8-sig", "delimiter": ";"}, root / "profile.json")

    def test_old_json_profile_without_registry_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "old.json"
            original = {
                "monitor": {"name": "Old JSON"},
                "sources": [{"id": "old", "name": "Old", "kind": "rss", "url": "https://example.test/feed", "enabled": True}],
                "filters": {"include_any": [], "exclude_any": []},
                "outputs": {},
            }
            profile.write_text(json.dumps(original), encoding="utf-8")
            config = load_config(profile)
            self.assertEqual(original["sources"], config["sources"])
            self.assertNotIn("sources_registry", config)

    def test_validate_and_check_do_not_modify_profile_or_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = self._write_csv(root, "active;TRUE;AI;Active;https://example.test/feed;rss;;;;;;\ndisabled;FALSE;AI;Disabled;https://example.test/off;rss;;;;;;\n")
            profile = self._profile(root)
            profile_before = profile.read_bytes()
            csv_before = csv_path.read_bytes()
            inspection = SourceInspection(
                "Active", "https://example.test/feed", "rss", InspectionStatus.READY,
                "Источник проверен.", title_confirmed=True, date_confirmed=True,
                link_confirmed=True, publication_count=1,
                source={"id": "active", "name": "Active", "kind": "rss", "url": "https://example.test/feed", "enabled": True},
            )
            for action in ("validate", "check"):
                output = io.StringIO()
                argv = ["open-news-monitor", "sources", action, str(profile)]
                with patch.object(sys, "argv", argv), redirect_stdout(output), patch("open_news_monitor.cli.probe_source_url", return_value=inspection) as probe:
                    main()
                if action == "validate":
                    probe.assert_not_called()
                else:
                    probe.assert_called_once_with("https://example.test/feed", "Active")
            self.assertEqual(profile_before, profile.read_bytes())
            self.assertEqual(csv_before, csv_path.read_bytes())

    def test_invalid_registry_is_reported_when_loading_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(root, "json;TRUE;API;JSON;https://example.test/api;json;;;;;;\n")
            with self.assertRaisesRegex(ConfigurationError, r"Строка CSV 2: для json нужны правила"):
                load_config(self._profile(root))

    def test_configured_html_recipe_is_ready_only_after_collector_confirms_articles(self) -> None:
        source = {
            "id": "html", "name": "HTML", "kind": "html", "url": "https://example.test/news",
            "enabled": True, "selectors": {"card": "article", "title": "h2", "link": "a", "date": "time"},
        }
        article = Article("html", "HTML", "Title", "https://example.test/article", "", "2026-11-11")
        with patch("open_news_monitor.source_registry.fetch_html", return_value=[article]) as fetch:
            result = check_configured_html_source(source)
        fetch.assert_called_once()
        self.assertEqual(InspectionStatus.READY, result.status)
        self.assertEqual(1, result.publication_count)

    def test_configured_html_recipe_reports_needs_setup_or_error(self) -> None:
        source = {
            "id": "html", "name": "HTML", "kind": "html", "url": "https://example.test/news",
            "enabled": True, "selectors": {"card": "article", "title": "h2", "link": "a", "date": "time"},
        }
        from open_news_monitor.rss import SourceFetchError
        with patch("open_news_monitor.source_registry.fetch_html", side_effect=SourceFetchError("No publication cards matched selector 'article' in HTML.")):
            recipe_error = check_configured_html_source(source)
        with patch("open_news_monitor.source_registry.fetch_html", side_effect=SourceFetchError("Could not download HTML: timeout")):
            network_error = check_configured_html_source(source)
        self.assertEqual(InspectionStatus.NEEDS_SETUP, recipe_error.status)
        self.assertEqual(InspectionStatus.ERROR, network_error.status)

    def test_sources_check_uses_html_recipe_instead_of_generic_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_csv(root, "html;TRUE;Sites;HTML;https://example.test/news;html;article;h2;a;time;.summary;\n")
            profile = self._profile(root)
            inspection = SourceInspection(
                "HTML", "https://example.test/news", "html", InspectionStatus.READY,
                "HTML-источник проверен по заданным правилам извлечения.",
                title_confirmed=True, date_confirmed=True, link_confirmed=True, publication_count=1,
            )
            with patch.object(sys, "argv", ["open-news-monitor", "sources", "check", str(profile)]), \
                 redirect_stdout(io.StringIO()), \
                 patch("open_news_monitor.cli.check_configured_html_source", return_value=inspection) as html_check, \
                 patch("open_news_monitor.cli.probe_source_url") as generic_probe:
                main()
            html_check.assert_called_once()
            generic_probe.assert_not_called()
