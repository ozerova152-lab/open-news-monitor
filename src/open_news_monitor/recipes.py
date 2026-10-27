from __future__ import annotations

import json
import re
from copy import deepcopy
from importlib.resources import files


def load_official_ai_recipes() -> list[dict]:
    """Return copies of built-in recipes so callers cannot mutate the catalog."""
    resource = files("open_news_monitor.catalog").joinpath("official_ai.json")
    document = json.loads(resource.read_text(encoding="utf-8"))
    return deepcopy(document["recipes"])


def suggest_recipes(topic: str) -> list[dict]:
    """Match a free-text topic against the small, verified local catalog.

    This is intentionally a transparent local match, not a claim that the
    catalog covers all websites or that sources have been connected already.
    """
    words = _topic_words(topic)
    matches: list[dict] = []
    for recipe in load_official_ai_recipes():
        haystack = set(re.findall(r"\w+", " ".join([recipe["name"], *recipe["categories"]]).lower(), flags=re.UNICODE))
        if words & haystack:
            matches.append(recipe)
    return matches


def _topic_words(topic: str) -> set[str]:
    words = set(re.findall(r"\w+", topic.lower(), flags=re.UNICODE))
    aliases = {
        "нейросети": {"ai"},
        "ии": {"ai"},
        "агенты": {"agents", "agent"},
        "агент": {"agents", "agent"},
        "модели": {"models", "model"},
        "модель": {"models", "model"},
        "видео": {"video"},
        "генеративное": {"generation"},
        "генерация": {"generation"},
        "исследования": {"research"},
        "исследование": {"research"},
        "открытый": {"open-source"},
    }
    for word in tuple(words):
        words.update(aliases.get(word, set()))
    return words


def render_recipe_suggestions(topic: str) -> str:
    recipes = suggest_recipes(topic)
    lines = [f"# Проверенные источники по теме: {topic}", ""]
    if not recipes:
        lines.extend(
            [
                "Готовых совпадений в каталоге пока нет.",
                "Это не означает, что источников не существует: добавьте свой URL через `probe-source`, и агент проверит его отдельно.",
            ]
        )
        return "\n".join(lines)
    lines.extend(["Это предложения, а не автоматическое подключение.", ""])
    for recipe in recipes:
        lines.append(
            f"- **{recipe['name']}** — {recipe['method']}; категории: {', '.join(recipe['categories'])}; проверено: {recipe['verified_at']}."
        )
    lines.extend(["", "Подключайте только выбранные пользователем источники и затем создавайте стартовую границу через `run ... --initialize`."])
    return "\n".join(lines)
