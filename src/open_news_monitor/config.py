from __future__ import annotations

import json
from pathlib import Path

from .models import Source
from .source_registry import SourceRegistryError, load_sources_registry


class ConfigurationError(ValueError):
    """Raised when a monitor configuration is incomplete or invalid."""


def load_config(path: str | Path) -> dict:
    config_path = Path(path)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigurationError(f"Configuration file was not found: {config_path}") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"Configuration is not valid JSON: {error}") from error

    if not isinstance(config, dict):
        raise ConfigurationError("The configuration root must be a JSON object.")
    if "sources_registry" in config:
        try:
            config["sources"] = load_sources_registry(config["sources_registry"], config_path)
        except SourceRegistryError as error:
            raise ConfigurationError(str(error)) from error
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    monitor = config.get("monitor")
    if not isinstance(monitor, dict) or not monitor.get("name"):
        raise ConfigurationError("monitor.name is required.")

    sources = config.get("sources")
    if not isinstance(sources, list):
        raise ConfigurationError("sources must be a list.")

    identifiers: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ConfigurationError("Every source must be an object.")
        for field in ("id", "name", "kind", "url"):
            if not source.get(field):
                raise ConfigurationError(f"Source is missing required field: {field}")
        if source["id"] in identifiers:
            raise ConfigurationError(f"Source id must be unique: {source['id']}")
        identifiers.add(source["id"])
        if source["kind"] not in {"rss", "atom", "html", "json"}:
            raise ConfigurationError(
                f"Unsupported source kind '{source['kind']}'. Supported now: rss, atom, html, json."
            )
        if not str(source["url"]).startswith(("https://", "http://")):
            raise ConfigurationError(f"Source URL must start with http:// or https://: {source['id']}")
        if source["kind"] in {"html", "json"}:
            selectors = source.get("selectors")
            required = {"card", "title", "link", "date"} if source["kind"] == "html" else {"items", "title", "link", "date"}
            if not isinstance(selectors, dict) or not required.issubset(selectors):
                raise ConfigurationError(
                    f"A {source['kind']} source needs selectors for {', '.join(sorted(required))}."
                )

    filters = config.get("filters", {})
    if not isinstance(filters, dict):
        raise ConfigurationError("filters must be an object.")
    for key in ("include_any", "exclude_any"):
        values = filters.get(key, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ConfigurationError(f"filters.{key} must be a list of text values.")

    outputs = config.get("outputs", {})
    if not isinstance(outputs, dict):
        raise ConfigurationError("outputs must be an object.")

    freshness = config.get("freshness", {})
    if not isinstance(freshness, dict):
        raise ConfigurationError("freshness must be an object.")
    age = freshness.get("max_age_hours")
    if age is not None and (type(age) is not int or not 1 <= age <= 720):
        raise ConfigurationError("freshness.max_age_hours must be an integer from 1 to 720.")

    notice = config.get("empty_news_notice", {})
    if (not isinstance(notice, dict)
            or type(notice.get("enabled", False)) is not bool
            or notice.get("language", "ru") not in ("ru", "en")):
        raise ConfigurationError("empty_news_notice requires enabled (boolean) and language (ru or en).")
    if notice.get("enabled") and age is None:
        raise ConfigurationError("empty_news_notice requires freshness.max_age_hours.")

    presentation = config.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ConfigurationError("presentation must be an object.")
    mode = presentation.get("summary_mode", "none")
    if mode not in {"none", "excerpt"}:
        raise ConfigurationError("presentation.summary_mode must be 'none' or 'excerpt'.")
    max_chars = presentation.get("summary_max_chars", 400)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 4000:
        raise ConfigurationError("presentation.summary_max_chars must be an integer from 1 to 4000.")


def enabled_sources(config: dict) -> list[Source]:
    return [
        Source(
            id=item["id"],
            name=item["name"],
            kind=item["kind"],
            url=item["url"],
            enabled=bool(item.get("enabled", True)),
            selectors=item.get("selectors"),
        )
        for item in config["sources"]
        if item.get("enabled", True)
    ]
