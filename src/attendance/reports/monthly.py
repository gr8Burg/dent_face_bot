"""Monthly attendance report: Telegram HTML summary plus an Excel file."""

from __future__ import annotations

import calendar
from datetime import date

from ..config import Config
from ..storage import Database
from .common import fmt_duration, load_period, who
from .excel import monthly_xlsx


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def monthly_report(db: Database, config: Config, year: int, month: int, *, until: date | None = None) -> tuple[str, bytes]:
    """Returns (HTML summary text, .xlsx bytes). `until` cuts off a month in progress."""
    first, last = month_range(year, month)
    if until and until < last:
        last = until
    people = load_period(db, config, first, last)

    title = f"{calendar.month_name[month]} {year}" + (f" (to {last:%d %b})" if last != month_range(year, month)[1] else "")
    lines = [f"📊 <b>Monthly attendance — {title}</b>", ""]
    for p in people:
        note = f", {p.incomplete_days} incomplete" if p.incomplete_days else ""
        lines.append(f"• {who(p.name, p.employee_no)}: {p.days_present} days, {fmt_duration(p.total)}{note}")
    if not people:
        lines.append("No employees or scans for this period.")
    return "\n".join(lines), monthly_xlsx(people, title)
