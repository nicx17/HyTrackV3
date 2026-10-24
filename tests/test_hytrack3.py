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
        setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
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
    def __init__(self, active_for_sender: int = 0) -> None:
        self.records: list[tuple[str, str, str]] = []
        self.active_for_sender = active_for_sender

    def add_waybill(self, waybill: str, courier: str, recipient_email: str) -> None:
        self.records.append((waybill, courier, recipient_email))

    def count_active_for_sender(self, _recipient_email: str) -> int:
        return self.active_for_sender + len(self.records)


class EmailServiceFetchTests(ConfigPatchMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = Hytrack3.EmailService()

    def test_fetch_new_waybills_deduplicates_and_logs_out(self) -> None:
        msg = EmailMessage()
        msg["From"] = "Customer <customer@example.com>"
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

    def test_imap_connection_uses_timeout(self) -> None:
        mail_client = FakeMailClient(("OK", [b""]), {})

        with patch("Hytrack3.imaplib.IMAP4_SSL", return_value=mail_client) as imap_cls:
            self.service.fetch_new_waybills(FakeDb())

        self.assertEqual(Hytrack3.Config.IMAP_TIMEOUT, imap_cls.call_args.kwargs.get("timeout"))
        self.assertGreater(Hytrack3.Config.IMAP_TIMEOUT, 0)

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


SAMPLE_EVENT = {
    "Courier": "Blue Dart",
    "Location": "Quepem",
    "Details": "Shipment Arrived",
    "Date": "18 Oct 2026",
    "Time": "11:42",
    "Link": "https://example.com/track",
    "History": [
        {"Location": "Quepem", "Details": "Shipment Arrived", "Date": "18 Oct 2026", "Time": "11:42"},
        {"Location": "Goa Hub", "Details": "Shipment Further Connected", "Date": "18 Oct 2026", "Time": "05:03"},
        {"Location": "Mumbai Hub", "Details": "Shipment Further Connected", "Date": "17 Oct 2026", "Time": "00:51"},
        {"Location": "Pune", "Details": "Shipment Picked Up", "Date": "16 Oct 2026", "Time": "15:07"},
    ],
    "Meta": {"From": "Pune", "To": "Quepem", "Expected Delivery": "19 Oct 2026"},
}


class StatusClassificationTests(unittest.TestCase):
    def test_undelivered_is_not_delivered(self) -> None:
        self.assertFalse(Hytrack3.is_delivered_status("Shipment Undelivered - Consignee not available"))
        self.assertFalse(Hytrack3.is_delivered_status("Not delivered, address incomplete"))
        self.assertFalse(Hytrack3.is_delivered_status("Delivery attempted - premises closed"))

    def test_classify_status(self) -> None:
        self.assertEqual("delivered", Hytrack3.classify_status("SHIPMENT DELIVERED"))
        self.assertEqual("out_for_delivery", Hytrack3.classify_status("Shipment Out For Delivery"))
        self.assertEqual("exception", Hytrack3.classify_status("Shipment Undelivered"))
        self.assertEqual("exception", Hytrack3.classify_status("Delivery failed"))
        self.assertEqual("in_transit", Hytrack3.classify_status("Shipment Arrived"))


class WaybillExtractionTests(unittest.TestCase):
    def test_extracts_bare_waybills_by_length(self) -> None:
        self.assertEqual(
            [("90700845933", "BLUEDART"), ("6949210969776", "DELHIVERY")],
            Hytrack3.extract_waybills("90700845933\n6949210969776"),
        )

    def test_ignores_phone_numbers(self) -> None:
        text = (
            "Please track 90700845933.\n"
            "Call me on +919876543210 or 919876543210, mobile: 02212345678"
        )
        self.assertEqual([("90700845933", "BLUEDART")], Hytrack3.extract_waybills(text))

    def test_accepts_phone_shaped_number_with_courier_keyword(self) -> None:
        self.assertEqual(
            [("919876543210", "DELHIVERY")],
            Hytrack3.extract_waybills("Delhivery AWB: 919876543210"),
        )

    def test_ignores_longer_digit_runs_and_deduplicates(self) -> None:
        text = "Order 1234567890123456 / 90700845933 / 90700845933"
        self.assertEqual([("90700845933", "BLUEDART")], Hytrack3.extract_waybills(text))


class SenderTrustTests(ConfigPatchMixin, unittest.TestCase):
    def _msg(self, sender: str = "Alice <alice@example.com>", **headers: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = sender
        for key, value in headers.items():
            msg[key.replace("_", "-")] = value
        msg.set_content("90700845933")
        return msg

    def test_accepts_normal_sender(self) -> None:
        self.assertTrue(Hytrack3.is_trusted_sender(self._msg(), "alice@example.com"))

    def test_rejects_bounces_auto_replies_and_self(self) -> None:
        self.assertFalse(
            Hytrack3.is_trusted_sender(self._msg("MAILER-DAEMON@example.com"), "mailer-daemon@example.com")
        )
        self.assertFalse(
            Hytrack3.is_trusted_sender(self._msg(Auto_Submitted="auto-replied"), "alice@example.com")
        )
        self.assertFalse(Hytrack3.is_trusted_sender(self._msg(), "sender@example.com"))

    def test_rejects_failed_authentication(self) -> None:
        msg = self._msg(Authentication_Results="mx.example.com; spf=fail smtp.mailfrom=x; dmarc=fail")
        self.assertFalse(Hytrack3.is_trusted_sender(msg, "alice@example.com"))

    def test_accepts_when_any_authentication_passes(self) -> None:
        msg = self._msg(Authentication_Results="mx.example.com; spf=softfail; dkim=pass header.d=example.com")
        self.assertTrue(Hytrack3.is_trusted_sender(msg, "alice@example.com"))


class IngestionLimitTests(ConfigPatchMixin, unittest.TestCase):
    def _run(self, body: str, db: FakeDb, sender: str = "Sender <sender2@example.com>") -> FakeMailClient:
        msg = EmailMessage()
        msg["From"] = sender
        msg["Subject"] = "track"
        msg.set_content(body)
        mail_client = FakeMailClient(("OK", [b"1"]), {b"1": ("OK", [(b"1", msg.as_bytes())])})
        with patch("Hytrack3.imaplib.IMAP4_SSL", return_value=mail_client):
            Hytrack3.EmailService().fetch_new_waybills(db)
        return mail_client

    def test_caps_waybills_per_message(self) -> None:
        body = " ".join(str(90700845900 + i) for i in range(30))
        db = FakeDb()
        self._run(body, db)
        self.assertEqual(Hytrack3.Config.MAX_WAYBILLS_PER_EMAIL, len(db.records))

    def test_respects_active_cap_per_sender(self) -> None:
        db = FakeDb(active_for_sender=Hytrack3.Config.MAX_ACTIVE_PER_SENDER)
        mail_client = self._run("90700845933", db)
        self.assertEqual([], db.records)
        self.assertIn(b"1", mail_client.stored)

    def test_reads_waybill_from_subject(self) -> None:
        msg = EmailMessage()
        msg["From"] = "Sender <sender2@example.com>"
        msg["Subject"] = "90700845933"
        msg.set_content("thanks!")
        mail_client = FakeMailClient(("OK", [b"1"]), {b"1": ("OK", [(b"1", msg.as_bytes())])})
        db = FakeDb()
        with patch("Hytrack3.imaplib.IMAP4_SSL", return_value=mail_client):
            Hytrack3.EmailService().fetch_new_waybills(db)
        self.assertEqual([("90700845933", "BLUEDART", "sender2@example.com")], db.records)

    def test_untrusted_sender_is_marked_seen_but_ignored(self) -> None:
        db = FakeDb()
        mail_client = self._run("90700845933", db, sender="Mail Delivery <mailer-daemon@example.com>")
        self.assertEqual([], db.records)
        self.assertIn(b"1", mail_client.stored)


class NotificationDeliveryTests(ConfigPatchMixin, unittest.TestCase):
    def test_send_notification_reports_success_and_failure(self) -> None:
        smtp_client = MagicMock()
        smtp_client.__enter__.return_value = smtp_client
        smtp_client.has_extn.return_value = True
        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client):
            self.assertTrue(Hytrack3.EmailService().send_notification("b@example.com", "S", "<p>x</p>", "x"))

        smtp_client.sendmail.side_effect = OSError("boom")
        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client):
            self.assertFalse(Hytrack3.EmailService().send_notification("b@example.com", "S", "<p>x</p>", "x"))

    def test_send_notification_includes_plain_text_part(self) -> None:
        smtp_client = MagicMock()
        smtp_client.__enter__.return_value = smtp_client
        smtp_client.has_extn.return_value = True
        with patch("Hytrack3.smtplib.SMTP", return_value=smtp_client):
            Hytrack3.EmailService().send_notification("b@example.com", "S", "<p>x</p>", "plain body")
        raw = smtp_client.sendmail.call_args.args[2]
        self.assertIn("text/plain", raw)
        self.assertIn("text/html", raw)

    def test_failed_send_does_not_store_new_state(self) -> None:
        row = {"waybill": "90700845933", "last_event_hash": None, "recipient_email": "a@example.com", "courier": "BLUEDART"}
        tracker = SimpleNamespace(fetch_latest_event=MagicMock(return_value=dict(SAMPLE_EVENT)))
        db = SimpleNamespace(update_shipment=MagicMock())
        email_service = SimpleNamespace(send_notification=MagicMock(return_value=False))

        Hytrack3.process_shipment(row, tracker, db, email_service)

        email_service.send_notification.assert_called_once()
        db.update_shipment.assert_not_called()


