from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from .scheduling import ScheduleConfigurationError, parse_schedule


TASK_PREFIX = "OpenNewsMonitor-"
TASK_MARKER = "Open News Monitor schedule-tick"
TICK_MINUTES = 5
TASK_VALIDATE_ONLY = 0x1
TASK_CREATE = 0x2
TASK_LOGON_INTERACTIVE_TOKEN = 3


class TaskSchedulerError(RuntimeError):
    """A safe, user-facing failure while talking to Windows Task Scheduler."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class TaskSpec:
    profile_path: Path
    python_path: Path
    task_name: str
    working_directory: Path
    user_id: str
    run_every_minutes: int = TICK_MINUTES

    @property
    def profile_fingerprint(self) -> str:
        return sha256(str(self.profile_path).encode("utf-8")).hexdigest()

    @property
    def arguments(self) -> str:
        return f'-m open_news_monitor.cli schedule-tick "{self.profile_path}"'


@dataclass(frozen=True)
class TaskStatus:
    exists: bool
    owned: bool
    enabled: bool | None = None
    last_run: str | None = None
    next_run: str | None = None
    last_result: str | None = None
    state: str | None = None
    message: str = ""


@dataclass(frozen=True)
class TaskValidationResult:
    """Result of a COM syntax validation that does not create a task."""

    hresult: str
    message: str


CommandRunner = Callable[[list[str]], CommandResult]


def build_task_spec(
    config: dict, profile_path: str | Path, *, python_path: str | Path | None = None,
    user_id: str | None = None,
) -> TaskSpec:
    """Validate a schedule and produce absolute, secret-free task parameters."""
    try:
        settings = parse_schedule(config)
    except ScheduleConfigurationError:
        raise
    if settings is None:
        raise TaskSchedulerError("Расписание в этом профиле отключено. Сначала включите schedule.enabled.")

    profile = Path(profile_path).expanduser().resolve()
    if not profile.is_file():
        raise TaskSchedulerError(f"Не найден профиль: {profile}")
    executable = Path(python_path or sys.executable).resolve()
    if not executable.is_file():
        raise TaskSchedulerError(f"Не найден Python из виртуального окружения: {executable}")
    if python_path is None and sys.prefix == sys.base_prefix:
        raise TaskSchedulerError(
            "Команду нужно запускать из виртуального окружения проекта. Откройте .venv и повторите попытку."
        )
    fingerprint = sha256(str(profile).encode("utf-8")).hexdigest()[:16]
    principal_user = user_id or current_windows_user()
    if not principal_user.strip():
        raise TaskSchedulerError("Не удалось определить текущего пользователя Windows для задачи Планировщика.")
    return TaskSpec(profile, executable, f"{TASK_PREFIX}{fingerprint}", profile.parent, principal_user)


def current_windows_user() -> str:
    """Return DOMAIN\\User through the Windows NameSamCompatible API.

    Microsoft documents this account-name form for Principal.UserId. We do not
    use a SID because SID support is not explicitly documented for the XML
    registration path used by schtasks.exe.
    """
    if sys.platform != "win32":
        raise TaskSchedulerError("Управление Планировщиком задач доступно только в Windows.")
    # EXTENDED_NAME_FORMAT.NameSamCompatible == 2.
    get_user_name_ex = ctypes.windll.secur32.GetUserNameExW
    size = wintypes.ULONG(0)
    get_user_name_ex(2, None, ctypes.byref(size))
    if size.value == 0:
        raise TaskSchedulerError("Windows API не вернул имя текущего пользователя для задачи Планировщика.")
    buffer = ctypes.create_unicode_buffer(size.value)
    if not get_user_name_ex(2, buffer, ctypes.byref(size)):
        raise TaskSchedulerError("Не удалось получить имя текущего пользователя Windows для задачи Планировщика.")
    return buffer.value


def render_task_preview(action: str, spec: TaskSpec) -> str:
    verb = {
        "install": "создать",
        "enable": "включить",
        "disable": "отключить",
        "remove": "удалить",
    }[action]
    lines = [
        f"Будет выполнено действие: {verb} задачу Планировщика Windows.",
        f"Название задачи: {spec.task_name}",
        f"Профиль: {spec.profile_path}",
        f"Пользователь Windows: {spec.user_id}",
        f"Python: {spec.python_path}",
        f"Проверка расписания: каждые {spec.run_every_minutes} минут.",
    ]
    if action == "install":
        lines.append("Задача будет автоматически запускать schedule-tick после входа в Windows и затем примерно каждые 5 минут.")
        lines.append("Когда компьютер выключен, новости не собираются; после следующего запуска будет выполнена только актуальная проверка.")
    return "\n".join(lines)


def confirmation_approved(input_func=input, *, interactive: bool | None = None) -> bool:
    """Require an explicit response; non-interactive calls never change tasks."""
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not interactive:
        return False
    response = input_func("Подтвердите действие: введите yes или да: ").strip().casefold()
    return response in {"yes", "да"}


class WindowsTaskScheduler:
    """Small guarded adapter around schtasks.exe and read-only PowerShell queries."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _run_command

    def query_xml(self, task_name: str) -> str | None:
        result = self._runner(["schtasks.exe", "/Query", "/TN", task_name, "/XML"])
        if result.returncode == 0:
            return result.stdout
        message = f"{result.stdout}\n{result.stderr}".casefold()
        if any(text in message for text in ("cannot find", "not exist", "не удается найти", "не удаётся найти", "не существует")):
            return None
        raise TaskSchedulerError(_command_error("Не удалось прочитать задачу Планировщика", result))

    def create(self, spec: TaskSpec) -> None:
        if self.query_xml(spec.task_name) is not None:
            raise TaskSchedulerError(
                f"Задача {spec.task_name} уже существует. Она не была изменена. Используйте status для проверки."
            )
        self._register_via_com(spec, TASK_CREATE, "Не удалось создать задачу Планировщика")

    def validate(self, spec: TaskSpec) -> TaskValidationResult:
        """Ask the Task Scheduler COM API to validate XML without registration."""
        data = self._register_via_com(
            spec, TASK_VALIDATE_ONLY, "Планировщик не принял проверку определения задачи"
        )
        return TaskValidationResult(str(data.get("hresult", "0x00000000")), str(data.get("message", "Проверка пройдена.")))

    def status(self, spec: TaskSpec) -> TaskStatus:
        xml = self.query_xml(spec.task_name)
        if xml is None:
            return TaskStatus(False, False, message="Задача для этого профиля не найдена.")
        if not is_owned_task(xml, spec):
            return TaskStatus(True, False, message="Задача с таким именем не похожа на задачу текущего профиля. Она не будет изменена.")
        result = self._runner(_status_command(spec.task_name))
        if result.returncode != 0:
            raise TaskSchedulerError(_command_error("Не удалось прочитать состояние задачи", result))
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise TaskSchedulerError("Планировщик вернул непонятный ответ о состоянии задачи.") from error
        return TaskStatus(
            True,
            True,
            enabled=bool(data.get("enabled")),
            last_run=data.get("last_run"),
            next_run=data.get("next_run"),
            last_result=data.get("last_result"),
            state=data.get("state"),
            message="Задача принадлежит текущему профилю.",
        )

    def set_enabled(self, spec: TaskSpec, enabled: bool) -> None:
        self._require_owned(spec)
        switch = "/Enable" if enabled else "/Disable"
        result = self._runner(["schtasks.exe", "/Change", "/TN", spec.task_name, switch])
        if result.returncode != 0:
            raise TaskSchedulerError(_command_error("Не удалось изменить состояние задачи", result))

    def remove(self, spec: TaskSpec) -> None:
        self._require_owned(spec)
        result = self._runner(["schtasks.exe", "/Delete", "/TN", spec.task_name, "/F"])
        if result.returncode != 0:
            raise TaskSchedulerError(_command_error("Не удалось удалить задачу", result))

    def _require_owned(self, spec: TaskSpec) -> None:
        xml = self.query_xml(spec.task_name)
        if xml is None:
            raise TaskSchedulerError("Задача для этого профиля не найдена; ничего не изменено.")
        if not is_owned_task(xml, spec):
            raise TaskSchedulerError("Задача с таким именем не принадлежит текущему профилю; ничего не изменено.")

    def _register_via_com(self, spec: TaskSpec, flags: int, error_prefix: str) -> dict[str, object]:
        """Use the built-in Task Scheduler COM API through PowerShell.

        The PowerShell command body is constant. Paths and the account name
        are encoded as JSON data, never interpolated as PowerShell source.
        TASK_VALIDATE_ONLY deliberately validates but never creates a task.
        """
        xml_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as stream:
                xml_path = Path(stream.name)
                stream.write(task_xml(spec))
            result = self._runner(_com_registration_command({
                "task_name": spec.task_name,
                "xml_path": str(xml_path),
                "user_id": spec.user_id,
                "flags": flags,
            }))
            return _com_result_or_error(error_prefix, result)
        finally:
            if xml_path is not None:
                xml_path.unlink(missing_ok=True)


