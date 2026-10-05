"""HTTP Digest auth tuned for Hikvision terminals.

httpx.DigestAuth reuses one nonce until the server sends a new challenge. The terminal accepts
a nonce for only ~8 requests and then answers with a bare 401 (no new challenge), which
httpx.DigestAuth treats as a final failure. A nonce also expires after an idle period
(rejected after 30 s idle). Each such 401 may count towards the terminal's failed-login
lockout. So this auth:

- refreshes the nonce proactively after MAX_NONCE_USES requests or MAX_NONCE_AGE seconds, and
- if a reused nonce is rejected anyway, fetches a fresh challenge once and retries.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Generator

import httpx

# Observed limit is 8 uses per nonce; stay well below it.
MAX_NONCE_USES = 5
# Requests in a burst (paging) reuse the nonce; anything after a pause gets a fresh one.
MAX_NONCE_AGE = 10.0


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _parse_challenge(response: httpx.Response) -> dict[str, str] | None:
    for header in response.headers.get_list("www-authenticate"):
        if header.lower().startswith("digest "):
            return {k.lower(): v1 or v2 for k, v1, v2 in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]*))', header[7:])}
    return None


class HikvisionDigestAuth(httpx.Auth):
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._challenge: dict[str, str] | None = None
        self._uses = 0
        self._issued_at = 0.0

    def _sign(self, request: httpx.Request) -> None:
        c = self._challenge
        assert c is not None
        self._uses += 1
        nc = f"{self._uses:08x}"
        cnonce = os.urandom(8).hex()
        uri = request.url.raw_path.decode()
        ha1 = _md5(f"{self._username}:{c['realm']}:{self._password}")
        ha2 = _md5(f"{request.method}:{uri}")
        qop = "auth" if "auth" in [q.strip() for q in c.get("qop", "").split(",")] else None
        response = (
            _md5(f"{ha1}:{c['nonce']}:{nc}:{cnonce}:{qop}:{ha2}") if qop else _md5(f"{ha1}:{c['nonce']}:{ha2}")
        )
        header = (
            f'Digest username="{self._username}", realm="{c["realm"]}", nonce="{c["nonce"]}", '
            f'uri="{uri}", response="{response}", algorithm=MD5'
        )
        if qop:
            header += f', qop={qop}, nc={nc}, cnonce="{cnonce}"'
        if c.get("opaque"):
            header += f', opaque="{c["opaque"]}"'
        request.headers["Authorization"] = header

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._challenge is not None and (
            self._uses >= MAX_NONCE_USES or time.monotonic() - self._issued_at > MAX_NONCE_AGE
        ):
            self._challenge = None  # nonce nearly used up or expired: get a fresh one below

        reused = self._challenge is not None
        if reused:
            self._sign(request)
        response = yield request
        if response.status_code != 401:
            return

        challenge = _parse_challenge(response)
        if challenge is None and reused:
            # Bare 401 for a used-up nonce: ask for a fresh challenge.
            self._challenge = None
            request.headers.pop("Authorization", None)
            response = yield request
            if response.status_code != 401:
                return
            challenge = _parse_challenge(response)
        if challenge is None:
            return

        self._challenge, self._uses, self._issued_at = challenge, 0, time.monotonic()
        self._sign(request)
        yield request
