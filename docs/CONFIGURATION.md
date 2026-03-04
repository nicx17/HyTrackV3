# Configuration Guide

HyTrackV3 uses environment variables for configuration. Create a `.env` file in the root directory modeled after `sample.env.txt`.

## Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `IMAP_SERVER` | Address of your email provider's IMAP server. | `imap.gmail.com` |
| `IMAP_PORT` | Port for IMAP (usually 993 for SSL). | `993` |
| `EMAIL_ADDRESS` | Your email address for fetching tracking numbers. | `user@example.com` |
| `EMAIL_PASSWORD` | Password or App Password for the email account. | `abcd1234efgh5678` |
| `SMTP_SERVER` | Address of your email provider's SMTP server. | `smtp.gmail.com` |
| `SMTP_PORT` | Port for SMTP (usually 587 for TLS). | `587` |
| `RECIPIENT_EMAIL` | Email address to receive status updates. | `user@example.com` |
| `LOG_LEVEL` | Logging verbosity (DEBUG, INFO, WARNING, ERROR). | `INFO` |

## Setting up Gmail

1.  **Enable IMAP**: Go to Gmail Settings > Forwarding and POP/IMAP > Enable IMAP.
2.  **App Password**: If you use 2-Step Verification (recommended):
    -   Go to [Google Account Security](https://myaccount.google.com/security).
    -   Under "Signing in to Google," select "App passwords."
    -   Select "Mail" and your device, then "Generate."
    -   Use the 16-character password in your `.env` file as `EMAIL_PASSWORD`.

## Multiple Couriers

The regex patterns for detecting tracking numbers are defined in `Hytrack3.py`.
- **Blue Dart**: looks for 11-digit numbers.
- **Delhivery**: looks for 12-14 digit numbers.

If you need to add more couriers, you will need to modify the `REGEX_*` constants and the `process_shipment` logic in `Hytrack3.py`.
