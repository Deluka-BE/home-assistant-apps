"""Synthetic startup prerequisites only: no network, listeners or subprocesses."""
import asyncio
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import startup
from native_ingress import SupervisorIngress
from logging_utils import Logger
import native_ingress


class NativeStartup(unittest.TestCase):
    def test_native_path_requires_valid_prerequisites(self):
        discovery = SupervisorIngress('/api/hassio_ingress/synthetic',
                                      frozenset({'https://ha.example.test'}))
        env = startup.environment({})
        serve = AsyncMock()
        with patch('native_runtime.serve', serve):
            asyncio.run(startup.run(discovery, None, env))
            serve.assert_awaited_once_with(discovery, None, env)
            serve.reset_mock()
            for policy, runtime_env in (
                    (None, env),
                    (discovery, dict(env, OH_ENABLE_VSCODE='true')),
                    (discovery, dict(env, AGENT_CANVAS_ALLOW_LAN_SESSION_KEY='true'))):
                with self.assertRaises(ValueError):
                    asyncio.run(startup.run(policy, None, runtime_env))
            serve.assert_not_awaited()
        with self.assertRaises(ValueError):
            SupervisorIngress(discovery.prefix, frozenset())
        with self.assertRaises(ValueError):
            SupervisorIngress(discovery.prefix, frozenset({'http://ha.example.test'}))

    def test_missing_token_stops_before_state_or_runtime(self):
        log = Mock()
        with patch.dict(startup.os.environ, {}, clear=True), \
                patch.object(startup, 'Logger', return_value=log), \
                patch.object(startup, 'options', return_value={
                    'log_level': 'INFO', 'timezone': 'UTC'}), \
                patch.object(startup.pwd, 'getpwnam', return_value=SimpleNamespace(
                    pw_uid=1000, pw_gid=1000)), \
                patch.object(startup, 'prepare') as prepare, \
                patch.object(startup, 'run') as run:
            self.assertEqual(startup.main(), 1)
            prepare.assert_not_called()
            run.assert_not_called()
            self.assertEqual(log.failure.call_args.args[0], 'supervisor_token_required')


    def test_discovery_classification_and_private_startup_log(self):
        # Synthetic payloads only; exercise discovery through the startup logger.
        private = 'private-host-and-session-secret'
        good_info = {'result': 'ok', 'data': {
            'ingress': True, 'ingress_url': '/api/hassio_ingress/synthetic'}}
        good_config = {'internal_url': None, 'external_url': 'https://example.test'}

        class Response:
            def __init__(self, status, payload):
                self.status, self.payload = status, payload
                self.content = self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def iter_chunked(self, size):
                yield self.payload

        cases = (
            (503, good_info, 200, good_config, 'supervisor_addon_info_http'),
            (200, good_info, 403, good_config, 'supervisor_core_config_http'),
            (200, {'result': 'ok', 'data': {'ingress': False}}, 200,
             good_config, 'supervisor_ingress_missing'),
            (200, good_info, 200, {'internal_url': None, 'external_url': None},
             'https_origin_missing'),
            (200, {'result': 'ok', 'data': {'ingress': True, 'ingress_url': private}},
             200, good_config, 'ingress_prefix_invalid'),
            (200, good_info, 200, [private], 'malformed_supervisor_response'),
            (200, good_info, 200, b'invalid-json-' + private.encode(),
             'malformed_supervisor_response'),
            (200, good_info, 200, good_config, 'transport_error'),
        )
        for info_status, info, config_status, config, reason in cases:
            with self.subTest(reason=reason):
                def response(status, payload):
                    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                    return Response(status, raw)
                session = Mock()
                session.get.side_effect = (
                    native_ingress.aiohttp.ClientConnectionError(private)
                    if reason == 'transport_error' else
                    [response(info_status, info), response(config_status, config)])
                context = AsyncMock()
                context.__aenter__.return_value = session
                output = io.StringIO()
                with patch('sys.stdout', output):
                    log = Logger('startup', timezone='UTC')
                with patch.dict(startup.os.environ, {'SUPERVISOR_TOKEN': private}, clear=True), \
                        patch.object(startup, 'Logger', return_value=log), \
                        patch.object(startup, 'options', return_value={
                            'log_level': 'INFO', 'timezone': 'UTC'}), \
                        patch.object(startup.pwd, 'getpwnam', return_value=SimpleNamespace(
                            pw_uid=1000, pw_gid=1000)), \
                        patch.object(native_ingress.aiohttp, 'ClientSession', return_value=context), \
                        patch.object(startup, 'prepare') as prepare:
                    self.assertEqual(startup.main(), 1)
                    prepare.assert_not_called()
                line = output.getvalue()
                self.assertIn('stage=supervisor_discovery', line)
                self.assertIn('reason=' + reason, line)
                self.assertEqual(line.count('reason='), 1)
                self.assertNotIn(private, line)
                self.assertNotIn('https://', line)

    def test_invalid_configured_origin_stops_before_discovery(self):
        # A usable Core origin must never rescue an invalid explicit trust value.
        for value in ('http://ha.example.test', 'https://ha.example.test/path',
                      'https://user:password@ha.example.test',
                      'https://ha.example.test?', 'https://ha.example.test#'):
            with self.subTest(value=value), patch.object(
                    native_ingress.aiohttp, 'ClientSession') as session:
                with self.assertRaisesRegex(native_ingress.DiscoveryError,
                                            '^ingress_origin_invalid$'):
                    asyncio.run(SupervisorIngress.discover('synthetic', value))
                session.assert_not_called()

    def test_cloud_default_with_admin_origin_and_errors(self):
        import tempfile
        from pathlib import Path
        cloud = 'https://private-instance.ui.nabu.casa'
        info = {'result': 'ok', 'data': {
            'ingress': True, 'ingress_url': '/api/hassio_ingress/synthetic'}}
        config = {'internal_url': 'http://homeassistant.local:8123', 'external_url': None}

        class Response:
            status = 200
            def __init__(self, payload):
                self.content = self
                self.payload = payload
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def iter_chunked(self, size):
                yield json.dumps(self.payload).encode()

        invalid = ('', 'http://private.test', cloud + '/canvas', cloud + '?',
                   cloud + '#', 'https://user:secret@private.test',
                   cloud + ':invalid', cloud + ':65536', cloud + ':0',
                   'https://private.test\\evil', 'https://private.test\n',
                   'https://private.test:', 'https://private%2etest', 42)
        for supplied, reason in ((None, 'https_origin_missing'), (cloud + '/', None),
                                 *((value, 'ingress_origin_invalid') for value in invalid)):
            with self.subTest(supplied=supplied):
                session = Mock()
                session.get.side_effect = [Response(info), Response(config)]
                context = AsyncMock()
                context.__aenter__.return_value = session
                # Exercise the real options reader as well as discovery wiring.
                cfg = {'log_level': 'INFO', 'timezone': 'UTC'}
                if supplied is not None:
                    cfg['ingress_origin'] = supplied
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'options.json'
                    path.write_text(json.dumps(cfg))
                    parsed = startup.options(path)
                with patch.object(native_ingress.aiohttp, 'ClientSession', return_value=context):
                    if reason:
                        with self.assertRaises(native_ingress.DiscoveryError) as error:
                            asyncio.run(SupervisorIngress.discover('secret', parsed.get('ingress_origin')))
                        self.assertEqual(str(error.exception), reason)
                        self.assertNotIn('private', str(error.exception))
                    else:
                        policy = asyncio.run(SupervisorIngress.discover('secret', parsed['ingress_origin']))
                        self.assertEqual(policy.origins, frozenset({cloud}))
                        self.assertEqual(session.get.call_count, 2)
