# attendance

Tracks when employees start and finish work, using the face-recognition events from a Hikvision **DS-K1T343MWX** terminal. Sends daily and monthly reports to Telegram.

The terminal does the face matching itself. This service only collects its events over the ISAPI HTTP API and turns them into work time.

## How it works

- **Collector**: keeps a live connection to the terminal's alert stream for instant events. Every `RECONCILE_MINUTES` it also re-reads the terminal's own event log to fill gaps, for example while the service was down. On first start it imports `BACKFILL_DAYS` of history. The employee list is synced from the terminal every hour.
- **Storage**: SQLite (`data/attendance.db`). Raw events are stored deduplicated by the terminal's serial number, and work time is always recalculated from them, so rule changes apply to past data too.
- **Work-time rule**: the first scan of a workday is the start and the last scan is the end. A workday runs from `DAY_CUTOFF` (default 04:00) to the same time the next day, so night shifts aren't split. A single scan is flagged as incomplete and counts 0 hours.
- **Telegram**: commands `/today`, `/yesterday`, `/day YYYY-MM-DD`, `/month [YYYY-MM]` (summary + Excel file), `/employee NAME [DAYS | FROM [TO]]` (one employee's history, last 7 days by default; without a name it shows buttons to pick one) and `/status`. A daily report is sent at `DAILY_REPORT_TIME`, and the previous month's report on the 1st at `MONTHLY_REPORT_TIME`. Optional "arrived at 09:02" messages.

## Project layout

```
src/attendance/
  cli.py            command line (run, sync, status, day, month)
  config.py         settings from .env
  hikvision/        terminal integration: ISAPI client (client.py), alert-stream parsing (multipart.py)
  domain/           pure business rules, no I/O: event model (events.py), workdays and totals (worktime.py)
  storage/          SQLite persistence (database.py)
  services/         long-running jobs: live stream + log reconciliation (collector.py)
  reports/          daily.py, monthly.py, excel.py, shared helpers in common.py
  bot/              Telegram bot: commands, scheduled reports, notifications (app.py)
tests/              one test file per layer; shared fixtures in factories.py
```

Dependencies point inwards: `domain` imports nothing from the project; `storage`, `hikvision` and `reports` build on it; `services`, `bot` and `cli` wire everything together.

## Setup

Requires [uv](https://docs.astral.sh/uv/); it provides Python and the dependencies.

1. Fill in `.env` (see `.env.example` for all settings). At minimum set `HIK_PASSWORD`. Using a dedicated non-admin user on the terminal is recommended.
2. Check the connection and import data:
   ```
   uv run attendance status
   uv run attendance sync
   uv run attendance day
   ```
3. Telegram: create a bot with [@BotFather](https://t.me/BotFather) and put the token in `TELEGRAM_BOT_TOKEN`. Start the service, send `/start` to the bot (or in the group you added it to), and copy the chat id it replies with into `TELEGRAM_REPORT_CHAT_IDS`. Restart the service.
4. Run it:
   ```
   uv run attendance run
   ```
   Without a bot token, `run` only collects events.

## Database

SQLite, a single file at `DB_PATH` (default `data/attendance.db`). There is no server to install. The file and its tables are created automatically by the first command that opens it (`sync`, `run`, `day`, ...). The schema is in `src/attendance/storage/database.py`.

| Table | Contents |
|---|---|
| `employees` | People enrolled on the terminal (synced hourly); `active = 0` once removed from the terminal |
| `events` | Face events from the terminal, one row per event, deduplicated by `serial_no`; `raw` holds the original JSON (incl. `pictureURL`) |
| `meta` | Service state, e.g. `synced_until` (how far the terminal log has been imported) |

Times are stored twice: `time` as the terminal reported it, and `ts` as Unix seconds for range queries.

### Viewing the data

A web UI ([sqlite-web](https://github.com/coleifer/sqlite-web)) runs in Docker:

```
docker compose up -d      # http://localhost:8080
docker compose down
```

It opens the database **read-only** and only listens on localhost. Browse tables in *Content*; run SQL in *Query*, for example:

```sql
SELECT e.name, substr(time, 1, 10) AS day, min(substr(time, 12, 5)) AS first, max(substr(time, 12, 5)) AS last, count(*) AS scans
FROM events e WHERE minor = 75 AND employee_no != ''
GROUP BY employee_no, day ORDER BY day DESC, first;
```

The database uses SQLite's classic rollback journal rather than WAL on purpose. WAL relies on shared memory, which isn't shared between Windows and the Docker VM, so a containerised reader could fail or damage the index. To edit data, stop the service first and use a desktop tool such as DB Browser for SQLite.

## Other commands

```
uv run attendance day 2026-10-01
uv run attendance month 2026-10 --xlsx october.xlsx
uv run attendance employee egor 30
uv run pytest
```

## Deployment (Raspberry Pi)

The service runs on a Raspberry Pi in the office network, managed by systemd. Code goes laptop → git remote → Pi over SSH. [Tailscale](https://tailscale.com/) on both machines gives SSH access from anywhere without opening ports.

**One-time setup on the Pi** (Raspberry Pi OS 64-bit, SSH enabled):

```
git clone <repo-url> ~/attendance
# from the laptop: scp .env <user>@attendance-pi:~/attendance/.env
bash ~/attendance/deploy/setup-pi.sh
```

`setup-pi.sh` installs git, sqlite3 and uv, installs the dependencies and checks the terminal connection. It then registers and starts the `attendance` systemd service (restarts on failure and on boot), and adds a nightly database backup to `backups/` (the last 30 are kept). The script is safe to re-run.

**Each deploy, from the laptop:**

```
.\deploy.ps1                       # git push, then pull + uv sync + restart on the Pi
.\deploy.ps1 -PiHost 100.64.0.5 -PiUser egor
.\deploy.ps1 -Logs                 # follow the service log
```

Only committed code is deployed. `.env` and `data/` stay on the Pi and are never in git.

Run only **one** instance per bot token. Telegram rejects a second poller with `Conflict`, so stop the laptop copy once the Pi is live, or use a separate test bot for local debugging.

## Notes

- The terminal's clock is the source of truth. Make sure NTP time sync is enabled on it.
- Hikvision locks an account after repeated failed logins. If the credentials are rejected, the service waits 5 minutes before retrying.
- Only face events are stored: successful matches count as scans, failed recognitions are kept for reference. Door, card-reader and other terminal events are dropped.
- Face templates stay on the terminal. This service only stores names, employee numbers and timestamps.
