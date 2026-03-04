# HyTrackV3

![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge&logo=activity&logoColor=white)
![GitHub Release](https://img.shields.io/github/v/release/nicx17/HyTrackV3?include_prereleases&style=for-the-badge&logo=github&color=FF90C3)
![Python](https://img.shields.io/badge/Python_3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=for-the-badge&logo=selenium&logoColor=white)
![License](https://img.shields.io/github/license/nicx17/HyTrackV3?style=for-the-badge&color=007EC6)

**HyTrackV3** is an automated logistics intelligence tracker designed to streamline package tracking. It connects to your email server via IMAP, intelligently parses waybills for supported couriers (currently Blue Dart and Delhivery), and uses Selenium with BeautifulSoup to fetch real-time delivery statuses.

## Try It Now

You can use our hosted instance to track your packages immediately.

Simply email your **Blue Dart** or **Delhivery** tracking numbers (waybills) to:

> **notify@hyclotron.com**

The system will ingest your tracking number, start monitoring the package, and send you beautiful, HTML-formatted status updates directly to your inbox whenever the status changes.

### Email Previews

Here is what the automated status updates look like:

<p align="center">
  <img src="assets/delivered_delhivery.png" width="45%" alt="Delivered Status (Delhivery)" />
  <img src="assets/out_for_delivery_delhivery.png" width="45%" alt="Out for Delivery" />
  <br>
  <img src="assets/on_the_way.png" width="45%" alt="In Transit" />
  <img src="assets/delivered_bluedart.png" width="45%" alt="Delivered Status (Blue Dart)" />
</p>

## Features

-   **Automated Email Ingestion**: Seamlessly connects to your email provider to discover new tracking numbers automatically.
-   **Multi-Courier Support**: Built-in support for major Indian logistics providers:
    -   **Blue Dart** (via Requests/BeautifulSoup)
    -   **Delhivery** (via Selenium Headless Browser)
-   **Smart Tracking**:
    -   Uses headless Chrome for dynamic content.
    -   Handles session management and retries for robust fetching.
-   **Database Persistence**: Maintains a local SQLite database (`hytrack3.db`) to track shipment history and avoid duplicate processing.
-   **Automated Notifications**: Sends HTML-formatted email updates whenever a shipment status changes.

## Prerequisites

Before running HyTrackV3 locally, ensure you have the following installed:

-   **Python 3.8+**
-   **Google Chrome** (latest stable version)
-   **Git**

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/nicx17/HyTrackV3.git
    cd HyTrackV3
    ```

2.  **Set up a virtual environment**:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *Note: This will install `selenium`, `beautifulsoup4`, `requests`, `webdriver-manager`, and other required packages.*

## Configuration

HyTrackV3 requires environment variables to connect to your email accounts.

1.  **Copy the sample configuration**:
    ```bash
    cp sample.env.txt .env
    ```

2.  **Edit `.env`**:
    Open the `.env` file and fill in your details.

    ```ini
    IMAP_SERVER=imap.gmail.com
    EMAIL_ADDRESS=your_email@gmail.com
    EMAIL_PASSWORD=your_app_password
    ...
    ```

    > **Note for Gmail Users**: You must use an **App Password** if 2-Step Verification is enabled. See the [Configuration Guide](docs/CONFIGURATION.md) for detailed instructions.

## Usage

Run the tracker manually:

```bash
python Hytrack3.py
```

### Automation (Cron Job)

To run the tracker periodically (e.g., every hour), add a cron job:

```bash
crontab -e
```

Add the following line (adjust paths as necessary):

```bash
0 * * * * /path/to/HyTrackV3/.venv/bin/python /path/to/HyTrackV3/Hytrack3.py >> /path/to/HyTrackV3/cron.log 2>&1
```

## Project Structure

```plaintext
HyTrackV3/
├── assets/             # Images and static assets
├── docs/               # Documentation files
├── Hytrack3.py         # Main application script
├── requirements.txt    # Python dependencies
├── sample.env.txt      # Template for environment variables
├── README.md           # Project documentation
└── hytrack3.db         # SQLite database (created on first run)
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

## License

Distributed under the GPL-3.0 License. See `LICENSE` for more information.

