from __future__ import annotations

import json
from pathlib import Path

from .models import Source


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
        if source["kind"] not in {"rss", "atom", "html"}:
            raise ConfigurationError(
                f"Unsupported source kind '{source['kind']}'. Supported now: rss, atom, html."
            )
        if not str(source["url"]).startswith(("https://", "http://")):
            raise ConfigurationError(f"Source URL must start with http:// or https://: {source['id']}")
        if source["kind"] == "html":
            selectors = source.get("selectors")
            required = {"card", "title", "link", "date"}
            if not isinstance(selectors, dict) or not required.issubset(selectors):
                raise ConfigurationError(
                    "An html source needs selectors for card, title, link, and date."
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
