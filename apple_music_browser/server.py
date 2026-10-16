"""Restricted MCP facade over the pinned upstream browser engine.

Do not import upstream server: its broad tools include playlist operations.
"""
import atexit
import threading
import time
from mcp.server.fastmcp import FastMCP
from applemusic_mcp import browser
from applemusic_mcp.auth import save_user_token

mcp = FastMCP("Apple Music Browser", host="127.0.0.1", port=8765,
              stateless_http=True, json_response=True)


@mcp.tool()
def play_song(catalog_id: str) -> tuple[bool, str]:
    """Play one catalog song by numeric Apple Music ID. Requires web sign-in."""
    if not catalog_id.isascii() or not catalog_id.isdigit():
        raise ValueError("A numeric catalog song ID is required")
    return browser.play_catalog_track(catalog_id)


@mcp.tool()
def control(action: str, seconds: float = 0) -> tuple[bool, str]:
    """Playback transport: play, pause, stop, next, previous, seek."""
    if action not in {"play", "pause", "stop", "next", "previous", "seek"}:
        raise ValueError("Unsupported transport action")
    if not 0 <= seconds <= 86400:
        raise ValueError("Seek must be between 0 and 86400 seconds")
    return browser.playback_control(action, seconds)


@mcp.tool()
def now_playing() -> dict | None:
    """Current playing track. Does not read library or playlists."""
    return browser.now_playing()


@mcp.tool()
def volume(level: int) -> tuple[bool, str]:
    """Set player volume from 0 to 100."""
    if not 0 <= level <= 100:
        raise ValueError("Volume must be between 0 and 100")
    return browser.browser_settings(volume=level)


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
    mcp.run(transport="streamable-http")
