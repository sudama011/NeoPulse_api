# Installation & Operations Guide

## 1. Infrastructure Preparation

### Cloud-Init (Oracle Linux / Ubuntu 22.04)

Run this script on first boot to harden the server and install dependencies.yaml
#cloud-config
package_update: true
packages:

* docker.io
* docker-compose
* postgresql-client
* ufw

runcmd:

# Security: Allow only SSH and Telegram Webhooks

* ufw default deny incoming
* ufw allow 22/tcp
* ufw allow 443/tcp
* ufw enable

# Timezone to IST (Crucial for NSE)

* timedatectl set-timezone Asia/Kolkata


## 2. Configuration (`.env`)
Create a `.env` file in the root directory. **Never commit this to Git.**

```ini
# Kotak Neo Credentials
NEO_CONSUMER_KEY="your_consumer_key"
NEO_CONSUMER_SECRET="your_consumer_secret"
NEO_MOBILE="9999999999"
NEO_PASSWORD="your_password"
NEO_MPIN="123456"

# TOTP Generation
TOTP_SECRET="JBSWY3DPEHPK3PXP"

# Telegram Bot
TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
TELEGRAM_ADMIN_ID="987654321"

# Database
POSTGRES_USER="neopulse"
POSTGRES_PASSWORD="secure_password"
POSTGRES_DB="neopulse_db"
POSTGRES_HOST="neopulse-db"

```

## 3. Database Maintenance

### Backup Strategy

A cron job runs every 4 hours to backup the trade ledger.

```bash
# Script: /scripts/backup_db.sh
pg_dump -h localhost -U neopulse neopulse_db | gzip > /backups/db_$(date +%F_%H%M).sql.gz

```

### Log Rotation

Logs are rotated daily and kept for 7 days to manage disk space.

/var/log/neopulse/*.log {
daily
rotate 7
compress
missingok
}

## 4. Morning Drill (08:00 AM)

The system automatically runs `scripts/sync_master.py` to ensure token mappings are fresh.

**Workflow:**

1. Downloads the Scrip Master CSV from Kotak Neo API.
2. Filters for NIFTY 50 and F&O tokens (to reduce database bloat).
3. Updates the `instrument_master` table in PostgreSQL.
