"""Attendance history for one employee over a period (Telegram HTML text)."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

from ..config import Config
from ..domain.worktime import build_days, period_bounds
from ..storage import Database
from .common import fmt_duration, hm, who


def employee_report(db: Database, config: Config, employee_no: str, name: str, first: date, last: date) -> str:
    start, end = period_bounds(first, last, config.tz, config.day_cutoff)
    rows = db.face_scans(start, end, employee_no=employee_no)
    records = {day: record for (_, day), record in build_days(rows, config.tz, config.day_cutoff).items()}

    lines = [
        f"👤 <b>{who(name, employee_no)}</b> (#{escape(employee_no)})",
        f"🗓 {first:%d %b %Y} – {last:%d %b %Y}",
        "",
    ]
    day = first
    while day <= last:
        label = f"{day:%a %d.%m}"
        record = records.get(day)
        if record is None:
            lines.append(f"▫️ {label} — no scans")
        elif record.complete:
            lines.append(f"✅ {label} — {hm(record.first)} → {hm(record.last)} ({fmt_duration(record.worked)})")
        else:
            lines.append(f"⚠️ {label} — {hm(record.first)} → ? (single scan)")
        day += timedelta(days=1)

    complete = [r for r in records.values() if r.complete]
    total = sum((r.worked for r in complete), timedelta(0))
    summary = f"Total: {fmt_duration(total)}"
    if complete:
        summary += f", avg {fmt_duration(total / len(complete))} per full day"
    lines += ["", f"Days present: {len(records)} / {(last - first).days + 1}", summary]
    if incomplete := len(records) - len(complete):
        lines.append(f"Incomplete days (single scan): {incomplete}")
    return "\n".join(lines)
