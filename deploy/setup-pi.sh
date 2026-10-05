#!/usr/bin/env bash
# One-time setup on the Raspberry Pi (Raspberry Pi OS 64-bit). Safe to run again.
#
#   git clone <repo-url> ~/attendance
#   scp .env <user>@<pi>:~/attendance/.env      # from the laptop
#   bash ~/attendance/deploy/setup-pi.sh
#
# Installs system packages and uv, installs dependencies, registers the systemd service
# and a nightly database backup, then starts the service.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$DIR"

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git sqlite3 curl

echo "==> uv"
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
"$UV" --version

echo "==> Dependencies"
"$UV" sync --frozen --no-dev

if [ ! -f .env ]; then
    echo "!! $DIR/.env is missing. Copy it from the laptop (scp .env $USER_NAME@<pi>:$DIR/.env) and run this script again."
    exit 1
fi

echo "==> Checking the terminal connection"
"$UV" run --frozen --no-dev attendance status

echo "==> systemd service"
sed -e "s|__USER__|$USER_NAME|" -e "s|__DIR__|$DIR|" -e "s|__UV__|$UV|" deploy/attendance.service \
    | sudo tee /etc/systemd/system/attendance.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable attendance
sudo systemctl restart attendance

echo "==> Nightly backup (03:15)"
chmod +x deploy/backup.sh
CRON_LINE="15 3 * * * $DIR/deploy/backup.sh >> $DIR/backups/backup.log 2>&1"
mkdir -p backups
( crontab -l 2>/dev/null | grep -v "deploy/backup.sh" ; echo "$CRON_LINE" ) | crontab -

sleep 3
systemctl status attendance --no-pager -n 10
echo
echo "Done. Logs: journalctl -u attendance -f"
