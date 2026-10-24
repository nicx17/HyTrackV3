"""
HyTrackV3 - Automated Logistics Tracker

This application connects to an IMAP server to fetch delivery tracking numbers (Blue Dart, Delhivery)
from emails, tracks their status using Selenium and Requests, and sends email notifications upon
status updates. It maintains a local SQLite database of active shipments.

Usage:
    python Hytrack3.py

Environment Variables:
    See sample.env.txt for required configuration.
"""
import imaplib
import email
import re
from html import escape
import ssl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import smtplib
import os
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import hashlib
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

from selenium.webdriver import Chrome as SeleniumChrome
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from dotenv import load_dotenv


class Config:
    """Configuration class to manage environment variables and application constants."""

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    IMAP_SERVER = os.getenv("IMAP_SERVER")
    IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
    EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
    SMTP_SERVER = os.getenv("SMTP_SERVER")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    DB_FILE = os.path.join(BASE_DIR, "hytrack3.db")
    LOG_FILE = os.path.join(BASE_DIR, "hytrack3.log")
    LOCK_FILE = os.path.join(BASE_DIR, "hytrack3.lock")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 3))

    HTML_PARSER = "html.parser"
    TRACKING_EVENT_FIELDS = ("Courier", "Location", "Details", "Date", "Time", "Link")
    REQUEST_TIMEOUT = 15
    SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", 30))
    # Without a timeout a server that stops responding leaves the run hung forever
    IMAP_TIMEOUT = int(os.getenv("IMAP_TIMEOUT", 60))

    # Abuse limits for the public intake mailbox
    MAX_WAYBILLS_PER_EMAIL = int(os.getenv("MAX_WAYBILLS_PER_EMAIL", 10))
    MAX_ACTIVE_PER_SENDER = int(os.getenv("MAX_ACTIVE_PER_SENDER", 25))

    # Stop tracking waybills that never resolve, or that stop changing
    UNFOUND_EXPIRY_DAYS = int(os.getenv("UNFOUND_EXPIRY_DAYS", 7))
    IDLE_EXPIRY_DAYS = int(os.getenv("IDLE_EXPIRY_DAYS", 30))

    # Number of journey-log rows rendered in notification emails
    HISTORY_LIMIT = 8

    @classmethod
    def get_required_str(cls, setting_name: str) -> str:
        """Returns a required string config value with type-safe validation."""
        value = getattr(cls, setting_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"CRITICAL: Missing environment variable in .env: {setting_name}")
        return value

    @classmethod
    def validate(cls):
        """Validates that all critical environment variables are loaded."""
        required = [
            "IMAP_SERVER",
            "EMAIL_ADDRESS",
            "EMAIL_PASSWORD",
            "SMTP_SERVER",
            "RECIPIENT_EMAIL",
        ]
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(
                f"CRITICAL: Missing environment variables in .env: {', '.join(missing)}"
            )


logger = logging.getLogger("hytrack3")


def configure_logging():
    """Attaches the rotating file and console handlers. Called from main(), never at import."""
    root = logging.getLogger()
    if any(getattr(h, "_hytrack", False) for h in root.handlers):
        return

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        Config.LOG_FILE, maxBytes=Config.LOG_MAX_BYTES, backupCount=Config.LOG_BACKUP_COUNT
    )
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        handler._hytrack = True
        root.addHandler(handler)
    root.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))


def acquire_run_lock(lock_path):
    """Takes an exclusive, non-blocking lock so overlapping cron runs exit early.

    Returns the open lock file (keep it open for the run's lifetime), or None if
    another run already holds the lock.
    """
    handle = open(lock_path, "w")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def build_retry_session():
    """Creates a requests session that retries transient HTTP failures."""
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# --- Status classification ---------------------------------------------------

_EXCEPTION_STATUS = re.compile(
    r"undeliver|not\s+delivered|fail|unable|attempt|refused|cancel|return|\brto\b|lost|damage",
    re.IGNORECASE,
)
_OUT_FOR_DELIVERY_STATUS = re.compile(r"out\s+for\s+delivery", re.IGNORECASE)
_DELIVERED_STATUS = re.compile(r"\bdelivered\b", re.IGNORECASE)

STATUS_LABELS = {
    "delivered": "Delivered",
    "out_for_delivery": "Out for delivery",
    "exception": "Needs attention",
    "in_transit": "In transit",
}


def classify_status(details):
    """Maps a courier status line to delivered / out_for_delivery / exception / in_transit."""
    if _EXCEPTION_STATUS.search(details):
        return "exception"
    if _OUT_FOR_DELIVERY_STATUS.search(details):
        return "out_for_delivery"
    if _DELIVERED_STATUS.search(details):
        return "delivered"
    return "in_transit"


def is_delivered_status(details):
    """Determines whether a shipment event should be treated as delivered."""
    return classify_status(details) == "delivered"


def is_valid_tracking_event(event):
    """Checks whether a tracker returned the minimum expected event payload."""
    if not isinstance(event, dict):
        return False

    return all(event.get(field) for field in Config.TRACKING_EVENT_FIELDS)


# --- Waybill extraction ------------------------------------------------------

