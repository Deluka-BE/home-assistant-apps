"""Offline configuration checks only; never verifies or uses credentials."""

from dataclasses import dataclass, field
import base64
import json
import os
import re
import time
from collections.abc import Mapping


@dataclass(frozen=True)
class AuthConfig:
    developer_token: str | None = field(default=None, repr=False)
    music_user_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        source = os.environ if env is None else env
        return cls(source.get("APPLE_MUSIC_DEVELOPER_TOKEN"), source.get("APPLE_MUSIC_USER_TOKEN"))

    def diagnostics(self, *, now: float | None = None) -> dict:
        """Report syntax/readiness only. JWT signatures are NOT verified."""
        developer = _developer_status(self.developer_token, time.time() if now is None else now)
        user = _user_status(self.music_user_token)
        ready = developer == user == "configured"
        return {
            "status": "configured_unverified" if ready else "configuration_required",
            "developer_token": developer,
            "music_user_token": user,
            "error_code": "NONE" if ready else "AUTH_CONFIG_INVALID" if "invalid" in (developer, user) else "AUTH_CONFIG_MISSING",
            "signature_verified": False,
        }


def _user_status(value):
    if value is None or value == "":
        return "missing"
    # User tokens are opaque: only enforce safe bounded printable transport.
    return "configured" if len(value) <= 16384 and all(33 <= ord(c) <= 126 for c in value) else "invalid"


def _developer_status(value, now):
    if value is None or value == "":
        return "missing"
    if len(value) > 16384:
        return "invalid"
    try:
        parts = value.split(".")
        if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
            return "invalid"
        def decode(part):
            return base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
        header, claims = (json.loads(decode(p)) for p in parts[:2])
        signature = decode(parts[2])
        if not isinstance(header, dict) or not isinstance(claims, dict):
            return "invalid"
        issued, expiry = claims.get("iat"), claims.get("exp")
        if (header.get("alg") != "ES256" or not isinstance(header.get("kid"), str)
                or not header["kid"].strip() or not isinstance(claims.get("iss"), str)
                or not claims["iss"].strip() or len(signature) != 64
                or type(issued) is not int or type(expiry) is not int
                or not 0 <= issued <= now < expiry or expiry - issued > 15777000):
            return "invalid"
        return "configured"
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return "invalid"
