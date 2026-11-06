"""Offline policy foundation, NOT a proxy or a deployable authentication boundary.

No sockets, environment reads, default Supervisor address, or browser credentials.
The future HTTP adapter must obtain peer from the transport, configuration from
trusted Supervisor discovery, and enforce this policy before opening upstream.
"""
from dataclasses import dataclass
from ipaddress import ip_address
import json
import re
from urllib.parse import unquote, parse_qsl


class Denied(ValueError):
    def __init__(self):
        super().__init__('ingress_denied')  # Never echo headers, paths or secrets.


@dataclass(frozen=True)
class Admission:
    method: str
    target: str
    headers: tuple[tuple[str, str], ...]


class IngressPolicy:
    # Deliberately narrow until real HTTP/WS/SSE integration establishes needs.
    _allowed = frozenset({'accept', 'content-type', 'if-none-match', 'last-event-id'})

    def __init__(self, *, supervisor_peer: str, origin: str, prefix: str):
        try:
            self._peer = ip_address(supervisor_peer)
        except ValueError:
            raise Denied() from None
        if (self._peer.is_unspecified or self._peer.is_multicast
                or self._peer.is_loopback or self._peer.is_link_local
                or self._peer.is_reserved or getattr(self._peer, 'ipv4_mapped', None)
                or '%' in supervisor_peer):
            raise Denied()
        if not re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::[0-9]{1,5})?', origin):
            raise Denied()
        if not re.fullmatch(r'/api/hassio_ingress/[a-zA-Z0-9_-]+', prefix):
            raise Denied()
        self._origin, self._prefix = origin, prefix

    def trusts_peer(self, socket_peer: str) -> bool:
        try:
            return ip_address(socket_peer) == self._peer
        except ValueError:
            return False

    def admit(self, *, socket_peer: str, method: str, target: str,
              headers: list[tuple[str, str]]) -> Admission:
        try:
            peer = ip_address(socket_peer)
        except ValueError:
            raise Denied() from None
        if peer != self._peer or method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise Denied()
        # Supervisor forwards /{path}, not the browser ingress prefix. Never
        # reconstruct this target from X-Ingress-Path or forwarding headers.
        # Keep suffix routing byte-for-byte. Encoded path separators/traversal
        # remain unsupported; queries are validated separately, never reassembled.
        path, separator, query = target.partition('?')
        if (len(target) > 4096 or not re.fullmatch(r'/[a-zA-Z0-9_./~-]*', path)
                or '//' in path or any(x in {'.', '..'} for x in path.split('/'))
                or path == '/api/hassio_ingress'
                or path.startswith('/api/hassio_ingress/')):
            raise Denied()
        # These upstream outputs/routes have no reviewed credential-free schema.
        # Keep denied even if the future adapter accidentally applies this policy.
        if any(path == base or path.startswith(base + '/') for base in
               ('/vscode', '/api/vscode', '/server_info', '/api/server_info')):
            raise Denied()
        if separator:
            if (not query or not re.fullmatch(r"[A-Za-z0-9_.~!$&'()*+,;=:@/?%+-]+", query)
                    or re.search(r'%(?![0-9a-fA-F]{2})', query)):
                raise Denied()
            try:
                decoded = unquote(query, errors='strict')
                pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=64,
                                  encoding='utf-8', errors='strict')
            except (ValueError, UnicodeError):
                raise Denied() from None
            if (any(ord(c) < 32 or ord(c) > 126 or c in '#\\;' for c in decoded)
                    or '%' in decoded
                    or any(any(c.isspace() or c in '&=?' for c in name) or name.lower() == 'tkn' or re.search(r'key|token|auth|secret|password|credential', name, re.I)
                           for name, _ in pairs)):
                raise Denied()
        if len(headers) > 64:
            raise Denied()
        seen, clean = {}, []
        for name, value in headers:
            if (not re.fullmatch(r'[A-Za-z0-9-]+', name) or len(value) > 8192
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise Denied()
            name = name.lower()
            if name in seen:
                raise Denied()
            seen[name] = value
            if name in self._allowed:
                clean.append((name, value))
        # Navigations/assets often omit Origin. If supplied it must match;
        # writes always require it. Peer trust never comes from these headers.
        if ((method not in {'GET', 'HEAD'} or 'origin' in seen)
                and seen.get('origin') != self._origin):
            raise Denied()
        # Drop all identity, authorization, cookie, host, ingress and forwarding
        # headers. Also reject hop-by-hop nominations rather than forwarding a
        # nominated allowlisted header. WS handshake belongs to a future adapter.
        if (any(name in seen for name in {'connection', 'transfer-encoding',
                'upgrade', 'keep-alive', 'proxy-connection', 'te', 'trailer', 'expect'})
                or any(name.startswith('sec-websocket-') for name in seen)):
            raise Denied()
        return Admission(method, target, tuple(clean))


class UpstreamSecret:
    """Server-owned credential; only the upstream WS sink receives its frame.

    This does not validate the image's auth contract or sanitize responses.
    Never pass this object or its frame to downstream send/log functions.
    """
    __slots__ = ('__key',)

    def __init__(self, key: str):
        if not isinstance(key, str) or not 1 <= len(key) <= 4096 or not key.isascii() or not key.isprintable():
            raise Denied()
        self.__key = key

    def __repr__(self):
        return 'UpstreamSecret([REDACTED])'

    def __copy__(self):
        raise Denied()

    def __deepcopy__(self, memo):
        raise Denied()

    def __reduce_ex__(self, protocol):
        raise Denied()

    async def authenticate_websocket(self, upstream_send):
        # Contract observed in retained Canvas websocket-auth.ts, not yet
        # verified against the pinned agent-server. Repeat for each connection.
        await upstream_send(json.dumps({'type': 'auth', 'session_api_key': self.__key},
                                       separators=(',', ':')))