# 11 digits = Blue Dart, 12-14 digits = Delhivery; not part of a longer digit run or a +phone
_WAYBILL_CANDIDATE = re.compile(r"(?<![\d+])(\d{11,14})(?!\d)")
_COURIER_CONTEXT = re.compile(
    r"awb|waybill|tracking|track|blue\s*dart|delhivery|consignment|docket", re.IGNORECASE
)
_PHONE_CONTEXT = re.compile(r"\b(phone|mobile|mob|tel|call|whatsapp|contact|ph)\b", re.IGNORECASE)
_PHONE_SHAPED = re.compile(r"^(91[6-9]\d{9}|0\d{10})$")
_CONTEXT_WINDOW = 30


def extract_waybills(text):
    """Finds unique (waybill, courier) pairs in free text, skipping things that look like phone numbers."""
    found = []
    seen = set()
    for match in _WAYBILL_CANDIDATE.finditer(text):
        number = match.group(1)
        if number in seen:
            continue

        prefix = text[max(0, match.start() - _CONTEXT_WINDOW) : match.start()]
        looks_like_phone = _PHONE_SHAPED.match(number) or _PHONE_CONTEXT.search(prefix)
        if looks_like_phone and not _COURIER_CONTEXT.search(prefix):
            logger.debug("Skipping phone-like number: %s", number)
            continue

        seen.add(number)
        found.append((number, "BLUEDART" if len(number) == 11 else "DELHIVERY"))
    return found


# --- Sender checks -----------------------------------------------------------

_AUTOMATED_LOCAL_PARTS = {"mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply", "do-not-reply"}
_AUTH_RESULT = re.compile(r"\b(spf|dkim|dmarc)=(\w+)", re.IGNORECASE)


def is_trusted_sender(msg, sender_email):
    """Rejects bounces, auto-replies, our own mail, and senders whose authentication failed."""
    address = (sender_email or "").lower()
    if not address or address == (Config.EMAIL_ADDRESS or "").lower():
        return False
    if address.split("@", 1)[0] in _AUTOMATED_LOCAL_PARTS:
        return False

    auto_submitted = (msg.get("Auto-Submitted") or "no").strip().lower()
    if auto_submitted != "no":
        return False
    if (msg.get("Precedence") or "").strip().lower() in {"auto_reply", "junk"}:
        return False

    results = []
    for header in msg.get_all("Authentication-Results") or []:
        results.extend(verdict.lower() for _, verdict in _AUTH_RESULT.findall(str(header)))
    if "fail" in results and "pass" not in results:
        return False
    return True


