"""Restricted MCP facade over the pinned upstream browser engine.

Expose selected cross-platform web API tools; never import the macOS AppleScript server.
"""
import atexit
import signal
import threading
import time
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from security import AuthenticatedMCP, load_settings
from applemusic_mcp import browser, amp_api
from applemusic_mcp.auth import save_user_token
from playlist_ops import search_playlist_tracks, copy_playlist, replace_playlist_track, serialized
import discovery
import web_ops
import local_tools
from mcp.types import ToolAnnotations

mcp = FastMCP("Apple Music Browser", host="0.0.0.0", port=8765,
              stateless_http=True, json_response=True, max_request_body_size=65536,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


def play_song(catalog_id: str) -> tuple[bool, str]:
    """Play one catalog song by numeric Apple Music ID. Requires web sign-in."""
    if not catalog_id.isascii() or not catalog_id.isdigit():
        raise ValueError("A numeric catalog song ID is required")
    return browser.play_catalog_track(catalog_id)


def control(action: str, seconds: float = 0) -> tuple[bool, str]:
    """Playback transport: play, pause, stop, next, previous, seek."""
    if action not in {"play", "pause", "stop", "next", "previous", "seek"}:
        raise ValueError("Unsupported transport action")
    if not 0 <= seconds <= 86400:
        raise ValueError("Seek must be between 0 and 86400 seconds")
    return browser.playback_control(action, seconds)


def now_playing() -> dict | None:
    """Current playing track. Does not read library or playlists."""
    return browser.now_playing()


def volume(level: int) -> tuple[bool, str]:
    """Set player volume from 0 to 100."""
    if not 0 <= level <= 100:
        raise ValueError("Volume must be between 0 and 100")
    return browser.browser_settings(volume=level)


# The upstream AMP web API reuses the signed-in browser's captured user token.
# IDs (rather than names) are required for mutations to avoid ambiguity.
@mcp.tool()
def list_playlists() -> list[dict]:
    """List Apple Music library playlists, including their IDs and editability."""
    return amp_api.list_playlists()


@mcp.tool()
def playlist_tracks(playlist_id: str) -> list[dict]:
    """List songs in a playlist, including track relationship IDs for removal."""
    return amp_api.get_tracks(playlist_id)


@mcp.tool()
def search_library(term: str, limit: int = 25) -> list[dict]:
    """Search songs in the signed-in user's library."""
    if not term.strip() or not 1 <= limit <= 100:
        raise ValueError("Nonempty term and limit 1..100 required")
    return amp_api.search_library_songs(term, limit)


@mcp.tool()
def search_catalog(term: str, limit: int = 5) -> list[dict]:
    """Search Apple Music catalog songs for IDs to add to playlists."""
    if not term.strip() or not 1 <= limit <= 25:
        raise ValueError("Nonempty term and limit 1..25 required")
    return amp_api.search_catalog_songs(term, limit)


@mcp.tool()
@serialized
def create_playlist(name: str, description: str = "") -> tuple[bool, str]:
    """Create a new Apple Music playlist in the signed-in account."""
    if not name.strip():
        raise ValueError("Playlist name is required")
    return amp_api.create_playlist(name, description)


@mcp.tool()
@serialized
def rename_playlist(playlist_id: str, new_name: str) -> tuple[bool, str]:
    """Rename a playlist by its ID."""
    if not playlist_id.strip() or not new_name.strip():
        raise ValueError("Playlist ID and new name are required")
    return amp_api.rename_playlist(playlist_id, new_name)


@mcp.tool()
@serialized
def add_playlist_tracks(playlist_id: str, catalog_ids: list[str]) -> tuple[bool, str]:
    """Add catalog songs by their IDs to an existing playlist."""
    if not playlist_id.strip() or not catalog_ids or len(catalog_ids) > 100 or any(not x.isdigit() for x in catalog_ids):
        raise ValueError("Playlist ID and 1..100 numeric catalog IDs required")
    return amp_api.add_tracks(playlist_id, catalog_ids)


@mcp.tool()
@serialized
def remove_playlist_track(playlist_id: str, relationship_id: str) -> tuple[bool, str]:
    """Remove a song from a playlist by its playlist-track relationship ID, not catalog ID."""
    if not playlist_id.strip() or not relationship_id.strip():
        raise ValueError("Playlist ID and relationship ID required")
    return amp_api.remove_track(playlist_id, relationship_id)


@mcp.tool()
@serialized
def delete_playlist(playlist_id: str, confirm: bool = False) -> tuple[bool, str]:
    """Permanently delete a playlist by ID; requires explicit confirm=true."""
    if not playlist_id.strip() or not confirm:
        raise ValueError("Playlist ID and confirm=true required")
    return amp_api.delete_playlist(playlist_id)


@mcp.tool()
def list_playlist_folders() -> list[dict]:
    """List folders containing Apple Music playlists."""
    return amp_api.list_folders()


@mcp.tool()
@serialized
def create_playlist_folder(name: str, parent_id: str = "p.playlistsroot") -> tuple[bool, str]:
    """Create a playlist folder."""
    if not name.strip():
        raise ValueError("Folder name required")
    return amp_api.create_folder(name, parent_id)


@mcp.tool()
@serialized
def move_playlist_to_folder(playlist_id: str, folder_id: str = "p.playlistsroot") -> tuple[bool, str]:
    """Move a playlist into a folder by ID."""
    if not playlist_id.strip() or not folder_id.strip():
        raise ValueError("Playlist and folder IDs required")
    return amp_api.move_playlist_to_folder(playlist_id, folder_id)


mcp.tool()(search_playlist_tracks)
mcp.tool()(copy_playlist)
mcp.tool()(replace_playlist_track)

for tool_name in discovery.READ_TOOLS:
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                        idempotentHint=True, openWorldHint=True))(
        getattr(discovery, tool_name))

