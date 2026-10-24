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

## Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SMTP_TIMEOUT` | Seconds to wait on the SMTP server. | `30` |
| `IMAP_TIMEOUT` | Seconds to wait on the IMAP server before giving up until the next run. | `60` |
| `LOG_MAX_BYTES` | Size at which `hytrack3.log` is rotated. | `5242880` (5 MB) |
| `LOG_BACKUP_COUNT` | Rotated log files to keep (`hytrack3.log.1` ...). | `3` |
| `MAX_WAYBILLS_PER_EMAIL` | Waybills accepted from a single email. | `10` |
| `MAX_ACTIVE_PER_SENDER` | Shipments tracked at once for one sender. | `25` |
| `UNFOUND_EXPIRY_DAYS` | Stop tracking a waybill the courier never recognised after this many days. | `7` |
| `IDLE_EXPIRY_DAYS` | Stop tracking a shipment whose status hasn't changed for this many days. | `30` |

Emailing an expired waybill again resumes tracking it.

## Setting up Gmail

1.  **Enable IMAP**: Go to Gmail Settings > Forwarding and POP/IMAP > Enable IMAP.
2.  **App Password**: If you use 2-Step Verification (recommended):
    -   Go to [Google Account Security](https://myaccount.google.com/security).
    -   Under "Signing in to Google," select "App passwords."
    -   Select "Mail" and your device, then "Generate."
    -   Use the 16-character password in your `.env` file as `EMAIL_PASSWORD`.

## Multiple Couriers

Waybills are detected by `extract_waybills` in `Hytrack3.py`, from the email subject and body:
- **Blue Dart**: 11-digit numbers.
- **Delhivery**: 12-14 digit numbers.

Numbers shaped like phone numbers (`91` + a 10-digit mobile, or a leading `0`), or preceded by words like "phone" or "call", are skipped unless a courier word ("AWB", "waybill", "tracking", "Blue Dart", "Delhivery") appears just before them.

If you need to add more couriers, add a tracker class, extend `extract_waybills`, and add a processing phase in `run_tracker`.
