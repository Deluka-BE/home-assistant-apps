"""Credential-free installed frontend slice; backend transports remain closed.

Not a standalone launcher. Supervisor startup remains unconditionally blocked.
Trusted deployment configuration supplies peer, origin and ingress prefix; request
headers cannot select any of them. Never run the upstream static-server here.
"""
import hashlib
import html
from pathlib import Path
import re

try:
    from .ingress_policy import IngressPolicy, Denied
except ImportError:
    from ingress_policy import IngressPolicy, Denied

INDEX_SHA256 = '1729b7f1f5dbe29999c257056743b3e2df5e8638a108b160b6c5a0872a4619f7'


class InstalledFrontend:
    """Read installed build bytes directly, without static-server injection.

    Only the inert review page is enabled: compiled application execution is
    intentionally withheld until its router, storage and output are reviewed.
    Asset retrieval permits review of installed bytes, not arbitrary files.
    """
    def __init__(self, root, *, supervisor_peer, origin, prefix):
        self.policy = IngressPolicy(supervisor_peer=supervisor_peer,
                                    origin=origin, prefix=prefix)
        self.prefix = prefix
        self.root = Path(root).resolve(strict=True)
        index = self._read('index.html')
        if hashlib.sha256(index).hexdigest() != INDEX_SHA256:
            raise ValueError('installed_frontend_digest_mismatch')
        self.index = index

    def _read(self, relative):
        target = (self.root / relative).resolve(strict=True)
        if not target.is_relative_to(self.root) or not target.is_file():
            raise Denied()
        if target.stat().st_size > 16 * 1024 * 1024:
            raise Denied()
        return target.read_bytes()

    def respond(self, *, peer, method, target, headers):
        """Return (status, headers, body) after transport-based admission.

        This function owns the full browser response. No upstream response,
        cookie, redirect, runtime-services object or exception is forwarded.
        """
        self.policy.admit(socket_peer=peer, method=method, target=target,
                          headers=headers)
        if method not in {'GET', 'HEAD'} or '?' in target:
            raise Denied()
        common = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                  'X-Content-Type-Options': 'nosniff',
                  'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"}
        if target == '/':
            # Native browser-facing slice, with no script, credential or storage.
            body = ("<!doctype html><html><meta charset=utf-8><title>OpenHands Ingress review</title>"
                    "<h1>OpenHands Canvas 1.35.0</h1>"
                    "<p>Installed frontend index verified. Backend access is blocked pending Iris review.</p>"
                    "<p><a href=\"" + html.escape(self.prefix + '/__ha/installed-index', quote=True) +
                    "\">Inspect the installed frontend HTML</a></p>"
                    "<p>HTTP API, SSE, WebSocket and VSCode routes are disabled.</p></html>").encode()
            kind = 'text/html; charset=utf-8'
        elif target == '/__ha/installed-index':
            # Display as text: do not execute an unreviewed compiled app.
            body, kind = self.index, 'text/plain; charset=utf-8'
        elif re.fullmatch(r'/canvas/assets/[A-Za-z0-9_-]+\.(?:js|css|woff2?|png|svg)', target):
            body = self._read(target.removeprefix('/canvas/'))
            kind = 'text/plain; charset=utf-8'
        else:
            raise Denied()
        common.update({'Content-Type': kind, 'Content-Length': str(len(body))})
        return 200, common, b'' if method == 'HEAD' else body


def handler(frontend):
    """HTTP handler for an explicitly configured, isolated review listener.

    No listener is opened here; no public/default bind or startup bypass exists.
    HTTP parsing is supplied by the standard library, one request per connection.
    """
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never log raw request targets, credentials or browser content.

        def handle_one_request(self):
            if not frontend.policy.trusts_peer(self.client_address[0]):
                self.close_connection = True
                return
            super().handle_one_request()

        def do_GET(self):
            self.close_connection = True
            try:
                if self.headers.get('Content-Length', '0') != '0':
                    raise Denied()
                code, headers, body = frontend.respond(
                    peer=self.client_address[0], method=self.command,
                    target=self.path, headers=list(self.headers.raw_items()))
            except (ValueError, OSError):
                code, headers, body = 403, {'Content-Length': '0', 'Cache-Control': 'no-store'}, b''
            self.send_response_only(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET
        do_POST = do_GET
        do_PUT = do_GET
        do_PATCH = do_GET
        do_DELETE = do_GET
        do_OPTIONS = do_GET

    return Handler