class DatabaseManager:
    """Context manager handling SQLite operations for tracking shipment states."""

    def __init__(self, db_file):
        self.db_file = db_file
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "DatabaseManager":
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        self.conn = conn
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns the active database connection or raises if not initialized."""
        conn = self.conn
        if conn is None:
            raise RuntimeError("Database connection has not been initialized")
        return conn

    def setup(self):
        """Initializes database schema and handles necessary migrations."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS shipments (
            waybill TEXT PRIMARY KEY,
            courier TEXT,
            last_event_hash TEXT,
            is_delivered INTEGER NOT NULL DEFAULT 0,
            recipient_email TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expired INTEGER NOT NULL DEFAULT 0
        )
        """
        )
        logger.debug("Database table 'shipments' initialized")

        cursor.execute("PRAGMA table_info(shipments)")
        columns = [column[1] for column in cursor.fetchall()]
        migrations = {
            "recipient_email": "ALTER TABLE shipments ADD COLUMN recipient_email TEXT",
            "expired": "ALTER TABLE shipments ADD COLUMN expired INTEGER NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                logger.warning("Migrating database: adding %s column", column)
                cursor.execute(statement)

        conn.commit()

    def add_waybill(self, waybill, courier_type, recipient_email):
        """Inserts a new waybill, or revives an expired one. Delivered shipments are left alone."""
        query = """
            INSERT INTO shipments (waybill, courier, recipient_email, is_delivered)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(waybill) DO UPDATE SET
                recipient_email = excluded.recipient_email,
                last_updated = CURRENT_TIMESTAMP,
                expired = 0
            WHERE is_delivered = 0
        """
        conn = self._get_connection()
        conn.cursor().execute(query, (waybill, courier_type, recipient_email))
        conn.commit()
        logger.info(
            "Tracked waybill (New/Updated): courier=%s waybill=%s recipient=%s",
            courier_type,
            waybill,
            recipient_email,
        )

    def get_active_shipments(self):
        """Retrieves all shipments that are currently in transit."""
        cursor = self._get_connection().cursor()
        cursor.execute(
            "SELECT waybill, courier, last_event_hash, recipient_email FROM shipments"
            " WHERE is_delivered = 0 AND expired = 0"
        )
        return cursor.fetchall()

    def count_active_for_sender(self, recipient_email):
        """Counts shipments still being tracked on behalf of one sender."""
        cursor = self._get_connection().cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM shipments WHERE is_delivered = 0 AND expired = 0 AND recipient_email = ?",
            (recipient_email,),
        )
        return cursor.fetchone()[0]

    def expire_stale_shipments(self, unfound_days, idle_days):
        """Stops tracking waybills never found for `unfound_days`, or unchanged for `idle_days`."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
        UPDATE shipments SET expired = 1
        WHERE is_delivered = 0 AND expired = 0 AND (
            (last_event_hash IS NULL AND last_updated < datetime('now', ?))
            OR last_updated < datetime('now', ?)
        )
        """,
            (f"-{int(unfound_days)} days", f"-{int(idle_days)} days"),
        )
        conn.commit()
        return cursor.rowcount

    def update_shipment(self, waybill, event_hash, is_delivered=False):
        """Updates the tracking status hash and delivery state for a specific waybill."""
        conn = self._get_connection()
        conn.cursor().execute(
            """
        UPDATE shipments
        SET last_event_hash = ?, is_delivered = ?, last_updated = CURRENT_TIMESTAMP
        WHERE waybill = ?
        """,
            (event_hash, 1 if is_delivered else 0, waybill),
        )
        conn.commit()


class BrowserManager:
    """Context manager for initializing and cleaning up a headless Selenium Chrome WebDriver."""

    def __init__(self):
        self.driver = None

    def __enter__(self):
        logger.info("Configuring headless Chrome WebDriver environments...")
        options = ChromeOptions()

        # CRITICAL FIX: This must be active to run on a Pi without a display
        options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        # Check system architecture
        import platform

        arch = platform.machine()

        # aarch64/arm64 for 64-bit Pi OS, armv7l for 32-bit Pi OS
        if arch in ["aarch64", "arm64", "armv7l"]:
            logger.info(
                f"ARM architecture ({arch}) detected. Using system ChromeDriver."
            )

            # Use standard Pi OS binary paths
            options.binary_location = "/usr/bin/chromium-browser"
            service = ChromeService("/usr/bin/chromedriver")
        else:
            logger.debug(
                f"x86 architecture ({arch}) detected. Installing via webdriver_manager..."
            )
            service = ChromeService(ChromeDriverManager().install())

        self.driver = SeleniumChrome(service=service, options=options)
        self.driver.set_page_load_timeout(60)
        logger.info("Chrome WebDriver initialized and ready for scraping.")
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver is not None:
            logger.info("Tearing down Chrome WebDriver session.")
            self.driver.quit()


class BlueDartTracker:
    """Tracker implementation for fetching Blue Dart shipment statuses."""

    # Shipment Details table labels -> keys shown in the notification email
    META_FIELDS = {
        "From": "From",
        "To": "To",
        "Pickup Date": "Picked Up",
        "Expected Date of Delivery": "Expected Delivery",
    }

    def __init__(self, waybill, session=None):
        self.waybill = waybill
        self.url = f"https://www.bluedart.com/trackdartresultthirdparty?trackFor=0&trackNo={waybill}"
        self.session = session or build_retry_session()

    def _parse_meta(self, soup):
        """Reads origin, destination and expected delivery from the Shipment Details tab."""
        meta = {}
        container = soup.find("div", id=f"SHIP{self.waybill}")
        if not container:
            return meta
        for row in container.find_all("tr"):
            label, value = row.find("th"), row.find("td")
            if not label or not value:
                continue
            key = self.META_FIELDS.get(label.get_text(strip=True))
            if key and value.get_text(strip=True):
                meta[key] = value.get_text(strip=True)
        return meta

    def fetch_latest_event(self, **kwargs):
        """Fetches the latest tracking event from Blue Dart, with the full scan history attached."""
        try:
            logger.debug("Fetching Blue Dart status: waybill=%s", self.waybill)
            response = self.session.get(
                self.url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=Config.REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                logger.warning(
                    "Blue Dart returned non-200 response: waybill=%s status_code=%s",
                    self.waybill,
                    response.status_code,
                )
                return None

            soup = BeautifulSoup(response.text, Config.HTML_PARSER)
            container = soup.find("div", id=f"SCAN{self.waybill}")
            if not container:
                logger.warning(
                    "No Blue Dart tracking data found: waybill=%s", self.waybill
                )
                return None

            table = container.find("table")
            tbody = table.find("tbody") if table else None
            rows = tbody.find_all("tr") if tbody else []
            history = []
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue
                # Blue Dart pads names with runs of spaces ("Goa  Hub"); collapse them
                location, details, date, time = (" ".join(col.text.split()) for col in cols[:4])
                history.append({"Location": location, "Details": details, "Date": date, "Time": time})

            if not history:
                logger.warning(
                    "Blue Dart tracking table has no complete rows: waybill=%s", self.waybill
                )
                return None

            logger.debug(
                "Blue Dart status parsed successfully: waybill=%s scans=%s",
                self.waybill,
                len(history),
            )
            return {
                "Courier": "Blue Dart",
                **history[0],
                "Link": self.url,
                "History": history,
                "Meta": self._parse_meta(soup),
            }
        except requests.RequestException as exc:
            logger.warning(
                "Blue Dart unreachable: waybill=%s error=%s: %s",
                self.waybill,
                type(exc).__name__,
                exc,
            )
            return None
        except Exception:
            logger.exception("Blue Dart fetch failed: waybill=%s", self.waybill)
            return None


_TIMELINE_DATETIME = re.compile(
    r"((?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{4})?)"
    r"[,\s]+(\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)",
    re.IGNORECASE,
)


def parse_timeline_datetime(text):
    """Pulls a ("18 Oct", "11:42 am")-style date and time out of a timeline row's text."""
    match = _TIMELINE_DATETIME.search(text or "")
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


