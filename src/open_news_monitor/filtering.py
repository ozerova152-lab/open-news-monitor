from __future__ import annotations

from .models import Article


def match_article(article: Article, include_any: list[str], exclude_any: list[str]) -> str | None:
    """Return a human-readable reason for a match, or None when it should be excluded."""
    corpus = f"{article.title}\n{article.summary}".casefold()
    excluded = [phrase for phrase in exclude_any if phrase.casefold() in corpus]
    if excluded:
        return None
    if not include_any:
        return "New publication from an enabled source; no include rules are set."
    matched = [phrase for phrase in include_any if phrase.casefold() in corpus]
    if not matched:
        return None
    return "Matched include rule: " + ", ".join(matched)
