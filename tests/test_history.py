from datetime import date

import pytest

from attendance.domain.history import HistoryArgsError, find_employees, parse_history_args
from attendance.reports import employee_report

from factories import CONFIG, seeded_db

TODAY = date(2026, 10, 5)
STAFF = {"1": "Alice", "2": "Bob", "3": "Ann", "4": "Anna", "10": "Егор Лыков"}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["alice"], ("alice", date(2026, 9, 29), TODAY)),                      # default: 7 days incl. today
        (["alice", "30"], ("alice", date(2026, 9, 6), TODAY)),
        (["Егор", "Лыков"], ("Егор Лыков", date(2026, 9, 29), TODAY)),        # multi-word name
        (["1"], ("1", date(2026, 9, 29), TODAY)),                              # lone number = employee number
        (["alice", "2026-09-01"], ("alice", date(2026, 9, 1), TODAY)),
        (["alice", "2026-09-30", "2026-09-01"], ("alice", date(2026, 9, 1), date(2026, 9, 30))),
    ],
)
def test_parse_history_args(args, expected):
    assert parse_history_args(args, TODAY) == expected


@pytest.mark.parametrize("args", [[], ["2026-09-01"], ["alice", "0"], ["alice", "400"], ["alice", "2026-11-01"]])
def test_parse_history_args_rejects(args):
    with pytest.raises(HistoryArgsError):
        parse_history_args(args, TODAY)


def test_find_employees():
    assert find_employees(STAFF, "1") == [("1", "Alice")]               # employee number wins
    assert find_employees(STAFF, "bo") == [("2", "Bob")]
    assert find_employees(STAFF, "ann") == [("3", "Ann")]               # exact name beats "Anna"
    assert find_employees(STAFF, "an") == [("3", "Ann"), ("4", "Anna")]
    assert find_employees(STAFF, "егор") == [("10", "Егор Лыков")]      # Cyrillic, case-insensitive
    assert find_employees(STAFF, "zed") == []


def test_employee_report_lists_every_day():
    text = employee_report(seeded_db(), CONFIG, "1", "Alice", date(2026, 9, 30), date(2026, 10, 2))
    assert "Alice</b> (#1)" in text
    assert "▫️ Wed 30.09 — no scans" in text
    assert "✅ Thu 01.10 — 09:00 → 18:30 (9h 30m)" in text
    assert "⚠️ Fri 02.10 — 09:15 → ? (single scan)" in text
    assert "Days present: 2 / 3" in text
    assert "Total: 9h 30m, avg 9h 30m per full day" in text
    assert "Incomplete days (single scan): 1" in text
    assert "Bob" not in text  # other employees' scans are not mixed in
