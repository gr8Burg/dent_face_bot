"""SQLite storage. Raw events are kept as-is; work time is always recomputed from them."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ..domain.events import FACE_PASS, MAJOR_EVENT, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    employee_no TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,   -- 0 once the user is removed from the terminal
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY,
    serial_no         INTEGER UNIQUE,         -- terminal's own event number
    ts                INTEGER NOT NULL,       -- unix seconds
    time              TEXT NOT NULL,          -- as reported by the terminal (ISO 8601 with offset)
    employee_no       TEXT NOT NULL DEFAULT '',
    name              TEXT NOT NULL DEFAULT '',
    major             INTEGER NOT NULL,
    minor             INTEGER NOT NULL,
    verify_mode       TEXT,
    attendance_status TEXT,
    source            TEXT NOT NULL,
    raw               TEXT NOT NULL,
    -- Fallback dedupe for events that arrive without a serial number.
    UNIQUE (ts, major, minor, employee_no)
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        # Rollback journal rather than WAL: WAL's shared-memory index can't be shared with readers
        # in Docker (e.g. the read-only DB viewer in compose.yaml), while a plain journal can.
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def insert_event(self, event: Event) -> bool:
        """Stores an event; returns False if it was already stored."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO events
                   (serial_no, ts, time, employee_no, name, major, minor, verify_mode, attendance_status, source, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.serial_no,
                    int(event.time.timestamp()),
                    event.time.isoformat(),
                    event.employee_no,
                    event.name,
                    event.major,
                    event.minor,
                    event.verify_mode,
                    event.attendance_status,
                    event.source,
                    json.dumps(event.raw, ensure_ascii=False),
                ),
            )
        return cur.rowcount == 1

    def face_scans(self, start: datetime, end: datetime, *, employee_no: str | None = None) -> list[sqlite3.Row]:
        """Successful face matches by known employees in [start, end), oldest first."""
        sql = """SELECT ts, employee_no, name FROM events
                 WHERE ts >= ? AND ts < ? AND employee_no != '' AND major = ? AND minor = ?"""
        params: list[object] = [int(start.timestamp()), int(end.timestamp()), MAJOR_EVENT, FACE_PASS]
        if employee_no is not None:
            sql += " AND employee_no = ?"
            params.append(employee_no)
        return self.conn.execute(sql + " ORDER BY ts", params).fetchall()

    def count_face_scans(self, employee_no: str, start: datetime, end: datetime) -> int:
        return self.conn.execute(
            """SELECT COUNT(*) FROM events
               WHERE employee_no = ? AND ts >= ? AND ts < ? AND major = ? AND minor = ?""",
            (employee_no, int(start.timestamp()), int(end.timestamp()), MAJOR_EVENT, FACE_PASS),
        ).fetchone()[0]

    def sync_employees(self, employees: Iterable[tuple[str, str]]) -> None:
        """Replaces the active employee list with what's on the terminal; removed users stay as inactive."""
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute("UPDATE employees SET active = 0")
            self.conn.executemany(
                """INSERT INTO employees (employee_no, name, active, updated_at) VALUES (?, ?, 1, ?)
                   ON CONFLICT (employee_no) DO UPDATE SET name = excluded.name, active = 1, updated_at = excluded.updated_at""",
                [(no, name, now) for no, name in employees],
            )

    def employees(self, *, active_only: bool = True) -> dict[str, str]:
        sql = "SELECT employee_no, name FROM employees" + (" WHERE active = 1" if active_only else "")
        return {row["employee_no"]: row["name"] for row in self.conn.execute(sql)}

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
