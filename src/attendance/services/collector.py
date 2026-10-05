"""Keeps the local database in step with the terminal.

The live alert stream gives instant events; a periodic re-read of the terminal's own log
fills anything missed while the service or the connection was down.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from ..config import Config
from ..domain.events import FACE_PASS, MAJOR_EVENT, Event, from_history, from_live
from ..domain.worktime import workday_bounds, workday_of
from ..hikvision import Device, DeviceAuthError, is_heartbeat
from ..storage import Database

log = logging.getLogger(__name__)

ArrivalCallback = Callable[[Event], Awaitable[None]]

# Re-read this much before the last sync point, in case the terminal's clock or log lagged.
RECONCILE_OVERLAP = timedelta(hours=1)
USER_SYNC_INTERVAL = timedelta(hours=1)
# Wait this long after rejected credentials so the terminal doesn't lock the account.
AUTH_FAILURE_BACKOFF = 300


class Collector:
    def __init__(self, device: Device, db: Database, config: Config, on_arrival: ArrivalCallback | None = None):
        self.device = device
        self.db = db
        self.config = config
        self.on_arrival = on_arrival
        self.stream_connected = False
        self.last_error: str | None = None
        self.last_stream_message: datetime | None = None
        self.last_reconcile: datetime | None = None
        self._last_user_sync: datetime | None = None

    async def run(self) -> None:
        """Runs until cancelled."""
        await self._safe(self.sync_users)
        await self._safe(self.reconcile)
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._stream_loop())
            tg.create_task(self._periodic_loop())

    async def _safe(self, job: Callable[[], Awaitable[object]]) -> None:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{job.__name__}: {exc}"
            log.warning("%s failed: %s", job.__name__, exc)

    async def sync_users(self) -> int:
        users = await self.device.list_users()
        self.db.sync_employees((str(u["employeeNo"]), u.get("name") or "") for u in users)
        self._last_user_sync = datetime.now(self.config.tz)
        log.info("synced %d employees from terminal", len(users))
        return len(users)

    async def reconcile(self) -> int:
        """Imports events from the terminal's log since the last sync (or BACKFILL_DAYS on first run)."""
        now = datetime.now(self.config.tz)
        synced_until = self.db.get_meta("synced_until")
        start = (
            datetime.fromisoformat(synced_until) - RECONCILE_OVERLAP
            if synced_until
            else now - timedelta(days=self.config.backfill_days)
        )
        records = await self.device.search_events(start, now)
        events = [e for e in (from_history(r, self.config.tz) for r in records) if e.is_face]
        added = sum(self.db.insert_event(e) for e in events)
        self.db.set_meta("synced_until", now.isoformat())
        self.last_reconcile = now
        if added:
            log.info("reconcile: %d new of %d events since %s", added, len(records), start.isoformat())
        return added

    async def _periodic_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.reconcile_minutes * 60)
            await self._safe(self.reconcile)
            if self._last_user_sync is None or datetime.now(self.config.tz) - self._last_user_sync > USER_SYNC_INTERVAL:
                await self._safe(self.sync_users)

    def _on_stream_open(self) -> None:
        self.stream_connected = True
        self.last_error = None
        log.info("alert stream connected to %s", self.config.device_url)

    async def _stream_loop(self) -> None:
        delay = 2.0
        while True:
            try:
                async for raw in self.device.alert_stream(on_open=self._on_stream_open):
                    delay = 2.0
                    self.last_stream_message = datetime.now(self.config.tz)
                    if is_heartbeat(raw):
                        continue
                    event = from_live(raw, self.config.tz)
                    if event is not None:
                        await self._handle_live(event)
                raise ConnectionError("alert stream closed by terminal")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stream_connected = False
                self.last_error = f"alert stream: {exc or type(exc).__name__}"
                wait = AUTH_FAILURE_BACKOFF if isinstance(exc, DeviceAuthError) else delay
                log.warning("%s - reconnecting in %ds", self.last_error, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2, 60.0)

    async def _handle_live(self, event: Event) -> None:
        if not event.is_face:
            return
        is_scan = event.employee_no and event.major == MAJOR_EVENT and event.minor == FACE_PASS
        first_today = False
        if is_scan and self.on_arrival:
            start, end = workday_bounds(workday_of(event.time, self.config.tz, self.config.day_cutoff),
                                        self.config.tz, self.config.day_cutoff)
            first_today = self.db.count_face_scans(event.employee_no, start, end) == 0

        if not self.db.insert_event(event):
            return
        who = " ".join(filter(None, [event.name, event.employee_no and f"#{event.employee_no}"]))
        log.info("%s %s%s", event.time.strftime("%H:%M:%S"), event.description, f" - {who}" if who else "")

        if first_today and self.on_arrival:
            try:
                await self.on_arrival(event)
            except Exception as exc:
                log.warning("arrival notification failed: %s", exc)
