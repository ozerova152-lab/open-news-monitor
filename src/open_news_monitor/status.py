from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_status(path: str | Path, status: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    append_run_event(destination, status)
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
    for channel, label in (("telegram", "Telegram"), ("email", "Email"), ("google_sheets", "Google Sheets")):
        delivery = status.get("deliveries", {}).get(channel)
        if not delivery:
            continue
        lines.extend([
            "",
            f"## {label}",
            "",
            f"- Состояние: {delivery.get('status', 'неизвестно')}",
            f"- В очереди: {delivery.get('queued', 0)}; подтверждённо отправлено: {delivery.get('sent', 0)}; ожидают повтора: {delivery.get('failed', 0)}",
        ])
    errors = status.get("errors", [])
    if errors:
        lines.extend(["", "## Ошибки", "", *[f"- {error}" for error in errors]])
    return "\n".join(lines)


def append_run_event(status_path, event):
    import os

    path = Path(status_path).with_suffix(".history.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("Incomplete history record")
    finally:
        os.close(fd)


def record_attempt(config, mode, action, *, result_kind=None):
    from datetime import datetime, timezone
    from uuid import uuid4
    from .locking import MonitorAlreadyRunningError

    monitor = config["monitor"]
    db = monitor.get("database_path", "runtime/open-news-monitor.sqlite3")
    path = monitor.get("status_path") or Path(db).with_suffix(".status.json")
    event = {"id": uuid4().hex, "monitor_name": monitor["name"], "mode": mode,
             "started_at": datetime.now(timezone.utc).isoformat()}
    append_run_event(path, {**event, "event": "started"})
    try:
        result = action()
    except BaseException as error:
        append_run_event(path, {**event, "finished_at": datetime.now(timezone.utc).isoformat(),
                               "event": "parallel" if isinstance(error, MonitorAlreadyRunningError) else "aborted",
                               "error_type": type(error).__name__})
        raise
    append_run_event(path, {**event, "finished_at": datetime.now(timezone.utc).isoformat(),
                           "event": result_kind(result) if result_kind else "finished"})
    return result
