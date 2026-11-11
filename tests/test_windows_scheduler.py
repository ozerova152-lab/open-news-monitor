from __future__ import annotations

import json
import base64
import re
import sys
import tempfile
import unittest
from pathlib import Path

from open_news_monitor.windows_scheduler import (
    CommandResult,
    TaskSchedulerError,
    WindowsTaskScheduler,
    TASK_VALIDATE_ONLY,
    build_task_spec,
    confirmation_approved,
    is_owned_task,
    task_xml,
)


class _FakeWindowsScheduler:
    def __init__(self) -> None:
        self.tasks: dict[str, str] = {}
        self.enabled: dict[str, bool] = {}
        self.calls: list[list[str]] = []
        self.last_registration_details: dict[str, object] | None = None

    def __call__(self, arguments: list[str]) -> CommandResult:
        self.calls.append(arguments)
        if arguments[:2] == ["schtasks.exe", "/Query"]:
            name = arguments[arguments.index("/TN") + 1]
            if name not in self.tasks:
                return CommandResult(1, stderr="ERROR: The system cannot find the file specified.")
            return CommandResult(0, stdout=self.tasks[name])
        if arguments[0] == "powershell.exe" and "-EncodedCommand" in arguments:
            script = base64.b64decode(arguments[-1]).decode("utf-16-le")
            match = re.search(r"FromBase64String\('([^']+)'\)", script)
            assert match is not None
            details = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
            self.last_registration_details = details
            name = details["task_name"]
            xml_path = Path(details["xml_path"])
            flags = int(details["flags"])
            xml = xml_path.read_text(encoding="utf-16")
            if flags != TASK_VALIDATE_ONLY:
                self.tasks[name] = xml
                self.enabled[name] = True
            return CommandResult(0, stdout=json.dumps({"ok": True, "hresult": "0x00000000", "message": "accepted"}))
        if arguments[:2] == ["schtasks.exe", "/Change"]:
            name = arguments[arguments.index("/TN") + 1]
            self.enabled[name] = "/Enable" in arguments
            return CommandResult(0, stdout="SUCCESS")
        if arguments[:2] == ["schtasks.exe", "/Delete"]:
            name = arguments[arguments.index("/TN") + 1]
            del self.tasks[name]
            self.enabled.pop(name, None)
            return CommandResult(0, stdout="SUCCESS")
        if arguments[0] == "powershell.exe":
            task_name = next(iter(self.tasks))
            return CommandResult(0, stdout=json.dumps({
                "enabled": self.enabled[task_name], "state": "Ready", "last_run": "2026-11-02T10:00:00", "next_run": "2026-11-02T10:05:00", "last_result": "0",
            }))
        raise AssertionError(f"Unexpected command: {arguments}")


class WindowsSchedulerTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "monitor": {"name": "Task scheduler test"},
            "sources": [],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": False}, "telegram": {"enabled": True},
                "email": {"enabled": False, "recipients": []},
                "google_sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": "News"},
            },
            "schedule": {
                "enabled": True, "timezone": "Europe/Moscow", "collect_every_minutes": 180,
                "deliver_at": {"telegram": ["10:00", "18:00"]},
            },
        }

    def _spec(self, root: Path):
        profile_dir = root / "Профиль с пробелом"
        profile_dir.mkdir()
        profile = profile_dir / "monitor profile.json"
        profile.write_text(json.dumps(self._config()), encoding="utf-8")
        return build_task_spec(
            self._config(), profile, python_path=Path(sys.executable), user_id="DESKTOP-TEST\\Обычный пользователь"
        )

    def test_install_status_disable_enable_and_remove_only_own_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            fake = _FakeWindowsScheduler()
            scheduler = WindowsTaskScheduler(fake)
            scheduler.create(spec)
            self.assertIn(spec.task_name, fake.tasks)
            self.assertIn(str(spec.profile_path), fake.tasks[spec.task_name])
            self.assertIn(str(spec.python_path), fake.tasks[spec.task_name])
            self.assertIn(spec.user_id, fake.tasks[spec.task_name])
            self.assertIn("PT5M", fake.tasks[spec.task_name])
            self.assertNotIn("ONM_TELEGRAM_BOT_TOKEN", fake.tasks[spec.task_name])
            self.assertTrue(is_owned_task(fake.tasks[spec.task_name], spec))

            status = scheduler.status(spec)
            self.assertTrue(status.exists)
            self.assertTrue(status.owned)
            self.assertTrue(status.enabled)
            self.assertEqual("0", status.last_result)

            fake.tasks["OtherNewsTask"] = "<Task><Description>unrelated</Description></Task>"
            scheduler.set_enabled(spec, False)
            self.assertFalse(fake.enabled[spec.task_name])
            self.assertIn("OtherNewsTask", fake.tasks)
            scheduler.set_enabled(spec, True)
            self.assertTrue(fake.enabled[spec.task_name])
            scheduler.remove(spec)
            self.assertNotIn(spec.task_name, fake.tasks)
            self.assertIn("OtherNewsTask", fake.tasks)

    def test_validate_only_does_not_create_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            fake = _FakeWindowsScheduler()
            result = WindowsTaskScheduler(fake).validate(spec)
            self.assertEqual("0x00000000", result.hresult)
            self.assertEqual({}, fake.tasks)
            command = next(call for call in fake.calls if call[0] == "powershell.exe")
            self.assertIn("-EncodedCommand", command)
            self.assertIsNotNone(fake.last_registration_details)
            self.assertEqual(TASK_VALIDATE_ONLY, fake.last_registration_details["flags"])

    def test_com_error_shows_hresult_and_does_not_create_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            def denied(arguments: list[str]) -> CommandResult:
                if arguments[:2] == ["schtasks.exe", "/Query"]:
                    return CommandResult(1, stderr="ERROR: The system cannot find the file specified.")
                return CommandResult(1, stdout=json.dumps({"ok": False, "hresult": "0x80070005", "message": "Access is denied."}))
            with self.assertRaisesRegex(TaskSchedulerError, r"HRESULT 0x80070005"):
                WindowsTaskScheduler(denied).create(spec)

    def test_existing_or_foreign_task_is_never_overwritten_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            fake = _FakeWindowsScheduler()
            fake.tasks[spec.task_name] = "<Task><Description>foreign</Description></Task>"
            fake.enabled[spec.task_name] = True
            scheduler = WindowsTaskScheduler(fake)
            with self.assertRaises(TaskSchedulerError):
                scheduler.create(spec)
            with self.assertRaises(TaskSchedulerError):
                scheduler.remove(spec)
            self.assertIn(spec.task_name, fake.tasks)
            self.assertFalse(any(command[:2] == ["schtasks.exe", "/Delete"] for command in fake.calls))

    def test_missing_task_status_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            status = WindowsTaskScheduler(_FakeWindowsScheduler()).status(spec)
            self.assertFalse(status.exists)
            self.assertFalse(status.owned)

    def test_confirmation_requires_interactive_explicit_yes(self) -> None:
        self.assertFalse(confirmation_approved(lambda _prompt: "yes", interactive=False))
        self.assertFalse(confirmation_approved(lambda _prompt: "no", interactive=True))
        self.assertTrue(confirmation_approved(lambda _prompt: "yes", interactive=True))
        self.assertTrue(confirmation_approved(lambda _prompt: "да", interactive=True))

    def test_disabled_or_invalid_schedule_cannot_produce_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            config = self._config()
            config["schedule"]["enabled"] = False
            profile.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(TaskSchedulerError):
                build_task_spec(config, profile, python_path=Path(sys.executable), user_id="TEST\\User")
            config = self._config()
            config["schedule"]["deliver_at"] = {"telegram": ["not-a-time"]}
            profile.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(Exception):
                build_task_spec(config, profile, python_path=Path(sys.executable), user_id="TEST\\User")

    def test_user_id_is_escaped_and_keeps_least_privilege(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(self._config()), encoding="utf-8")
            user_id = "DOMAIN\\Пользователь & <test>"
            spec = build_task_spec(self._config(), profile, python_path=Path(sys.executable), user_id=user_id)
            xml = task_xml(spec)
            self.assertIn("DOMAIN\\Пользователь &amp; &lt;test&gt;", xml)
            self.assertIn("<UserId>DOMAIN\\Пользователь &amp; &lt;test&gt;</UserId>", xml)
            self.assertIn("<LogonType>InteractiveToken</LogonType>", xml)
            self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", xml)
            self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
            self.assertNotIn("HighestAvailable", xml)
            self.assertNotIn("RequiredPrivileges", xml)

    def test_ownership_check_requires_matching_user_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = self._spec(Path(directory))
            xml = task_xml(spec).replace(spec.user_id, "OTHER\\User")
            self.assertFalse(is_owned_task(xml, spec))

