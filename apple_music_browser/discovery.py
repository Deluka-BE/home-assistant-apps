"""Strict, read-only web-session API tools. Never import upstream's native server.

Routes mirror epheterson/applemusic-mcp at 6226528 (server.py). Public API
requests deliberately reuse AMP's harvested, origin-bound web credentials.
"""
import re
from typing import Literal
from urllib.parse import parse_qsl, urljoin, urlsplit

from applemusic_mcp import amp_api

API = "https://api.music.apple.com/v1"
READ_TOOLS = (
    "catalog_identifiers", "resolve_isrc", "album_tracks", "artist_discovery",
    "catalog_genres", "catalog_suggestions", "browse_library", "library_history",
    "music_recommendations", "music_charts", "account_storefront", "session_diagnostics",
)


def _choice(value, allowed):
    if value not in allowed:
        raise ValueError("Unsupported operation or resource type")
    return value


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,30}", value):
        raise ValueError("ASCII numeric catalog ID required")
    return value


def _store(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]{2}", value):
        raise ValueError("Explicit two-letter lowercase storefront required")
    return value


def _limit(value):
    if type(value) is not int or not 1 <= value <= 25:
        raise ValueError("Limit must be 1..25")
    return value


def _cursor(path, params, cursor):
    """Accept only same-route pagination; never forward credentials to a redirect."""
    if not cursor:
        return params
    if not isinstance(cursor, str) or len(cursor) > 4096:
        raise ValueError("Invalid pagination cursor")
    parsed = urlsplit(urljoin(API + path, cursor))
    expected = urlsplit(API + path)
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path) or parsed.fragment:
        raise ValueError("Pagination cursor must remain on the same API route")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(query) != len(pairs) or any(k not in {*params, "offset"} for k in query):
        raise ValueError("Unexpected pagination parameters")
    if any(k != "offset" and str(params[k]) != v for k, v in query.items()):
        raise ValueError("Pagination must preserve original filters and limit")
    if "offset" not in query or not re.fullmatch(r"[0-9]{1,9}", query["offset"]):
        raise ValueError("Pagination requires a nonnegative offset")
    return dict(params, offset=int(query["offset"]))


def _request(path, params):
    if amp_api.throttled_recently(rail=amp_api.API):
        raise RuntimeError("Apple Music API recently rate limited; wait before retrying")
    try:
        headers = amp_api._headers()
        response = amp_api.requests.get(API + path, headers=headers, params=params,
                                        timeout=amp_api.TIMEOUT, allow_redirects=False)
    except Exception:
        # Upstream auth/network exceptions can include tokens or sensitive URLs.
        raise RuntimeError("Apple Music web credentials unavailable or API connection failed; inspect browser sign-in") from None
    status = response.status_code
    amp_api.note_status(status, rail=amp_api.API)
    if status != 200:
        reason = {401: "web session unauthorized; sign in through the browser",
                  403: "access denied; check session, subscription and storefront",
                  404: "resource or endpoint unavailable; not an empty result",
                  429: "rate limited; wait before retrying"}.get(status, "API request failed; no automatic retry")
        raise RuntimeError(f"Apple Music HTTP {status}: {reason}")
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError("Apple Music returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise RuntimeError("Apple Music returned an invalid response object")
    if payload.get("errors"):
        raise RuntimeError("Apple Music reported API errors; no complete result")
    return payload


def _page(path, params, cursor=""):
    actual = _cursor(path, params, cursor)
    payload = _request(path, actual)
    data = payload.get("data")
    if not isinstance(data, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                                           or not isinstance(item.get("type"), str) for item in data):
        raise RuntimeError("Apple Music returned invalid resource data")
    nxt = payload.get("next")
    if nxt is not None:
        if not nxt:
            raise RuntimeError("Apple Music returned an invalid pagination cursor")
        following = _cursor(path, params, nxt)
        if following["offset"] <= int(actual.get("offset", 0)):
            raise RuntimeError("Apple Music returned non-advancing pagination")
    return {"data": data, "next": nxt}


def catalog_identifiers(identifiers: list[str], content_type: Literal["songs", "albums", "artists"] = "songs", storefront: str = "us") -> dict:
    """Batch catalog song/album/artist details. Return raw metadata and explicit missing IDs; no fuzzy matching."""
    _choice(content_type, {"songs", "albums", "artists"})
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 100:
        raise ValueError("Supply 1..100 catalog IDs")
    wanted = list(dict.fromkeys(_number(i) for i in identifiers))
    path = f"/catalog/{_store(storefront)}/{content_type}"
    data = _all(path, {"ids": ",".join(wanted)})
    if any(item["id"] not in wanted or item["type"] != content_type for item in data):
        raise RuntimeError("Apple Music returned unexpected catalog identifiers")
    return {"storefront": storefront, "data": data, "missing": [i for i in wanted if i not in {d["id"] for d in data}]}


def _all(path, params):
    data, cursor = [], ""
    for _ in range(100):
        page = _page(path, params, cursor)
        data.extend(page["data"])
        cursor = page["next"]
        if cursor is None:
            return data
    raise RuntimeError("Apple Music pagination exceeded 100 pages; no complete result")


def resolve_isrc(isrcs: list[str], storefront: str = "us") -> dict:
    """Resolve 1..100 ISRCs in batches of 25. Keep every candidate; never auto-select a release. Errors abort the result."""
    _store(storefront)
    if not isinstance(isrcs, list) or not 1 <= len(isrcs) <= 100:
        raise ValueError("Supply 1..100 ISRCs")
    wanted = []
    for raw in isrcs:
        if not isinstance(raw, str):
            raise ValueError("ISRC must be a string")
        code = raw.strip().upper().replace("-", "")
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", code):
            raise ValueError("Invalid ISRC; use CCXXXYYNNNNN, optionally hyphenated")
        if code not in wanted:
            wanted.append(code)
    matches = {code: {} for code in wanted}
    for start in range(0, len(wanted), 25):
        batch = wanted[start:start + 25]
        for item in _all(f"/catalog/{storefront}/songs", {"filter[isrc]": ",".join(batch)}):
            attrs = item.get("attributes")
            code = attrs.get("isrc") if isinstance(attrs, dict) else None
            if code not in batch or item["type"] != "songs" or not re.fullmatch(r"[0-9]{1,30}", item["id"]):
                raise RuntimeError("Apple Music returned an unexpected ISRC candidate")
            matches[code][item["id"]] = item
    return {"storefront": storefront, "matches": {k: list(v.values()) for k, v in matches.items()},
            "missing": [k for k, v in matches.items() if not v],
            "ambiguous": [k for k, v in matches.items() if len(v) > 1]}


def album_tracks(album_id: str, storefront: str = "us", limit: int = 25, cursor: str = "") -> dict:
    """One catalog album track page in Apple order; follow next using the same album, storefront and limit."""
    return _page(f"/catalog/{_store(storefront)}/albums/{_number(album_id)}/tracks", {"limit": _limit(limit)}, cursor)


def artist_discovery(artist_id: str, view: Literal["top-songs", "similar-artists"] = "top-songs", storefront: str = "us", limit: int = 25, cursor: str = "") -> dict:
    """One artist top-songs or similar-artists page by exact catalog ID, where Apple provides the view."""
    _choice(view, {"top-songs", "similar-artists"})
    return _page(f"/catalog/{_store(storefront)}/artists/{_number(artist_id)}/view/{view}", {"limit": _limit(limit)}, cursor)


def catalog_genres(storefront: str = "us", limit: int = 25, cursor: str = "") -> dict:
    """One catalog genre page, with Apple's IDs."""
    return _page(f"/catalog/{_store(storefront)}/genres", {"limit": _limit(limit)}, cursor)


def catalog_suggestions(term: str, storefront: str = "us") -> dict:
    """Up to 10 catalog search-term suggestions; not a migration match."""
    if not isinstance(term, str) or not term.strip() or len(term) > 200:
        raise ValueError("Search term must contain 1..200 characters")
    payload = _request(f"/catalog/{_store(storefront)}/search/suggestions", {"term": term, "kinds": "terms", "limit": 10})
    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("suggestions"), list):
        raise RuntimeError("Apple Music returned invalid suggestions")
    return {"suggestions": results["suggestions"]}