class ExpiryTests(unittest.TestCase):
    def test_expires_never_found_and_idle_shipments_and_readd_revives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with Hytrack3.DatabaseManager(os.path.join(temp_dir, "t.db")) as db:
                db.setup()
                db.add_waybill("11111111111", "BLUEDART", "a@example.com")  # never found
                db.add_waybill("22222222222", "BLUEDART", "a@example.com")  # idle
                db.add_waybill("33333333333", "BLUEDART", "a@example.com")  # fresh
                db.update_shipment("22222222222", "somehash")
                conn = db._get_connection()
                conn.execute("UPDATE shipments SET last_updated = datetime('now', '-8 days') WHERE waybill = '11111111111'")
                conn.execute("UPDATE shipments SET last_updated = datetime('now', '-31 days') WHERE waybill = '22222222222'")
                conn.commit()

                self.assertEqual(2, db.expire_stale_shipments(unfound_days=7, idle_days=30))
                self.assertEqual(["33333333333"], [r["waybill"] for r in db.get_active_shipments()])
                self.assertEqual(1, db.count_active_for_sender("a@example.com"))

                db.add_waybill("11111111111", "BLUEDART", "a@example.com")
                self.assertEqual(2, len(db.get_active_shipments()))

    def test_setup_migrates_old_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "old.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE shipments (waybill TEXT PRIMARY KEY, courier TEXT, last_event_hash TEXT,"
                " is_delivered INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute("INSERT INTO shipments (waybill, courier) VALUES ('90700845933', 'BLUEDART')")
            conn.commit()
            conn.close()

            with Hytrack3.DatabaseManager(path) as db:
                db.setup()
                self.assertEqual(1, len(db.get_active_shipments()))


