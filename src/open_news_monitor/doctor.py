from __future__ import annotations

import importlib.util
import json
import os
import re
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Mapping


READY = "READY"
NEEDS_SETUP = "NEEDS_SETUP"
ERROR = "ERROR"
DISABLED = "DISABLED"


@dataclass(frozen=True)
class DoctorCheck:
    channel: str
    status: str
    message: str
    next_step: str | None = None


def inspect_profile(config: dict, *, environment: Mapping[str, str] | None = None) -> list[DoctorCheck]:
    """Perform local-only readiness checks for configured delivery channels.

    This function intentionally does not create folders, contact services, or
    instantiate delivery clients. It only checks configuration and local files.
    """
    env = os.environ if environment is None else environment
    outputs = config.get("outputs", {})
    return [
        _check_markdown(outputs.get("markdown", {"enabled": True})),
        _check_telegram(outputs.get("telegram", {}), env),
        _check_email(outputs.get("email", {}), env),
        _check_google_sheets(outputs.get("google_sheets", {}), env),
    ]


def render_doctor_report(config: dict, *, environment: Mapping[str, str] | None = None) -> str:
    """Render a Russian, secret-free local readiness report."""
    lines = [f"# Проверка готовности: {config['monitor']['name']}", ""]
    for check in inspect_profile(config, environment=environment):
        label = {
            "markdown": "Локальный Markdown",
            "telegram": "Telegram",
            "email": "Email",
            "google_sheets": "Google Sheets",
        }[check.channel]
        status = {
            READY: "ГОТОВО",
            NEEDS_SETUP: "ТРЕБУЕТ НАСТРОЙКИ",
            ERROR: "ОШИБКА",
            DISABLED: "ОТКЛЮЧЕНО",
        }[check.status]
        lines.extend([f"## {label}: {status}", "", check.message])
        if check.next_step:
            lines.extend(["", f"Следующий шаг: {check.next_step}"])
        lines.append("")
    lines.extend(
        [
            "Проверка выполнена только локально: сообщения не отправлялись, доступ к Telegram, почте и Google Sheets не проверялся.",
            "Для первой реальной доставки используйте отдельный тестовый чат, адрес или таблицу.",
        ]
    )
    return "\n".join(lines)


def _check_markdown(settings: dict) -> DoctorCheck:
    if not settings.get("enabled", True):
        return DoctorCheck("markdown", DISABLED, "Локальный Markdown не выбран в этом профиле.")
    path = Path(settings.get("path", "runtime/latest_digest.md"))
    parent = path.parent
    if not parent.exists():
        return DoctorCheck(
            "markdown", NEEDS_SETUP, "Папка для локальной подборки пока не существует.",
            f"Создайте папку {parent} или укажите существующую папку в профиле.",
        )
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        return DoctorCheck(
            "markdown", ERROR, "Папка для локальной подборки недоступна для записи.",
            "Выберите папку, в которую у вашей учётной записи есть право записи.",
        )
    return DoctorCheck("markdown", READY, "Папка для локальной подборки доступна.")


