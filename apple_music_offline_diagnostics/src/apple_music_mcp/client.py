"""Standalone, read-only catalog search. No MCP registration or implicit transport.

Callers must provide a transport and close the client with ``async with``.
Only developer credentials are used; user credentials are never transmitted.
"""

import asyncio
import json
import re
import unicodedata

import httpx

from apple_music_mcp.auth import AuthConfig
from apple_music_mcp.logging import http_status, operation

MAX_RESPONSE_BYTES = 256 * 1024
TOTAL_TIMEOUT_SECONDS = 10


class AppleMusicError(Exception):
    """Safe public failure: code and optional HTTP status, never upstream text."""

    def __init__(self, code: str, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def validate_search_input(storefront, term, limit):
    """Validate without coercion or echoing potentially secret input."""
    if (not isinstance(storefront, str) or not re.fullmatch(r"[a-z]{2}", storefront)
            or not isinstance(term, str) or not 1 <= len(term) <= 100
            or term != term.strip()
            or any(unicodedata.category(c).startswith("C") for c in term)
            or len(term.encode("utf-8")) > 400
            or type(limit) is not int or not 1 <= limit <= 25):
        raise AppleMusicError("INVALID_INPUT")


class AppleMusicClient:
    """GET /v1/catalog/{storefront}/search, songs only, one page, no retries.

    Mandatory transport injection prevents accidental network access at construction.
    A supplied transport must not implement its own retries. The client owns it.
    """

    def __init__(self, config: AuthConfig, *, transport: httpx.AsyncBaseTransport):
        self._config = config
        self._http = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=3, read=5, write=5, pool=1),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._http.aclose()

    async def search_songs(self, storefront: str, term: str, *, limit: int = 10) -> dict:
        """Return validated songs data, without following links or pagination.

        storefront: two lowercase ASCII letters. term: 1–100 trimmed characters,
        at most 400 UTF-8 bytes, no control/format/surrogate characters.
        limit: integer 1–25 (booleans rejected). Errors expose fixed codes only.
        """
        marker = http_status.set(None)
        try:
            with operation("http"):
                validate_search_input(storefront, term, limit)
                # Reuse existing offline checks without requiring a Music User Token.
                auth = self._config.diagnostics()
                state = auth["developer_token"]
                if state != "configured":
                    raise AppleMusicError("AUTH_CONFIG_MISSING" if state == "missing" else "AUTH_CONFIG_INVALID")
                failure = None
                try:
                    async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                        async with self._http.stream(
                            "GET", f"https://api.music.apple.com/v1/catalog/{storefront}/search",
                            params={"term": term, "types": "songs", "limit": limit},
                            headers={"Authorization": f"Bearer {self._config.developer_token}"},
                        ) as response:
                            http_status.set(response.status_code)
                            status = response.status_code
                            if status != 200:
                                code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 429: "RATE_LIMITED"}.get(
                                    status, "UPSTREAM_UNAVAILABLE" if 500 <= status <= 599 else "HTTP_ERROR")
                                raise AppleMusicError(code, status)
                            # Do not decompress untrusted payloads (including decompression bombs).
                            if response.headers.get("content-encoding", "identity").lower() != "identity":
                                raise AppleMusicError("INVALID_RESPONSE", status)
                            if response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                                raise AppleMusicError("INVALID_RESPONSE", status)
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                    raise AppleMusicError("RESPONSE_TOO_LARGE", status)
                                body.extend(chunk)
                            return _parse(body, limit)
                except (httpx.TimeoutException, TimeoutError):
                    failure = "TIMEOUT"
                except httpx.RequestError:
                    failure = "TRANSPORT_ERROR"
                # Raise outside the exception handler; no secret-bearing exception chain.
                raise AppleMusicError(failure)
        finally:
            http_status.reset(marker)


def _parse(body: bytes, limit: int) -> dict:
    invalid = False
    try:
        payload = json.loads(body, parse_constant=_reject_constant)
        songs = payload["results"].get("songs", {"data": []})
        data = songs["data"]
        if not isinstance(data, list) or len(data) > limit:
            invalid = True
        else:
            for item in data:
                if (not isinstance(item, dict) or item.get("type") != "songs"
                        or not isinstance(item.get("id"), str) or not 1 <= len(item["id"]) <= 128
                        or not isinstance(item.get("attributes"), dict)
                        or any(not isinstance(item["attributes"].get(k), str)
                               or len(item["attributes"][k]) > 1000 for k in ("name", "artistName"))):
                    invalid = True
                    break
        if not invalid:
            # Narrow output: discard links, pagination, unexpected fields and resources.
            return {"songs": [{"id": item["id"], "name": item["attributes"]["name"],
                               "artist_name": item["attributes"]["artistName"]} for item in data]}
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        pass
    raise AppleMusicError("INVALID_RESPONSE", 200)


def _reject_constant(value):
    raise ValueError("Invalid JSON constant")
