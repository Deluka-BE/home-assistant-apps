"""Privacy boundaries and bounded process-local state, with no upstream file access."""
from pathlib import Path
import importlib.util
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class LocalTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / 'local_tools.py').exists():
            root = Path('/opt/addon')
        package = ModuleType('applemusic_mcp')
        package.browser = Mock()
        package.browser.browser_settings.return_value = (True, 'volume=50 shuffle=off repeat=none')
        package.amp_api = Mock()
        spec = importlib.util.spec_from_file_location('fixture_local', root / 'local_tools.py')
        self.ops = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'applemusic_mcp': package}), patch.object(sys, 'path', [str(root)] + sys.path):
            spec.loader.exec_module(self.ops)
        self.browser = package.browser
        self.lookup = patch.object(self.ops.discovery, 'catalog_identifiers', return_value={'data': [{'id': '1'}], 'missing': []}).start()
        self.addCleanup(patch.stopall)

    def test_browser_read_and_write_use_only_browser_settings(self):
        self.assertTrue(self.ops.browser_settings_info()['ok'])
        self.browser.browser_settings.assert_called_with(volume=-1, shuffle=None, repeat=None)
        self.ops.set_browser_settings(75, True, 'all')
        self.browser.browser_settings.assert_called_with(volume=75, shuffle=True, repeat='all')
        self.browser.reset_mock()
        for args in ({'volume': 101}, {'volume': -2}, {'volume': True}, {'shuffle': 1}, {'repeat': 'native'}):
            with self.assertRaises(ValueError):
                self.ops.set_browser_settings(**args)
        self.browser.browser_settings.assert_not_called()

    def test_browser_failures_and_unexpected_state_are_sanitized(self):
        for result in ((False, 'fixture-secret'), (True, 'fixture-secret')):
            self.browser.browser_settings.return_value = result
            with self.assertRaises(RuntimeError) as exc:
                self.ops.browser_settings_info()
            self.assertNotIn('fixture-secret', str(exc.exception))
        self.browser.browser_settings.side_effect = RuntimeError('fixture-secret')
        with self.assertRaises(RuntimeError) as exc:
            self.ops.set_browser_settings(50)
        self.assertNotIn('fixture-secret', str(exc.exception))

    def test_cache_disabled_by_default_and_validated_preferences(self):
        self.assertEqual(self.ops.local_preferences()['cache_ttl_seconds'], 0)
        self.ops.cached_catalog_identifiers(['1'])
        self.ops.cached_catalog_identifiers(['1'])
        self.assertEqual(self.lookup.call_count, 2)
        for value in (-1, 301, True, '60'):
            with self.assertRaises(ValueError):
                self.ops.set_local_preferences(value)
        self.assertFalse(self.ops.set_local_preferences(60)['persistent'])

    def test_cache_scoped_detached_expiring_and_bounded(self):
        self.ops.set_local_preferences(60)
        with patch.object(self.ops.time, 'monotonic', return_value=10):
            first = self.ops.cached_catalog_identifiers(['1'])
            first['data'][0]['id'] = 'altered'
            hit = self.ops.cached_catalog_identifiers(['1'])
            self.assertTrue(hit['cached'])
            self.assertEqual(hit['data'][0]['id'], '1')
            self.ops.cached_catalog_identifiers(['1'], storefront='be')
            self.ops.cached_catalog_identifiers(['1'], content_type='albums')
            self.ops.cached_catalog_identifiers(['2'])
            self.assertEqual(self.lookup.call_count, 4)
        with patch.object(self.ops.time, 'monotonic', return_value=71):
            self.assertFalse(self.ops.cached_catalog_identifiers(['1'])['cached'])
            self.assertEqual(self.ops.local_preferences()['cache_entries'], 1)
            for i in range(130):
                self.ops.cached_catalog_identifiers([str(i)])
            self.assertEqual(self.ops.local_preferences()['cache_entries'], 128)
        self.ops.set_local_preferences(0)
        self.assertEqual(self.ops.local_preferences()['cache_entries'], 0)

    def test_cache_never_caches_failures_and_clear_only_with_confirm(self):
        self.ops.set_local_preferences(60)
        self.lookup.side_effect = RuntimeError('lookup failed')
        with self.assertRaises(RuntimeError):
            self.ops.cached_catalog_identifiers(['1'])
        self.assertEqual(self.ops.local_preferences()['cache_entries'], 0)
        self.lookup.side_effect = None
        self.ops.cached_catalog_identifiers(['1'])
        for confirm in (False, 1, 'true'):
            with self.assertRaises(ValueError):
                self.ops.clear_catalog_cache(confirm)
        self.assertEqual(self.ops.clear_catalog_cache(True)['cleared_entries'], 1)
        self.assertEqual(self.ops.local_preferences()['cache_entries'], 0)

    def test_audit_no_arguments_results_exceptions_and_failed_writes_clear_cache(self):
        self.ops.set_local_preferences(60)
        self.ops.cached_catalog_identifiers(['1'])
        def private_action(secret):
            raise ValueError(secret)
        wrapped = self.ops.audited(private_action, write=True)
        with self.assertRaises(ValueError):
            wrapped('fixture-secret account-id private-path')
        summary = self.ops.audit_summary()
        self.assertNotIn('fixture-secret', str(summary))
        self.assertNotIn('account-id', str(summary))
        self.assertNotIn('private-path', str(summary))
        self.assertEqual(summary['events'][0]['tool'], 'private_action')
        self.assertEqual(summary['events'][0]['outcome'], 'error')
        self.assertEqual(self.ops.local_preferences()['cache_entries'], 0)
        def diagnostic():
            return {'ok': False, 'error': 'fixture-secret'}
        self.ops.audited(diagnostic)()
        self.assertEqual(self.ops.audit_summary()['events'][-1]['outcome'], 'error')
        for _ in range(105):
            self.ops.audited(self.ops.local_preferences)()
        self.assertEqual(len(self.ops.audit_summary(100)['events']), 100)
        self.assertEqual(len(self.ops.audit_summary(1)['events']), 1)
        for value in (0, 101, True):
            with self.assertRaises(ValueError):
                self.ops.audit_summary(value)

    def test_cached_lookup_invalid_input_no_request(self):
        for args in (([],), (['i.song'],), (['1'], 'ratings'), (['1'], 'songs', '../us')):
            with self.assertRaises(ValueError):
                self.ops.cached_catalog_identifiers(*args)
        self.lookup.assert_not_called()
