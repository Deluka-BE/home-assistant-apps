"""New web writes tested only with mock transports; never an Apple account."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'web_ops.py').exists():
    ROOT = Path('/opt/addon')


def load(name, package):
    spec = importlib.util.spec_from_file_location('fixture_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'applemusic_mcp': package}), patch.object(sys, 'path', [str(ROOT)] + sys.path):
        spec.loader.exec_module(module)
    return module


class WebTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock(AMP='https://amp-api.music.apple.com/v1', API='api', WEB='web', TIMEOUT=30)
        self.api.throttled_recently.return_value = False
        self.api._headers.return_value = {'Authorization': 'Bearer fixture-secret', 'Music-User-Token': 'fixture-user'}
        package = ModuleType('applemusic_mcp')
        package.amp_api = self.api
        self.ops = load('web_ops', package)
        # Use the real validators but isolate the catalog request.
        self.ops.discovery = load('discovery', package)
        self.catalog = patch.object(self.ops.discovery, 'catalog_identifiers', return_value={'missing': []}).start()
        self.addCleanup(patch.stopall)
        for method in ('post', 'put', 'delete'):
            getattr(self.api.requests, method).return_value = self.response(status=204)
        self.api.requests.get.return_value = self.response({'data': []})

    @staticmethod
    def response(payload=None, status=200):
        return Mock(status_code=status, content=b'' if status == 204 else b'{}',
                    json=Mock(return_value=payload if payload is not None else {'data': []}))

    def test_add_exact_types_dedup_one_submission_fresh_catalog(self):
        for kind in ('songs', 'albums'):
            result = self.ops.add_library_catalog(['1', '2', '1'], kind, 'be')
            self.assertEqual(result['submitted'], 2)
            self.assertTrue(result['acknowledged'])
            self.assertFalse(result['verified'])
            self.catalog.assert_called_with(['1', '2'], kind, 'be')
            self.api.requests.post.assert_called_with('https://api.music.apple.com/v1/me/library',
                headers=self.api._headers.return_value, timeout=30, allow_redirects=False,
                params={f'ids[{kind}]': '1,2'})
        self.assertEqual(self.api.requests.post.call_count, 2)

    def test_add_missing_or_failed_lookup_never_writes(self):
        self.catalog.return_value = {'missing': ['2']}
        with self.assertRaises(ValueError):
            self.ops.add_library_catalog(['1', '2'])
        self.catalog.side_effect = RuntimeError('catalog unavailable')
        with self.assertRaises(RuntimeError):
            self.ops.add_library_catalog(['1'])
        self.api.requests.post.assert_not_called()

    def test_song_removal_only_exact_scoped_id_and_confirmation(self):
        self.assertTrue(self.ops.remove_library_song('i.song-1', True)['ok'])
        self.api.requests.delete.assert_called_once_with('https://amp-api.music.apple.com/v1/me/library/songs/i.song-1',
            headers=self.api._headers.return_value, timeout=30, allow_redirects=False)
        self.api.requests.delete.reset_mock()
        for value in ('123', 'l.album', 'i.', 'i.a,i.b', 'i.a/b', 'i.a?ids=all', 'i.a%2f', 'i.a\n', '*', '../songs'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ops.remove_library_song(value, True)
        for confirm in (False, 1, 'true', None):
            with self.assertRaises(ValueError):
                self.ops.remove_library_song('i.song', confirm)
        self.api.requests.delete.assert_not_called()

    def test_folder_empty_preflight_and_root_protection(self):
        self.ops.delete_playlist_folder('p.folder', True)
        self.api.requests.get.assert_called_once_with('https://amp-api.music.apple.com/v1/me/library/playlist-folders/p.folder/children',
            headers=self.api._headers.return_value, timeout=30, allow_redirects=False)
        self.assertEqual(self.api.requests.delete.call_args.args[0], 'https://amp-api.music.apple.com/v1/me/library/playlist-folders/p.folder')
        for value in ('p.playlistsroot', 'p.', 'p.a/children', 'i.song', '*'):
            self.api.requests.get.reset_mock()
            with self.assertRaises(ValueError):
                self.ops.delete_playlist_folder(value, True)
            self.api.requests.get.assert_not_called()
        self.api.requests.get.reset_mock()
        with self.assertRaises(ValueError):
            self.ops.delete_playlist_folder('p.folder')
        self.api.requests.get.assert_not_called()

    def test_folder_nonempty_paginated_malformed_or_failed_read_never_deletes(self):
        for payload in ({'data': [{'id': 'p.child'}]}, {'data': [], 'next': '/more'},
                        {'data': [], 'next': ''}, {}, {'data': None}, {'data': [None]}, {'errors': [{}]}):
            self.api.requests.get.return_value = self.response(payload)
            with self.assertRaises((ValueError, RuntimeError)):
                self.ops.delete_playlist_folder('p.folder', True)
        for status in (301, 401, 403, 404, 429, 500):
            self.api.requests.get.return_value = self.response(status=status)
            with self.assertRaises(RuntimeError):
                self.ops.delete_playlist_folder('p.folder', True)
        self.api.requests.delete.assert_not_called()

    def test_web_rating_values_empty_and_strict_failures(self):
        self.assertEqual(self.ops.web_rating('1'), {'value': None, 'rating': 'unrated'})
        for value, label in ((1, 'love'), (-1, 'dislike')):
            self.api.requests.get.return_value = self.response({'data': [{'id': '1', 'type': 'ratings', 'attributes': {'value': value}}]})
            self.assertEqual(self.ops.web_rating('1', 'albums'), {'value': value, 'rating': label})
        for payload in ({'data': [{'id': '1', 'type': 'ratings', 'attributes': {'value': True}}]},
                        {'data': [{'id': '2', 'type': 'ratings', 'attributes': {'value': 1}}]},
                        {'data': [{'id': '1', 'type': 'songs', 'attributes': {'value': 1}}]},
                        {'data': [{}]}, {'data': [], 'next': '/more'}, {'data': 'secret'}):
            self.api.requests.get.return_value = self.response(payload)
            with self.assertRaises(RuntimeError):
                self.ops.web_rating('1')
        self.api.requests.get.return_value = self.response(status=404)
        with self.assertRaisesRegex(RuntimeError, '404'):
            self.ops.web_rating('1')

    def test_rating_writes_and_confirmed_clear(self):
        for kind in ('songs', 'albums'):
            for action, value in (('favorite', 1), ('love', 1), ('dislike', -1)):
                self.ops.set_web_rating('123', action, kind)
                self.api.requests.put.assert_called_with(f'https://amp-api.music.apple.com/v1/me/ratings/{kind}/123',
                    headers=self.api._headers.return_value, timeout=30, allow_redirects=False,
                    json={'attributes': {'value': value}})
        with self.assertRaises(ValueError):
            self.ops.set_web_rating('123', 'clear')
        self.api.requests.delete.assert_not_called()
        self.ops.set_web_rating('123', 'clear', confirm=True)
        self.api.requests.delete.assert_called_once()

    def test_invalid_inputs_do_not_harvest_auth_or_send(self):
        cases = [lambda: self.ops.add_library_catalog([]), lambda: self.ops.add_library_catalog(['1'] * 101),
                 lambda: self.ops.add_library_catalog(['１２３']), lambda: self.ops.add_library_catalog(['i.song']),
                 lambda: self.ops.add_library_catalog(['1'], 'artists'), lambda: self.ops.add_library_catalog(['1'], storefront='../us'),
                 lambda: self.ops.set_web_rating('1', 'stars'), lambda: self.ops.set_web_rating('1', 'love', 'artists'),
                 lambda: self.ops.web_rating('1/path')]
        for call in cases:
            with self.assertRaises(ValueError):
                call()
        self.api._headers.assert_not_called()
        self.api.requests.assert_not_called()
        self.assertEqual(self.api.requests.mock_calls, [])

    def test_all_status_errors_no_redirects_bodies_retries_or_leaks(self):
        calls = [('get', lambda: self.ops.web_rating('1')),
                 ('post', lambda: self.ops.add_library_catalog(['1'])),
                 ('delete', lambda: self.ops.remove_library_song('i.song', True)),
                 ('put', lambda: self.ops.set_web_rating('1', 'love'))]
        for method, call in calls:
            request = getattr(self.api.requests, method)
            for status in (301, 302, 307, 400, 401, 403, 404, 429, 500):
                request.reset_mock()
                request.return_value = self.response({'errors': ['fixture-secret']}, status)
                with self.assertRaisesRegex(RuntimeError, str(status)) as exc:
                    call()
                self.assertNotIn('fixture-secret', str(exc.exception))
                request.assert_called_once()
                self.assertFalse(request.call_args.kwargs['allow_redirects'])
            request.side_effect = RuntimeError('fixture-secret private-path')
            with self.assertRaisesRegex(RuntimeError, 'uncertain') as exc:
                call()
            self.assertNotIn('fixture-secret', str(exc.exception))
            request.side_effect = None
        self.api._err.assert_not_called()

    def test_throttling_and_auth_fail_before_transport(self):
        self.api.throttled_recently.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'rate limited'):
            self.ops.remove_library_song('i.song', True)
        self.api._headers.assert_not_called()
        self.api.throttled_recently.return_value = False
        self.api._headers.side_effect = RuntimeError('fixture-secret')
        with self.assertRaisesRegex(RuntimeError, 'credentials unavailable') as exc:
            self.ops.remove_library_song('i.song', True)
        self.assertNotIn('fixture-secret', str(exc.exception))
        self.api.requests.delete.assert_not_called()

    def test_mutation_acknowledgments_invalid_json_and_api_errors(self):
        for status in (200, 201, 202, 204):
            self.api.requests.delete.return_value = self.response(status=status)
            self.assertEqual(self.ops.remove_library_song('i.song', True)['status'], status)
        for payload in ([], {'errors': ['fixture-secret']}):
            self.api.requests.delete.return_value = self.response(payload)
            with self.assertRaises(RuntimeError):
                self.ops.remove_library_song('i.song', True)
        self.api.requests.delete.return_value = self.response()
        self.api.requests.delete.return_value.json.side_effect = ValueError('fixture-secret')
        with self.assertRaisesRegex(RuntimeError, 'uncertain') as exc:
            self.ops.remove_library_song('i.song', True)
        self.assertNotIn('fixture-secret', str(exc.exception))

    def test_diagnostics_one_probe_sanitized_no_reset(self):
        self.assertTrue(self.ops.web_auth_diagnostics()['ok'])
        self.api.requests.get.assert_called_once()
        self.api.requests.get.side_effect = RuntimeError('fixture-secret /private')
        result = self.ops.web_auth_diagnostics()
        self.assertFalse(result['ok'])
        self.assertNotIn('fixture-secret', str(result))
        self.assertNotIn('/private', str(result))
        self.api.requests.post.assert_not_called()
        self.api.requests.delete.assert_not_called()
