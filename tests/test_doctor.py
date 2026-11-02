from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from open_news_monitor.cli import main
from open_news_monitor.doctor import ERROR, NEEDS_SETUP, READY, inspect_profile, render_doctor_report


class DoctorTests(unittest.TestCase):
    def _profile(self, root: Path, **outputs: dict) -> dict:
        return {
            "monitor": {"name": "Doctor test"},
            "sources": [],
            "filters": {"include_any": [], "exclude_any": []},
            "outputs": {
                "markdown": {"enabled": True, "path": str(root / "digest.md")},
                "telegram": {"enabled": False},
                "email": {"enabled": False, "recipients": []},
                "google_sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": ""},
                **outputs,
            },
        }

    def _results(self, profile: dict, env: dict[str, str]) -> dict[str, object]:
        return {result.channel: result for result in inspect_profile(profile, environment=env)}

    def test_fully_configured_profile_is_locally_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "service-account.json"
            key.write_text(json.dumps({"type": "service_account", "client_email": "bot@example.test", "private_key": "not-a-real-key"}), encoding="utf-8")
            profile = self._profile(
                root,
                telegram={"enabled": True},
                email={"enabled": True, "recipients": ["reader@example.test"]},
                google_sheets={"enabled": True, "spreadsheet_id": "sheet-id", "worksheet": "News"},
            )
            env = {
                "ONM_TELEGRAM_BOT_TOKEN": "12345:test_token",
                "ONM_TELEGRAM_CHAT_ID": "-100123456",
                "ONM_SMTP_HOST": "smtp.example.test",
                "ONM_SMTP_PORT": "587",
                "ONM_SMTP_USERNAME": "sender@example.test",
                "ONM_SMTP_PASSWORD": "not-a-real-password",
                "ONM_SMTP_FROM": "sender@example.test",
                "ONM_GOOGLE_SERVICE_ACCOUNT_FILE": str(key),
            }
            with patch("open_news_monitor.doctor._module_available", return_value=True):
                results = self._results(profile, env)
            self.assertTrue(all(results[channel].status == READY for channel in results))

    def test_missing_telegram_token_or_chat_id_requires_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory), telegram={"enabled": True})
            self.assertEqual(NEEDS_SETUP, self._results(profile, {"ONM_TELEGRAM_CHAT_ID": "123"})["telegram"].status)
            self.assertEqual(NEEDS_SETUP, self._results(profile, {"ONM_TELEGRAM_BOT_TOKEN": "123:token"})["telegram"].status)

    def test_invalid_telegram_format_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory), telegram={"enabled": True})
            result = self._results(profile, {"ONM_TELEGRAM_BOT_TOKEN": "not a token", "ONM_TELEGRAM_CHAT_ID": "not-a-chat"})["telegram"]
            self.assertEqual(ERROR, result.status)

    def test_incomplete_email_and_missing_recipient_require_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory), email={"enabled": True, "recipients": []})
            self.assertEqual(NEEDS_SETUP, self._results(profile, {"ONM_SMTP_HOST": "smtp.example.test"})["email"].status)

    def test_invalid_email_port_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory), email={"enabled": True, "recipients": ["reader@example.test"]})
            env = {
                "ONM_SMTP_HOST": "smtp.example.test", "ONM_SMTP_PORT": "not-a-port",
                "ONM_SMTP_USERNAME": "sender@example.test", "ONM_SMTP_PASSWORD": "password",
                "ONM_SMTP_FROM": "sender@example.test",
            }
            self.assertEqual(ERROR, self._results(profile, env)["email"].status)

    def test_missing_sheets_library_or_key_requires_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory), google_sheets={"enabled": True, "spreadsheet_id": "sheet-id", "worksheet": "News"})
            self.assertEqual(NEEDS_SETUP, self._results(profile, {})["google_sheets"].status)

    def test_invalid_sheets_key_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad_key = root / "bad.json"
            bad_key.write_text("not json", encoding="utf-8")
            profile = self._profile(root, google_sheets={"enabled": True, "spreadsheet_id": "sheet-id", "worksheet": "News"})
            with patch("open_news_monitor.doctor._module_available", return_value=True):
                result = self._results(profile, {"ONM_GOOGLE_SERVICE_ACCOUNT_FILE": str(bad_key)})["google_sheets"]
            self.assertEqual(ERROR, result.status)

    def test_missing_sheets_key_file_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root, google_sheets={"enabled": True, "spreadsheet_id": "sheet-id", "worksheet": "News"})
            with patch("open_news_monitor.doctor._module_available", return_value=True):
                result = self._results(profile, {"ONM_GOOGLE_SERVICE_ACCOUNT_FILE": str(root / "missing.json")})["google_sheets"]
            self.assertEqual(ERROR, result.status)

    def test_missing_sheets_target_requires_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.json"
            key.write_text(json.dumps({"type": "service_account", "client_email": "bot@example.test", "private_key": "key"}), encoding="utf-8")
            profile = self._profile(root, google_sheets={"enabled": True, "spreadsheet_id": "", "worksheet": ""})
            with patch("open_news_monitor.doctor._module_available", return_value=True):
                result = self._results(profile, {"ONM_GOOGLE_SERVICE_ACCOUNT_FILE": str(key)})["google_sheets"]
            self.assertEqual(NEEDS_SETUP, result.status)

    def test_disabled_channels_are_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = self._results(self._profile(Path(directory)), {})
            self.assertEqual("DISABLED", results["telegram"].status)
            self.assertEqual("DISABLED", results["email"].status)
            self.assertEqual("DISABLED", results["google_sheets"].status)

    def test_doctor_without_profile_explains_next_step(self) -> None:
        output = StringIO()
        with patch("sys.argv", ["open-news-monitor", "doctor"]), redirect_stdout(output):
            main()
        self.assertIn("Профиль не указан", output.getvalue())

    def test_report_does_not_make_network_calls_or_reveal_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = "very-secret-value"
            key = root / "service-account.json"
            key.write_text(json.dumps({"type": "service_account", "client_email": "bot@example.test", "private_key": secret}), encoding="utf-8")
            profile = self._profile(
                root,
                telegram={"enabled": True},
                email={"enabled": True, "recipients": ["reader@example.test"]},
                google_sheets={"enabled": True, "spreadsheet_id": "sheet-id", "worksheet": "News"},
            )
            environment = {
                "ONM_TELEGRAM_BOT_TOKEN": secret,
                "ONM_TELEGRAM_CHAT_ID": "123",
                "ONM_SMTP_HOST": "smtp.example.test",
                "ONM_SMTP_PORT": "587",
                "ONM_SMTP_USERNAME": "sender@example.test",
                "ONM_SMTP_PASSWORD": secret,
                "ONM_SMTP_FROM": "sender@example.test",
                "ONM_GOOGLE_SERVICE_ACCOUNT_FILE": str(key),
            }
            with patch("open_news_monitor.outputs.urlopen") as telegram_request, patch("smtplib.SMTP") as smtp:
                with patch("open_news_monitor.doctor._module_available", return_value=True):
                    report = render_doctor_report(profile, environment=environment)
            telegram_request.assert_not_called()
            smtp.assert_not_called()
            self.assertNotIn(secret, report)
            self.assertIn("доступ к Telegram", report)