class DelhiveryTracker:
    """Tracker implementation for fetching Delhivery shipment statuses using Selenium."""

    STATUS_XPATH = ".//span[contains(@style, 'font-weight: 600')]"
    DESC_XPATH = ".//div[contains(@class, 'text-[#525B7A]') or contains(@class, 'font-[400]')]"
    ROW_ANCESTOR = "ancestor::div[contains(@class, 'flex') and contains(@class, 'gap-4')][1]"

    def __init__(self, waybill):
        self.waybill = waybill
        self.url = f"https://www.delhivery.com/track-v2/package/{self.waybill}"

    def _read_row(self, row):
        """Extracts status, description and timestamp from one timeline row."""
        status = row.find_element(By.XPATH, self.STATUS_XPATH).text.strip()
        try:
            desc = row.find_element(By.XPATH, self.DESC_XPATH).text.strip()
        except Exception:
            desc = ""
        date, time = parse_timeline_datetime(row.text)
        return {
            "Location": desc or "Tracking Timeline",
            "Details": status,
            "Date": date,
            "Time": time,
        }

    def _read_history(self, driver, current_row):
        """Best-effort read of every timeline row, newest first. Returns [] if the layout differs."""
        try:
            rows = driver.find_elements(
                By.XPATH, f"//span[contains(@style, 'font-weight: 600')]/{self.ROW_ANCESTOR}"
            )
            unique_rows = []
            for row in rows:
                if row not in unique_rows:
                    unique_rows.append(row)
            if unique_rows and unique_rows[-1] == current_row and unique_rows[0] != current_row:
                unique_rows.reverse()
            return [self._read_row(row) for row in unique_rows]
        except Exception:
            logger.debug("Delhivery timeline history unavailable: waybill=%s", self.waybill, exc_info=True)
            return []

    def _checked_now(self):
        now = datetime.now()
        return {"Date": now.strftime("%Y-%m-%d"), "Time": now.strftime("%H:%M"), "TimeIsCheckTime": True}

    def fetch_latest_event(self, driver=None):
        """Fetches and parses the latest tracking event from Delhivery via a live browser instance."""
        if not driver:
            logger.error("Delhivery tracker requires a Selenium driver instance")
            return None

        try:
            logger.debug("Fetching Delhivery status: waybill=%s", self.waybill)
            driver.get(self.url)

            wait = WebDriverWait(driver, 25)

            # Use an OR XPath query to wait for EITHER the delivery card OR the active timeline ping.
            # This drastically reduces scraping time for in-transit shipments by removing artificial timeouts.
            delivered_header_xpath = "//h2[contains(text(), 'Order Delivered')]"
            dot_xpath = "//span[contains(@class, 'animate-ping')]"
            combined_xpath = f"{delivered_header_xpath} | {dot_xpath}"

            wait.until(EC.presence_of_element_located((By.XPATH, combined_xpath)))

            if driver.find_elements(By.XPATH, delivered_header_xpath):
                logger.info("Delivered status card detected: waybill=%s", self.waybill)
                return {
                    "Courier": "Delhivery",
                    "Location": "Final Destination",
                    "Details": "Delivered: Your order has been delivered",
                    **self._checked_now(),
                    "Link": self.url,
                }

            # If not delivered, parsing active timeline (In-Transit states)
            row = driver.find_element(By.XPATH, f"{dot_xpath}/{self.ROW_ANCESTOR}")
            current = self._read_row(row)
            status = current["Details"]
            desc = current["Location"] if current["Location"] != "Tracking Timeline" else "Update available"

            event = {
                "Courier": "Delhivery",
                "Location": "Tracking Timeline",
                "Details": f"{status}: {desc}",
                "Link": self.url,
                "History": self._read_history(driver, row),
            }
            if current["Date"] and current["Time"]:
                event.update(Date=current["Date"], Time=current["Time"])
            else:
                event.update(self._checked_now())
            return event

        except TimeoutException:
            logger.warning("Delhivery page timed out: waybill=%s", self.waybill)
            return None
        except Exception:
            logger.exception("Delhivery fetch failed: waybill=%s", self.waybill)
            return None


