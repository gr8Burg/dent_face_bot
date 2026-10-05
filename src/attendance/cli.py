"""Command line: `attendance run | sync | status | day [DATE] | month [YYYY-MM] [--xlsx FILE] | employee NAME [DAYS | FROM [TO]]`."""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

from .config import Config, load_config
from .domain.history import HistoryArgsError, find_employees, parse_history_args
from .domain.worktime import workday_of
from .hikvision import Device
from .reports import daily_report, employee_report, monthly_report
from .services.collector import Collector
from .storage import Database

log = logging.getLogger("attendance")


def _plain(text: str) -> str:
    """Telegram HTML → console text."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _device(config: Config) -> Device:
    return Device(config.device_url, config.hik_user, config.hik_password)


def _today(config: Config) -> date:
    return workday_of(datetime.now(config.tz), config.tz, config.day_cutoff)


async def _collect_only(config: Config, db: Database) -> None:
    device = _device(config)
    try:
        await Collector(device, db, config).run()
    finally:
        await device.aclose()


async def _sync(config: Config, db: Database) -> None:
    device = _device(config)
    try:
        collector = Collector(device, db, config)
        print(f"employees: {await collector.sync_users()}")
        print(f"new events: {await collector.reconcile()}")
    finally:
        await device.aclose()


async def _status(config: Config) -> None:
    device = _device(config)
    try:
        for key, value in (await device.device_info()).items():
            print(f"{key:>22}: {value}")
    finally:
        await device.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="attendance", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="collect events and run the Telegram bot (default)")
    sub.add_parser("sync", help="one-off: import employees and events from the terminal")
    sub.add_parser("status", help="print terminal device info")
    day = sub.add_parser("day", help="print the daily report")
    day.add_argument("date", nargs="?", help="YYYY-MM-DD (default: today)")
    month = sub.add_parser("month", help="print the monthly report")
    month.add_argument("month", nargs="?", help="YYYY-MM (default: current month)")
    month.add_argument("--xlsx", type=Path, help="also write the Excel report to this file")
    employee = sub.add_parser("employee", help="print one employee's history (default: last 7 days)")
    employee.add_argument("args", nargs="+", metavar="NAME [DAYS | FROM [TO]]")
    args = parser.parse_args()
    command = args.command or "run"

    # Reports contain emoji; legacy Windows code pages (e.g. cp1251) can't print them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(require_device=command in {"run", "sync", "status"})
    db = Database(config.db_path)
    try:
        if command == "run":
            if config.telegram_token:
                from .bot import build_application

                build_application(config, db, _device(config)).run_polling()
            else:
                log.info("TELEGRAM_BOT_TOKEN not set - collecting events only")
                asyncio.run(_collect_only(config, db))
        elif command == "sync":
            asyncio.run(_sync(config, db))
        elif command == "status":
            asyncio.run(_status(config))
        elif command == "day":
            print(_plain(daily_report(db, config, date.fromisoformat(args.date) if args.date else _today(config))))
        elif command == "month":
            year, mon = map(int, args.month.split("-")) if args.month else (_today(config).year, _today(config).month)
            text, xlsx = monthly_report(db, config, year, mon)
            print(_plain(text))
            if args.xlsx:
                args.xlsx.write_bytes(xlsx)
                print(f"\nwrote {args.xlsx}")
        elif command == "employee":
            try:
                query, first, last = parse_history_args(args.args, _today(config))
            except HistoryArgsError as exc:
                raise SystemExit(str(exc)) from None
            matches = find_employees(db.employees(active_only=False), query)
            if len(matches) != 1:
                found = ", ".join(f"{name} (#{no})" for no, name in matches) or "none"
                raise SystemExit(f'"{query}" matches {len(matches)} employees: {found}')
            print(_plain(employee_report(db, config, *matches[0], first, last)))
    except KeyboardInterrupt:
        pass
    finally:
        db.close()

