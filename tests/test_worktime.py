from datetime import date, datetime, time, timedelta

from attendance.domain.worktime import build_days, workday_bounds, workday_of

from factories import TZ, seeded_db


def test_workday_cutoff():
    assert workday_of(datetime(2026, 10, 2, 3, 59, tzinfo=TZ), TZ, time(4)) == date(2026, 10, 1)
    assert workday_of(datetime(2026, 10, 2, 4, 0, tzinfo=TZ), TZ, time(4)) == date(2026, 10, 2)


def test_build_days_first_and_last_scan():
    db = seeded_db()
    start, end = workday_bounds(date(2026, 10, 1), TZ, time(4))
    days = build_days(db.face_scans(start, end), TZ, time(4))
    alice, bob = days[("1", date(2026, 10, 1))], days[("2", date(2026, 10, 1))]
    assert (alice.scans, alice.worked) == (3, timedelta(hours=9, minutes=30))
    assert bob.worked == timedelta(hours=5, minutes=30)
