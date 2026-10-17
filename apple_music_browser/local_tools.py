"""Process-local preferences, catalog cache and privacy-preserving audit.

Deliberately independent of upstream credential/config/cache files. No file deletion.
"""
from collections import OrderedDict, deque
from copy import deepcopy
from functools import wraps
import threading
import time
from typing import Literal
from pydantic import StrictBool

from applemusic_mcp import browser
import discovery

READ_TOOLS = ("browser_settings_info", "local_preferences", "audit_summary", "cached_catalog_identifiers")
WRITE_TOOLS = ("set_browser_settings", "set_local_preferences", "clear_catalog_cache")
_lock = threading.RLock()
_audit = deque(maxlen=100)
_cache = OrderedDict()
_ttl = 0
_sequence = 0


def audited(function, write=False):
    """Record only fixed tool names, timestamps and outcome, never arguments/results/errors."""
    @wraps(function)
    def call(*args, **kwargs):
        global _sequence
        outcome = "error"
        try:
            result = function(*args, **kwargs)
            outcome = "error" if isinstance(result, dict) and result.get("ok") is False else "returned"
            return result
        finally:
            with _lock:
                if write:
                    _cache.clear()
                _sequence += 1
                _audit.append({"sequence": _sequence, "time": int(time.time()),
                               "tool": function.__name__, "outcome": outcome})
    return call


def _settings(volume=-1, shuffle=None, repeat=None):
    try:
        ok, state = browser.browser_settings(volume=volume, shuffle=shuffle, repeat=repeat)
    except Exception:
        raise RuntimeError("Browser settings unavailable; inspect the signed-in web player") from None
    if not ok:
        raise RuntimeError("Browser settings unavailable; changes may be uncertain; inspect the web player")
    # Upstream returns a formatted state string. Do not pass arbitrary browser errors through.
    import re
    if not isinstance(state, str) or not re.fullmatch(r"volume=[0-9.]+ shuffle=(on|off) repeat=(none|one|all)", state):
        raise RuntimeError("Unexpected browser settings response; inspect the web player")
    return {"ok": True, "state": state}


def browser_settings_info() -> dict:
    """Read web-player volume/shuffle/repeat. May initialize the signed-in player; no native output routing or quality controls."""
    return _settings()


def set_browser_settings(volume: int = -1, shuffle: bool | None = None, repeat: Literal["none", "one", "all"] | None = None) -> dict:
    """Set browser volume 0..100 (-1 leaves it), shuffle or repeat. Changes only the web player, not Music.app or HA audio routing."""
    if type(volume) is not int or not -1 <= volume <= 100:
        raise ValueError("Volume must be -1 or 0..100")
    if shuffle is not None and type(shuffle) is not bool:
        raise ValueError("Shuffle must be boolean")
    if repeat is not None:
        discovery._choice(repeat, {"none", "one", "all"})
    return _settings(volume, shuffle, repeat)


def local_preferences() -> dict:
    """Inspect only facade cache preferences; process-local and reset on restart. No upstream config, secrets, paths or account preferences."""
    with _lock:
        return {"cache_ttl_seconds": _ttl, "cache_entries": len(_cache), "cache_capacity": 128,
                "persistent": False, "cache_scope": "catalog metadata only"}


def set_local_preferences(cache_ttl_seconds: int) -> dict:
    """Opt in to catalog metadata caching for 0..300 seconds (0 disables). Process-local; no auth/engine/storefront/auto-add changes."""
    global _ttl
    if type(cache_ttl_seconds) is not int or not 0 <= cache_ttl_seconds <= 300:
        raise ValueError("Cache TTL must be 0..300 seconds")
    with _lock:
        _ttl = cache_ttl_seconds
        _cache.clear()
        return local_preferences()


def cached_catalog_identifiers(identifiers: list[str], content_type: Literal["songs", "albums", "artists"] = "songs", storefront: str = "us") -> dict:
    """Optional short-lived catalog-only metadata cache; country/type/IDs scoped, default off. No library/auth/ratings cache; writes always use fresh catalog validation."""
    discovery._choice(content_type, {"songs", "albums", "artists"})
    discovery._store(storefront)
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 100:
        raise ValueError("Supply 1..100 catalog IDs")
    wanted = tuple(dict.fromkeys(discovery._number(i) for i in identifiers))
    key = (storefront, content_type, wanted)
    # Hold through the read so a preference change/cache clear cannot repopulate
    # from an already in-flight request after clearing.
    with _lock:
        now = time.monotonic()
        for expired in [k for k, (until, _) in _cache.items() if until <= now]:
            del _cache[expired]
        if _ttl and key in _cache:
            until, data = _cache[key]
            _cache.move_to_end(key)
            return dict(deepcopy(data), cached=True, age_seconds=max(0, int(_ttl - (until - now))))
        data = discovery.catalog_identifiers(list(wanted), content_type, storefront)
        if _ttl:
            _cache[key] = (time.monotonic() + _ttl, deepcopy(data))
            while len(_cache) > 128:
                _cache.popitem(last=False)
        return dict(data, cached=False, age_seconds=0)


def clear_catalog_cache(confirm: StrictBool = False) -> dict:
    """Clear only this process's facade catalog cache; requires confirm=true. Never deletes browser, credentials, upstream cache, exports or audit files."""
    if confirm is not True:
        raise ValueError("Explicit confirm=true required")
    with _lock:
        count = len(_cache)
        _cache.clear()
    return {"ok": True, "cleared_entries": count}


def audit_summary(limit: int = 20) -> dict:
    """Last 1..100 facade audit events in this process only. Fixed tool name, sequence, timestamp and outcome; no IDs, arguments, metadata, tokens or raw errors. Not a durable account history."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Audit limit must be 1..100")
    with _lock:
        return {"events": deepcopy(list(_audit)[-limit:]), "capacity": 100, "persistent": False,
                "scope": "0.1.6 web and local tools only; validation before dispatch is not recorded"}
