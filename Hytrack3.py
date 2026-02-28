import imaplib
import email
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import smtplib
import os
import logging
import sqlite3
import hashlib
from datetime import datetime
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from dotenv import load_dotenv


class Config:
    """Configuration class to manage environment variables and application constants."""

    load_dotenv()
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    IMAP_SERVER = os.getenv("IMAP_SERVER")
    IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
    EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
    SMTP_SERVER = os.getenv("SMTP_SERVER")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    DB_FILE = os.path.join(BASE_DIR, "hytrack3.db")
    LOG_FILE = os.path.join(BASE_DIR, "hytrack3.log")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    REGEX_BLUEDART = r"\b\d{11}\b"
    REGEX_DELHIVERY = r"\b\d{12,14}\b"
    REQUEST_TIMEOUT = 15

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


logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(Config.LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class DatabaseManager:
    """Context manager handling SQLite operations for tracking shipment states."""

    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_file)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def setup(self):
        """Initializes database schema and handles necessary migrations."""
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS shipments (
            waybill TEXT PRIMARY KEY,
            courier TEXT, 
            last_event_hash TEXT,
            is_delivered INTEGER NOT NULL DEFAULT 0,
            recipient_email TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
        logger.info("Database table 'shipments' initialized")

        cursor.execute("PRAGMA table_info(shipments)")
        columns = [column[1] for column in cursor.fetchall()]
        if "recipient_email" not in columns:
            logger.warning("Migrating database: adding recipient_email column")
            cursor.execute("ALTER TABLE shipments ADD COLUMN recipient_email TEXT")
            logger.info("Database migration completed")

        self.conn.commit()

    def add_waybill(self, waybill, courier_type, recipient_email):
        """Inserts a new waybill or updates an existing one to mark it as undelivered."""
        query = """
            INSERT INTO shipments (waybill, courier, recipient_email, is_delivered) 
            VALUES (?, ?, ?, 0)
            ON CONFLICT(waybill) DO UPDATE SET 
                last_event_hash = CASE WHEN is_delivered = 1 THEN NULL ELSE last_event_hash END,
                is_delivered = 0,
                recipient_email = excluded.recipient_email,
                last_updated = CURRENT_TIMESTAMP
        """
        self.conn.cursor().execute(query, (waybill, courier_type, recipient_email))
        self.conn.commit()
        logger.info(
            "Tracked waybill (New/Updated): courier=%s waybill=%s recipient=%s",
            courier_type,
            waybill,
            recipient_email,
        )

    def get_active_shipments(self):
        """Retrieves all shipments that are currently in transit."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT waybill, courier, last_event_hash, recipient_email FROM shipments WHERE is_delivered = 0"
        )
        return cursor.fetchall()

    def update_shipment(self, waybill, event_hash, is_delivered=False):
        """Updates the tracking status hash and delivery state for a specific waybill."""
        self.conn.cursor().execute(
            """
        UPDATE shipments
        SET last_event_hash = ?, is_delivered = ?, last_updated = CURRENT_TIMESTAMP
        WHERE waybill = ?
        """,
            (event_hash, 1 if is_delivered else 0, waybill),
        )
        self.conn.commit()


class BrowserManager:
    """Context manager for initializing and cleaning up a headless Selenium Chrome WebDriver."""

    def __init__(self):
        self.driver = None

    def __enter__(self):
        logger.info("Configuring headless Chrome WebDriver environments...")
        options = Options()

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
            service = Service("/usr/bin/chromedriver")
        else:
            logger.debug(
                f"x86 architecture ({arch}) detected. Installing via webdriver_manager..."
            )
            service = Service(ChromeDriverManager().install())

        self.driver = webdriver.Chrome(service=service, options=options)
        logger.info("Chrome WebDriver initialized and ready for scraping.")
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver is not None:
            logger.info("Tearing down Chrome WebDriver session.")
            self.driver.quit()


class BlueDartTracker:
    """Tracker implementation for fetching Blue Dart shipment statuses."""

    def __init__(self, waybill, session=None):
        self.waybill = waybill
        self.url = f"https://www.bluedart.com/trackdartresultthirdparty?trackFor=0&trackNo={waybill}"

        if session:
            self.session = session
        else:
            # Initialize session with retry mechanism
            self.session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retries))
            self.session.mount("http://", HTTPAdapter(max_retries=retries))

    def fetch_latest_event(self, **kwargs):
        """Fetches and parses the latest tracking event from Blue Dart."""
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

            soup = BeautifulSoup(response.text, "html.parser")
            container = soup.find("div", id=f"SCAN{self.waybill}")
            if not container:
                logger.warning(
                    "No Blue Dart tracking data found: waybill=%s", self.waybill
                )
                return None

            row = container.find("table").find("tbody").find_all("tr")[0]
            cols = row.find_all("td")

            logger.debug(
                "Blue Dart status parsed successfully: waybill=%s", self.waybill
            )
            return {
                "Courier": "Blue Dart",
                "Location": cols[0].text.strip(),
                "Details": cols[1].text.strip(),
                "Date": cols[2].text.strip(),
                "Time": cols[3].text.strip(),
                "Link": self.url,
            }
        except Exception:
            logger.exception("Blue Dart fetch failed: waybill=%s", self.waybill)
            return None


class DelhiveryTracker:
    """Tracker implementation for fetching Delhivery shipment statuses using Selenium."""

    def __init__(self, waybill):
        self.waybill = waybill
        self.url = f"https://www.delhivery.com/track-v2/package/{self.waybill}"

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
                    "Date": datetime.now().strftime("%Y-%m-%d"),
                    "Time": datetime.now().strftime("%H:%M"),
                    "Link": self.url,
                }

            # If not delivered, parsing active timeline (In-Transit states)
            row = driver.find_element(
                By.XPATH,
                f"{dot_xpath}/ancestor::div[contains(@class, 'flex') and contains(@class, 'gap-4')][1]",
            )

            status = row.find_element(
                By.XPATH, ".//span[contains(@style, 'font-weight: 600')]"
            ).text.strip()

            try:
                desc = row.find_element(
                    By.XPATH,
                    ".//div[contains(@class, 'text-[#525B7A]') or contains(@class, 'font-[400]')]",
                ).text.strip()
            except Exception:
                desc = "Update available"

            return {
                "Courier": "Delhivery",
                "Location": "Tracking Timeline",
                "Details": f"{status}: {desc}",
                "Date": datetime.now().strftime("%Y-%m-%d"),
                "Time": datetime.now().strftime("%H:%M"),
                "Link": self.url,
            }

        except Exception:
            logger.exception("Delhivery fetch failed: waybill=%s", self.waybill)
            return None


class EmailService:
    """Service for handling IMAP email ingestion and SMTP notification dispatches."""

    def __init__(self):
        self.config = Config()

    def _get_email_content(self, msg):
        """Extracts plain text content from a potentially multipart email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode(errors="ignore")

            # Fallback for HTML-only multipart messages
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html_content = part.get_payload(decode=True).decode(errors="ignore")
                    return BeautifulSoup(html_content, "html.parser").get_text(
                        separator=" "
                    )
            return ""

        # Handle non-multipart emails natively
        payload = msg.get_payload(decode=True).decode(errors="ignore")
        if msg.get_content_type() == "text/html":
            return BeautifulSoup(payload, "html.parser").get_text(separator=" ")
        return payload

    def fetch_new_waybills(self, db):
        """Scans unread emails to extract Blue Dart and Delhivery waybills using regex."""
        found_data = []  # Just for memory logging
        try:
            logger.info("Connecting to IMAP server")
            mail = imaplib.IMAP4_SSL(Config.IMAP_SERVER, Config.IMAP_PORT)
            mail.login(Config.EMAIL_ADDRESS, Config.EMAIL_PASSWORD)
            logger.info("IMAP authentication successful")
            mail.select("INBOX")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                logger.info("No unseen emails")
                return []

            unseen_count = len(messages[0].split())
            logger.info("Found unseen emails: count=%s", unseen_count)

            for num in messages[0].split():
                _, msg_data = mail.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                sender_raw = msg.get("From", "")
                sender_name, parsed_email = parseaddr(sender_raw)

                # Fallback to configured recipient if sender email parsing fails
                sender_email = parsed_email if parsed_email else Config.RECIPIENT_EMAIL

                content = self._get_email_content(msg)

                bd_waybills = re.findall(Config.REGEX_BLUEDART, content)
                dh_waybills = re.findall(Config.REGEX_DELHIVERY, content)

                # Check for uniqueness to avoid sending to db duplicate queries
                wb_set_for_msg = set()

                for wb in bd_waybills:
                    if wb not in wb_set_for_msg:
                        wb_set_for_msg.add(wb)
                        found_data.append((wb, "BLUEDART", sender_email))
                        db.add_waybill(wb, "BLUEDART", sender_email)
                        logger.debug(
                            "Extracted Blue Dart waybill: waybill=%s sender=%s",
                            wb,
                            sender_email,
                        )

                for wb in dh_waybills:
                    if wb not in wb_set_for_msg:
                        wb_set_for_msg.add(wb)
                        found_data.append((wb, "DELHIVERY", sender_email))
                        db.add_waybill(wb, "DELHIVERY", sender_email)
                        logger.debug(
                            "Extracted Delhivery waybill: waybill=%s sender=%s",
                            wb,
                            sender_email,
                        )

                # Mark as seen ONLY AFTER the DB records have been stored!
                mail.store(num, "+FLAGS", "\\Seen")

            mail.logout()
            logger.info("Email scan complete: extracted_waybills=%s", len(found_data))
        except Exception:
            logger.exception("IMAP fetch failed")
        return found_data

    def send_notification(self, recipient, subject, html_content):
        """Constructs and sends an HTML notification email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = Config.EMAIL_ADDRESS
        msg["To"] = recipient
        msg.attach(MIMEText(html_content, "html"))

        try:
            logger.debug(
                "Connecting to SMTP server: recipient=%s subject=%s", recipient, subject
            )
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_ADDRESS, Config.EMAIL_PASSWORD)
                server.sendmail(Config.EMAIL_ADDRESS, recipient, msg.as_string())
            logger.info(
                "Notification sent: recipient=%s subject=%s", recipient, subject
            )
        except Exception:
            logger.exception(
                "SMTP send failed: recipient=%s subject=%s", recipient, subject
            )


def build_html_message(waybill, event):
    """Generates the HTML payload for shipment notification emails."""
    courier = event.get("Courier", "Unknown")
    details = event.get("Details", "")
    location = event.get("Location", "")
    date = event.get("Date", "")
    time = event.get("Time", "")
    link = event.get("Link", "#")

    del_txt = details.lower()
    is_delivered = (
        "delivered" in del_txt
        and "failed" not in del_txt
        and "unable" not in del_txt
        and "out for delivery" not in del_txt
    )

    bg_charcoal = "#171717"
    card_bg = "#212121"
    text_white = "#E5E5E5"
    text_muted = "#999999"
    accent_sand = "#D1C7BD"
    accent_coral = "#D97757"
    border_color = "#333333"

    status_label = "Completed" if is_delivered else "In Progress"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400;500&family=Inter:wght@400;500&display=swap');

            body {{ margin: 0; padding: 0; font-family: 'Inter', sans-serif; background-color: {bg_charcoal}; color: {text_white}; -webkit-font-smoothing: antialiased; }}
            .wrapper {{ width: 100%; background-color: {bg_charcoal}; padding: 30px 0; }}
            .container {{ max-width: 520px; margin: 0 auto; padding: 0 15px; box-sizing: border-box; }}
            
            .card {{ 
                background-color: {card_bg}; 
                border: 1px solid {border_color}; 
                border-radius: 24px; 
                padding: 40px; 
                box-shadow: 0 10px 40px rgba(0,0,0,0.4);
            }}

            .sparkle {{ color: {accent_coral}; font-size: 24px; margin-bottom: 20px; }}
            .header-info {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.2em; color: {text_muted}; margin-bottom: 24px; }}

            .main-status {{ 
                font-family: 'Source Serif 4', serif; 
                font-size: 32px; 
                line-height: 1.15; 
                color: {accent_sand}; 
                margin: 0 0 16px 0; 
                font-weight: 400;
            }}

            .location-bubble {{ 
                display: inline-block;
                background-color: rgba(255, 255, 255, 0.03); 
                border: 1px solid {border_color};
                padding: 12px 18px; 
                border-radius: 16px 16px 16px 4px; 
                font-size: 14px; 
                color: {text_white};
                margin-bottom: 35px;
                line-height: 1.4;
            }}

            .data-grid {{ border-top: 1px solid {border_color}; padding-top: 25px; margin-bottom: 35px; }}
            .data-item {{ margin-bottom: 20px; }}
            .data-label {{ font-size: 9px; color: {text_muted}; text-transform: uppercase; letter-spacing: 0.15em; display: block; margin-bottom: 4px; }}
            .data-value {{ font-family: ui-monospace, 'SFMono-Regular', Menlo, Monaco, Consolas, monospace; font-size: 13px; color: {accent_sand}; }}

            .action-btn {{ 
                display: block; 
                text-align: center;
                background-color: transparent; 
                color: {text_white}; 
                border: 1px solid {border_color}; 
                padding: 16px 24px; 
                border-radius: 14px; 
                text-decoration: none; 
                font-size: 14px; 
                font-weight: 500;
            }}
            
            @media screen and (max-width: 480px) {{
                .wrapper {{ padding: 20px 0; }}
                .card {{ padding: 30px 20px; border-radius: 20px; }}
                .main-status {{ font-size: 26px; }}
                .location-bubble {{ font-size: 13px; padding: 10px 14px; }}
                .action-btn {{ padding: 14px 20px; }}
            }}

            .footer {{ text-align: center; margin-top: 30px; font-size: 9px; color: {text_muted}; letter-spacing: 0.25em; }}
        </style>
    </head>
    <body>
        <div class="wrapper">
            <div class="container">
                <div class="card">
                    <div class="sparkle">✷</div>
                    <div class="header-info">{courier} • {status_label}</div>
                    
                    <h1 class="main-status">{details}</h1>
                    <div class="location-bubble">📍 {location}</div>

                    <div class="data-grid">
                        <div class="data-item">
                            <span class="data-label">Reference</span>
                            <span class="data-value">{waybill}</span>
                        </div>
                        <div class="data-item">
                            <span class="data-label">Update Time</span>
                            <span class="data-value">{date} — {time}</span>
                        </div>
                    </div>

                    <a href="{link}" class="action-btn">Track live update ↗</a>
                </div>
                <div class="footer">HYTRACK</div>
            </div>
        </div>
    </body>
    </html>
    """


def process_shipment(row, tracker, db, email_service, **kwargs):
    """Executes the tracking verification, status comparison, and notification logic for a single shipment."""
    waybill = row["waybill"]
    last_hash = row["last_event_hash"]
    target_recipient = row["recipient_email"]
    courier = row["courier"]

    logger.debug("Processing shipment: courier=%s waybill=%s", courier, waybill)
    event = tracker.fetch_latest_event(**kwargs)
    if not event:
        logger.warning(
            "Failed to fetch tracking event: courier=%s waybill=%s", courier, waybill
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

        del_txt = event["Details"].lower()
        is_delivered = (
            "delivered" in del_txt
            and "failed" not in del_txt
            and "unable" not in del_txt
            and "out for delivery" not in del_txt
        )

        status_text = "Delivered" if is_delivered else "In Transit"
        subject = f"{status_text} | {event['Courier']} | {waybill}"

        msg_html = build_html_message(waybill, event)
        email_service.send_notification(target_recipient, subject, msg_html)

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


def main():
    """Application entry point handling data retrieval, tracking execution, and cleanup."""
    Config.validate()

    logger.info("=" * 60)
    logger.info("Starting shipment tracker")
    logger.info("=" * 60)

    # Database connection managed for the whole execution
    with DatabaseManager(Config.DB_FILE) as db:
        email_service = EmailService()
        db.setup()

        logger.info("Phase 1/4: Email ingestion")
        # Db records are stored within this method securely before seen flags are toggled
        email_service.fetch_new_waybills(db)

        logger.info("Phase 2/4: Retrieve active shipments")
        active = db.get_active_shipments()

        if not active:
            logger.info("No active shipments to track")
            logger.info("=" * 60)
            logger.info("Tracker completed successfully")
            return

        logger.info("Active shipments to process: count=%s", len(active))

        logger.info("Phase 3/4: Process Blue Dart shipments")
        bd_rows = [s for s in active if s["courier"] == "BLUEDART"]
        if bd_rows:
            logger.info("Processing Blue Dart shipments: count=%s", len(bd_rows))

            # Setup shared session for efficiency
            bd_session = requests.Session()
            retries = Retry(
                total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
            )
            bd_session.mount("https://", HTTPAdapter(max_retries=retries))

            for row in bd_rows:
                process_shipment(
                    row,
                    BlueDartTracker(row["waybill"], session=bd_session),
                    db,
                    email_service,
                )
        else:
            logger.info("No Blue Dart shipments")

        logger.info("Phase 4/4: Process Delhivery shipments")
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
        else:
            logger.info("No Delhivery shipments")

    logger.info("=" * 60)
    logger.info("Tracker completed successfully")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
