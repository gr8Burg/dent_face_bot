"""Integration with the Hikvision terminal's ISAPI HTTP API."""

from .auth import HikvisionDigestAuth
from .client import Device, DeviceAuthError, isapi_time
from .multipart import decode_part, is_heartbeat, parse_multipart

__all__ = ["Device", "HikvisionDigestAuth", "DeviceAuthError", "decode_part", "is_heartbeat", "isapi_time", "parse_multipart"]
