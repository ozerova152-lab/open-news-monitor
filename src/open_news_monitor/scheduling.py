from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .locking import DatabaseRunLock
from .pipeline import _deliver_monitor_locked, _run_monitor_locked
from .store import ArticleStore


DELIVERY_CHANNELS = {"telegram", "email", "google_sheets"}


class ScheduleConfigurationError(ValueError):
    """Raised when an enabled schedule cannot be interpreted safely."""


@dataclass(frozen=True)
class ScheduleSettings:
    timezone_name: str
    collect_every_minutes: int
    delivery_times: dict[str, tuple[str, ...]]


@dataclass
class ScheduleTickReport:
    """A local report of one scheduler tick, suitable for CLI output and tests."""

    local_time: datetime
    collected: bool = False
    delivered_channels: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return self.collected or bool(self.delivered_channels)


def parse_schedule(config: dict) -> ScheduleSettings | None:
    """Read an optional profile schedule without changing the profile.

    A disabled or absent schedule is intentionally a no-op so old profiles
    continue to work unchanged.
    """
    raw = config.get("schedule")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ScheduleConfigurationError("Раздел schedule должен быть объектом JSON.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ScheduleConfigurationError("schedule.enabled должен быть true или false.")
    if not enabled:
        return None

    timezone_name = raw.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ScheduleConfigurationError("Для включённого расписания укажите часовой пояс IANA, например Europe/Moscow.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ScheduleConfigurationError(f"Неизвестный часовой пояс: {timezone_name}.") from error

    interval = raw.get("collect_every_minutes")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        raise ScheduleConfigurationError("Периодичность сбора должна быть целым числом минут больше нуля.")

    raw_deliveries = raw.get("deliver_at", {})
    if not isinstance(raw_deliveries, dict):
        raise ScheduleConfigurationError("schedule.deliver_at должен содержать время доставки по каналам.")

    delivery_times: dict[str, tuple[str, ...]] = {}
    outputs = config.get("outputs", {})
    for channel, values in raw_deliveries.items():
        if channel not in DELIVERY_CHANNELS:
            raise ScheduleConfigurationError(f"Неизвестный канал в расписании: {channel}.")
        if not isinstance(outputs.get(channel), dict) or not outputs[channel].get("enabled"):
            raise ScheduleConfigurationError(f"Канал {channel} указан в расписании, но не включён в профиле.")
        if not isinstance(values, list) or not values:
            raise ScheduleConfigurationError(f"Для канала {channel} укажите хотя бы одно время доставки.")
        parsed = tuple(sorted(set(_validate_time(value) for value in values)))
        if len(parsed) != len(values):
            raise ScheduleConfigurationError(f"В расписании канала {channel} есть повторяющееся время.")
        delivery_times[channel] = parsed

    return ScheduleSettings(timezone_name, interval, delivery_times)


def schedule_configuration_problem(config: dict) -> str | None:
    """Return a Russian diagnostic for doctor without contacting external services."""
    try:
        parse_schedule(config)
    except ScheduleConfigurationError as error:
        return str(error)
    return None


def schedule_tick(config: dict, *, now: datetime | None = None) -> ScheduleTickReport:
    """Run only the collection and deliveries that are due at this tick.

    Collection uses the current interval bucket. A computer that was off does
    one current collection, never a chain of historical catch-up collections.
    Delivery uses the latest due local time for today per channel; older missed
    times are intentionally skipped. This keeps a restart from producing a
    burst of stale digests while leaving queued publications intact.
    """
    settings = parse_schedule(config)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    if settings is None:
        return ScheduleTickReport(local_time=instant)

    local_now = instant.astimezone(ZoneInfo(settings.timezone_name))
    report = ScheduleTickReport(local_time=local_now)
    database_path = config["monitor"].get("database_path", "runtime/open-news-monitor.sqlite3")

    # One lock covers both state decisions and all external delivery work.
    with DatabaseRunLock(database_path):
        store = ArticleStore(database_path)
        try:
            collection_key = _collection_key(local_now, settings)
            if not store.schedule_run_completed(collection_key):
                try:
                    _findings, errors, _markdown = _run_monitor_locked(config, deliver=False)
                except Exception as error:
                    report.errors.append(f"Сбор: {error}")
                else:
                    if errors:
                        report.errors.extend(f"Сбор: {error}" for error in errors)
                    else:
                        store.mark_schedule_run_completed(
                            collection_key, "collect", None, _collection_slot(local_now, settings), local_now.isoformat()
                        )
                        report.collected = True

            for channel, times in settings.delivery_times.items():
                due_time = _latest_due_time(times, local_now)
                if due_time is None:
                    continue
                delivery_key = _delivery_key(channel, settings.timezone_name, local_now.date().isoformat(), due_time)
                if store.schedule_run_completed(delivery_key):
                    continue
                try:
                    errors, _status = _deliver_monitor_locked(config, channels={channel})
                except Exception as error:
                    report.errors.append(f"Доставка {channel}: {error}")
                else:
                    if errors:
                        report.errors.extend(f"Доставка {channel}: {error}" for error in errors)
                    else:
                        store.mark_schedule_run_completed(
                            delivery_key, "deliver", channel, f"{local_now.date().isoformat()}T{due_time}", local_now.isoformat()
                        )
                        report.delivered_channels.append(channel)
        finally:
            store.close()
    return report


def render_schedule_tick_report(report: ScheduleTickReport) -> str:
    """Keep CLI output short and understandable for a non-technical user."""
    lines = [f"Проверка расписания: {report.local_time.isoformat()}"]
    if report.collected:
        lines.append("- Сбор выполнен.")
    for channel in report.delivered_channels:
        lines.append(f"- Доставка выполнена: {channel}.")
    if not report.did_work and not report.errors:
        lines.append("- Сейчас нет действий по расписанию.")
    if report.errors:
        lines.append("- Есть ошибки; неуспешные действия будут повторены при следующем подходящем запуске.")
        lines.extend(f"  - {error}" for error in report.errors)
    return "\n".join(lines)


def _validate_time(value: object) -> str:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ScheduleConfigurationError("Время доставки должно быть в формате ЧЧ:ММ, например 10:00.")
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise ScheduleConfigurationError("Время доставки должно быть в формате ЧЧ:ММ, например 10:00.") from error
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleConfigurationError("Время доставки должно быть в диапазоне от 00:00 до 23:59.")
    return f"{hour:02d}:{minute:02d}"


def _collection_key(local_now: datetime, settings: ScheduleSettings) -> str:
    # UTC buckets remain unambiguous when a local clock moves backward.
    minute = int(local_now.astimezone(timezone.utc).timestamp() // 60)
    return f"collect:{settings.collect_every_minutes}:{minute // settings.collect_every_minutes}"


def _collection_slot(local_now: datetime, settings: ScheduleSettings) -> str:
    return f"{settings.timezone_name}:{_collection_key(local_now, settings)}"


def _latest_due_time(times: tuple[str, ...], local_now: datetime) -> str | None:
    current = (local_now.hour, local_now.minute)
    due = [value for value in times if tuple(int(part) for part in value.split(":")) <= current]
    return due[-1] if due else None


def _delivery_key(channel: str, timezone_name: str, local_date: str, due_time: str) -> str:
    # Deliberately omit datetime.fold: a repeated autumn clock hour still has
    # one intended human-facing delivery slot, not two duplicate digests.
    return f"deliver:{channel}:{timezone_name}:{local_date}:{due_time}"
