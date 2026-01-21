import imaplib
import email
import re
import json
import requests
import smtplib
import os
import logging
import sqlite3
import time
import hashlib
from datetime import datetime

# --- Dependencies for Selenium (Delhivery) ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --- Dependencies for Email/Env ---
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

# ===================== 1. CONFIGURATION =====================

class Config:
    """Loads all configuration from environment variables and constants."""
    load_dotenv()

    # IMAP Settings
    IMAP_SERVER = os.getenv("IMAP_SERVER")
    IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
    EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

    # SMTP Settings
    SMTP_SERVER = os.getenv("SMTP_SERVER")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    # Script Constants
    DB_FILE = "Hytrack3_shipments.db"
    LOG_FILE = "Hytrack3_tracker.log"
    
    # Regex Patterns
    REGEX_BLUEDART = r"\b\d{11}\b"
    REGEX_DELHIVERY = r"\b\d{12,14}\b"
    
    REQUEST_TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_DELAY = 5 

# ===================== 2. LOGGING & DB =====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(Config.LOG_FILE), logging.StreamHandler()]
)

class DatabaseManager:
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
        with self as db:
            cursor = db.conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                waybill TEXT PRIMARY KEY,
                courier TEXT, 
                last_event_hash TEXT,
                is_delivered INTEGER NOT NULL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            db.conn.commit()

    def add_waybill(self, waybill, courier_type):
        try:
            with self as db:
                db.conn.cursor().execute(
                    "INSERT INTO shipments (waybill, courier) VALUES (?, ?)", 
                    (waybill, courier_type)
                )
                db.conn.commit()
                logging.info(f"Added new {courier_type} shipment: {waybill}")
        except sqlite3.IntegrityError:
            pass

    def get_active_shipments(self):
        with self as db:
            cursor = db.conn.cursor()
            cursor.execute("SELECT waybill, courier, last_event_hash FROM shipments WHERE is_delivered = 0")
            return cursor.fetchall()

    def update_shipment(self, waybill, event_hash, is_delivered=False):
        with self as db:
            db.conn.cursor().execute("""
            UPDATE shipments
            SET last_event_hash = ?, is_delivered = ?, last_updated = CURRENT_TIMESTAMP
            WHERE waybill = ?
            """, (event_hash, 1 if is_delivered else 0, waybill))
            db.conn.commit()

# ===================== 3. TRACKING LOGIC =====================

class BrowserManager:
    """Handles Headless Chrome for Delhivery"""
    def __init__(self):
        self.driver = None

    def __enter__(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--log-level=3")
        self.driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"),
            options=options
        )
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.quit()

class BlueDartTracker:
    def __init__(self, waybill):
        self.waybill = waybill
        self.url = f"https://www.bluedart.com/trackdartresultthirdparty?trackFor=0&trackNo={waybill}"

    def fetch_latest_event(self, **kwargs):
        try:
            response = requests.get(self.url, headers={"User-Agent": "Mozilla/5.0"}, timeout=Config.REQUEST_TIMEOUT)
            if response.status_code != 200: return None
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            container = soup.find("div", id=f"SCAN{self.waybill}")
            if not container: return None
            
            row = container.find("table").find("tbody").find_all("tr")[0]
            cols = row.find_all("td")
            
            return {
                "Courier": "Blue Dart",
                "Location": cols[0].text.strip(),
                "Details": cols[1].text.strip(),
                "Date": cols[2].text.strip(),
                "Time": cols[3].text.strip(),
                "Link": self.url
            }
        except Exception:
            return None

class DelhiveryTracker:
    def __init__(self, waybill):
        self.waybill = waybill
        self.url = f"https://www.delhivery.com/track-v2/package/{waybill}"

    def fetch_latest_event(self, driver=None):
        if not driver: return None
        try:
            driver.get(self.url)
            wait = WebDriverWait(driver, 20)
            wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'cursor-pointer')]")))
            
            blocks = driver.find_elements(By.XPATH, "//div[contains(@class,'pl-6') and contains(@class,'cursor-pointer')]")
            if not blocks: return None

            latest = blocks[0]
            status = latest.find_element(By.TAG_NAME, "h3").text.strip()
            desc = latest.find_element(By.TAG_NAME, "p").text.strip()
            
            return {
                "Courier": "Delhivery",
                "Location": "Check Link",
                "Details": f"{status}: {desc}",
                "Date": datetime.now().strftime("%Y-%m-%d"),
                "Time": datetime.now().strftime("%H:%M"),
                "Link": self.url
            }
        except Exception:
            return None