def task_xml(spec: TaskSpec) -> str:
    """Generate a task with a logon trigger and a five-minute trigger."""
    root = ET.Element("Task", {"version": "1.4", "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task"})
    registration = ET.SubElement(root, "RegistrationInfo")
    ET.SubElement(registration, "Description").text = f"{TASK_MARKER}; profile_sha256={spec.profile_fingerprint}"
    triggers = ET.SubElement(root, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    timer = ET.SubElement(triggers, "TimeTrigger")
    ET.SubElement(timer, "StartBoundary").text = datetime.now().astimezone().replace(microsecond=0).isoformat()
    repetition = ET.SubElement(timer, "Repetition")
    ET.SubElement(repetition, "Interval").text = f"PT{spec.run_every_minutes}M"
    ET.SubElement(repetition, "StopAtDurationEnd").text = "false"
    ET.SubElement(timer, "Enabled").text = "true"
    principals = ET.SubElement(root, "Principals")
    principal = ET.SubElement(principals, "Principal", {"id": "Author"})
    ET.SubElement(principal, "UserId").text = spec.user_id
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(root, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("StartWhenAvailable", "true"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("ExecutionTimeLimit", "PT1H"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("RunOnlyIfIdle", "false"),
        ("WakeToRun", "false"),
    ):
        ET.SubElement(settings, name).text = value
    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    action = ET.SubElement(actions, "Exec")
    ET.SubElement(action, "Command").text = str(spec.python_path)
    ET.SubElement(action, "Arguments").text = spec.arguments
    ET.SubElement(action, "WorkingDirectory").text = str(spec.working_directory)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + body


def is_owned_task(xml: str, spec: TaskSpec) -> bool:
    """Require both a marker and the exact profile/Python action before mutation."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return False
    values = [element.text or "" for element in root.iter()]
    return (
        any(f"{TASK_MARKER}; profile_sha256={spec.profile_fingerprint}" == value for value in values)
        and spec.user_id in values
        and str(spec.python_path) in values
        and spec.arguments in values
        and str(spec.working_directory) in values
    )


def render_task_status(spec: TaskSpec, status: TaskStatus) -> str:
    lines = [f"Название задачи: {spec.task_name}", f"Профиль: {spec.profile_path}"]
    if not status.exists:
        return "\n".join([*lines, "Состояние: задача не установлена."])
    if not status.owned:
        return "\n".join([*lines, "Состояние: задачу нельзя безопасно связать с текущим профилем.", status.message])
    enabled = "включена" if status.enabled else "отключена"
    lines.extend([
        f"Состояние: {enabled}.",
        f"Последний запуск: {status.last_run or 'ещё не было'}.",
        f"Следующий запуск: {status.next_run or 'не указан'}.",
        f"Последний результат: {status.last_result or 'не указан'}.",
        f"Текущее состояние Windows: {status.state or 'не указано'}.",
        "Задача запускает только schedule-tick текущего профиля.",
    ])
    return "\n".join(lines)


def _status_command(task_name: str) -> list[str]:
    # The task name is generated from a SHA-256 digest, but quote it anyway.
    escaped = task_name.replace("'", "''")
    script = (
        "$task = Get-ScheduledTask -TaskName '" + escaped + "'; "
        "$info = $task | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{enabled=($task.State -ne 'Disabled'); state=[string]$task.State; "
        "last_run=$info.LastRunTime.ToString('o'); next_run=$info.NextRunTime.ToString('o'); "
        "last_result=[string]$info.LastTaskResult} | ConvertTo-Json -Compress"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


_COM_REGISTRATION_SCRIPT = r'''$ErrorActionPreference = "Stop"
try {
    $payloadJson = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('__PAYLOAD_B64__'))
    $input = $payloadJson | ConvertFrom-Json
    $xml = [System.IO.File]::ReadAllText([string]$input.xml_path, [System.Text.Encoding]::Unicode)
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $folder = $service.GetFolder("\")
    # 3 is TASK_LOGON_INTERACTIVE_TOKEN. Password remains null and is never stored.
    $null = $folder.RegisterTask([string]$input.task_name, $xml, [int]$input.flags, [string]$input.user_id, $null, 3, $null)
    [pscustomobject]@{ ok = $true; hresult = "0x00000000"; message = "Task Scheduler accepted the definition." } |
        ConvertTo-Json -Compress
    exit 0
}
catch {
    $code = "0x{0:X8}" -f ($_.Exception.HResult -band 0xffffffff)
    [pscustomobject]@{ ok = $false; hresult = $code; message = $_.Exception.Message } |
        ConvertTo-Json -Compress
    exit 1
}
'''


def _com_registration_command(payload: dict[str, object]) -> list[str]:
    """Build a policy-safe PowerShell command with data-only user inputs."""
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = _COM_REGISTRATION_SCRIPT.replace("__PAYLOAD_B64__", payload_b64)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def _run_command(arguments: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as error:
        raise TaskSchedulerError(f"Не удалось запустить системную команду Планировщика: {error}") from error
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _command_error(prefix: str, result: CommandResult) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"{prefix}. {detail}" if detail else prefix + "."


def _com_result_or_error(prefix: str, result: CommandResult) -> dict[str, object]:
    payload = result.stdout.strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise TaskSchedulerError(_command_error(prefix, result)) from error
    if result.returncode == 0 and data.get("ok") is True:
        return data
    hresult = str(data.get("hresult", "unknown HRESULT"))
    message = str(data.get("message", "Неизвестная ошибка COM API."))
    raise TaskSchedulerError(f"{prefix}. HRESULT {hresult}: {message}")