def _check_telegram(settings: dict, env: Mapping[str, str]) -> DoctorCheck:
    if not settings.get("enabled"):
        return DoctorCheck("telegram", DISABLED, "Telegram не выбран в этом профиле.")
    token = env.get("ONM_TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("ONM_TELEGRAM_CHAT_ID", "")
    missing = []
    if not token:
        missing.append("токен бота")
    if not chat_id:
        missing.append("ID чата")
    if missing:
        return DoctorCheck(
            "telegram", NEEDS_SETUP, "Не указаны: " + ", ".join(missing) + ".",
            "Добавьте нужные значения в локальный файл .env. Не отправляйте их в чат и не добавляйте в Git.",
        )
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?\d+", chat_id):
        return DoctorCheck(
            "telegram", ERROR, "Токен бота или ID чата имеют некорректный базовый формат.",
            "Проверьте значения в .env. Не публикуйте токен и ID чата.",
        )
    return DoctorCheck("telegram", READY, "Токен бота и ID чата присутствуют и имеют базовый формат.")


def _check_email(settings: dict, env: Mapping[str, str]) -> DoctorCheck:
    if not settings.get("enabled"):
        return DoctorCheck("email", DISABLED, "Email не выбран в этом профиле.")
    required = {
        "SMTP-хост": env.get("ONM_SMTP_HOST", ""),
        "SMTP-порт": env.get("ONM_SMTP_PORT", ""),
        "SMTP-логин": env.get("ONM_SMTP_USERNAME", ""),
        "пароль приложения": env.get("ONM_SMTP_PASSWORD", ""),
        "адрес отправителя": env.get("ONM_SMTP_FROM", ""),
    }
    missing = [label for label, value in required.items() if not value]
    recipients = settings.get("recipients", [])
    if not recipients:
        missing.append("адрес получателя")
    if missing:
        return DoctorCheck(
            "email", NEEDS_SETUP, "Не указаны: " + ", ".join(missing) + ".",
            "Заполните SMTP-настройки в .env и добавьте адрес получателя в профиль.",
        )
    try:
        port = int(required["SMTP-порт"])
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        return DoctorCheck("email", ERROR, "SMTP-порт должен быть числом от 1 до 65535.", "Проверьте ONM_SMTP_PORT в .env.")
    addresses = [required["адрес отправителя"], *recipients]
    if not all(_looks_like_email(address) for address in addresses):
        return DoctorCheck("email", ERROR, "Адрес отправителя или получателя имеет некорректный базовый формат.", "Проверьте email-адреса в .env и профиле.")
    return DoctorCheck("email", READY, "SMTP-настройки и адрес получателя присутствуют и имеют базовый формат.")


def _check_google_sheets(settings: dict, env: Mapping[str, str]) -> DoctorCheck:
    if not settings.get("enabled"):
        return DoctorCheck("google_sheets", DISABLED, "Google Sheets не выбран в этом профиле.")
    missing = []
    if not _module_available("google.oauth2.service_account") or not _module_available("googleapiclient.discovery"):
        missing.append("библиотеки Google Sheets")
    key_path = env.get("ONM_GOOGLE_SERVICE_ACCOUNT_FILE", "")
    if not key_path:
        missing.append("файл доступа к Google Sheets")
    if not settings.get("spreadsheet_id"):
        missing.append("ID таблицы")
    if not settings.get("worksheet"):
        missing.append("название листа")
    if missing:
        return DoctorCheck(
            "google_sheets", NEEDS_SETUP, "Не указаны или не установлены: " + ", ".join(missing) + ".",
            "Установите библиотеки Google Sheets, создайте сервисный аккаунт и укажите путь к его локальному ключу в .env. AI-помощник может провести вас через настройку.",
        )
    key_check = _check_service_account_key(Path(key_path))
    if key_check:
        return DoctorCheck("google_sheets", ERROR, key_check, "Проверьте путь и структуру локального JSON-файла сервисного аккаунта.")
    return DoctorCheck("google_sheets", READY, "Библиотеки, локальный ключ, ID таблицы и название листа присутствуют.")


def _looks_like_email(value: str) -> bool:
    _display_name, address = parseaddr(value)
    return bool(address and "@" in address and " " not in address)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _check_service_account_key(path: Path) -> str | None:
    if not path.is_file():
        return "Не найден локальный файл доступа к Google Sheets."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Локальный файл доступа к Google Sheets не является корректным JSON-файлом."
    if not isinstance(data, dict) or data.get("type") != "service_account":
        return "JSON-файл не похож на ключ сервисного аккаунта Google."
    if not isinstance(data.get("client_email"), str) or not data.get("client_email"):
        return "В JSON-файле отсутствует обязательное служебное поле сервисного аккаунта."
    if not isinstance(data.get("private_key"), str) or not data.get("private_key"):
        return "В JSON-файле отсутствует обязательное служебное поле сервисного аккаунта."
    return None
