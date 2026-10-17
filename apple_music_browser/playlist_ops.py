"""Conservative playlist workflows for the pinned, cross-platform AMP API."""
from functools import wraps
import threading
from urllib.parse import quote, urljoin, urlsplit
from applemusic_mcp import amp_api

_lock = threading.RLock()


def serialized(function):
    """Serialize facade writes with multi-step playlist workflows."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            return function(*args, **kwargs)
    return wrapped


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonempty ID required")
    return quote(value, safe="")


def _catalog(value):
    return isinstance(value, str) and value.isascii() and value.isdigit()


def _tracks(playlist_id):
    # Upstream get_tracks swallows failures, including failures on later pages.
    url = f"{amp_api.AMP}/me/library/playlists/{_id(playlist_id)}/tracks?limit=100"
    seen, tracks = set(), []
    headers = amp_api._headers()
    while url:
        if url in seen:
            raise RuntimeError("Repeated pagination URL")
        seen.add(url)
        response = amp_api.requests.get(url, headers=headers, timeout=amp_api.TIMEOUT)
        amp_api.note_status(response.status_code)
        if response.status_code != 200:
            raise RuntimeError(amp_api._err(response, "read playlist tracks"))
        payload = response.json()
        if not isinstance(payload.get("data"), list):
            raise RuntimeError("Missing playlist track data")
        for item in payload["data"]:
            attrs = item.get("attributes", {})
            params = attrs.get("playParams") or {}
            catalog = params.get("catalogId")
            if not catalog and params.get("kind") == "song" and _catalog(params.get("id")):
                catalog = params["id"]
            _id(item["id"])
            tracks.append({"relationship_id": item["id"],
                           "library_id": item["id"] if item.get("type") == "library-songs" else None,
                           "catalog_id": catalog if _catalog(catalog) else None,
                           "name": attrs.get("name", ""), "artist": attrs.get("artistName", "")})
        nxt = payload.get("next")
        url = urljoin(amp_api.AMP + "/", nxt) if nxt else None
        if url and (urlsplit(url).netloc != urlsplit(amp_api.AMP).netloc or
                    urlsplit(url).scheme != "https" or
                    urlsplit(url).path != urlsplit(amp_api.AMP).path +
                    f"/me/library/playlists/{_id(playlist_id)}/tracks"):
            raise RuntimeError("Unexpected pagination URL")
    return tracks


def _item(track):
    if track["catalog_id"]:
        return track["catalog_id"], "songs"
    if track["library_id"]:
        return track["library_id"], "library-songs"
    raise ValueError("Track has no usable catalog or library song ID")


def search_playlist_tracks(playlist_id: str, term: str, limit: int = 25) -> list[dict]:
    """Search one complete playlist by case-insensitive title/artist substring."""
    if not term.strip() or not 1 <= limit <= 100:
        raise ValueError("Nonempty term and limit 1..100 required")
    with _lock:
        tracks = _tracks(playlist_id)
    needle = term.strip().casefold()
    return [dict(track, position=index + 1) for index, track in enumerate(tracks)
            if needle in track["name"].casefold() or needle in track["artist"].casefold()][:limit]


def copy_playlist(playlist_id: str, name: str, description: str = "") -> dict:
    """Copy tracks in source order into a new playlist; retain partial copies on failure."""
    if not name.strip():
        raise ValueError("Playlist name required")
    result = {"ok": False, "playlist_id": None, "stage": "read_source", "submitted_tracks": 0}
    with _lock:
        try:
            items = [_item(track) for track in _tracks(playlist_id)]
            result["stage"] = "create"
            ok, detail = amp_api.create_playlist(name, description)
            if not ok:
                raise RuntimeError(detail)
            _id(detail)
            result["playlist_id"] = detail
            result["stage"] = "add"
            for start in range(0, len(items), 100):
                batch = items[start:start + 100]
                ok, detail = amp_api.add_tracks(result["playlist_id"], batch)
                if not ok:
                    raise RuntimeError(detail)
                result["submitted_tracks"] += len(batch)
            result["stage"] = "verify"
            target = _tracks(result["playlist_id"])
            if len(target) != len(items):
                raise RuntimeError("Copy count mismatch")
            actual = [(t["library_id"], "library-songs") if expected[1] == "library-songs"
                      else _item(t) for t, expected in zip(target, items)]
            if actual != items:
                raise RuntimeError("Copy order or track identity mismatch")
            result.update(ok=True, stage="complete")
        except Exception as exc:
            result["error"] = str(exc)
    return result


def replace_playlist_track(playlist_id: str, relationship_id: str, replacement_catalog_id: str) -> dict:
    """Append and verify a catalog replacement before removing a unique original relationship."""
    _id(playlist_id)
    _id(relationship_id)
    if not _catalog(replacement_catalog_id):
        raise ValueError("ASCII numeric replacement catalog ID required")
    result = {"ok": False, "stage": "read_original", "removal_attempted": False}
    with _lock:
        try:
            before = _tracks(playlist_id)
            originals = [t for t in before if t["relationship_id"] == relationship_id]
            if len(originals) != 1:
                raise ValueError("Original relationship must occur exactly once; mode=all removal is unsafe otherwise")
            if originals[0]["catalog_id"] == replacement_catalog_id:
                raise ValueError("Replacement is already the original track")
            result["stage"] = "add"
            ok, detail = amp_api.add_tracks(playlist_id, [replacement_catalog_id])
            if not ok:
                raise RuntimeError(detail)
            result["stage"] = "verify_replacement"
            after = _tracks(playlist_id)
            # Exact prefix ensures no observed concurrent edit; count increase
            # proves an add even if this catalog song already existed.
            if (len(after) != len(before) + 1 or after[:-1] != before or
                    after[-1]["catalog_id"] != replacement_catalog_id or
                    after[-1]["relationship_id"] in {t["relationship_id"] for t in before}):
                raise RuntimeError("Replacement addition/order unverified; original retained")
            result["stage"] = "remove_original"
            result["removal_attempted"] = True
            ok, detail = amp_api.remove_track(playlist_id, relationship_id)
            if not ok:
                raise RuntimeError(detail + "; removal outcome may be uncertain; inspect playlist before retrying")
            result["stage"] = "verify_removal"
            final = _tracks(playlist_id)
            if final != [t for t in after if t["relationship_id"] != relationship_id]:
                raise RuntimeError("Removal outcome unverified; inspect playlist before retrying")
            result.update(ok=True, stage="complete")
        except Exception as exc:
            result["error"] = str(exc)
    return result
