from attendance.domain.events import from_live
from attendance.storage import Database

from factories import TZ, scan


def test_duplicate_events_are_ignored():
    db = Database(":memory:")
    assert db.insert_event(scan("1", "Alice", "2026-10-01T09:00:00+03:00", serial=5))
    assert not db.insert_event(scan("1", "Alice", "2026-10-01T09:00:00+03:00", serial=5))
    # Same scan arriving live without a serial number is caught by the fallback key.
    live = from_live({"eventType": "AccessControllerEvent", "dateTime": "2026-10-01T09:00:00+03:00",
                      "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75,
                                                "employeeNoString": "1"}}, TZ)
    assert not db.insert_event(live)
