
# 📦 HyTrackV3: Autonomous Shipment Tracker

**A headless tracking engine that monitors emails, scrapes carrier sites, and sends real-time status notifications.**

HyTrackV3 is designed for users who want a centralized, self-hosted solution to monitor multiple shipments without manually checking tracking numbers. It automatically "sniffs" new tracking numbers from your inbox, tracks them via Blue Dart or Delhivery, and emails you beautifully formatted HTML updates when a package moves.

---

## 🛠 Features

* **Automatic Discovery:** Scans your Gmail/Outlook inbox for new Blue Dart (11-digit) and Delhivery (12-14 digit) tracking numbers.
* **Smart Change Detection:** Uses SHA-256 hashing to compare shipment "fingerprints," ensuring you only get notified when the status actually changes.
* **Hybrid Scraper:** - **Requests + BeautifulSoup:** Fast, lightweight scraping for Blue Dart.
* **Selenium (Headless Chrome):** Handles dynamic JavaScript rendering for Delhivery.


* **Persistent Database:** SQLite backend tracks active shipments and prevents duplicate notifications.
* **Mobile-First Notifications:** Sends modern, brand-colored HTML emails with direct tracking links and status icons.

---

## 🏗 System Architecture

1. **Ingestion:** Script logs into IMAP server  Extracts waybills via Regex.
2. **Tracking:** * Blue Dart  `requests` + `BeautifulSoup`.
* Delhivery  `Selenium` (Headless).


3. **Analysis:** Compares current status hash vs. Database hash.
4. **Notification:** If hash differs, trigger **SMTP** to send a mobile-responsive HTML card.

---

## 🚀 Setup & Installation

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

## 📦 Database Schema

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

## 📱 Live Notification Sequence

HyTrackV3 converts raw courier data into clean, chronological mobile notifications. Below is the actual progression of a shipment as tracked and delivered by the system:

| **1. Order Detected** | **2. Picked Up** | **3. In Transit** |
| --- | --- | --- |
| <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-20-obfuscated" src="https://github.com/user-attachments/assets/bbdecc44-731c-4307-8484-6faed22bd924" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-17-obfuscated" src="https://github.com/user-attachments/assets/ec277659-c6d4-4242-bd23-009733916d8b" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-15-obfuscated" src="https://github.com/user-attachments/assets/2a601dec-518b-406a-853d-40114d5fc5af" /> |
| **4. Facility Arrival** | **5. Sorting Center** | **6. Processing** |
| <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-10-obfuscated" src="https://github.com/user-attachments/assets/0355d68a-dd66-4115-b126-403a1728da46" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-07-obfuscated" src="https://github.com/user-attachments/assets/ce9e98a6-52a5-41c4-8e2c-7556a1815a3b" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-04-obfuscated" src="https://github.com/user-attachments/assets/f429216c-1aaf-42f2-bac6-55553bc3441d" /> |
| **7. Out for Hub** | **8. Last-Mile Facility** | **9. Local Sorting** |
|<img width="714" height="876" alt="Screenshot From 2026-02-18 21-03-01-obfuscated" src="https://github.com/user-attachments/assets/70c1ca60-ef29-4b72-8e66-b67241e85484" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-02-58-obfuscated" src="https://github.com/user-attachments/assets/62b2517d-581d-4edd-8d71-e32539380786" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-02-55-obfuscated" src="https://github.com/user-attachments/assets/7f44233b-d811-4a4b-befa-3006ff9d7e3f" /> |
| **10. Out for Delivery** | **11. Final Mile** | **12. Delivered ✅** |
| <img width="714" height="876" alt="Screenshot From 2026-02-18 21-02-51-obfuscated" src="https://github.com/user-attachments/assets/70c5071a-8a27-4e6a-9071-bad3baedf38c" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-02-47-obfuscated" src="https://github.com/user-attachments/assets/4545a0e9-4675-4dd5-94f2-0fbbbac3c96d" /> | <img width="714" height="876" alt="Screenshot From 2026-02-18 21-02-40-obfuscated" src="https://github.com/user-attachments/assets/625b6ab4-439c-4794-814a-1a00c1901f29" /> |

---
