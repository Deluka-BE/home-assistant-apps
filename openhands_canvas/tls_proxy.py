"""Fail-closed replacement for the unsafe unauthenticated TLS/TCP relay."""
import asyncio
import ssl
from contextlib import suppress


def context(certfile, keyfile):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


async def close(writer):
    writer.close()
    with suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), 2)


class SecurityBlocked(RuntimeError):
    """The pinned upstream access boundary has not been verified."""


BLOCKED_REASON = ('startup_blocked image_rootfs_source_match=unverified '
                  'vscode_installed_behavior=unverified browser_output_audit=incomplete '
                  'supervisor_authentication_and_binding=unverified')


def require_access_design():
    # Deliberately unconditional. No option/env flag can bypass this gate.
    # Replace only with a reviewed authenticated frontend AND verified backend
    # loopback binding/session-key behavior, with HTTP/WS/SSE negative tests.
    raise SecurityBlocked(BLOCKED_REASON)


class Proxy:
    """Disabled compatibility surface: never connect to or expose upstream."""
    def __init__(self, logger, upstream_host='127.0.0.1', upstream_port=8000):
        self.logger = logger
        self.connections = set()

    async def relay(self, reader, writer):
        # Defense in depth for accidental direct use of this former callback.
        # Read no request and emit no upstream HTML, keys, WS frames or SSE.
        await close(writer)

    async def stop(self):
        pass

    async def listen(self, ctx, host='0.0.0.0', port=8443):
        require_access_design()
