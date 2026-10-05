"""Employee lookup and period parsing for the per-employee history report."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

DEFAULT_DAYS = 7
MAX_DAYS = 366

HISTORY_USAGE = (
    "Usage: /employee NAME [DAYS | FROM [TO]]\n"
    "  /employee egor             last 7 days\n"
    "  /employee egor 30          last 30 days\n"
    "  /employee egor 2026-09-01  from that date until today\n"
    "  /employee egor 2026-09-01 2026-09-30\n"
    "NAME can be part of the name or the employee number."
)


class HistoryArgsError(ValueError):
    pass


def find_employees(employees: Mapping[str, str], query: str) -> list[tuple[str, str]]:
    """Matches an employee number exactly, otherwise a case-insensitive part of the name.

    An exact (case-insensitive) name match wins over partial ones, so "Ann" doesn't also return "Anna".
    """
    query = query.strip()
    if query in employees:
        return [(query, employees[query])]
    needle = query.casefold()
    exact = [(no, name) for no, name in employees.items() if name.casefold() == needle]
    matches = exact or [(no, name) for no, name in employees.items() if needle in name.casefold()]
    return sorted(matches, key=lambda m: (m[1].casefold(), m[0]))


def default_period(today: date, days: int = DEFAULT_DAYS) -> tuple[date, date]:
    return today - timedelta(days=days - 1), today


def parse_history_args(args: list[str], today: date) -> tuple[str, date, date]:
    """Splits `NAME [DAYS | FROM [TO]]` into (query, first workday, last workday).

    A lone number is treated as the name (an employee number), not as a day count.
    """
    tokens = list(args)
    dates: list[date] = []
    while tokens and len(dates) < 2:
        try:
            dates.insert(0, date.fromisoformat(tokens[-1]))
        except ValueError:
            break
        tokens.pop()

    days = None
    if not dates and len(tokens) >= 2 and tokens[-1].isdigit():
        days = int(tokens.pop())

    query = " ".join(tokens).strip()
    if not query:
        raise HistoryArgsError("Employee name or number is missing.")

    if len(dates) == 2:
        first, last = sorted(dates)
    elif len(dates) == 1:
        first, last = dates[0], today
    else:
        first, last = default_period(today, days or DEFAULT_DAYS)

    if days is not None and not 1 <= days <= MAX_DAYS:
        raise HistoryArgsError(f"Number of days must be between 1 and {MAX_DAYS}.")
    if first > last:
        raise HistoryArgsError("Start date is after the end date.")
    if (last - first).days + 1 > MAX_DAYS:
        raise HistoryArgsError(f"Period is longer than {MAX_DAYS} days.")
    return query, first, last
