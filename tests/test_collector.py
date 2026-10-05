import asyncio
from dataclasses import replace

from attendance.domain.events import Event, from_live
from attendance.services.collector import Collector
from attendance.storage import Database

from factories import CONFIG, TZ, scan


def test_arrival_notified_once_per_workday():
    db = Database(":memory:")
    notified = []

    async def on_arrival(event):
        notified.append(event.time)

    collector = Collector(device=None, db=db, config=CONFIG, on_arrival=on_arrival)

    def live(when: str, serial: int) -> Event:
        return replace(scan("1", "Alice", when, serial=serial), source="live")

    async def feed():
        await collector._handle_live(live("2026-10-02T09:00:00+03:00", 1))
        await collector._handle_live(live("2026-10-02T09:00:00+03:00", 1))  # duplicate
        await collector._handle_live(live("2026-10-02T18:00:00+03:00", 2))
        await collector._handle_live(live("2026-10-03T09:05:00+03:00", 3))  # next day

    asyncio.run(feed())
    assert [t.day for t in notified] == [2, 3]


def test_non_face_live_events_are_not_stored():
    db = Database(":memory:")
    collector = Collector(device=None, db=db, config=CONFIG)
    door = from_live({"eventType": "AccessControllerEvent", "dateTime": "2026-10-02T09:00:00+03:00",
                      "AccessControllerEvent": {"majorEventType": 5, "subEventType": 21, "serialNo": 9}}, TZ)
    asyncio.run(collector._handle_live(door))
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
