import io
from datetime import date

from openpyxl import load_workbook

from attendance.reports import daily_report, monthly_report

from factories import CONFIG, seeded_db


def test_daily_report():
    text = daily_report(seeded_db(), CONFIG, date(2026, 10, 1))
    assert "Present: 2 / 3" in text
    assert "Alice — 09:00 → 18:30 (9h 30m)" in text
    assert "Bob — 22:00 → 03:30 (5h 30m)" in text
    assert "Absent:</b> Carol" in text

    text = daily_report(seeded_db(), CONFIG, date(2026, 10, 2))
    assert "Alice — 09:15 → ? (single scan)" in text


def test_monthly_report_and_excel():
    text, xlsx = monthly_report(seeded_db(), CONFIG, 2026, 10)
    assert "Alice: 2 days, 9h 30m, 1 incomplete" in text
    assert "Carol: 0 days, 0h 00m" in text

    wb = load_workbook(io.BytesIO(xlsx))
    summary = list(wb["Summary"].values)
    assert summary[0][:5] == ("Employee No", "Name", "Days present", "Incomplete days", "Total hours")
    assert summary[1][:5] == ("1", "Alice", 2, 1, 9.5)
    assert len(list(wb["Daily"].values)) == 1 + 3
