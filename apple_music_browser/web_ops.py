"""Bounded web writes using routes in the pinned upstream AMP/API modules.

No native server imports, redirects, retries, raw HTTP errors or credential dumps.
"""
import re
from typing import Literal
from pydantic import StrictBool

from applemusic_mcp import amp_api
import discovery
from playlist_ops import serialized

READ_TOOLS = ("web_rating", "web_auth_diagnostics")
WRITE_TOOLS = ("add_library_catalog", "remove_library_song", "delete_playlist_folder", "set_web_rating")


def _library_id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("An exact library ID of the required type is required")
    if value == "p.playlistsroot":
        raise ValueError("The root playlist folder cannot be deleted")
    return value


def _confirm(confirm):
    if confirm is not True:
        raise ValueError("Explicit confirm=true required")


def _request(method, path, *, public=False, params=None, body=None):
    rail = amp_api.API if public else amp_api.WEB
    if amp_api.throttled_recently(rail=rail):
        raise RuntimeError("Apple Music recently rate limited this API; wait before retrying")
    try:
        headers = amp_api._headers()
    except Exception:
        raise RuntimeError("Web credentials unavailable; inspect browser sign-in") from None
    try:
        kwargs = dict(headers=headers, timeout=amp_api.TIMEOUT, allow_redirects=False)
        if params is not None:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
        response = getattr(amp_api.requests, method)((discovery.API if public else amp_api.AMP) + path, **kwargs)
    except Exception:
        raise RuntimeError("Apple Music connection failed; outcome may be uncertain for writes; inspect before retrying") from None
    status = response.status_code
    amp_api.note_status(status, rail=rail)
    if status not in ((200,) if method == "get" else (200, 201, 202, 204)):
        reason = {401: "session unauthorized; inspect browser sign-in", 403: "access denied",
                  404: "resource or endpoint unavailable", 429: "rate limited; wait"}.get(status, "request failed")
        raise RuntimeError(f"Apple Music HTTP {status}: {reason}; no retry; inspect account before repeating a write")
    if method != "get":
        # Acknowledgment is not verification of eventual account state.
        # If a successful HTTP response carries API errors, fail closed too.
        if status != 204 and response.content:
            payload = _json(response)
            if payload.get("errors"):
                raise RuntimeError("Apple Music reported API errors; write outcome uncertain; inspect before retrying")
        return {"ok": True, "status": status, "acknowledged": True, "verified": False}
    return _json(response)


def _json(response):
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError("Invalid Apple Music response; write outcome may be uncertain") from None
    if not isinstance(payload, dict) or payload.get("errors"):
        raise RuntimeError("Invalid Apple Music response or API errors; no complete result")
    return payload


def _data(payload):
    data = payload.get("data")
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise RuntimeError("Invalid Apple Music resource data")
    return data


@serialized
def add_library_catalog(catalog_ids: list[str], content_type: Literal["songs", "albums"] = "songs", storefront: str = "us") -> dict:
    """Add 1..100 exact catalog songs or albums after catalog validation. One request, no retry or rollback; acknowledgment is not verification."""
    discovery._choice(content_type, {"songs", "albums"})
    discovery._store(storefront)
    if not isinstance(catalog_ids, list) or not 1 <= len(catalog_ids) <= 100:
        raise ValueError("Supply 1..100 catalog IDs")
    identifiers = list(dict.fromkeys(discovery._number(i) for i in catalog_ids))
    found = discovery.catalog_identifiers(identifiers, content_type, storefront)
    if found["missing"]:
        raise ValueError("Catalog items unavailable in the explicit storefront; nothing submitted")
    result = _request("post", "/me/library", public=True, params={f"ids[{content_type}]": ",".join(identifiers)})
    return dict(result, submitted=len(identifiers), content_type=content_type)


@serialized
def remove_library_song(library_song_id: str, confirm: StrictBool = False) -> dict:
    """Delete exactly one i.* library song with confirm=true. Can also affect playlists. No catalog IDs, search, batches or broad deletion."""
    _confirm(confirm)
    identifier = _library_id(library_song_id, "i.")
    return _request("delete", f"/me/library/songs/{identifier}")


@serialized
def delete_playlist_folder(folder_id: str, confirm: StrictBool = False) -> dict:
    """Delete one non-root p.* playlist folder with confirm=true, only after a strict empty-children read. Never recursively delete or move children."""
    _confirm(confirm)
    identifier = _library_id(folder_id, "p.")
    payload = _request("get", f"/me/library/playlist-folders/{identifier}/children")
    if _data(payload) or payload.get("next") is not None:
        raise ValueError("Folder is nonempty or incompletely read; move its children explicitly first")
    return _request("delete", f"/me/library/playlist-folders/{identifier}")


def _rating_path(catalog_id, content_type):
    discovery._choice(content_type, {"songs", "albums"})
    return f"/me/ratings/{content_type}/{discovery._number(catalog_id)}"


def web_rating(catalog_id: str, content_type: Literal["songs", "albums"] = "songs") -> dict:
    """Read a web love/dislike rating by catalog ID. Only a successful empty data list means unrated; 404/auth/network errors remain errors. No stars or favorites list."""
    payload = _request("get", _rating_path(catalog_id, content_type))
    data = _data(payload)
    if payload.get("next") is not None or len(data) > 1:
        raise RuntimeError("Unexpected rating response")
    value = None
    if data:
        attrs = data[0].get("attributes")
        value = attrs.get("value") if isinstance(attrs, dict) else None
        if type(value) is not int or value not in (-1, 1):
            raise RuntimeError("Invalid web rating value")
        if data[0].get("id") != catalog_id or data[0].get("type") != "ratings":
            raise RuntimeError("Unexpected rating identity")
    return {"value": value, "rating": {None: "unrated", 1: "love", -1: "dislike"}[value]}


@serialized
def set_web_rating(catalog_id: str, action: Literal["favorite", "love", "dislike", "clear"], content_type: Literal["songs", "albums"] = "songs", confirm: StrictBool = False) -> dict:
    """Set web love (+1, also called favorite) or dislike (-1); clear requires confirm=true. Favorite aliases love, not a verified favorites-list API."""
    path = _rating_path(catalog_id, content_type)
    discovery._choice(action, {"favorite", "love", "dislike", "clear"})
    if action == "clear":
        _confirm(confirm)
        return _request("delete", path)
    return _request("put", path, body={"attributes": {"value": -1 if action == "dislike" else 1}})


def web_auth_diagnostics() -> dict:
    """Probe AMP web access once, respecting cached throttling. Never resets login or returns tokens, headers, profile paths, claims or raw failures."""
    try:
        _data(_request("get", "/me/library/playlists", params={"limit": 1}))
        return {"ok": True, "web_access": "available", "read_only": True}
    except RuntimeError as exc:
        return {"ok": False, "web_access": "unverified", "error": str(exc), "read_only": True}