class EmailService:
    """Service for handling IMAP email ingestion and SMTP notification dispatches."""

    @staticmethod
    def _decode_payload(part):
        """Decodes an email part payload safely, returning an empty string if absent."""
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(errors="ignore")

    def _get_email_content(self, msg):
        """Extracts plain text content from a potentially multipart email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return self._decode_payload(part)

            # Fallback for HTML-only multipart messages
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html_content = self._decode_payload(part)
                    return BeautifulSoup(html_content, Config.HTML_PARSER).get_text(
                        separator=" "
                    )
            return ""

        # Handle non-multipart emails natively
        payload = self._decode_payload(msg)
        if msg.get_content_type() == "text/html":
            return BeautifulSoup(payload, Config.HTML_PARSER).get_text(separator=" ")
        return payload

    def _process_unseen_message(self, mail, num, db, found_data):
        """Extracts and stores any new waybills found in a single unseen message."""
        status, msg_data = mail.fetch(num, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            logger.warning("Unable to fetch unseen email: message_id=%s", num.decode())
            return

        msg = email.message_from_bytes(msg_data[0][1])

        sender_raw = msg.get("From", "")
        _, parsed_email = parseaddr(sender_raw)

        # Fallback to configured recipient if sender email parsing fails
        sender_email = parsed_email if parsed_email else Config.RECIPIENT_EMAIL

        if not is_trusted_sender(msg, sender_email):
            logger.info("Ignoring automated or unauthenticated email: sender=%s", sender_email)
            mail.store(num, "+FLAGS", "\\Seen")
            return

        content = f"{msg.get('Subject', '')}\n{self._get_email_content(msg)}"
        waybills = extract_waybills(content)
        if len(waybills) > Config.MAX_WAYBILLS_PER_EMAIL:
            logger.warning(
                "Email has too many waybills, keeping first %s: sender=%s found=%s",
                Config.MAX_WAYBILLS_PER_EMAIL,
                sender_email,
                len(waybills),
            )
            waybills = waybills[: Config.MAX_WAYBILLS_PER_EMAIL]

        for waybill, courier_type in waybills:
            if db.count_active_for_sender(sender_email) >= Config.MAX_ACTIVE_PER_SENDER:
                logger.warning(
                    "Sender reached active shipment limit (%s), skipping the rest: sender=%s",
                    Config.MAX_ACTIVE_PER_SENDER,
                    sender_email,
                )
                break
            found_data.append((waybill, courier_type, sender_email))
            db.add_waybill(waybill, courier_type, sender_email)
            logger.debug(
                "Extracted %s waybill: waybill=%s sender=%s",
                courier_type.title(),
                waybill,
                sender_email,
            )

        # Mark as seen ONLY AFTER the DB records have been stored!
        mail.store(num, "+FLAGS", "\\Seen")

    def fetch_new_waybills(self, db):
        """Scans unread emails to extract Blue Dart and Delhivery waybills."""
        found_data = []  # Just for memory logging
        mail = None
        try:
            imap_server = Config.get_required_str("IMAP_SERVER")
            email_address = Config.get_required_str("EMAIL_ADDRESS")
            email_password = Config.get_required_str("EMAIL_PASSWORD")
            ssl_context = ssl.create_default_context()
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

            logger.debug("Connecting to IMAP server")
            mail = imaplib.IMAP4_SSL(
                imap_server,
                Config.IMAP_PORT,
                ssl_context=ssl_context,
                timeout=Config.IMAP_TIMEOUT,
            )
            mail.login(email_address, email_password)
            logger.debug("IMAP authentication successful")
            mail.select("INBOX")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                logger.debug("No unseen emails")
                return []

            unseen_count = len(messages[0].split())
            logger.info("Found unseen emails: count=%s", unseen_count)

            for num in messages[0].split():
                self._process_unseen_message(mail, num, db, found_data)

            logger.info("Email scan complete: extracted_waybills=%s", len(found_data))
        except (OSError, imaplib.IMAP4.abort) as exc:
            # DNS hiccups, dropped TLS sessions, timeouts: transient, the next run retries.
            logger.warning("IMAP unavailable, will retry next run: %s: %s", type(exc).__name__, exc)
        except Exception:
            logger.exception("IMAP fetch failed")
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    logger.debug("IMAP logout failed during cleanup", exc_info=True)
        return found_data

    def send_notification(self, recipient, subject, html_content, text_content=None):
        """Sends an HTML (plus plain-text) notification via SMTP. Returns True only if it was sent."""
        email_address = Config.get_required_str("EMAIL_ADDRESS")
        email_password = Config.get_required_str("EMAIL_PASSWORD")
        smtp_server = Config.get_required_str("SMTP_SERVER")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = email_address
        msg["To"] = recipient
        if text_content:
            msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        try:
            logger.debug(
                "Connecting to SMTP server: recipient=%s subject=%s", recipient, subject
            )
            with smtplib.SMTP(
                smtp_server, Config.SMTP_PORT, timeout=Config.SMTP_TIMEOUT
            ) as server:
                server.ehlo()
                if not server.has_extn("STARTTLS"):
                    logger.error(
                        "SMTP server does not support STARTTLS: server=%s port=%s",
                        smtp_server,
                        Config.SMTP_PORT,
                    )
                    return False

                tls_context = ssl.create_default_context()
                tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
                server.starttls(context=tls_context)
                server.ehlo()
                server.login(email_address, email_password)
                server.sendmail(email_address, recipient, msg.as_string())
            logger.info(
                "Notification sent: recipient=%s subject=%s", recipient, subject
            )
            return True
        except Exception:
            logger.exception(
                "SMTP send failed: recipient=%s subject=%s", recipient, subject
            )
            return False


# --- Notification email ------------------------------------------------------
# Styled after nickcardoso.com (ivory theme). Email clients ignore CSS variables and
# often strip <style>, so every colour is inlined; <style> only adds the mobile layout.

PALETTE = {
    "page": "#ece9e3",
    "card": "#f9f6f1",
    "panel": "#f0ece5",
    "border": "#d9dadb",
    "text": "#181d25",
    "muted": "#495566",
    "soft": "#5f6f83",
    "slate": "#253243",
    "slate_text": "#f4f7fb",
    "line": "#d3d6da",
    "dot": "#bfc4cb",
    "accent": "#e08a3c",
    "accent_text": "#a3561c",
}

STATUS_BADGES = {
    "delivered": ("#253243", "#f4f7fb"),
    "out_for_delivery": ("#f6e1c9", "#7a4214"),
    "in_transit": ("#e6ebf2", "#33445a"),
    "exception": ("#f4dcd6", "#8a2f22"),
}

FONT_SERIF = "ui-serif, Georgia, Cambria, 'Times New Roman', Times, serif"
FONT_SANS = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

_WHEN_FORMATS = ("%d %b %Y %H:%M", "%d %B %Y %H:%M", "%Y-%m-%d %H:%M", "%d %b %Y %I:%M %p")


def format_when(date, time):
    """Formats courier date/time strings like the journey log: '18 Oct, 11:42 am'."""
    raw = f"{date} {time}".strip()
    for fmt in _WHEN_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        hour = parsed.strftime("%I").lstrip("0")
        meridiem = "am" if parsed.hour < 12 else "pm"
        return f"{parsed.day} {parsed:%b}, {hour}:{parsed:%M} {meridiem}"
    return ", ".join(part for part in (date, time) if part)


def _journey(event):
    """Returns (rows to render, number of older rows hidden), newest first."""
    history = event.get("History") or [
        {key: event.get(key, "") for key in ("Location", "Details", "Date", "Time")}
    ]
    return history[: Config.HISTORY_LIMIT], max(0, len(history) - Config.HISTORY_LIMIT)


def _timeline_row(entry, position, total, delivered):
    """Renders one journey-log row: dot + connector line, timestamp, status and place."""
    p = PALETTE
    is_current = position == 0
    is_last = position == total - 1
    when = escape(format_when(entry.get("Date", ""), entry.get("Time", "")))
    details = escape(entry.get("Details", ""))
    location = escape(entry.get("Location", ""))

    # The connector is a 2px background gradient on the dot cell, so it stretches with the row.
    # Clients without gradient support simply show the dots.
    if total == 1:
        line = "none"
    elif is_current:
        line = f"linear-gradient({p['line']},{p['line']}) no-repeat 9px 14px / 2px 100%"
    elif is_last:
        line = f"linear-gradient({p['line']},{p['line']}) no-repeat 9px 0 / 2px 14px"
    else:
        line = f"linear-gradient({p['line']},{p['line']}) no-repeat 9px 0 / 2px 100%"

    if is_current:
        dot_color = p["slate"] if delivered else p["accent"]
        ring = "rgba(37,50,67,0.14)" if delivered else "rgba(224,138,60,0.22)"
        dot = (
            f'<div style="width:12px;height:12px;margin:7px 0 0 4px;border-radius:50%;'
            f'background:{dot_color};box-shadow:0 0 0 4px {ring};"></div>'
        )
        when_color, title_color = (p["slate"] if delivered else p["accent_text"]), p["text"]
    else:
        dot = (
            f'<div style="width:12px;height:12px;margin:7px 0 0 4px;border-radius:50%;'
            f'background:{p["dot"]};box-shadow:0 0 0 4px {p["panel"]};"></div>'
        )
        when_color, title_color = p["soft"], p["text"]

    pad = "0" if is_last else "18px"
    when_weight = "600" if is_current else "400"
    return f"""
              <tr data-current="{'true' if is_current else 'false'}">
                <td width="20" valign="top" style="width:20px;padding:0;background:{line};">{dot}</td>
                <td class="tl-when" valign="top" width="128" style="width:128px;padding:4px 12px {pad} 12px;font-family:{FONT_SANS};font-size:14px;line-height:1.45;color:{when_color};font-weight:{when_weight};white-space:nowrap;">{when}</td>
                <td valign="top" style="padding:3px 0 {pad} 4px;font-family:{FONT_SANS};">
                  <div class="tl-when-inline" style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:13px;line-height:1.4;color:{when_color};font-weight:{when_weight};">{when}</div>
                  <div style="font-size:15px;line-height:1.45;font-weight:600;color:{title_color};">{details}</div>
                  <div style="font-size:13px;line-height:1.45;color:{p['muted']};">{location}</div>
                </td>
              </tr>"""


def _meta_cells(meta, delivered):
    """Renders the route / expected-delivery summary under the headline."""
    p = PALETTE
    cells = []
    if meta.get("From") and meta.get("To"):
        cells.append(("Route", f"{escape(meta['From'])} &rarr; {escape(meta['To'])}"))
    if meta.get("Expected Delivery") and not delivered:
        cells.append(("Expected delivery", escape(meta["Expected Delivery"])))
    if not cells:
        return ""
    tds = "".join(
        f'<td class="meta-cell" valign="top" style="padding:0 18px 0 0;font-family:{FONT_SANS};">'
        f'<div style="font-size:12px;line-height:1.4;color:{p["soft"]};letter-spacing:0.02em;">{label}</div>'
        f'<div style="font-size:15px;line-height:1.5;color:{p["text"]};">{value}</div></td>'
        for label, value in cells
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 26px;">'
        f"<tr>{tds}</tr></table>"
    )


def build_html_message(waybill, event):
    """Generates the HTML notification: status headline, route summary and a journey log."""
    p = PALETTE
    state = classify_status(event.get("Details", ""))
    delivered = state == "delivered"
    badge_bg, badge_text = STATUS_BADGES[state]
    status_label = STATUS_LABELS[state]

    courier = escape(event.get("Courier", "Unknown"))
    details = escape(event.get("Details", ""))
    location = escape(event.get("Location", ""))
    link = escape(event.get("Link", "#"), quote=True)
    safe_waybill = escape(waybill)
    when = escape(format_when(event.get("Date", ""), event.get("Time", "")))
    when_label = "Checked" if event.get("TimeIsCheckTime") else "Updated"

    rows, hidden = _journey(event)
    timeline = "".join(_timeline_row(entry, i, len(rows), delivered) for i, entry in enumerate(rows))
    earlier = (
        f'<div style="padding:14px 0 0 32px;font-family:{FONT_SANS};font-size:13px;color:{p["soft"]};">'
        f"+ {hidden} earlier update{'s' if hidden != 1 else ''} on the courier site</div>"
        if hidden
        else ""
    )
    preheader = escape(f"{status_label}: {event.get('Details', '')} — {event.get('Location', '')}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>{status_label} &middot; {safe_waybill}</title>
  <style>
    @media screen and (max-width: 520px) {{
      .outer {{ padding: 20px 12px !important; }}
      .card {{ padding: 26px 20px !important; border-radius: 18px !important; }}
      .headline {{ font-size: 27px !important; }}
      .panel {{ padding: 18px 14px !important; }}
      .tl-when {{ display: none !important; }}
      .tl-when-inline {{ display: block !important; max-height: none !important; overflow: visible !important; }}
      .meta-cell {{ display: block !important; padding: 0 0 12px 0 !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:{p['page']};color:{p['text']};-webkit-font-smoothing:antialiased;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheader}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{p['page']};">
    <tr>
      <td class="outer" align="center" style="padding:36px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:580px;">
          <tr>
            <td style="padding:0 6px 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="font-family:{FONT_SERIF};font-size:20px;letter-spacing:0.02em;color:{p['text']};">HyTrack</td>
                  <td align="right" style="font-family:{FONT_SANS};">
                    <span style="display:inline-block;border:1px solid {p['border']};border-radius:999px;background:{p['card']};padding:3px 11px;font-size:12px;color:{p['muted']};">{courier}</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="card" style="background:{p['card']};border:1px solid {p['border']};border-radius:22px;padding:34px 30px;box-shadow:0 30px 80px -32px rgba(39,49,64,0.2);">
              <span style="display:inline-block;border-radius:999px;background:{badge_bg};color:{badge_text};padding:4px 12px;font-family:{FONT_SANS};font-size:12px;font-weight:600;letter-spacing:0.02em;">{status_label}</span>
              <h1 class="headline" style="margin:16px 0 8px;font-family:{FONT_SERIF};font-size:34px;line-height:1.15;font-weight:400;letter-spacing:-0.02em;color:{p['text']};">{details}</h1>
              <p style="margin:0 0 24px;font-family:{FONT_SANS};font-size:15px;line-height:1.6;color:{p['muted']};">{location} &middot; {when_label} {when}</p>
              {_meta_cells(event.get("Meta") or {}, delivered)}
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px;">
                <tr>
                  <td style="padding:0 8px 0 0;font-family:{FONT_SANS};">
                    <span style="display:inline-block;border-radius:999px;background:{p['slate']};color:{p['slate_text']};padding:9px 18px;font-size:14px;font-weight:500;">Journey log</span>
                  </td>
                  <td style="font-family:{FONT_SANS};">
                    <span style="display:inline-block;border:1px solid {p['border']};border-radius:999px;color:{p['muted']};padding:8px 17px;font-size:14px;">Waybill {safe_waybill}</span>
                  </td>
                </tr>
              </table>
              <div class="panel" style="background:{p['panel']};border-radius:14px;padding:22px 20px;margin:0 0 28px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{timeline}
                </table>
                {earlier}
              </div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" style="border-radius:999px;background:{p['slate']};">
                    <a href="{link}" style="display:block;padding:14px 24px;border-radius:999px;font-family:{FONT_SANS};font-size:15px;font-weight:500;color:{p['slate_text']};text-decoration:none;">Track on {courier} -&gt;</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:22px 12px 0;font-family:{FONT_SANS};font-size:12px;line-height:1.7;color:{p['soft']};">
              Reply with another waybill number to track it too.<br>
              HyTrack &middot; <a href="https://nickcardoso.com/projects/hytrackv3/" style="color:{p['muted']};text-decoration:underline;">nickcardoso.com</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def build_text_message(waybill, event):
    """Plain-text alternative for clients that don't render HTML."""
    state = classify_status(event.get("Details", ""))
    rows, hidden = _journey(event)
    meta = event.get("Meta") or {}
    lines = [
        f"{STATUS_LABELS[state]} - {event.get('Courier', '')} {waybill}",
        "",
        event.get("Details", ""),
        f"{event.get('Location', '')} - {format_when(event.get('Date', ''), event.get('Time', ''))}",
    ]
    if meta.get("From") and meta.get("To"):
        lines.append(f"Route: {meta['From']} -> {meta['To']}")
    if meta.get("Expected Delivery") and state != "delivered":
        lines.append(f"Expected delivery: {meta['Expected Delivery']}")
    lines += ["", "Journey log"]
    for entry in rows:
        when = format_when(entry.get("Date", ""), entry.get("Time", ""))
        lines.append(f"  {when:<20} {entry.get('Details', '')}, {entry.get('Location', '')}")
    if hidden:
        lines.append(f"  + {hidden} earlier updates")
    lines += ["", f"Track live: {event.get('Link', '')}", "", "HyTrack - nickcardoso.com"]
    return "\n".join(lines)


