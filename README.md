# HyTrackV3

[![GitHub Release](https://img.shields.io/github/v/release/nicx17/HyTrackV3?style=flat-square)](https://github.com/nicx17/HyTrackV3/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com/)

An automated logistics intelligence tracker that ingests emails, parses Blue Dart and Delhivery waybills, and tracks their delivery status using Selenium and BeautifulSoup.

## Table of Contents
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [License](#license)

## Features
- **Automated Email Ingestion**: Connects to your email server via IMAP to automatically fetch tracking waybills.
- **Multi-Courier Support**: Currently supports Blue Dart and Delhivery packages.
- **Browser Automation**: Uses headless Chrome via Selenium for dynamic tracking status fetching.
- **Smart Database Management**: Utilizes SQLite to manage and track active shipments.
- **Notification System**: Automatically sends out an HTML-formatted delivery status update via SMTP.

## Prerequisites
- Python 3.x
- Google Chrome installed (for Selenium)
- Valid IMAP/SMTP credentials

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/nicx17/HyTrackV3.git
   cd HyTrackV3
   ```

2. Set up a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install selenium webdriver-manager beautifulsoup4 requests python-dotenv
   ```

## Configuration

Copy the sample environment file to `.env` and fill in your details:
```bash
cp sample.env.txt .env
```

> [!WARNING]
> Please handle your API keys and email credentials with care. Never commit your `.env` file to version control. It contains sensitive credentials such as your IMAP and SMTP passwords. Ensure your `.gitignore` correctly ignores the `.env` file.

> [!NOTE]
> Ensure that Chrome is installed on the machine running this application, as Selenium relies on it for headless browser operations.

## Usage

Run the main tracking script:
```bash
python Hytrack3.py
```

## License

This project is licensed under the GNU General Public License v3.0. Please see the [LICENSE](LICENSE) file for more information.
