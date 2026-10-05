"""Daily attendance report (Telegram HTML text)."""

from __future__ import annotations

from datetime import date

from ..config import Config
from ..storage import Database
from .common import fmt_duration, hm, load_period, who


def daily_report(db: Database, config: Config, day: date) -> str:
    """HTML-formatted text for Telegram."""
    people = load_period(db, config, day, day)
    present = [p for p in people if p.days]
    absent = [p for p in people if not p.days]

    lines = [f"📅 <b>Attendance — {day:%a %d %b %Y}</b>", f"Present: {len(present)} / {len(people)}", ""]
    for p in sorted(present, key=lambda p: p.days[0].first):
        d = p.days[0]
        if d.complete:
            lines.append(f"✅ {who(p.name, p.employee_no)} — {hm(d.first)} → {hm(d.last)} ({fmt_duration(d.worked)})")
        else:
            lines.append(f"⚠️ {who(p.name, p.employee_no)} — {hm(d.first)} → ? (single scan)")
    if absent:
        lines += ["", "❌ <b>Absent:</b> " + ", ".join(who(p.name, p.employee_no) for p in absent)]
    return "\n".join(lines)
