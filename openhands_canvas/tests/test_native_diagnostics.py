"""One focused diagnostic check; no claim of installed frontend rendering."""
import unittest
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from openhands_canvas.native_ingress import NativeCanvas, SupervisorIngress


class DiagnosticRequest(SimpleNamespace):
    def get(self, key, default=None):
        return default


class Diagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_categories_statuses_and_real_admission_denial(self):
        messages = []
        proxy = NativeCanvas(SupervisorIngress('/api/hassio_ingress/check',
            frozenset({'https://example.test'})), None, 'a' * 64,
            log=SimpleNamespace(info=messages.append))
        # Exercise real app response preparation on a forbidden transport peer.
        async with TestClient(TestServer(proxy.app())) as client:
            response = await client.get('/canvas/assets/private.js?secret=private',
                headers={'X-Remote-User-Name': 'private'})
            self.assertEqual(response.status, 403)
        for path, status, kind in [('/canvas/', 200, 'text/html'),
                                   ('/canvas/assets/private.css', 404, 'text/plain'),
                                   ('/private', 404, 'text/plain')]:
            await proxy.log_response(DiagnosticRequest(path=path),
                web.Response(status=status, content_type=kind))
        self.assertEqual(messages, [
            'native_ingress_response category=js status=403 reason=peer',
            'native_ingress_response category=html status=200 reason=none',
            'native_ingress_response category=css status=404 reason=none',
            'native_ingress_response category=other status=404 reason=none'])
        await proxy.log_response(DiagnosticRequest(path='/another.js'), web.Response(status=403))
        self.assertEqual(len(messages), 5)
        for status in range(400, 450):
            await proxy.log_response(DiagnosticRequest(path='/private.js'), web.Response(status=status))
        self.assertEqual(len(messages), 54)
        for status in range(450, 600):
            await proxy.log_response(DiagnosticRequest(path='/private.js'), web.Response(status=status))
        self.assertEqual(len(messages), 128)
        self.assertTrue(all('private' not in message for message in messages))


class RejectionReasons(unittest.TestCase):
    def test_each_admission_gate_and_browser_asset_headers(self):
        from openhands_canvas.tests.test_native_ingress import request, PREFIX, ORIGIN
        from openhands_canvas.native_ingress import AdmissionDenied
        policy = SupervisorIngress(PREFIX, frozenset({ORIGIN}))
        cases = [
            ('peer', request(peer='127.0.0.1')),
            ('user', request(**{'X-Remote-User-Id': 'private'})),
            ('ingress_path', request(**{'X-Ingress-Path': PREFIX + '/canvas'})),
            ('origin', request(Origin='https://private.invalid')),
            ('origin', request(method='POST')),
            ('fetch_site', request(**{'Sec-Fetch-Site': 'cross-site'})),
            ('unsafe_method', request(method='OPTIONS', Origin=ORIGIN)),
            ('unsafe_path', request(path='/canvas/%2e%2e/private.js')),
            ('token_query', request(path='/canvas/assets/private.js?token=private')),
        ]
        duplicate = request()
        duplicate.raw_headers.append((b'x-remote-user-id', b'private'))
        cases.append(('duplicate_headers', duplicate))
        for reason, req in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(AdmissionDenied) as caught:
                    policy.admit(req)
                self.assertEqual(caught.exception.admission_reason, reason)
                self.assertEqual(caught.exception.status, 403)
                self.assertEqual(caught.exception.text, '403: Forbidden')
        # Stylesheets can omit Origin; module scripts can supply it. Neither
        # browser pattern alters Supervisor's peer, identity or prefix checks.
        for headers in ({}, {'Origin': ORIGIN}):
            for path in ('/canvas/', '/canvas/assets/entry.client-Dsugdwo6.js',
                         '/canvas/assets/vendor.css'):
                self.assertEqual(policy.admit(request(path=path, **headers)), 'a' * 32)
