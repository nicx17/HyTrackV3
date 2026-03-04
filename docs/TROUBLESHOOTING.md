# Troubleshooting Guide

## Common Issues

### 1. `Selenium` / `webdriver-manager` Errors
If you encounter errors related to Chrome or ChromeDriver:
- Ensure Google Chrome is installed on your system.
- Ensure the version of Chrome matches the ChromeDriver version (managed automatically by `webdriver-manager` usually).
- If on a server (headless):
  - Ensure `Xvfb` or similar is installed if not running purely headless (though the code uses `--headless`).

### 2. Email Connection Failed (`imaplib.IMAP4.error`)
- **Gmail Users**: You likely need to use an **App Password** instead of your regular password if 2FA is enabled.
  - Go to your Google Account > Security > App passwords.
  - Generate a new password for "Mail" and use that in your `.env` file.
- Check if IMAP is enabled in your email provider's settings.

### 3. Database Locked
- Only run one instance of `Hytrack3.py` at a time. The SQLite database file locks to prevent corruption.

### 4. No Emails Found
- The script looks for specific keywords or regex patterns in subject lines or bodies. Ensure your delivery emails match the expected format.
- Check if your email provider is filtering these emails to Spam.