def browse_library(content_type: Literal["songs", "albums", "artists", "music-videos"] = "songs", limit: int = 25, cursor: str = "") -> dict:
    """One library page. Library IDs are distinct from catalog IDs; raw playParams may supply catalog mapping."""
    _choice(content_type, {"songs", "albums", "artists", "music-videos"})
    return _page(f"/me/library/{content_type}", {"limit": _limit(limit)}, cursor)


def library_history(kind: Literal["recently-added", "recently-played"] = "recently-added", limit: int = 25, cursor: str = "") -> dict:
    """One recently-added library or recently-played track page, where available. A 404 is an error."""
    path = {"recently-added": "/me/library/recently-added", "recently-played": "/me/recent/played/tracks"}
    _choice(kind, path)
    return _page(path[kind], {"limit": _limit(limit)}, cursor)


def music_recommendations(kind: Literal["recommendations", "heavy-rotation"] = "recommendations", limit: int = 10, cursor: str = "") -> dict:
    """One personalized recommendation-group or heavy-rotation page. Nested relationships are retained, not flattened."""
    _choice(kind, {"recommendations", "heavy-rotation"})
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Recommendation limit must be 1..10")
    path = "/me/recommendations" if kind == "recommendations" else "/me/history/heavy-rotation"
    return _page(path, {"limit": limit}, cursor)


def music_charts(content_type: Literal["songs", "albums", "music-videos"] = "songs", storefront: str = "us", limit: int = 25) -> dict:
    """First chart page, retaining chart metadata and nested next links. Chart continuation is not exposed."""
    _choice(content_type, {"songs", "albums", "music-videos"})
    payload = _request(f"/catalog/{_store(storefront)}/charts", {"types": content_type, "limit": _limit(limit)})
    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get(content_type), list):
        raise RuntimeError("Apple Music returned invalid charts")
    return {"storefront": storefront, "results": results, "first_page_only": True}


def account_storefront() -> dict:
    """Read the authenticated account storefront. Does not alter saved preferences or guess a country."""
    return _page("/me/storefront", {})


def session_diagnostics() -> dict:
    """Probe API access once (or report cached throttling). No tokens, profile paths, settings or audit history are exposed."""
    try:
        account_storefront()
        return {"ok": True, "api_access": "available", "auth": "captured_web_session", "read_only": True}
    except RuntimeError as exc:
        return {"ok": False, "api_access": "unverified", "error": str(exc), "read_only": True}