# ===================== 4. UI/UX EMAIL ENGINE =====================

class EmailService:
    def __init__(self):
        self.config = Config()

    def fetch_new_waybills(self):
        found_data = []
        try:
            mail = imaplib.IMAP4_SSL(Config.IMAP_SERVER, Config.IMAP_PORT)
            mail.login(Config.EMAIL_ADDRESS, Config.EMAIL_PASSWORD)
            mail.select("INBOX")
            
            status, messages = mail.search(None, 'UNSEEN')
            if status != "OK" or not messages[0]: return []

            for num in messages[0].split():
                _, msg_data = mail.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                content = self._get_email_content(msg)
                
                bd_matches = re.findall(Config.REGEX_BLUEDART, content)
                for wb in bd_matches: found_data.append((wb, "BLUEDART"))

                dl_matches = re.findall(Config.REGEX_DELHIVERY, content)
                for wb in dl_matches:
                    if wb not in bd_matches: found_data.append((wb, "DELHIVERY"))
                
                mail.store(num, '+FLAGS', '\\Seen')
            mail.logout()
        except Exception:
            pass
        return found_data

    def _get_email_content(self, msg):
        content = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    content += part.get_payload(decode=True).decode(errors="ignore")
        else:
            content = msg.get_payload(decode=True).decode(errors="ignore")
        return content

    def send_notification(self, subject, html_content):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = Config.EMAIL_ADDRESS
        msg["To"] = Config.RECIPIENT_EMAIL
        msg.attach(MIMEText(html_content, "html"))

        try:
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_ADDRESS, Config.EMAIL_PASSWORD)
                server.sendmail(Config.EMAIL_ADDRESS, Config.RECIPIENT_EMAIL, msg.as_string())
            logging.info(f"Notification sent: {subject}")
        except Exception as e:
            logging.error(f"SMTP Error: {e}")

