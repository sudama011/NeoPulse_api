#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/home/ubuntu/backups"
FILENAME="neopulse_db_$TIMESTAMP.sql.gz"

# Dump and Compress
PGPASSWORD=$DB_PASS pg_dump -h localhost -U neopulse_user neopulse_db | gzip > $BACKUP_DIR/$FILENAME

# Keep only last 10 backups
ls -tp $BACKUP_DIR/*.gz | grep -v '/$' | tail -n +11 | xargs -I {} rm -- {}

# Optional: Sync to S3 (requires aws-cli configured)
# aws s3 cp $BACKUP_DIR/$FILENAME s3://my-trading-backups/