class RunLockTests(unittest.TestCase):
    def test_second_lock_is_refused_until_released(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "run.lock")
            first = Hytrack3.acquire_run_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(Hytrack3.acquire_run_lock(path))
            first.close()
            again = Hytrack3.acquire_run_lock(path)
            self.assertIsNotNone(again)
            again.close()


class LoggingSetupTests(unittest.TestCase):
    def test_import_does_not_attach_file_handlers(self) -> None:
        import logging as std_logging

        handlers = std_logging.getLogger().handlers + Hytrack3.logger.handlers
        self.assertFalse(any(isinstance(h, std_logging.FileHandler) for h in handlers))


class BlueDartHistoryTests(unittest.TestCase):
    def test_parses_full_history_and_shipment_meta(self) -> None:
        html = """
        <div id="SHIP90700845933"><table><tbody>
          <tr><th>Waybill No</th><td>90700845933</td></tr>
          <tr><th>From </th><td>Pune</td></tr>
          <tr><th>To </th><td>Quepem</td></tr>
          <tr><th>Expected Date of Delivery </th><td>19 Oct 2026</td></tr>
        </tbody></table></div>
        <div id="SCAN90700845933"><table><thead><tr><th>Location</th></tr></thead><tbody>
          <tr><td>Quepem</td><td>Shipment Arrived </td><td>18 Oct 2026</td><td>11:42</td></tr>
          <tr><td>Goa  Hub</td><td>Shipment Further Connected </td><td>18 Oct 2026</td><td>05:03</td></tr>
          <tr><td>Pune</td><td>Shipment Picked Up</td><td>16 Oct 2026</td><td>15:07</td></tr>
        </tbody></table></div>
        """
        session = MagicMock()
        session.get.return_value = SimpleNamespace(status_code=200, text=html)

        event = Hytrack3.BlueDartTracker("90700845933", session=session).fetch_latest_event()

        self.assertIsNotNone(event)
        self.assertEqual("Shipment Arrived", event["Details"])
        self.assertEqual(3, len(event["History"]))
        self.assertEqual("Pune", event["History"][-1]["Location"])
        self.assertEqual("Goa Hub", event["History"][1]["Location"])
        self.assertEqual(
            {"From": "Pune", "To": "Quepem", "Expected Delivery": "19 Oct 2026"}, event["Meta"]
        )