for module in (web_ops, local_tools):
    for tool_name in (*module.READ_TOOLS, *module.WRITE_TOOLS):
        write = tool_name in module.WRITE_TOOLS
        destructive = tool_name in {"remove_library_song", "delete_playlist_folder", "set_web_rating", "clear_catalog_cache"}
        mcp.tool(annotations=ToolAnnotations(readOnlyHint=not write, destructiveHint=destructive,
                                            idempotentHint=not write, openWorldHint=True))(
            local_tools.audited(getattr(module, tool_name), write=write))


def open_browser():
    # Start the SAME upstream-owned persistent browser used by all MCP calls.
    # Navigate only once; never interrupt a human's login with polling navigation.
    def restore_and_open(page):
        browser._ensure_session_cookie(page)
        page.goto("https://music.apple.com", wait_until="domcontentloaded")
    browser._engine.submit(restore_and_open)


def preserve_web_session():
    # Upstream notes that Chrome session cookies may not survive profile restart.
    # Capture only the web player's session token after normal human login. Never
    # navigate during login/2FA, request a developer token, or log credentials.
    previous = None
    while True:
        try:
            token = browser.capture_user_token()
            if token and token != previous:
                save_user_token(token)
                previous = token
        except Exception:
            pass  # Retry; the human may still be in the Apple sign-in flow.
        time.sleep(5)


if __name__ == "__main__":
    atexit.register(browser._engine.shutdown)
    open_browser()
    threading.Thread(target=preserve_web_session, daemon=True).start()
    settings = load_settings()
    if settings is None:
        # Keep the persistent browser and its token preservation alive.
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        print("MCP disabled; browser remains available", flush=True)
        stopped.wait()
    else:
        token, certfile, keyfile = settings
        uvicorn.run(AuthenticatedMCP(mcp.streamable_http_app(), token),
                    host="0.0.0.0", port=8765, ssl_certfile=certfile,
                    ssl_keyfile=keyfile, access_log=False,
                    limit_concurrency=32, timeout_keep_alive=5)
