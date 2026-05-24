#!/usr/bin/env bash
# Backs up regime.db with a timestamped copy. Keeps the last 14 backups.
# Usage: run via cron or launchd daily.

set -euo pipefail

DB_PATH="${1:-$(dirname "$0")/../data/regime.db}"
BACKUP_DIR="$(dirname "$DB_PATH")/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
  echo "No database at $DB_PATH — skipping."
  exit 0
fi

# Use sqlite3 .backup for a consistent snapshot (safe even while the app is running)
if command -v sqlite3 &>/dev/null; then
  sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/regime_${TIMESTAMP}.db'"
else
  cp "$DB_PATH" "$BACKUP_DIR/regime_${TIMESTAMP}.db"
fi

echo "Backed up to $BACKUP_DIR/regime_${TIMESTAMP}.db"

# Prune old backups, keep latest 14
cd "$BACKUP_DIR"
ls -1t regime_*.db 2>/dev/null | tail -n +15 | xargs -r rm -f

echo "Backup complete. $(ls -1 regime_*.db 2>/dev/null | wc -l | tr -d ' ') backups retained."
