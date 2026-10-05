import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from attendance.hikvision import Device, DeviceAuthError, decode_part, is_heartbeat, parse_multipart
from attendance.hikvision import auth as auth_module

USER, PASSWORD, REALM = "admin", "secret", "DS-test"
# Like the real terminal: a nonce works for 8 requests, then gets a bare 401 with no new challenge.
NONCE_LIMIT = 8
TZ = timezone(timedelta(hours=3))


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class FakeTerminal:
    """httpx MockTransport handler emulating the terminal's Digest auth and ISAPI endpoints."""

    def __init__(self) -> None:
        self.nonce_uses: dict[str, int] = {}
        self.bare_401s = 0

    def challenge(self) -> httpx.Response:
        nonce = f"nonce{len(self.nonce_uses)}"
        self.nonce_uses[nonce] = 0
        return httpx.Response(401, headers={"WWW-Authenticate":
            f'Digest qop="auth", realm="{REALM}", nonce="{nonce}", stale="false", opaque="", domain="::"'})

    def authenticate(self, request: httpx.Request) -> httpx.Response | None:
        """Returns a 401 response, or None when the request is authorized."""
        header = request.headers.get("authorization", "")
        p = {m[0]: m[1] or m[2] for m in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]*))', header)}
        if not p:
            return self.challenge()
        if self.nonce_uses.get(p["nonce"], NONCE_LIMIT) >= NONCE_LIMIT:
            self.bare_401s += 1
            return httpx.Response(401)
        self.nonce_uses[p["nonce"]] += 1
        ha1 = md5(f"{USER}:{REALM}:{PASSWORD}")
        ha2 = md5(f"{request.method}:{p['uri']}")
        if p["response"] != md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}"):
            return self.challenge()
        return None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.authenticate(request) or fake_routes(request)


class Chunked(httpx.AsyncByteStream):
    """Delivers a body in tiny pieces to exercise boundary handling."""

    def __init__(self, data: bytes, size: int = 7):
        self.data, self.size = data, size

    async def __aiter__(self):
        for i in range(0, len(self.data), self.size):
            yield self.data[i : i + self.size]


LIVE_EVENT = {
    "dateTime": "2026-10-02T09:02:13+03:00",
    "eventType": "AccessControllerEvent",
    "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75, "name": "Alice",
                              "employeeNoString": "7", "serialNo": 101, "currentVerifyMode": "face"},
}


def stream_body() -> bytes:
    ev = json.dumps(LIVE_EVENT).encode()
    img = b"\xff\xd8\x01\x02\xff\xd9"
    return b"".join([
        b"--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(ev), ev, b"\r\n",
        b"--MIME_boundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(img), img, b"\r\n",
        b'--MIME_boundary\r\nContent-Type: application/json\r\n\r\n{"eventType":"heartBeat"}\r\n',
        b"--MIME_boundary\r\nContent-Type: application/xml\r\n\r\n<EventNotificationAlert><eventType>videoloss"
        b"</eventType><eventState>inactive</eventState></EventNotificationAlert>\r\n--MIME_boundary--\r\n",
    ])


def fake_routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/ISAPI/System/deviceInfo":
        return httpx.Response(200, content=b'<DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">'
                                            b"<model>DS-K1T343MWX</model><serialNumber>X1</serialNumber></DeviceInfo>")
    if path == "/ISAPI/AccessControl/AcsEvent":
        cond = json.loads(request.content)["AcsEventCond"]
        assert cond["startTime"].endswith("+03:00")
        total, pos = 45, cond["searchResultPosition"]
        n = min(cond["maxResults"], total - pos)
        items = [{"major": 5, "minor": 75, "time": "2026-10-02T09:00:00+03:00", "serialNo": pos + i,
                  "employeeNoString": str(pos + i)} for i in range(n)]
        return httpx.Response(200, json={"AcsEvent": {"totalMatches": total, "InfoList": items,
                                                       "responseStatusStrg": "MORE" if pos + n < total else "OK"}})
    if path == "/ISAPI/AccessControl/UserInfo/Search":
        return httpx.Response(200, json={"UserInfoSearch": {"responseStatusStrg": "OK",
                                                             "UserInfo": [{"employeeNo": "7", "name": "Alice"}]}})
    if path == "/ISAPI/Event/notification/alertStream":
        return httpx.Response(200, headers={"Content-Type": "multipart/mixed; boundary=MIME_boundary"},
                              stream=Chunked(stream_body()))
    return httpx.Response(404)


