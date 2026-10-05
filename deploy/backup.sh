#!/usr/bin/env bash
# Consistent copy of the SQLite database (safe while the service is running).
# Run nightly by cron (see setup-pi.sh); keeps the newest KEEP copies in backups/.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$DIR/data/attendance.db"
OUT="$DIR/backups"
KEEP="${KEEP:-30}"

mkdir -p "$OUT"
TARGET="$OUT/attendance-$(date +%F).db"
sqlite3 "$DB" ".backup '$TARGET'"
echo "$(date '+%F %T') backup written: $TARGET"

ls -1t "$OUT"/attendance-*.db | tail -n +"$((KEEP + 1))" | xargs -r rm --