def process_shipment(row, tracker, db, email_service, **kwargs):
    """Executes the tracking verification, status comparison, and notification logic for a single shipment."""
    waybill = row["waybill"]
    last_hash = row["last_event_hash"]
    target_recipient = row["recipient_email"] or Config.RECIPIENT_EMAIL
    courier = row["courier"]

    logger.debug("Processing shipment: courier=%s waybill=%s", courier, waybill)
    event = tracker.fetch_latest_event(**kwargs)
    if not event:
        logger.warning(
            "Failed to fetch tracking event: courier=%s waybill=%s", courier, waybill
        )
        return
    if not is_valid_tracking_event(event):
        logger.warning(
            "Tracker returned invalid event payload: courier=%s waybill=%s",
            courier,
            waybill,
        )
        return

    # Generate a stable state fingerprint
    fingerprint = f"{event['Details']}{event['Location']}"
    current_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    if current_hash != last_hash:
        logger.info(
            "Tracking update detected: courier=%s waybill=%s recipient=%s",
            courier,
            waybill,
            target_recipient,
        )

        state = classify_status(event["Details"])
        is_delivered = state == "delivered"
        subject = f"{STATUS_LABELS[state]} · {event['Courier']} · {waybill}"

        sent = email_service.send_notification(
            target_recipient,
            subject,
            build_html_message(waybill, event),
            build_text_message(waybill, event),
        )
        if not sent:
            # Leave the stored hash untouched so the next run retries this notification.
            logger.warning(
                "Notification not sent, will retry next run: courier=%s waybill=%s",
                courier,
                waybill,
            )
            return

        db.update_shipment(waybill, current_hash, is_delivered)
        delivery_status = "DELIVERED" if is_delivered else "IN TRANSIT"
        logger.info(
            "Shipment state stored: courier=%s waybill=%s state=%s",
            courier,
            waybill,
            delivery_status,
        )
    else:
        logger.debug("No status change: courier=%s waybill=%s", courier, waybill)


