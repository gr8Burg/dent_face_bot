"""Client for the terminal's ISAPI HTTP API (Digest auth)."""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import httpx

from .auth import HikvisionDigestAuth
from .multipart import decode_part, parse_multipart

# The terminal returns at most ~30 records per page regardless of maxResults.
PAGE_SIZE = 30
# The terminal sends heartbeats regularly; this long without any data means the stream is dead.
STREAM_IDLE_TIMEOUT = 120.0


class DeviceAuthError(Exception):
    """Credentials rejected. The terminal locks accounts after repeated failures, so don't retry quickly."""


def isapi_time(dt: datetime) -> str:
    """ISAPI wants ISO 8601 with a numeric offset, e.g. 2026-10-02T09:00:00+03:00."""
    if dt.tzinfo is None:
        raise ValueError("ISAPI times must be timezone-aware")
    return dt.isoformat(timespec="seconds")


class Device:
    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=HikvisionDigestAuth(user, password),
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise DeviceAuthError("Terminal rejected credentials (401). Check HIK_USER / HIK_PASSWORD.")
        response.raise_for_status()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, path, **kwargs)
        self._check(response)
        return response

    async def device_info(self) -> dict[str, str]:
        response = await self._request("GET", "/ISAPI/System/deviceInfo")
        root = ET.fromstring(response.content)
        return {el.tag.split("}")[-1]: (el.text or "").strip() for el in root if len(el) == 0}

    async def _search(
        self, path: str, cond_key: str, result_key: str, items_key: str, cond: dict[str, Any], limit: int
    ) -> list[dict[str, Any]]:
        """Runs a paged ISAPI search; the same searchID must be reused for every page."""
        search_id = uuid.uuid4().hex
        items: list[dict[str, Any]] = []
        while len(items) < limit:
            body = {
                cond_key: {
                    **cond,
                    "searchID": search_id,
                    "searchResultPosition": len(items),
                    "maxResults": min(PAGE_SIZE, limit - len(items)),
                }
            }
            page = (await self._request("POST", path, json=body)).json()[result_key]
            batch = page.get(items_key) or []
            items.extend(batch)
            if page.get("responseStatusStrg") != "MORE" or not batch:
                break
        return items

    async def search_events(
        self, start: datetime, end: datetime, *, major: int = 5, minor: int = 0, limit: int = 100_000
    ) -> list[dict[str, Any]]:
        """Access-control events stored on the terminal. major 5 = "event" (passes, failures, door state)."""
        return await self._search(
            "/ISAPI/AccessControl/AcsEvent?format=json",
            "AcsEventCond",
            "AcsEvent",
            "InfoList",
            {"major": major, "minor": minor, "startTime": isapi_time(start), "endTime": isapi_time(end)},
            limit,
        )

    async def list_users(self, limit: int = 10_000) -> list[dict[str, Any]]:
        return await self._search(
            "/ISAPI/AccessControl/UserInfo/Search?format=json",
            "UserInfoSearchCond",
            "UserInfoSearch",
            "UserInfo",
            {},
            limit,
        )

    async def alert_stream(self, on_open: Callable[[], None] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yields every event the terminal pushes, heartbeats included, until the connection drops."""
        timeout = httpx.Timeout(15.0, read=STREAM_IDLE_TIMEOUT)
        async with self._client.stream("GET", "/ISAPI/Event/notification/alertStream", timeout=timeout) as response:
            self._check(response)
            m = re.search(r'boundary="?([^";]+)"?', response.headers.get("content-type", ""))
            boundary = m.group(1) if m else "MIME_boundary"
            if on_open:
                on_open()
            async for headers, body in parse_multipart(response.aiter_bytes(), boundary):
                event = decode_part(headers, body)
                if event is not None:
                    yield event
