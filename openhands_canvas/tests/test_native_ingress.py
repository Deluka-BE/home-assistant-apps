"""Focused boundary checks; real installed-runtime evidence is separate."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL
from openhands_canvas.native_ingress import SupervisorIngress, CanvasFiles, NativeCanvas

PREFIX = '/api/hassio_ingress/review'
ORIGIN = 'https://ha.example.test'


def request(peer='172.30.32.2', method='GET', path='/api/settings', **changes):
    headers = {'X-Remote-User-Id': 'a' * 32, 'X-Ingress-Path': PREFIX}
    headers.update(changes)
    return SimpleNamespace(transport=SimpleNamespace(get_extra_info=lambda _: (peer, 1)),
        method=method, headers=CIMultiDict(headers),
        raw_headers=[(k.encode(), v.encode()) for k, v in headers.items()],
        rel_url=URL(path), query=URL(path).query)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.policy = SupervisorIngress(PREFIX, frozenset({ORIGIN}))

    def test_identity_only_from_supervisor_transport(self):
        self.assertEqual(self.policy.admit(request()), 'a' * 32)
        with self.assertRaises(web.HTTPForbidden):
            self.policy.admit(request(peer='127.0.0.1'))
        with self.assertRaises(web.HTTPForbidden):
            self.policy.admit(request(**{'X-Remote-User-Id': ''}))

    def test_mutation_and_upgrade_require_discovered_origin(self):
        for change in ({}, {'Origin': 'https://attacker.example'}):
            with self.assertRaises(web.HTTPForbidden):
                self.policy.admit(request(method='POST', **change))
        self.policy.admit(request(method='POST', Origin=ORIGIN))
        with self.assertRaises(web.HTTPForbidden):
            self.policy.admit(request(Upgrade='websocket'))

    def test_prefix_and_token_urls_fail_closed(self):
        with self.assertRaises(web.HTTPForbidden):
            self.policy.admit(request(**{'X-Ingress-Path': '/untrusted'}))
        for path in ('/api/settings?session_api_key=x', '/api/settings?tkn=x', '/api/%2e%2e/file'):
            with self.assertRaises(web.HTTPForbidden):
                self.policy.admit(request(path=path))

    def test_no_plaintext_credential_response(self):
        proxy = NativeCanvas(self.policy, None, 'a' * 64)
        with self.assertRaises(ValueError):
            proxy.safe(b'prefix' + b'a' * 64 + b'suffix')
        self.assertEqual(proxy.safe(b'{"ok":true}'), b'{"ok":true}')

    def test_telemetry_disabled_before_frontend_module(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = object.__new__(CanvasFiles)
            files.root = root
            files.index = (b'<html><head><script type="module" '
                           b'src="/canvas/assets/entry.js"></script></head>'
                           b'<body></body></html>')
            html = files.get('/canvas/', PREFIX).body
            self.assertIn(b'window.__AGENT_CANVAS_DO_NOT_TRACK__=true;', html)
            self.assertIn((PREFIX + '/canvas/assets/entry.js').encode(), html)
            self.assertLess(html.index(b'__AGENT_CANVAS_DO_NOT_TRACK__'),
                            html.index(b'<script type="module"'))
            self.assertNotIn(b'X-Session-API-Key', html)
            self.assertNotIn(b'apiKey', html)

    @unittest.skipUnless(os.environ.get('CANVAS_FRONTEND_ROOT'), 'requires pinned installed frontend')
    def test_actual_compiled_index_registry_and_asset_rewrite(self):
        files = CanvasFiles(Path(os.environ['CANVAS_FRONTEND_ROOT']))
        html = files.get('/canvas/', PREFIX).body
        self.assertIn((PREFIX + '/canvas/assets/').encode(), html)
        self.assertNotIn(b'"/canvas/assets/', html)
        self.assertIn(b'function S(){return null}', files.active)
        self.assertIn(b'function Ne(){return [P()]}', files.active)
        self.assertNotIn(b'Promise.all([o(),w()])', files.entry)
        self.assertIn(b'apiKey:``', files.active)


if __name__ == '__main__':
    unittest.main()


class ResponseBounds(unittest.IsolatedAsyncioTestCase):
    async def test_complete_body_is_read_across_short_reads(self):
        from openhands_canvas.native_ingress import bounded
        class Content:
            async def iter_chunked(self, size):
                for chunk in (b'{"ok":', b'true}'):
                    yield chunk
        self.assertEqual(await bounded(Content(), 32), b'{"ok":true}')
        with self.assertRaises(ValueError):
            await bounded(Content(), 5)
