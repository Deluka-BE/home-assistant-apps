"""Native Supervisor boundary for the pinned Canvas runtime.

Startup remains gated in startup.py. This module has no command-line listener.
Supervisor authenticates users; only its documented transport peer is trusted.
The upstream key never participates in the downstream HTTP or WS handshake.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web, WSMsgType

SUPERVISOR = 'http://172.30.32.2'
PEER = '172.30.32.2'
MAX_BODY = 16 * 1024 * 1024
PREFIX = re.compile(r'/api/hassio_ingress/[A-Za-z0-9_-]+')
TOKEN_NAME = re.compile(r'key|token|auth|secret|password|credential|^tkn$', re.I)
INDEX_SHA = '1729b7f1f5dbe29999c257056743b3e2df5e8638a108b160b6c5a0872a4619f7'
ENTRY_SHA = '3aafb27134f0041c64d8395b332a41ca88f0f15316884e9bd03df23bad4fa2e7'
ACTIVE_SHA = '5e0b93d4e9bfb9fa56e51a33b1b1122f60caffc86e59a03927b29962089125cc'
ADMISSION_REASON = getattr(web, 'RequestKey', web.AppKey)('native_ingress_reason', str)
SAFE_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff'}


DISCOVERY_REASONS = frozenset({
    'supervisor_addon_info_http', 'supervisor_core_config_http',
    'supervisor_ingress_missing', 'https_origin_missing', 'ingress_origin_invalid',
    'ingress_prefix_invalid', 'malformed_supervisor_response', 'transport_error',
    'ingress_local_origin_invalid',
})


class DiscoveryError(ValueError):
    """Carry only a fixed classification, never a remote value or exception text."""
    def __init__(self, reason):
        if reason not in DISCOVERY_REASONS:
            raise ValueError('invalid_discovery_reason')
        self.reason = reason
        super().__init__(reason)


class AdmissionDenied(web.HTTPForbidden):
    """Fixed internal reason; the HTTP response remains the generic 403."""
    def __init__(self, reason):
        super().__init__()
        self.admission_reason = reason


async def bounded(content, maximum):
    result = bytearray()
    async for chunk in content.iter_chunked(16384):
        result.extend(chunk)
        if len(result) > maximum:
            raise ValueError('upstream_body_too_large')
    return bytes(result)


def origin(value):
    # Reject parser normalization (whitespace/control characters), userinfo and
    # browser-ambiguous authorities before trusting this as an exact origin.
    try:
        if not isinstance(value, str) or not value.isascii() or any(
                c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value):
            raise ValueError()
        u = urlsplit(value)
        if (u.scheme != 'https' or not u.hostname or '@' in u.netloc
                or '?' in value or '#' in value or u.path not in ('', '/')
                or '\\' in value or '%' in u.netloc or u.netloc.endswith(':')):
            raise ValueError()
        host = u.hostname.lower()
        if ':' in host:
            ipaddress.IPv6Address(host)
            host = '[' + host + ']'
        elif len(host) > 253 or not all(re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in host.split('.')):
            raise ValueError()
        if not re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(?::[0-9]+)?', u.netloc):
            raise ValueError()
        port = u.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        return 'https://' + host + (f':{port}' if port not in (None, 443) else '')
    except ValueError:
        raise ValueError('invalid_ingress_origin') from None


LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8',
    '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10'))


def local_origin(value):
    """Only literal LAN/loopback IP origins; no DNS or browser-derived trust."""
    try:
        if not isinstance(value, str) or not value.isascii() or any(
                c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value):
            raise ValueError()
        u = urlsplit(value)
        if (u.scheme != 'http' or u.path or '?' in value or '#' in value
                or '@' in u.netloc or '%' in value or '\\' in value
                or not re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]|[0-9.]+)(?::[0-9]+)?', u.netloc)):
            raise ValueError()
        address = ipaddress.ip_address(u.hostname)
        if not any(address in network for network in LOCAL_NETWORKS):
            raise ValueError()
        port = u.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        host = f'[{address}]' if address.version == 6 else str(address)
        return 'http://' + host + (f':{port}' if port not in (None, 80) else '')
    except ValueError:
        raise ValueError('invalid_ingress_local_origin') from None


@dataclass(frozen=True)
class SupervisorIngress:
    prefix: str
    origins: frozenset[str]

    def __post_init__(self):
        if not PREFIX.fullmatch(self.prefix) or not self.origins:
            raise ValueError('invalid_supervisor_discovery')
        for value in self.origins:
            if isinstance(value, str) and value.startswith('http://'):
                local_origin(value)
            else:
                origin(value)

    @classmethod
    async def discover(cls, token, ingress_origin=None, ingress_local_origin=None):
        configured = None
        if ingress_origin is not None:
            try:
                configured = origin(ingress_origin)
            except ValueError:
                raise DiscoveryError('ingress_origin_invalid') from None
        configured_local = None
        if ingress_local_origin is not None:
            try:
                configured_local = local_origin(ingress_local_origin)
            except ValueError:
                raise DiscoveryError('ingress_local_origin_invalid') from None
        # No environment proxies, redirects, cookies or browser-controlled URLs.
        try:
            async with aiohttp.ClientSession(trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=15)) as session:
                async def get(path, http_reason):
                    async with session.get(SUPERVISOR + path,
                            headers={'Authorization': 'Bearer ' + token},
                            allow_redirects=False) as response:
                        if response.status != 200:
                            raise DiscoveryError(http_reason)
                        try:
                            raw = await bounded(response.content, 1024 * 1024)
                            value = json.loads(raw)
                        except (ValueError, UnicodeError):
                            raise DiscoveryError('malformed_supervisor_response') from None
                        if not isinstance(value, dict):
                            raise DiscoveryError('malformed_supervisor_response')
                        return value
                info = await get('/addons/self/info', 'supervisor_addon_info_http')
                config = await get('/core/api/config', 'supervisor_core_config_http')
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise DiscoveryError('transport_error') from None
        data = info.get('data')
        if info.get('result') != 'ok' or not isinstance(data, dict):
            raise DiscoveryError('malformed_supervisor_response')
        if data.get('ingress') is not True or not data.get('ingress_url'):
            raise DiscoveryError('supervisor_ingress_missing')
        prefix = data['ingress_url']
        if not isinstance(prefix, str) or not PREFIX.fullmatch(prefix.rstrip('/')):
            raise DiscoveryError('ingress_prefix_invalid')
        # HA config is the authenticated Core response, not forwarded Host/Origin.
        # Require the expected Core fields so a wrapper is not mistaken for an
        # otherwise valid configuration with no HTTPS URL configured.
        if not {'internal_url', 'external_url'} <= config.keys():
            raise DiscoveryError('malformed_supervisor_response')
        origins = {configured} if configured else set()
        for key in ('internal_url', 'external_url'):
            value = config[key]
            if value is None or value == '':
                continue
            if not isinstance(value, str):
                raise DiscoveryError('malformed_supervisor_response')
            try:
                if urlsplit(value).scheme == 'https':
                    origins.add(origin(value))
            except ValueError:
                raise DiscoveryError('https_origin_missing') from None
        internal = config['internal_url']
        if internal in (None, ''):
            if configured_local:
                origins.add(configured_local)
        elif isinstance(internal, str):
            # Non-IP/public HTTP config never establishes local trust. HTTPS
            # config and hostname-based installations retain their old behavior.
            try:
                origins.add(local_origin(internal))
            except ValueError:
                pass
        if not origins:
            raise DiscoveryError('https_origin_missing')
        return cls(prefix.rstrip('/'), frozenset(origins))

    def admit(self, request):
        peer = request.transport.get_extra_info('peername') if request.transport else None
        if not peer or peer[0] != PEER:
            raise AdmissionDenied('peer')
        # Supervisor strips and replaces user headers after session validation.
        # Fail closed on older Supervisors that do not establish a user identity.
        uid = request.headers.get('X-Remote-User-Id', '')
        if not re.fullmatch(r'[a-f0-9]{32}', uid):
            raise AdmissionDenied('user')
        if request.headers.get('X-Ingress-Path') != self.prefix:
            raise AdmissionDenied('ingress_path')
        supplied = request.headers.get('Origin')
        if supplied is not None and supplied not in self.origins:
            raise AdmissionDenied('origin')
        if (request.method not in {'GET', 'HEAD'} or request.headers.get('Upgrade')) and supplied not in self.origins:
            raise AdmissionDenied('origin')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise AdmissionDenied('fetch_site')
        # Ambiguous duplicates must not select one identity or framing value.
        names = [k.lower() for k, _ in request.raw_headers]
        if len(names) != len(set(names)):
            raise AdmissionDenied('duplicate_headers')
        if request.method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise AdmissionDenied('unsafe_method')
        path = request.rel_url.raw_path
        if ('%' in path or '\\' in path or '//' in path
                or any(p in {'.', '..'} for p in path.split('/'))):
            raise AdmissionDenied('unsafe_path')
        if any(TOKEN_NAME.search(k) for k in request.query):
            raise AdmissionDenied('token_query')
        return uid


def patch_active_store(raw):
    """Pin the UI to a keyless local backend; never load stored backend keys."""
    if hashlib.sha256(raw).hexdigest() != ACTIVE_SHA:
        raise ValueError('unrecognized_canvas_backend_registry')
    s = raw.decode()
    replacements = {
        'function S(){return v()}': 'function S(){return null}',
        'function x(){return ie()||(typeof window<`u`?window.location.origin:null)}':
            'function x(){return window.location.origin+window.__HA_CANVAS_PREFIX__}',
        'function P(){if(y())return null;let e=x(),t=S();return!e||!t?null:{id:M,name:Te,host:e,apiKey:t,kind:`local`}}':
            'function P(){return {id:M,name:`Home Assistant`,host:x(),apiKey:``,kind:`local`}}',
    }
    for before, after in replacements.items():
        if s.count(before) != 1:
            raise ValueError('unrecognized_canvas_backend_registry')
        s = s.replace(before, after)
    # Replace complete functions using known following boundaries; source digest
    # above prevents a new minifier output from changing the meaning silently.
    for start, end, replacement in (
        ('function Ne(){', 'function z(', 'function Ne(){return [P()]}'),
        ('function R(', 'function Ne(){', 'function R(e){}'),
        ('function qe(', 'function Je(', 'function qe(e){Q=X([P()],{backendId:M});Be()}'),
    ):
        a, b = s.index(start), s.index(end, s.index(start))
        s = s[:a] + replacement + s[b:]
    return s.encode()


class CanvasFiles:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.index = (self.root / 'index.html').read_bytes()
        if hashlib.sha256(self.index).hexdigest() != INDEX_SHA:
            raise ValueError('unrecognized_canvas_index')
        # Fail the image build/startup if the real HTML references missing files.
        # The pinned index uses absolute Canvas URLs for module/CSS/preload links.
        for asset in re.findall(rb'(?:src|href)=["\'](/canvas/[^"\']+)["\']', self.index):
            target = (self.root / urlsplit(asset.decode()).path[len('/canvas/'):]).resolve()
            if not target.is_relative_to(self.root) or not target.is_file():
                raise ValueError('missing_canvas_index_asset')
        self.active = patch_active_store((self.root / 'assets/active-store-DXLvaQxK.js').read_bytes())
        entry = (self.root / 'assets/entry.client-Dsugdwo6.js').read_bytes()
        if hashlib.sha256(entry).hexdigest() != ENTRY_SHA or entry.count(b'Promise.all([o(),w()])') != 1:
            raise ValueError('unrecognized_canvas_entry')
        # The DigitalOcean bridge is unavailable in this deployment. Avoid its
        # origin-root probe, which would escape the Supervisor ingress prefix.
        self.entry = entry.replace(b'Promise.all([o(),w()])', b'Promise.all([o()])')

    def get(self, path, prefix):
        relative = (path[len('/canvas/'):] if path.startswith('/canvas/')
                    else path.lstrip('/'))
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise web.HTTPForbidden()
        if target.is_file():
            raw = (self.active if target.name == 'active-store-DXLvaQxK.js' else
                   self.entry if target.name == 'entry.client-Dsugdwo6.js' else target.read_bytes())
            kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        elif (path.startswith(('/canvas/assets/', '/canvas/locales/'))
              or Path(relative).suffix):
            raise web.HTTPNotFound()
        else:
            raw, kind = self.index, 'text/html'
        if kind in {'text/html', 'text/javascript', 'application/javascript', 'text/css', 'application/json'}:
            raw = raw.replace(b'/canvas/', (prefix + '/canvas/').encode())
            raw = raw.replace(b'"/canvas"', json.dumps(prefix + '/canvas').encode())
            raw = raw.replace(b"'/canvas'", ("'" + prefix + '/canvas' + "'").encode())
            raw = raw.replace(b'`/canvas`', ('`' + prefix + '/canvas`').encode())
        if kind == 'text/html':
            # Constant, non-secret deployment path, before all module evaluation.
            boot = ('<script>window.__AGENT_CANVAS_DO_NOT_TRACK__=true;window.__HA_CANVAS_PREFIX__=' + json.dumps(prefix) + ';</script>').encode()
            raw = raw.replace(b'<head>', b'<head>' + boot, 1)
        return web.Response(body=raw, content_type=kind, headers=SAFE_HEADERS)


class NativeCanvas:
    def __init__(self, discovery, files, key, agent_port=18000, automation_port=18001, log=None):
        if not re.fullmatch(r'[a-f0-9]{64}', key):
            raise ValueError('invalid_server_owned_key')
        self.discovery, self.files, self._key = discovery, files, key
        self.agent = f'http://127.0.0.1:{agent_port}'
        self.automation = f'http://127.0.0.1:{automation_port}'
        self.session = None
        self.sockets = set()
        self.log = log
        self._diagnostics_seen = set()

    def safe(self, payload):
        # Last-resort leak tripwire, not a substitute for the independent review.
        decoded = re.sub(rb'\\u00([0-9a-fA-F]{2})',
                         lambda m: bytes([int(m[1], 16)]), payload)
        if self._key.encode() in decoded:
            raise ValueError('upstream_credential_in_output')
        return payload

    async def log_response(self, request, response):
        # Fixed categories only: never log a target, header, query or identity.
        # First observation per category/status/reason, capped for this process lifetime.
        suffix = Path(request.path).suffix.lower()
        category = ('js' if suffix in {'.js', '.mjs'} else
                    'css' if suffix == '.css' else
                    'html' if response.content_type == 'text/html' or request.path in {
                        '/', '/canvas', '/canvas/'} else 'other')
        status = int(response.status)
        reason = request.get(ADMISSION_REASON, 'none')
        event = (category, status, reason)
        if (self.log is not None and len(self._diagnostics_seen) < 128
                and event not in self._diagnostics_seen
                and (category != 'other' or status in {403, 404})):
            self._diagnostics_seen.add(event)
            self.log.info(f'native_ingress_response category={category} status={status} reason={reason}')

    def app(self):
        app = web.Application(client_max_size=MAX_BODY)
        async def lifecycle(app):
            trace = aiohttp.TraceConfig()
            async def reject_redirect(session, context, params):
                raise ValueError('upstream_redirect')
            trace.on_request_redirect.append(reject_redirect)
            async with aiohttp.ClientSession(trust_env=False, trace_configs=[trace],
                    cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=True,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=90)) as session:
                self.session = session
                yield
        async def shutdown(app):
            await asyncio.gather(*(socket.close(code=1001) for socket in tuple(self.sockets)),
                                 return_exceptions=True)
        app.on_shutdown.append(shutdown)
        app.on_response_prepare.append(self.log_response)
        app.cleanup_ctx.append(lifecycle)
        app.router.add_route('*', '/{tail:.*}', self.handle)
        return app

    async def handle(self, request):
        try:
            self.discovery.admit(request)
        except AdmissionDenied as exc:
            request[ADMISSION_REASON] = exc.admission_reason
            raise
        path = request.path
        if path.startswith('/sockets/'):
            return await self.websocket(request)
        if request.headers.get('Upgrade'):
            raise web.HTTPForbidden()
        if any(path == x or path.startswith(x + '/') for x in (
                '/vscode', '/api/vscode', '/api/auth', '/docs', '/redoc', '/openapi.json')):
            raise web.HTTPForbidden()
        if path in {'/', '/canvas'}:
            raise web.HTTPFound(self.discovery.prefix + '/canvas/')
        if path == '/server_info' or path.startswith('/api/') or path in {
                '/health', '/ready', '/alive', '/v1/models', '/v1/chat/completions', '/v1/responses'}:
            return await self.http(request)
        if request.method not in {'GET', 'HEAD'}:
            raise web.HTTPMethodNotAllowed(request.method, ['GET', 'HEAD'])
        return self.files.get(path, self.discovery.prefix)

    def upstream(self, request):
        target = request.rel_url.raw_path_qs
        if request.path == '/api/automation' or request.path.startswith('/api/automation/'):
            # The pinned static server preserves the complete request path.
            return self.automation + target
        return self.agent + target

    async def http(self, request):
        body = await request.read()
        connection_headers = {name.strip().lower() for name in
                              request.headers.get('Connection', '').split(',')}
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in {'accept', 'content-type'}
                   and k.lower() not in connection_headers}
        if (request.headers.get('X-Expose-Secrets') == 'encrypted'
                and 'x-expose-secrets' not in connection_headers):
            headers['X-Expose-Secrets'] = 'encrypted'
        headers['X-Session-API-Key'] = self._key
        try:
            async with self.session.request(request.method, self.upstream(request),
                    headers=headers, data=body, allow_redirects=False) as result:
                if 300 <= result.status < 400:
                    raise web.HTTPBadGateway()
                kind = result.headers.get('Content-Type', 'application/octet-stream')
                self.safe(kind.encode())
                if kind.split(';')[0].strip().lower() == 'text/event-stream':
                    return await self.sse(request, result)
                raw = await bounded(result.content, MAX_BODY)
                if len(raw) > MAX_BODY:
                    raise web.HTTPBadGateway()
                self.safe(raw)
                # Editor disabled at the service AND route boundaries. No cookies,
                # upstream redirects, auth challenges or runtime URL injection.
                return web.Response(status=result.status, body=raw,
                    headers={**SAFE_HEADERS, 'Content-Type': kind})
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise web.HTTPBadGateway() from None

    async def sse(self, request, upstream):
        response = web.StreamResponse(status=upstream.status, headers={
            **SAFE_HEADERS, 'Content-Type': 'text/event-stream', 'X-Accel-Buffering': 'no'})
        await response.prepare(request)
        pending = bytearray()
        try:
            async for chunk in upstream.content.iter_chunked(16384):
                pending.extend(chunk)
                if len(pending) > MAX_BODY:
                    raise ValueError('sse_event_too_large')
                while True:
                    # SSE permits CR, LF and CRLF. Hold a final CR until the
                    # next chunk so a split CRLF cannot create a false event.
                    # Work in original bytes to preserve upstream framing.
                    match = re.search(rb'(?:\r\n|\r(?!\n)|\n)(?:\r\n|\r(?!\n)|\n)', pending)
                    if not match or (pending[match.end()-1:] == b'\r'):
                        break
                    frame = bytes(pending[:match.end()]); del pending[:match.end()]
                    await asyncio.wait_for(response.write(self.safe(frame)), 15)
            if pending:
                await asyncio.wait_for(response.write(self.safe(bytes(pending))), 15)
            await response.write_eof()
        except (ValueError, TimeoutError, aiohttp.ClientError, ConnectionError):
            if request.transport:
                request.transport.close()
        return response

    async def websocket(self, request):
        if not re.fullmatch(r'/sockets/(?:(?:session|events)/[a-fA-F0-9-]{32,36}|bash-events)', request.path):
            raise web.HTTPForbidden()
        if (request.method != 'GET' or request.headers.get('Upgrade', '').lower() != 'websocket'
                or not web.WebSocketResponse().can_prepare(request).ok
                or request.headers.get('Sec-WebSocket-Protocol')):
            raise web.HTTPForbidden()
        try:
            # No browser headers, cookies, origin or legacy auth are forwarded.
            # A header key would bypass the required first-frame auth path.
            async with await asyncio.wait_for(self.session.ws_connect(self.upstream(request),
                    max_msg_size=MAX_BODY, heartbeat=30), 10) as upstream:
                # The actual 1.59.0 protocol has no auth ACK. Send first, before
                # preparing downstream or starting either forwarding task.
                await asyncio.wait_for(upstream.send_json(
                    {'type': 'auth', 'session_api_key': self._key}), 15)
                downstream = web.WebSocketResponse(max_msg_size=MAX_BODY, heartbeat=30)
                await downstream.prepare(request)
                self.sockets.add(downstream)
                async def relay(source, sink, to_upstream):
                    async for msg in source:
                        if msg.type != WSMsgType.TEXT:
                            return 1008
                        self.safe(msg.data.encode())
                        value = json.loads(msg.data)
                        if not isinstance(value, dict):
                            return 1008
                        # Browser has no auth frame to send; reject attempts to
                        # replace the server-owned credential, even after auth.
                        if to_upstream and (value.get('type') == 'auth' or 'session_api_key' in value):
                            return 1008
                        # Decode/re-encode before checking: JSON escapes must
                        # not bypass the credential tripwire.
                        self.safe(json.dumps(value, ensure_ascii=False).encode())
                        await asyncio.wait_for(sink.send_str(msg.data), 15)
                    return 1000 if source.close_code == 1000 else 1011
                tasks = [asyncio.create_task(relay(downstream, upstream, True)),
                         asyncio.create_task(relay(upstream, downstream, False))]
                close_code = 1011
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    codes = [task.result() if not task.exception() else 1011 for task in done]
                    close_code = max(codes)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    # Never forward upstream close reasons (may contain secrets).
                    await downstream.close(code=close_code)
                    self.sockets.discard(downstream)
                return downstream
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise web.HTTPBadGateway() from None
