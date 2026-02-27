# HyTrackV3: Autonomous Shipment Tracker

**A headless tracking engine that monitors emails, scrapes carrier sites, and sends real-time status notifications.**

HyTrackV3 is designed for users who want a centralized, self-hosted solution to monitor multiple shipments without manually checking tracking numbers. It automatically "sniffs" new tracking numbers from your inbox, tracks them via Blue Dart or Delhivery, and emails you beautifully formatted HTML updates when a package moves.

---

## Features

* **Automatic Discovery:** Scans your Gmail/Outlook inbox for new Blue Dart (11-digit) and Delhivery (12-14 digit) tracking numbers.
* **Smart Change Detection:** Uses SHA-256 hashing to compare shipment "fingerprints," ensuring you only get notified when the status actually changes.
* **Hybrid Scraper:** - **Requests + BeautifulSoup:** Fast, lightweight scraping for Blue Dart.
* **Selenium (Headless Chrome):** Handles dynamic JavaScript rendering for Delhivery.


* **Persistent Database:** SQLite backend tracks active shipments and prevents duplicate notifications.
* **Mobile-First Notifications:** Sends modern, brand-colored HTML emails with direct tracking links and status icons.

---

## System Architecture

1. **Ingestion:** Script logs into IMAP server  Extracts waybills via Regex.
2. **Tracking:** * Blue Dart  `requests` + `BeautifulSoup`.
* Delhivery  `Selenium` (Headless).


3. **Analysis:** Compares current status hash vs. Database hash.
4. **Notification:** If hash differs, trigger **SMTP** to send a mobile-responsive HTML card.

---

## Setup & Installation

### 1. Prerequisites

* **Python 3.10+**
* **Google Chrome** & **Chromedriver** (installed in `/usr/bin/chromedriver` for RPi5/Linux)
* A dedicated email account (or App Password) for IMAP/SMTP.

### 2. Environment Variables

Create a `.env` file in the root directory:

```env
# IMAP (Incoming)
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
EMAIL_ADDRESS=your-email@gmail.com
EMAIL_PASSWORD=your-app-password

# SMTP (Outgoing)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
RECIPIENT_EMAIL=where-to-send-alerts@gmail.com

```

### 3. Installation

```bash
git clone https://github.com/nicx17/HyTrackV3.git
cd HyTrackV3
pip install -r requirements.txt

```

---

## Database Schema

The system manages state using a single-table SQLite database:
| Column | Type | Description |
| :--- | :--- | :--- |
| `waybill` | TEXT (PK) | The tracking number. |
| `courier` | TEXT | BLUEDART or DELHIVERY. |
| `last_event_hash` | TEXT | SHA-256 hash of the last known status. |
| `is_delivered` | INTEGER | Boolean flag (0/1) to stop tracking. |

---

## 🖥 Deployment (Raspberry Pi 5)

To run this automatically every hour, add a Cron job:

```bash
crontab -e
# Add the following line to run every hour:
0 * * * * /usr/bin/python3 /path/to/HyTrackV3/main.py

```
Got it. You've provided updated links for the tracking sequence. I've organized them into a clean  grid that flows chronologically from the first detection to the final delivery.

Copy the block below directly into your **README.md**:

---

## Live Notification Sequence

HyTrackV3 converts raw courier data into clean, chronological mobile notifications. Below is the actual progression of a shipment as tracked and delivered by the system:

| **1. Order Delivered** | **2. In Transit** | **3. In Transit** | **3. In Transit** |
| --- | --- | --- | --- |
| <img width="714" height="876" alt="Screenshot From 2026-02-27 20-22-53-obfuscated" src="https://github.com/user-attachments/assets/a4b09509-fb2e-4d1f-af85-cca9a8f23cc1" /> | <img width="714" height="876" alt="Screenshot From 2026-02-27 20-23-16-obfuscated" src="https://github.com/user-attachments/assets/647c40f1-b6c1-495b-a20c-fe5bf17090db" /> | <img width="714" height="876" alt="Screenshot From 2026-02-27 20-23-32-obfuscated" src="https://github.com/user-attachments/assets/6c220c8f-f5db-48f6-9c03-1e084d223d4d" /> |  <img width="714" height="876" alt="Screenshot From 2026-02-27 20-23-50-obfuscated" src="https://github.com/user-attachments/assets/ccde6a5b-609b-4a4f-8967-4c7d6bcaea68" /> |

---
