from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_status(path: str | Path, status: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_status(path: str | Path) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        return None
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def render_status(status: dict[str, Any] | None) -> str:
    if not status:
        return "Статус ещё не создан. Сначала выполните обычный запуск монитора."
    lines = [
        f"# Статус: {status.get('monitor_name', 'Open News Monitor')}",
        "",
        f"- Последний запуск: {status.get('ran_at', 'неизвестно')}",
        f"- Режим: {status.get('mode', 'обычный')}",
        f"- Новых подходящих публикаций: {status.get('new_matching_publications', 0)}",
        "",
        "## Источники",
        "",
    ]
    for source in status.get("sources", []):
        if source.get("status") == "ok":
            lines.append(f"- {source.get('name', 'Источник')}: доступен, получено {source.get('articles', 0)} публикаций")
        else:
            lines.append(f"- {source.get('name', 'Источник')}: ошибка — {source.get('error', 'неизвестная ошибка')}")
    errors = status.get("errors", [])
    if errors:
        lines.extend(["", "## Ошибки", "", *[f"- {error}" for error in errors]])
    return "\n".join(lines)