def build_html_message(waybill, event):
    """
    Generates a modern, mobile-first email card.
    Uses courier-specific branding and safe HTML tables for maximum compatibility.
    """
    courier = event.get('Courier', 'Unknown')
    details = event.get('Details', '')
    location = event.get('Location', '')
    date = event.get('Date', '')
    time = event.get('Time', '')
    link = event.get('Link', '#')

    # --- 1. THEME LOGIC ---
    is_delivered = "delivered" in details.lower()
    
    if courier == "Blue Dart":
        theme_color = "#2563EB" # Modern Royal Blue
        bg_icon = "🔵"
    elif courier == "Delhivery":
        theme_color = "#DC2626" # Modern Vibrant Red
        bg_icon = "🔴"
    else:
        theme_color = "#4B5563" # Neutral Gray
        bg_icon = "📦"

    # Green status text if delivered, otherwise use brand color
    status_color = "#166534" if is_delivered else theme_color 
    status_emoji = "✅" if is_delivered else "🚚"

    # --- 2. HTML TEMPLATE ---
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Shipment Update</title>
        <style>
            /* Base Reset */
            body {{ margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #F3F4F6; color: #1F2937; }}
            
            /* Responsive Container */
            .wrapper {{ width: 100%; table-layout: fixed; background-color: #F3F4F6; padding-bottom: 40px; }}
            .email-card {{ max-width: 450px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05); }}
            
            /* Header */
            .header {{ background-color: {theme_color}; padding: 20px; text-align: center; }}
            .header-text {{ color: #ffffff; font-size: 16px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; margin: 0; }}
            
            /* Content Area */
            .content {{ padding: 32px 24px; }}
            
            /* Hero Status */
            .status-icon {{ font-size: 42px; display: block; margin-bottom: 10px; text-align: center; }}
            .status-text {{ font-size: 22px; font-weight: 800; color: {status_color}; text-align: center; margin: 0; line-height: 1.3; }}
            .status-sub {{ color: #6B7280; font-size: 14px; text-align: center; margin-top: 5px; margin-bottom: 30px; }}
            
            /* Data Grid (Table used for Outlook safety) */
            .data-table {{ width: 100%; border-collapse: separate; border-spacing: 0; background-color: #F9FAFB; border-radius: 12px; border: 1px solid #E5E7EB; }}
            .data-cell {{ padding: 12px 16px; border-bottom: 1px solid #E5E7EB; }}
            .data-cell-last {{ padding: 12px 16px; border-bottom: none; }}
            
            .label {{ font-size: 11px; color: #6B7280; text-transform: uppercase; font-weight: 700; display: block; margin-bottom: 2px; }}
            .value {{ font-size: 15px; color: #111827; font-weight: 500; display: block; }}
            
            /* Button */
            .btn-container {{ text-align: center; margin-top: 32px; }}
            .track-btn {{ display: inline-block; background-color: {theme_color}; color: #ffffff; padding: 16px 36px; font-size: 16px; font-weight: 700; text-decoration: none; border-radius: 99px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); transition: opacity 0.2s; }}
            
            /* Footer */
            .footer {{ text-align: center; padding-top: 24px; color: #9CA3AF; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="wrapper">
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding-top: 40px; padding-bottom: 40px;">
                        
                        <div class="email-card">
                            <div class="header">
                                <p class="header-text">{bg_icon} &nbsp; {courier} UPDATE</p>
                            </div>

                            <div class="content">
                                <div class="status-icon">{status_emoji}</div>
                                <h1 class="status-text">{details}</h1>
                                <p class="status-sub">{location}</p>

                                <table class="data-table">
                                    <tr>
                                        <td class="data-cell">
                                            <span class="label">Date & Time</span>
                                            <span class="value">{date} <span style="color:#9CA3AF">•</span> {time}</span>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td class="data-cell-last">
                                            <span class="label">Tracking Number</span>
                                            <span class="value" style="font-family: monospace; letter-spacing: 0.5px;">{waybill}</span>
                                        </td>
                                    </tr>
                                </table>

                                <div class="btn-container">
                                    <a href="{link}" class="track-btn">Track Package</a>
                                </div>
                            </div>
                        </div>

                        <div class="footer">
                            Automated by Hytrack V3
                        </div>

                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
    """

# ===================== 5. MAIN EXECUTION =====================

def main():
    db = DatabaseManager(Config.DB_FILE)
    email_service = EmailService()
    db.setup()

    new_items = email_service.fetch_new_waybills()
    for wb, courier in new_items:
        db.add_waybill(wb, courier)

    active_shipments = db.get_active_shipments()
    
    bd_shipments = [s for s in active_shipments if s['courier'] == 'BLUEDART']
    dl_shipments = [s for s in active_shipments if s['courier'] == 'DELHIVERY']

    if bd_shipments:
        for row in bd_shipments:
            process_shipment(row, BlueDartTracker(row['waybill']), db, email_service)

    if dl_shipments:
        with BrowserManager() as driver:
            for row in dl_shipments:
                tracker = DelhiveryTracker(row['waybill'])
                process_shipment(row, tracker, db, email_service, driver=driver)

def process_shipment(row, tracker, db, email_service, **kwargs):
    waybill = row['waybill']
    last_hash = row['last_event_hash']
    
    # 1. Fetch the data
    event = tracker.fetch_latest_event(**kwargs)
    if not event: return

    # 2. CREATE STABLE FINGERPRINT (The Fix)
    # Instead of hashing the whole event (which includes the changing time),
    # we only hash the 'Details' and 'Location' which define the shipment status.
    fingerprint_string = f"{event['Details']}{event['Location']}"
    current_hash = hashlib.sha256(fingerprint_string.encode('utf-8')).hexdigest()

    # 3. Compare
    if current_hash != last_hash:
        logging.info(f"Update detected for {waybill}")
        
        is_delivered = "delivered" in event['Details'].lower()
        subject = f"{'✅' if is_delivered else '📦'} {event['Courier']}: {event['Details'][:30]}..."
        
        email_service.send_notification(subject, build_html_message(waybill, event))
        db.update_shipment(waybill, current_hash, is_delivered)
    else:
        # Optional: Log that checks are passing but no change found
        # logging.info(f"No change for {waybill}")
        pass
if __name__ == "__main__":
    main()