import asyncio
import json
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, '/opt/addon')
from security import AuthenticatedMCP, load_settings


class SecurityTests(unittest.TestCase):
    def test_defaults_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'options.json'
            self.assertIsNone(load_settings(path))
            for options in ({}, {'mcp_enabled': False}):
                path.write_text(json.dumps(options))
                self.assertIsNone(load_settings(path))
            for token in ('', 'short', 'x' * 32 + '\n', 'é' * 32, None):
                path.write_text(json.dumps({'mcp_enabled': True, 'mcp_token': token}))
                with self.assertRaises(ValueError):
                    load_settings(path)
            path.write_text(json.dumps({'mcp_enabled': True, 'mcp_token': secrets.token_urlsafe(48),
                                        'mcp_certfile': '../etc/passwd'}))
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_boundary(self):
        async def run():
            token = secrets.token_urlsafe(48)
            called = []
            async def app(scope, receive, send):
                called.append(scope)
            secured = AuthenticatedMCP(app, token)
            for method in ('GET', 'POST', 'DELETE', 'OPTIONS'):
                for headers in ([], [(b'authorization', b'Bearer wrong')],
                                [(b'authorization', ('Bearer ' + token).encode())] * 2):
                    messages = []
                    async def send(message):
                        messages.append(message)
                    await secured({'type': 'http', 'method': method, 'path': '/mcp',
                                   'headers': headers}, None, send)
                    self.assertEqual(messages[0]['status'], 401)
            self.assertEqual(called, [])
            await secured({'type': 'http', 'path': '/mcp', 'headers': [
                (b'authorization', ('Bearer ' + token).encode())]}, None, None)
            self.assertEqual(len(called), 1)
        asyncio.run(run())

if __name__ == '__main__':
    unittest.main()
