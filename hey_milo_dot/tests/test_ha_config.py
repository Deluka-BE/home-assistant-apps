import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from milo_dot.ha_config import provision

class HAConfigTests(unittest.TestCase):
    def test_config_paths_permissions_and_no_secret_in_options(self):
        with tempfile.TemporaryDirectory() as td, patch('milo_dot.ha_config.ssl.create_default_context') as tls:
            result = provision({'bridge_endpoint':'https://example.test:9443', 'bridge_token':'synthetic', 'bridge_ca_pem':'fixture', 'poll_interval':5}, td)
            tls.assert_called_once_with(cadata='fixture')
            self.assertNotIn('bridge_token', result)
            config = json.loads(Path(result['config']).read_text())
            self.assertEqual(Path(config['token_file']).read_text(), 'synthetic')
            for name in ('bridge.json','bridge-token','bridge-ca.pem'):
                self.assertEqual(stat.S_IMODE((Path(td)/name).stat().st_mode), 0o600)
    def test_invalid_input_writes_nothing(self):
        for endpoint in ('http://example.test','https://u:p@example.test','https://example.test/path'):
            with tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError):
                    provision({'bridge_endpoint':endpoint,'bridge_token':'synthetic','bridge_ca_pem':'invalid'},td)
                self.assertEqual(list(Path(td).iterdir()),[])
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): provision({},td)
    def test_symlink_token_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td, patch('milo_dot.ha_config.ssl.create_default_context'):
            root=Path(td); target=root/'unrelated'; target.write_text('preserve')
            (root/'bridge-token').symlink_to(target)
            with self.assertRaises(OSError):
                provision({'bridge_endpoint':'https://example.test','bridge_token':'synthetic','bridge_ca_pem':'fixture'},td)
            self.assertEqual(target.read_text(),'preserve')
