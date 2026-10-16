"""Fail-closed MCP boundary; credentials are never accepted in URLs or logged."""
import hmac
import json
from pathlib import Path
from starlette.responses import Response


def load_settings(path='/data/options.json'):
    options = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    if not options.get('mcp_enabled', False):
        return None
    token = options.get('mcp_token', '')
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('MCP requires a random ASCII bearer token of at least 32 characters')
    paths = []
    for field in ('mcp_certfile', 'mcp_keyfile'):
        path = Path('/ssl') / options.get(field, '')
        if not path.resolve().is_relative_to(Path('/ssl')) or not path.is_file():
            raise ValueError('MCP TLS files must exist inside /ssl')
        paths.append(str(path))
    return token, *paths


class AuthenticatedMCP:
    def __init__(self, app, token):
        self.app = app
        self.expected = ('Bearer ' + token).encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope['headers']
        auth = [v for k, v in headers if k.lower() == b'authorization']
        if len(auth) != 1 or not hmac.compare_digest(auth[0], self.expected):
            response = Response(status_code=401, headers={'WWW-Authenticate': 'Bearer realm="apple-music-mcp"', 'Cache-Control': 'no-store'})
        elif any(k.lower() == b'origin' for k, _ in headers):
            # Inspector's local Node proxy and server-side bridges send no Origin.
            # Reject browser-origin requests rather than trusting arbitrary LAN hosts.
            response = Response(status_code=403)
        elif scope['path'] != '/mcp' or scope.get('query_string'):
            response = Response(status_code=404)
        elif scope.get('method', 'POST') != 'POST':
            response = Response(status_code=405, headers={'Allow': 'POST'})
        else:
            return await self.app(scope, receive, send)
        await response(scope, receive, send)
