"""Formatting helpers and data loading shared by all reports."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

from ..config import Config
from ..domain.worktime import EmployeeMonth, build_days, period_bounds, summarize_period
from ..storage import Database


def fmt_duration(td: timedelta) -> str:
    minutes = int(td.total_seconds()) // 60
    return f"{minutes // 60}h {minutes % 60:02d}m"


def hm(moment: datetime | None) -> str:
    return moment.strftime("%H:%M") if moment else "—"


def who(name: str, employee_no: str) -> str:
    return escape(name or f"#{employee_no}")


def load_period(db: Database, config: Config, first: date, last: date) -> list[EmployeeMonth]:
    start, end = period_bounds(first, last, config.tz, config.day_cutoff)
    rows = db.face_scans(start, end)
    return summarize_period(build_days(rows, config.tz, config.day_cutoff), db.employees())
