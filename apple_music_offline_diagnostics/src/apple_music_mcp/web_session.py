"""Optional read-only web-session rail, independent of catalog auth.

No credential discovery, persistence, login or refresh exists.
The caller owns logging configuration; use the existing allowlisted formatter.
"""

import asyncio
from dataclasses import dataclass, field
import json
import re
import unicodedata

import httpx

from apple_music_mcp.logging import http_status, operation

MAX_RESPONSE_BYTES = 64 * 1024
TOTAL_TIMEOUT_SECONDS = 10


class WebSessionError(Exception):
    """Fixed public codes only; never retains requests, bodies or credentials."""

    def __init__(self, code, status_code=None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class WebSessionAuth:
    """Explicit memory-only session values from protected runtime configuration."""

    web_token: str | None = field(default=None, repr=False)
    music_user_token: str | None = field(default=None, repr=False)

    def validate(self):
        values = (self.web_token, self.music_user_token)
        if any(value is None or value == "" for value in values):
            raise WebSessionError("AUTH_CONFIG_MISSING")
        if any(not isinstance(value, str) or not 1 <= len(value) <= 16384
               or not re.fullmatch(r"[\x21-\x7e]+", value) for value in values):
            raise WebSessionError("AUTH_CONFIG_INVALID")


class WebSessionClient:
    """One read-only page using an exact mock or explicitly opted-in live adapter.

    Owns/closes the injected transport. No retries, redirects or pagination.
    Live HTTP requires a separate literal boolean opt-in and isolated adapter.
    """

    def __init__(self, auth: WebSessionAuth, *, transport: httpx.AsyncBaseTransport,
                 enabled: bool = False, live_opt_in: bool = False):
        from apple_music_mcp.web_session_http import LiveWebSessionTransport

        if type(live_opt_in) is not bool:
            raise WebSessionError("INVALID_HTTP_CONFIG")
        if type(transport) is LiveWebSessionTransport:
            if not live_opt_in or enabled is not True:
                raise WebSessionError("BACKEND_DISABLED")
        elif type(transport) is not httpx.MockTransport:
            raise WebSessionError("MOCK_TRANSPORT_REQUIRED")
        if type(enabled) is not bool or type(auth) is not WebSessionAuth:
            raise WebSessionError("INVALID_INPUT")
        self._auth = auth
        self._enabled = enabled
        self._http = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=3, read=5, write=5, pool=1),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._http.aclose()

    async def list_playlists(self, *, limit: int = 10) -> dict:
        marker = http_status.set(None)
        try:
            with operation("http"):
                if not self._enabled:
                    raise WebSessionError("BACKEND_DISABLED")
                if type(limit) is not int or not 1 <= limit <= 25:
                    raise WebSessionError("INVALID_INPUT")
                self._auth.validate()
                failure = None
                try:
                    async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                        async with self._http.stream(
                            "GET", "https://amp-api.music.apple.com/v1/me/library/playlists",
                            params={"limit": limit}, headers={
                                "Authorization": f"Bearer {self._auth.web_token}",
                                "Music-User-Token": self._auth.music_user_token,
                                "Origin": "https://music.apple.com",
                            },
                        ) as response:
                            status = response.status_code
                            http_status.set(status)
                            if status != 200:
                                code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 429: "RATE_LIMITED"}.get(
                                    status, "UPSTREAM_UNAVAILABLE" if 500 <= status <= 599 else "HTTP_ERROR")
                                raise WebSessionError(code, status)
                            if (response.headers.get("content-encoding", "identity").lower() != "identity"
                                    or response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"):
                                raise WebSessionError("INVALID_RESPONSE", status)
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                    raise WebSessionError("RESPONSE_TOO_LARGE", status)
                                body.extend(chunk)
                            return _parse(body, limit)
                except (httpx.TimeoutException, TimeoutError):
                    failure = "TIMEOUT"
                except WebSessionError:
                    raise
                except Exception:
                    # Injected handlers may raise arbitrary secret-bearing exceptions.
                    failure = "TRANSPORT_ERROR"
                raise WebSessionError(failure)
        finally:
            http_status.reset(marker)


def _reject_constant(value):
    raise ValueError("INVALID_RESPONSE")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("INVALID_RESPONSE")
        result[key] = value
    return result


def _parse(body, limit):
    try:
        payload = json.loads(body, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        data = payload["data"]
        if not isinstance(data, list) or len(data) > limit:
            raise ValueError
        playlists = []
        for item in data:
            attrs = item["attributes"]
            if (item["type"] != "library-playlists"
                    or not isinstance(item["id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9.\-]{1,128}", item["id"])
                    or not isinstance(attrs["name"], str)
                    or not 1 <= len(attrs["name"]) <= 500
                    or any(unicodedata.category(c).startswith("C") for c in attrs["name"])
                    or type(attrs["canEdit"]) is not bool):
                raise ValueError
            playlists.append({"id": item["id"], "name": attrs["name"], "can_edit": attrs["canEdit"]})
        return {"playlists": playlists, "has_more": "next" in payload}
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        pass
    raise WebSessionError("INVALID_RESPONSE", 200)
