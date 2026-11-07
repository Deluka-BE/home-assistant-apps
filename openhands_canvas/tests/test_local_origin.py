"""Local HTTP trust discovery and real admission gates, using synthetic values."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import native_ingress
from native_ingress import SupervisorIngress, DiscoveryError, local_origin
from openhands_canvas.tests.test_native_ingress import request, PREFIX, ORIGIN

LOCAL = 'http://192.168.50.10:8123'


class LocalOrigin(unittest.TestCase):
    def discover(self, internal, fallback=None, external=None):
        class Response:
            status = 200
            def __init__(self, payload):
                self.content, self.payload = self, payload
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def iter_chunked(self, size):
                yield json.dumps(self.payload).encode()
        session = Mock()
        session.get.side_effect = [Response({'result': 'ok', 'data': {
            'ingress': True, 'ingress_url': PREFIX}}), Response({
                'internal_url': internal, 'external_url': external})]
        context = AsyncMock()
        context.__aenter__.return_value = session
        with patch.object(native_ingress.aiohttp, 'ClientSession', return_value=context):
            policy = asyncio.run(SupervisorIngress.discover(
                'synthetic', ingress_local_origin=fallback))
        for call, path in zip(session.get.call_args_list,
                              ('/addons/self/info', '/core/api/config')):
            self.assertEqual(call.args, (native_ingress.SUPERVISOR + path,))
            self.assertEqual(call.kwargs, {'headers': {'Authorization': 'Bearer synthetic'},
                                           'allow_redirects': False})
        return policy

    def test_core_and_missing_core_fallback_allow_html_and_modules(self):
        for policy in (self.discover(LOCAL), self.discover(None, LOCAL),
                       self.discover('', LOCAL)):
            self.assertEqual(policy.origins, frozenset({LOCAL}))
            for path in ('/canvas/', '/canvas/assets/entry.client-Dsugdwo6.js'):
                for headers in ({}, {'Origin': LOCAL}):
                    self.assertEqual(policy.admit(request(path=path, **headers)), 'a' * 32)
            policy.admit(request(method='POST', Origin=LOCAL))
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                with self.assertRaises(native_ingress.AdmissionDenied):
                    policy.admit(request(method=method))
            for headers in ({'Origin': 'http://192.168.50.11:8123'},
                            {'Origin': 'http://8.8.8.8:8123'},
                            {'Upgrade': 'websocket'},
                            {'Origin': LOCAL, 'Sec-Fetch-Site': 'cross-site'}):
                with self.assertRaises(native_ingress.AdmissionDenied):
                    policy.admit(request(**headers))
            for changes in ({'peer': '127.0.0.1'}, {'X-Remote-User-Id': ''},
                            {'X-Ingress-Path': '/wrong'}):
                with self.assertRaises(native_ingress.AdmissionDenied):
                    policy.admit(request(Origin=LOCAL, **changes))

    def test_fallback_only_when_internal_missing_and_https_preserved(self):
        other = 'http://10.0.0.5:8123'
        self.assertEqual(self.discover(LOCAL, other, ORIGIN).origins,
                         frozenset({LOCAL, ORIGIN}))
        for internal in ('http://homeassistant.local:8123', 'http://8.8.8.8:8123',
                         ORIGIN):
            self.assertEqual(self.discover(internal, other, ORIGIN).origins,
                             frozenset({ORIGIN}))
        with self.assertRaises(DiscoveryError):
            self.discover('http://8.8.8.8:8123')
        # An external HTTP URL cannot establish local trust either.
        with self.assertRaises(DiscoveryError):
            self.discover(None, external=LOCAL)

    def test_strict_local_literal_validation(self):
        for value in (LOCAL, 'http://10.1.2.3', 'http://172.16.1.2:65535',
                      'http://127.0.0.1:1', 'http://169.254.1.2',
                      'http://[::1]:8123', 'http://[fd00::1]:8123',
                      'http://[fe80::1]:8123'):
            self.assertTrue(local_origin(value).startswith('http://'))
        invalid = ('http://8.8.8.8', 'http://100.64.0.1', 'http://0.0.0.0',
                   'http://224.0.0.1', 'http://[2001:4860::1]',
                   'http://[::ffff:192.168.1.1]', 'http://localhost:8123',
                   LOCAL + '/', LOCAL + '/canvas', LOCAL + '?', LOCAL + '#',
                   'http://user:secret@192.168.50.10:8123',
                   'http://192.168.50.10:0', 'http://192.168.50.10:65536',
                   'http://192.168.50.10:', 'http://192.168.50.10:bad',
                   'http://192.168.50.10\\evil', LOCAL + '\n',
                   'http://[fe80::1%25eth0]', 'http://3232235777',
                   'http://192.168.050.10', '*', '', None, 42, ORIGIN)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '^invalid_ingress_local_origin$'):
                    local_origin(value)
                if value is not None:
                    with patch.object(native_ingress.aiohttp, 'ClientSession') as session:
                        with self.assertRaisesRegex(DiscoveryError, '^ingress_local_origin_invalid$'):
                            asyncio.run(SupervisorIngress.discover(
                                'synthetic', ORIGIN, value))
                        session.assert_not_called()

    def test_startup_option_reader_accepts_fallback(self):
        import tempfile
        from pathlib import Path
        import startup
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'options.json'
            path.write_text(json.dumps({'log_level': 'INFO', 'timezone': 'UTC',
                                        'ingress_local_origin': LOCAL}))
            self.assertEqual(startup.options(path)['ingress_local_origin'], LOCAL)