class DelhiveryTimelineTests(unittest.TestCase):
    def test_parse_timeline_date(self) -> None:
        self.assertEqual(("18 Oct", "11:42 am"), Hytrack3.parse_timeline_datetime("Reached\n18 Oct, 11:42 am"))
        self.assertEqual(("Sat, 18 Oct", "5:03 PM"), Hytrack3.parse_timeline_datetime("Sat, 18 Oct 5:03 PM in Goa"))
        self.assertEqual(("", ""), Hytrack3.parse_timeline_datetime("no timestamp here"))


class EmailDesignTests(unittest.TestCase):
    def test_html_renders_journey_log_newest_first_with_current_marker(self) -> None:
        html = Hytrack3.build_html_message("90700845933", dict(SAMPLE_EVENT))
        self.assertIn("Journey log", html)
        for place in ("Quepem", "Goa Hub", "Mumbai Hub", "Pune"):
            self.assertIn(place, html)
        self.assertLess(html.index("Goa Hub"), html.index("Mumbai Hub"))
        self.assertEqual(1, html.count('data-current="true"'))
        self.assertIn("19 Oct 2026", html)
        self.assertIn("nickcardoso.com", html)

    def test_html_without_history_still_shows_latest_event(self) -> None:
        event = {k: v for k, v in SAMPLE_EVENT.items() if k not in ("History", "Meta")}
        html = Hytrack3.build_html_message("90700845933", event)
        self.assertIn("Shipment Arrived", html)
        self.assertEqual(1, html.count('data-current="true"'))

    def test_html_truncates_long_history(self) -> None:
        event = dict(SAMPLE_EVENT)
        event["History"] = [
            {"Location": f"Hub {i}", "Details": "Moved", "Date": "1 Oct 2026", "Time": "10:00"} for i in range(20)
        ]
        html = Hytrack3.build_html_message("90700845933", event)
        self.assertIn("Hub 0", html)
        self.assertNotIn("Hub 19<", html)
        self.assertIn("earlier", html)

    def test_delivered_email_hides_expected_delivery(self) -> None:
        event = dict(SAMPLE_EVENT, Details="Shipment Delivered")
        html = Hytrack3.build_html_message("90700845933", event)
        self.assertNotIn("19 Oct 2026", html)
        self.assertIn("Delivered", html)

    def test_plain_text_version(self) -> None:
        text = Hytrack3.build_text_message("90700845933", dict(SAMPLE_EVENT))
        self.assertIn("Shipment Arrived", text)
        self.assertIn("Goa Hub", text)
        self.assertIn("https://example.com/track", text)


if __name__ == "__main__":
    unittest.main()
