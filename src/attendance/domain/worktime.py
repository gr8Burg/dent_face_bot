"""Turns raw scans into workdays.

One terminal can't tell entry from exit, so the rule is: first scan of the workday = start,
last scan = end. A workday runs from DAY_CUTOFF to DAY_CUTOFF the next day, so night shifts
aren't split at midnight.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from sqlite3 import Row


def workday_of(moment: datetime, tz: tzinfo, cutoff: time) -> date:
    local = moment.astimezone(tz)
    return local.date() - timedelta(days=1) if local.time() < cutoff else local.date()


def workday_bounds(day: date, tz: tzinfo, cutoff: time) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, cutoff, tzinfo=tz),
        datetime.combine(day + timedelta(days=1), cutoff, tzinfo=tz),
    )


def period_bounds(first: date, last: date, tz: tzinfo, cutoff: time) -> tuple[datetime, datetime]:
    return workday_bounds(first, tz, cutoff)[0], workday_bounds(last, tz, cutoff)[1]


@dataclass
class DayRecord:
    employee_no: str
    name: str
    day: date
    first: datetime
    last: datetime
    scans: int = 1

    @property
    def complete(self) -> bool:
        """A single scan means the start or the end is missing."""
        return self.scans >= 2 and self.last > self.first

    @property
    def worked(self) -> timedelta:
        return self.last - self.first if self.complete else timedelta(0)


def build_days(rows: Iterable[Row], tz: tzinfo, cutoff: time) -> dict[tuple[str, date], DayRecord]:
    """Groups scans (oldest first, as returned by storage.Database.face_scans) per employee per workday."""
    days: dict[tuple[str, date], DayRecord] = {}
    for row in rows:
        moment = datetime.fromtimestamp(row["ts"], tz)
        key = (row["employee_no"], workday_of(moment, tz, cutoff))
        record = days.get(key)
        if record is None:
            days[key] = DayRecord(row["employee_no"], row["name"], key[1], moment, moment)
        else:
            record.last = moment
            record.scans += 1
            record.name = row["name"] or record.name
    return days


@dataclass
class EmployeeMonth:
    employee_no: str
    name: str
    days: list[DayRecord] = field(default_factory=list)

    @property
    def days_present(self) -> int:
        return len(self.days)

    @property
    def incomplete_days(self) -> int:
        return sum(not d.complete for d in self.days)

    @property
    def total(self) -> timedelta:
        return sum((d.worked for d in self.days), timedelta(0))

    @property
    def avg_start(self) -> time | None:
        return _avg_time_of_day([d.first for d in self.days])

    @property
    def avg_end(self) -> time | None:
        return _avg_time_of_day([d.last for d in self.days if d.complete])


def _avg_time_of_day(moments: list[datetime]) -> time | None:
    if not moments:
        return None
    seconds = sum(m.hour * 3600 + m.minute * 60 + m.second for m in moments) // len(moments)
    return time(seconds // 3600, seconds % 3600 // 60)


def summarize_period(
    days: Mapping[tuple[str, date], DayRecord], employees: Mapping[str, str]
) -> list[EmployeeMonth]:
    """Per-employee totals, including active employees who never showed up. Sorted by name."""
    result: dict[str, EmployeeMonth] = {no: EmployeeMonth(no, name) for no, name in employees.items()}
    for record in sorted(days.values(), key=lambda r: r.day):
        entry = result.setdefault(record.employee_no, EmployeeMonth(record.employee_no, record.name))
        entry.days.append(record)
        entry.name = entry.name or record.name
    return sorted(result.values(), key=lambda e: (e.name or e.employee_no).lower())
