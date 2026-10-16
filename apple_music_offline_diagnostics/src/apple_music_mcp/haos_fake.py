"""Offline HAOS diagnostics; never starts a listening MCP server."""

import signal
import subprocess
import sys
import threading
import time
import os

STARTUP_TIMEOUT = 20.0


async def self_test():
    """Exercise the existing MCP protocol entirely through in-memory ASGI."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from apple_music_mcp.auth import AuthConfig
    from apple_music_mcp.server import HOST, PORT, create_server

    server = create_server(AuthConfig())
    if server.catalog is not None or server.web_session is not None:
        raise RuntimeError("OFFLINE_STATE_INVALID")
    app = server.streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), trust_env=False,
        ) as client:
            async with streamable_http_client(
                f"http://{HOST}:{PORT}/mcp", http_client=client,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = [tool.name for tool in (await session.list_tools()).tools]
                    if names != ["diagnostics", "search_songs", "list_playlists"]:
                        raise RuntimeError("TOOLS_INVALID")
                    result = await session.call_tool("diagnostics", {})
                    expected = {
                        "status": "ok", "transport": "streamable-http",
                        "apple_api_enabled": False, "credentials_required": False,
                        "auth": AuthConfig().diagnostics(),
                    }
                    if result.isError or result.structuredContent != expected:
                        raise RuntimeError("DIAGNOSTICS_INVALID")


def startup(stop, *, timeout=STARTUP_TIMEOUT):
    """Watch imports, protocol work and cleanup from outside the async runtime."""
    # Deliberate allowlist: do not pass inherited credentials/proxies to worker.
    env = {"PYTHONPATH": os.pathsep.join(sys.path), "PYTHONDONTWRITEBYTECODE": "1"}
    with subprocess.Popen(
        [sys.executable, "-m", "apple_music_mcp.haos_fake", "--worker"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
    ) as worker:
        deadline = time.monotonic() + timeout
        try:
            while worker.poll() is None:
                if stop.wait(0.05):
                    return False
                if time.monotonic() >= deadline:
                    raise TimeoutError
            return worker.returncode == 0
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=1)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--worker"]:
        try:
            import asyncio
            asyncio.run(self_test())
            return 0
        except BaseException:
            return 1
    if args not in ([], ["--self-test"]):
        print("OFFLINE_ARGUMENT_ERROR", flush=True)
        return 2
    stop = threading.Event()
    previous = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        if not startup(stop):
            if stop.is_set():
                return 0
            raise RuntimeError
        if stop.is_set():
            return 0
        print("OFFLINE_SELF_TEST_OK", flush=True)
        if not args:
            stop.wait()
        return 0
    except BaseException:
        print("OFFLINE_STARTUP_FAILED", flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