def run_tracker():
    """Runs one ingestion + tracking pass."""
    # Database connection managed for the whole execution
    with DatabaseManager(Config.DB_FILE) as db:
        email_service = EmailService()
        db.setup()

        logger.debug("Phase 1/4: Email ingestion")
        # Db records are stored within this method securely before seen flags are toggled
        email_service.fetch_new_waybills(db)

        logger.debug("Phase 2/4: Retrieve active shipments")
        expired = db.expire_stale_shipments(Config.UNFOUND_EXPIRY_DAYS, Config.IDLE_EXPIRY_DAYS)
        if expired:
            logger.info("Stopped tracking stale shipments: count=%s", expired)
        active = db.get_active_shipments()

        if not active:
            logger.debug("No active shipments to track")
            return

        logger.info("Active shipments to process: count=%s", len(active))

        logger.debug("Phase 3/4: Process Blue Dart shipments")
        bd_rows = [s for s in active if s["courier"] == "BLUEDART"]
        if bd_rows:
            # Setup shared session for efficiency
            bd_session = build_retry_session()
            for row in bd_rows:
                process_shipment(
                    row,
                    BlueDartTracker(row["waybill"], session=bd_session),
                    db,
                    email_service,
                )

        logger.debug("Phase 4/4: Process Delhivery shipments")
        dl_rows = [s for s in active if s["courier"] == "DELHIVERY"]
        if dl_rows:
            logger.info("Processing Delhivery shipments: count=%s", len(dl_rows))
            with BrowserManager() as driver:
                for row in dl_rows:
                    process_shipment(
                        row,
                        DelhiveryTracker(row["waybill"]),
                        db,
                        email_service,
                        driver=driver,
                    )


def main():
    """Application entry point handling data retrieval, tracking execution, and cleanup."""
    configure_logging()
    Config.validate()

    lock = acquire_run_lock(Config.LOCK_FILE)
    if lock is None:
        logger.info("Previous run still in progress, skipping this one")
        return

    with lock:
        logger.debug("Starting shipment tracker")
        run_tracker()
        logger.debug("Tracker completed successfully")


if __name__ == "__main__":
    main()
