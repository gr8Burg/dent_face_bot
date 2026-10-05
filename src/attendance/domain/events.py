"""One event shape for both the live stream and the terminal's stored log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

# Access-control event category (major) and the face sub-events (minor) from the Hikvision ISAPI spec.
# Only face events are stored; a successful match is what counts as an attendance scan.
MAJOR_EVENT = 5
FACE_PASS = 75
FACE_FAIL = 76

MINOR_NAMES: dict[int, str] = {
    FACE_PASS: "Face authenticated",
    FACE_FAIL: "Face authentication failed",
}


def describe(major: int, minor: int) -> str:
    return (major == MAJOR_EVENT and MINOR_NAMES.get(minor)) or f"major {major} / minor {minor}"


@dataclass(frozen=True)
class Event:
    time: datetime  # timezone-aware
    major: int
    minor: int
    employee_no: str  # "" for an unrecognised face
    name: str
    serial_no: int | None
    verify_mode: str | None
    attendance_status: str | None
    source: str  # "live" | "history"
    raw: dict[str, Any]

    @property
    def description(self) -> str:
        return describe(self.major, self.minor)

    @property
    def is_face(self) -> bool:
        """Door state, card-reader and other terminal events are dropped on import."""
        return self.major == MAJOR_EVENT and self.minor in MINOR_NAMES


def parse_time(value: str | None, tz: tzinfo) -> datetime:
    if not value:
        return datetime.now(tz)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def from_live(raw: dict[str, Any], tz: tzinfo) -> Event | None:
    """Alert-stream JSON → Event. Returns None for anything that isn't an access-control event."""
    acs = raw.get("AccessControllerEvent")
    if raw.get("eventType") != "AccessControllerEvent" or not isinstance(acs, dict):
        return None
    return Event(
        time=parse_time(raw.get("dateTime"), tz),
        major=_int(acs.get("majorEventType")) or 0,
        minor=_int(acs.get("subEventType")) or 0,
        employee_no=str(acs.get("employeeNoString") or acs.get("employeeNo") or ""),
        name=acs.get("name") or "",
        serial_no=_int(acs.get("serialNo")),
        verify_mode=acs.get("currentVerifyMode"),
        attendance_status=acs.get("attendanceStatus"),
        source="live",
        raw=raw,
    )


def from_history(record: dict[str, Any], tz: tzinfo) -> Event:
    """AcsEvent InfoList item → Event."""
    return Event(
        time=parse_time(record.get("time"), tz),
        major=_int(record.get("major")) or 0,
        minor=_int(record.get("minor")) or 0,
        employee_no=str(record.get("employeeNoString") or record.get("employeeNo") or ""),
        name=record.get("name") or "",
        serial_no=_int(record.get("serialNo")),
        verify_mode=record.get("currentVerifyMode"),
        attendance_status=record.get("attendanceStatus"),
        source="history",
        raw=record,
    )
