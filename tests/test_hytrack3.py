"""Unit tests for HyTrack3.

These tests intentionally stub optional runtime dependencies like Selenium so the
suite can run in a lightweight environment. Pyright/Pylance is therefore told to
relax a few "unknown from mock/dynamic module" diagnostics in this file only.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from typing import Any


def _install_import_stubs() -> None:
    if "selenium" not in sys.modules:
        selenium = types.ModuleType("selenium")
        webdriver = types.ModuleType("selenium.webdriver")
        setattr(webdriver, "Chrome", MagicMock())

        chrome = types.ModuleType("selenium.webdriver.chrome")
        chrome_service = types.ModuleType("selenium.webdriver.chrome.service")
        chrome_options = types.ModuleType("selenium.webdriver.chrome.options")
        setattr(chrome_service, "Service", MagicMock())
        setattr(chrome_options, "Options", MagicMock())

        common = types.ModuleType("selenium.webdriver.common")
        by_module = types.ModuleType("selenium.webdriver.common.by")
        setattr(by_module, "By", SimpleNamespace(XPATH="xpath"))

        support = types.ModuleType("selenium.webdriver.support")
        ui_module = types.ModuleType("selenium.webdriver.support.ui")
        ec_module = types.ModuleType("selenium.webdriver.support.expected_conditions")
        setattr(ui_module, "WebDriverWait", MagicMock())
        setattr(ec_module, "presence_of_element_located", MagicMock())

        exceptions = types.ModuleType("selenium.common.exceptions")
        setattr(exceptions, "TimeoutException", Exception)

        sys.modules["selenium"] = selenium
        sys.modules["selenium.webdriver"] = webdriver
        sys.modules["selenium.webdriver.chrome"] = chrome
        sys.modules["selenium.webdriver.chrome.service"] = chrome_service
        sys.modules["selenium.webdriver.chrome.options"] = chrome_options
        sys.modules["selenium.webdriver.common"] = common
        sys.modules["selenium.webdriver.common.by"] = by_module
        sys.modules["selenium.webdriver.support"] = support
        sys.modules["selenium.webdriver.support.ui"] = ui_module
        sys.modules["selenium.webdriver.support.expected_conditions"] = ec_module
        sys.modules["selenium.common.exceptions"] = exceptions

    if "webdriver_manager.chrome" not in sys.modules:
        webdriver_manager = types.ModuleType("webdriver_manager")
        chrome_module = types.ModuleType("webdriver_manager.chrome")

        class _ChromeDriverManager:
            def install(self) -> str:
                return "/tmp/chromedriver"

        setattr(chrome_module, "ChromeDriverManager", _ChromeDriverManager)
        sys.modules["webdriver_manager"] = webdriver_manager
        sys.modules["webdriver_manager.chrome"] = chrome_module

    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        setattr(dotenv, "load_dotenv", lambda: None)
        sys.modules["dotenv"] = dotenv


_install_import_stubs()

import Hytrack3


class ConfigPatchMixin:
    _config_patch: Any

    def setUp(self) -> None:  # NOSONAR - unittest lifecycle hook name
        self._config_patch = patch.multiple(
            Hytrack3.Config,
            IMAP_SERVER="imap.example.com",
            EMAIL_ADDRESS="sender@example.com",
            EMAIL_PASSWORD="secret-password",
            SMTP_SERVER="smtp.example.com",
            RECIPIENT_EMAIL="notify@example.com",
        )
        self._config_patch.start()

    def tearDown(self) -> None:  # NOSONAR - unittest lifecycle hook name
        self._config_patch.stop()


class DatabaseManagerTests(unittest.TestCase):
    def test_setup_add_and_update_shipment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")

            with Hytrack3.DatabaseManager(db_path) as db:
                db.setup()
                db.add_waybill("12345678901", "BLUEDART", "alice@example.com")

                active = db.get_active_shipments()
                self.assertEqual(1, len(active))
                self.assertEqual("12345678901", active[0]["waybill"])
                self.assertEqual("alice@example.com", active[0]["recipient_email"])

                db.update_shipment("12345678901", "hash123", is_delivered=True)
                active = db.get_active_shipments()
                self.assertEqual([], active)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT last_event_hash, is_delivered FROM shipments WHERE waybill = ?",
                    ("12345678901",),
                ).fetchone()
                self.assertEqual(("hash123", 1), row)
            finally:
                conn.close()


class ConfigTests(unittest.TestCase):
    def test_validate_raises_for_missing_required_values(self) -> None:
        original = {
            "IMAP_SERVER": Hytrack3.Config.IMAP_SERVER,
            "EMAIL_ADDRESS": Hytrack3.Config.EMAIL_ADDRESS,
            "EMAIL_PASSWORD": Hytrack3.Config.EMAIL_PASSWORD,
            "SMTP_SERVER": Hytrack3.Config.SMTP_SERVER,
            "RECIPIENT_EMAIL": Hytrack3.Config.RECIPIENT_EMAIL,
        }
        try:
            Hytrack3.Config.IMAP_SERVER = None
            Hytrack3.Config.EMAIL_ADDRESS = None
            with self.assertRaises(ValueError):
                Hytrack3.Config.validate()
        finally:
            for key, value in original.items():
                setattr(Hytrack3.Config, key, value)


class BlueDartTrackerTests(unittest.TestCase):
    def test_fetch_latest_event_parses_response(self) -> None:
        html = """
        <div id="SCAN12345678901">
            <table>
                <tbody>
                    <tr>
                        <td>Mumbai</td>
                        <td>Shipment Delivered</td>
                        <td>2026-04-09</td>
                        <td>10:30</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """
        session = MagicMock()
        session.get.return_value = SimpleNamespace(status_code=200, text=html)

        event = Hytrack3.BlueDartTracker(
            "12345678901", session=session
        ).fetch_latest_event()

        self.assertIsNotNone(event)
        self.assertEqual("Blue Dart", event["Courier"])
        self.assertEqual("Mumbai", event["Location"])
        self.assertEqual("Shipment Delivered", event["Details"])

    def test_fetch_latest_event_returns_none_for_incomplete_table(self) -> None:
        html = """
        <div id="SCAN12345678901">
            <table>
                <tbody>
                    <tr>
                        <td>Mumbai</td>
                        <td>Shipment Delivered</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """
        session = MagicMock()
        session.get.return_value = SimpleNamespace(status_code=200, text=html)

        event = Hytrack3.BlueDartTracker(
            "12345678901", session=session
        ).fetch_latest_event()

        self.assertIsNone(event)


class FakeDriverDelivered:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)

    def find_elements(self, by: object, xpath: str) -> list[object]:
        return [object()] if "Order Delivered" in xpath else []


class DelhiveryTrackerTests(unittest.TestCase):
    def test_fetch_latest_event_requires_driver(self) -> None:
        event = Hytrack3.DelhiveryTracker("123456789012").fetch_latest_event()
        self.assertIsNone(event)

    def test_fetch_latest_event_returns_delivered_status(self) -> None:
        driver = FakeDriverDelivered()

        with patch("Hytrack3.WebDriverWait") as wait_cls, patch(
            "Hytrack3.EC.presence_of_element_located", side_effect=lambda locator: locator
        ):
            wait_cls.return_value.until.return_value = True

            event = Hytrack3.DelhiveryTracker("123456789012").fetch_latest_event(
                driver=driver
            )

        self.assertIsNotNone(event)
        self.assertEqual("Delhivery", event["Courier"])
        self.assertIn("Delivered", event["Details"])
        self.assertTrue(driver.visited)


class EmailServiceContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = Hytrack3.EmailService()

    def test_get_email_content_prefers_plain_text(self) -> None:
        msg = EmailMessage()
        msg.set_content("Plain body 12345678901")
        msg.add_alternative("<p>HTML body</p>", subtype="html")

        self.assertEqual("Plain body 12345678901\n", self.service._get_email_content(msg))

    def test_get_email_content_falls_back_to_html(self) -> None:
        msg = EmailMessage()
        msg.add_alternative("<p>Waybill <b>12345678901</b></p>", subtype="html")

        content = self.service._get_email_content(msg)
        self.assertIn("12345678901", content)
        self.assertNotIn("<b>", content)

    def test_decode_payload_handles_missing_content(self) -> None:
        part = MagicMock()
        part.get_payload.return_value = None

        self.assertEqual("", self.service._decode_payload(part))


class FakeMailClient:
    def __init__(self, search_result: tuple[str, list[bytes]], fetch_map: dict[bytes, tuple[str, list[tuple[bytes, bytes]]]]) -> None:
        self.search_result = search_result
        self.fetch_map = fetch_map
        self.stored: list[bytes] = []
        self.logged_out = False

    def login(self, *_args: object) -> tuple[str, list[object]]:
        return "OK", []

    def select(self, *_args: object) -> tuple[str, list[object]]:
        return "OK", []

    def search(self, *_args: object) -> tuple[str, list[bytes]]:
        return self.search_result

    def fetch(self, num: bytes, *_args: object) -> tuple[str, list[tuple[bytes, bytes]]]:
        return self.fetch_map[num]

    def store(self, num: bytes, *_args: object) -> tuple[str, list[object]]:
        self.stored.append(num)
        return "OK", []

    def logout(self) -> tuple[str, list[object]]:
        self.logged_out = True
        return "BYE", []


class FakeDb:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str]] = []

    def add_waybill(self, waybill: str, courier: str, recipient_email: str) -> None:
        self.records.append((waybill, courier, recipient_email))


class EmailServiceFetchTests(ConfigPatchMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = Hytrack3.EmailService()

    def test_fetch_new_waybills_deduplicates_and_logs_out(self) -> None:
        msg = EmailMessage()
        msg["From"] = "Sender <sender@example.com>"
        msg.set_content(
            "Blue Dart 12345678901 and duplicate 12345678901 and Delhivery 123456789012"
        )
        raw_msg = msg.as_bytes()

        mail_client = FakeMailClient(
            ("OK", [b"1"]),
            {b"1": ("OK", [(b"1", raw_msg)])},
        )
        db = FakeDb()

        with patch("Hytrack3.imaplib.IMAP4_SSL", return_value=mail_client):
            found = self.service.fetch_new_waybills(db)

        self.assertEqual(2, len(found))
        self.assertEqual(2, len(db.records))
        self.assertIn((b"1"), mail_client.stored)
        self.assertTrue(mail_client.logged_out)

    def test_fetch_new_waybills_logs_out_when_no_messages(self) -> None:
        mail_client = FakeMailClient(("OK", [b""]), {})

        with patch("Hytrack3.imaplib.IMAP4_SSL", return_value=mail_client):
            found = self.service.fetch_new_waybills(FakeDb())

        self.assertEqual([], found)
        self.assertTrue(mail_client.logged_out)

    def test_process_unseen_message_skips_bad_fetch_response(self) -> None:
        mail_client = FakeMailClient(("OK", [b"1"]), {b"1": ("NO", [])})

        self.service._process_unseen_message(mail_client, b"1", FakeDb(), [])

        self.assertEqual([], mail_client.stored)


class NotificationTests(ConfigPatchMixin, unittest.TestCase):
    def test_send_notification_uses_tls_context(self) -> None:
        smtp_client = MagicMock()
        smtp_client.__enter__.return_value = smtp_client
        smtp_client.has_extn.return_value = True
        tls_context = SimpleNamespace(minimum_version=None)

        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client), patch(
            "Hytrack3.ssl.create_default_context", return_value=tls_context
        ):
            Hytrack3.EmailService().send_notification(
                "bob@example.com", "Subject", "<p>Hello</p>"
            )

        self.assertEqual(Hytrack3.ssl.TLSVersion.TLSv1_2, tls_context.minimum_version)
        smtp_client.starttls.assert_called_once_with(context=tls_context)
        smtp_client.sendmail.assert_called_once()

    def test_send_notification_aborts_when_starttls_is_unavailable(self) -> None:
        smtp_client = MagicMock()
        smtp_client.__enter__.return_value = smtp_client
        smtp_client.has_extn.return_value = False

        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client):
            Hytrack3.EmailService().send_notification(
                "bob@example.com", "Subject", "<p>Hello</p>"
            )

        smtp_client.starttls.assert_not_called()
        smtp_client.login.assert_not_called()
        smtp_client.sendmail.assert_not_called()

    def test_send_notification_uses_configured_timeout(self) -> None:
        smtp_client = MagicMock()
        smtp_client.__enter__.return_value = smtp_client
        smtp_client.has_extn.return_value = True
        tls_context = SimpleNamespace(minimum_version=None)

        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client) as smtp_cls, patch(
            "Hytrack3.ssl.create_default_context", return_value=tls_context
        ):
            Hytrack3.EmailService().send_notification(
                "bob@example.com", "Subject", "<p>Hello</p>"
            )

        smtp_cls.assert_called_once_with(
            Hytrack3.Config.SMTP_SERVER,
            Hytrack3.Config.SMTP_PORT,
            timeout=Hytrack3.Config.SMTP_TIMEOUT,
        )


class HtmlMessageTests(unittest.TestCase):
    def test_build_html_message_escapes_user_content_and_avoids_remote_font_import(self) -> None:
        html = Hytrack3.build_html_message(
            "WB<script>",
            {
                "Courier": "Blue Dart",
                "Details": "<b>Delivered</b>",
                "Location": "<img src=x onerror=alert(1)>",
                "Date": "2026-04-09",
                "Time": "10:30",
                "Link": 'https://example.com/?q="bad"',
            },
        )

        self.assertIn("&lt;b&gt;Delivered&lt;/b&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("WB&lt;script&gt;", html)
        self.assertNotIn("@import url(", html)

    def test_is_delivered_status_distinguishes_failed_or_in_progress_events(self) -> None:
        self.assertTrue(Hytrack3.is_delivered_status("Shipment delivered successfully"))
        self.assertFalse(Hytrack3.is_delivered_status("Out for delivery"))
        self.assertFalse(Hytrack3.is_delivered_status("Delivery failed"))

    def test_is_valid_tracking_event_requires_all_fields(self) -> None:
        self.assertTrue(
            Hytrack3.is_valid_tracking_event(
                {
                    "Courier": "Blue Dart",
                    "Location": "Mumbai",
                    "Details": "Delivered",
                    "Date": "2026-04-09",
                    "Time": "10:30",
                    "Link": "https://example.com",
                }
            )
        )
        self.assertFalse(Hytrack3.is_valid_tracking_event({"Courier": "Blue Dart"}))


class ProcessShipmentTests(unittest.TestCase):
    def test_process_shipment_updates_db_and_sends_notification_for_new_status(self) -> None:
        row = {
            "waybill": "12345678901",
            "last_event_hash": None,
            "recipient_email": "notify@example.com",
            "courier": "BLUEDART",
        }
        tracker = SimpleNamespace(
            fetch_latest_event=MagicMock(
                return_value={
                    "Courier": "Blue Dart",
                    "Location": "Mumbai",
                    "Details": "Shipment delivered",
                    "Date": "2026-04-09",
                    "Time": "10:30",
                    "Link": "https://example.com",
                }
            )
        )
        db = SimpleNamespace(update_shipment=MagicMock())
        email_service = SimpleNamespace(send_notification=MagicMock())

        Hytrack3.process_shipment(row, tracker, db, email_service)

        email_service.send_notification.assert_called_once()
        db.update_shipment.assert_called_once()
        self.assertTrue(db.update_shipment.call_args.args[2])

    def test_process_shipment_skips_invalid_event_payload(self) -> None:
        row = {
            "waybill": "12345678901",
            "last_event_hash": None,
            "recipient_email": "notify@example.com",
            "courier": "BLUEDART",
        }
        tracker = SimpleNamespace(fetch_latest_event=MagicMock(return_value={"Courier": "Blue Dart"}))
        db = SimpleNamespace(update_shipment=MagicMock())
        email_service = SimpleNamespace(send_notification=MagicMock())

        Hytrack3.process_shipment(row, tracker, db, email_service)

        email_service.send_notification.assert_not_called()
        db.update_shipment.assert_not_called()

    def test_process_shipment_skips_when_hash_is_unchanged(self) -> None:
        event = {
            "Courier": "Blue Dart",
            "Location": "Mumbai",
            "Details": "Shipment delivered",
            "Date": "2026-04-09",
            "Time": "10:30",
            "Link": "https://example.com",
        }
        current_hash = Hytrack3.hashlib.sha256(
            f"{event['Details']}{event['Location']}".encode("utf-8")
        ).hexdigest()
        row = {
            "waybill": "12345678901",
            "last_event_hash": current_hash,
            "recipient_email": "notify@example.com",
            "courier": "BLUEDART",
        }
        tracker = SimpleNamespace(fetch_latest_event=MagicMock(return_value=event))
        db = SimpleNamespace(update_shipment=MagicMock())
        email_service = SimpleNamespace(send_notification=MagicMock())

        Hytrack3.process_shipment(row, tracker, db, email_service)

        email_service.send_notification.assert_not_called()
        db.update_shipment.assert_not_called()

    def test_process_shipment_falls_back_to_configured_recipient(self) -> None:
        original_recipient = Hytrack3.Config.RECIPIENT_EMAIL
        Hytrack3.Config.RECIPIENT_EMAIL = "fallback@example.com"
        try:
            row = {
                "waybill": "12345678901",
                "last_event_hash": None,
                "recipient_email": None,
                "courier": "BLUEDART",
            }
            tracker = SimpleNamespace(
                fetch_latest_event=MagicMock(
                    return_value={
                        "Courier": "Blue Dart",
                        "Location": "Mumbai",
                        "Details": "Shipment delivered",
                        "Date": "2026-04-09",
                        "Time": "10:30",
                        "Link": "https://example.com",
                    }
                )
            )
            db = SimpleNamespace(update_shipment=MagicMock())
            email_service = SimpleNamespace(send_notification=MagicMock())

            Hytrack3.process_shipment(row, tracker, db, email_service)

            self.assertEqual(
                "fallback@example.com",
                email_service.send_notification.call_args.args[0],
            )
        finally:
            Hytrack3.Config.RECIPIENT_EMAIL = original_recipient


if __name__ == "__main__":
    unittest.main()
