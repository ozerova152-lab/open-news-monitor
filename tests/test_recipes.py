from __future__ import annotations

import unittest

from open_news_monitor.recipes import render_recipe_suggestions, suggest_recipes


class RecipeTests(unittest.TestCase):
    def test_suggests_verified_sources_for_agents(self) -> None:
        names = {recipe["name"] for recipe in suggest_recipes("ИИ-агенты")}
        self.assertIn("OpenAI News", names)
        self.assertIn("Anthropic News", names)

    def test_suggests_runway_for_video(self) -> None:
        names = {recipe["name"] for recipe in suggest_recipes("генеративное видео")}
        self.assertIn("Runway Updates", names)

    def test_no_match_does_not_claim_sources_exist(self) -> None:
        report = render_recipe_suggestions("гранты для НКО")
        self.assertIn("пока нет", report)
