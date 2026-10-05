"""Parsing of the terminal's alert stream: an endless multipart/mixed HTTP body."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any


async def parse_multipart(
    chunks: AsyncIterable[bytes], boundary: str
) -> AsyncIterator[tuple[dict[str, str], bytes]]:
    """Splits the endless multipart/mixed alert stream into (headers, body) parts.

    Uses Content-Length when the terminal sends it, otherwise scans for the next boundary.
    """
    delim = b"--" + boundary.encode()
    buf = b""
    async for chunk in chunks:
        buf += chunk
        while True:
            start = buf.find(delim)
            if start < 0:
                # Keep just enough to catch a delimiter split across chunks.
                buf = buf[-len(delim) :]
                break
            header_end = buf.find(b"\r\n\r\n", start)
            if header_end < 0:
                break

            headers: dict[str, str] = {}
            for line in buf[start + len(delim) : header_end].decode("latin-1").split("\r\n"):
                name, sep, value = line.partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()

            body_start = header_end + 4
            if "content-length" in headers:
                body_end = body_start + int(headers["content-length"])
                if len(buf) < body_end:
                    break
            else:
                body_end = buf.find(delim, body_start)
                if body_end < 0:
                    break

            yield headers, buf[body_start:body_end]
            buf = buf[body_end:]


def _xml_tag(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
    return m.group(1) if m else None


def decode_part(headers: dict[str, str], body: bytes) -> dict[str, Any] | None:
    """Turns one stream part into an event dict; returns None for images and unreadable parts."""
    if headers.get("content-type", "").startswith("image/"):
        return None  # snapshot that follows a face event; not used yet
    text = body.decode("utf-8", "replace").strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    if text.startswith("<"):
        # Older-style XML notification; keep only the essentials.
        return {
            "eventType": _xml_tag(text, "eventType") or "unknown",
            "eventState": _xml_tag(text, "eventState"),
            "dateTime": _xml_tag(text, "dateTime"),
        }
    return None


def is_heartbeat(event: dict[str, Any]) -> bool:
    """Keep-alives: JSON 'heartBeat', or the classic XML 'videoloss' + 'inactive'."""
    kind = event.get("eventType")
    return kind == "heartBeat" or (kind == "videoloss" and event.get("eventState") == "inactive")
