"""Shared test data: a UTC+3 config and a seeded in-memory database."""

from datetime import time, timedelta, timezone
from pathlib import Path

from attendance.config import Config
from attendance.domain.events import Event, from_history
from attendance.storage import Database

TZ = timezone(timedelta(hours=3))

CONFIG = Config(
    hik_host="terminal", hik_port=80, hik_user="admin", hik_password="x",
    tz=TZ, day_cutoff=time(4, 0),
    db_path=Path(":memory:"), reconcile_minutes=10, backfill_days=31,
    telegram_token=None, report_chat_ids=frozenset(), admin_ids=frozenset(),
    daily_report_time=time(20, 0), daily_report_for="today", monthly_report_time=time(9, 0),
    notify_arrivals=False,
)


def scan(emp: str, name: str, when: str, minor: int = 75, serial: int | None = None) -> Event:
    return from_history({"major": 5, "minor": minor, "time": when, "employeeNoString": emp,
                         "name": name, "serialNo": serial}, TZ)


def seeded_db() -> Database:
    db = Database(":memory:")
    db.sync_employees([("1", "Alice"), ("2", "Bob"), ("3", "Carol")])
    for e in [
        scan("1", "Alice", "2026-10-01T09:00:00+03:00"),
        scan("1", "Alice", "2026-10-01T13:00:00+03:00"),
        scan("1", "Alice", "2026-10-01T18:30:00+03:00"),
        scan("2", "Bob", "2026-10-01T22:00:00+03:00"),       # night shift ...
        scan("2", "Bob", "2026-10-02T03:30:00+03:00"),       # ... ends before the 04:00 cutoff
        scan("1", "Alice", "2026-10-02T09:15:00+03:00"),     # single scan
        scan("1", "Alice", "2026-10-02T09:16:00+03:00", minor=76),  # failed face: ignored
        scan("3", "Carol", "2026-10-01T09:05:00+03:00", minor=1),   # card pass: ignored
    ]:
        db.insert_event(e)
    return db