def device(password: str = PASSWORD, terminal: FakeTerminal | None = None) -> Device:
    return Device("http://terminal", USER, password, transport=httpx.MockTransport(terminal or FakeTerminal()))


def test_device_info_with_digest_auth():
    info = asyncio.run(device().device_info())
    assert info == {"model": "DS-K1T343MWX", "serialNumber": "X1"}


def test_wrong_password_raises_auth_error():
    with pytest.raises(DeviceAuthError):
        asyncio.run(device("wrong").device_info())


def test_event_search_pages_through_all_results():
    now = datetime(2026, 10, 2, 18, tzinfo=TZ)
    events = asyncio.run(device().search_events(now - timedelta(days=1), now))
    assert len(events) == 45
    assert [e["serialNo"] for e in events] == list(range(45))


def test_list_users():
    assert asyncio.run(device().list_users()) == [{"employeeNo": "7", "name": "Alice"}]


def test_alert_stream_yields_events_and_heartbeats_but_not_images():
    async def collect():
        opened = []
        events = [e async for e in device().alert_stream(on_open=lambda: opened.append(True))]
        return opened, events

    opened, events = asyncio.run(collect())
    assert opened == [True]
    assert events[0] == LIVE_EVENT
    assert [is_heartbeat(e) for e in events] == [False, True, True]


@pytest.mark.parametrize("chunk", [1, 3, 64, 10_000])
def test_parse_multipart_any_chunking(chunk):
    async def parts():
        return [p async for p in parse_multipart(Chunked(stream_body(), chunk), "MIME_boundary")]

    result = asyncio.run(parts())
    assert len(result) == 4
    assert json.loads(result[0][1]) == LIVE_EVENT
    assert result[1][0]["content-type"] == "image/jpeg" and result[1][1] == b"\xff\xd8\x01\x02\xff\xd9"
    assert decode_part(*result[1]) is None


def test_many_requests_never_exhaust_the_nonce():
    """The terminal rejects a nonce after 8 uses; the client must refresh it before that happens."""
    terminal = FakeTerminal()
    dev = device(terminal=terminal)

    async def hammer():
        now = datetime(2026, 10, 2, 18, tzinfo=TZ)
        for _ in range(10):
            await dev.list_users()
            assert len(await dev.search_events(now - timedelta(days=1), now)) == 45  # 2 pages

    asyncio.run(hammer())
    assert terminal.bare_401s == 0


def test_recovers_when_terminal_rejects_a_reused_nonce(monkeypatch):
    monkeypatch.setattr(auth_module, "MAX_NONCE_USES", 100)  # disable the proactive refresh
    terminal = FakeTerminal()
    dev = device(terminal=terminal)

    async def hammer():
        for _ in range(20):
            await dev.list_users()

    asyncio.run(hammer())
    assert terminal.bare_401s >= 2  # rejections happened and were recovered from


def test_nonce_is_refreshed_after_max_age(monkeypatch):
    monkeypatch.setattr(auth_module, "MAX_NONCE_AGE", -1)  # every nonce counts as expired
    terminal = FakeTerminal()
    dev = device(terminal=terminal)

    async def calls():
        for _ in range(3):
            await dev.list_users()

    asyncio.run(calls())
    assert len(terminal.nonce_uses) == 3 and terminal.bare_401s == 0
