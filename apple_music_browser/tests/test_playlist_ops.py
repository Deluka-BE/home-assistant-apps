"""Account-free workflow tests; no upstream browser or credentials are loaded."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

api = Mock(AMP='https://amp-api.music.apple.com/v1', TIMEOUT=30)
package = ModuleType('applemusic_mcp')
package.amp_api = api
path = Path(__file__).resolve().parents[1] / 'playlist_ops.py'
if not path.exists():
    path = Path('/opt/addon/playlist_ops.py')
spec = importlib.util.spec_from_file_location('tested_playlist_ops', path)
ops = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'applemusic_mcp': package}):
    spec.loader.exec_module(ops)


def track(rel='i.original', catalog='123', library=None, name='Title', artist='Artist'):
    return dict(relationship_id=rel, catalog_id=catalog, library_id=library or rel,
                name=name, artist=artist)


def response(data, next=None, status=200):
    return Mock(status_code=status, json=Mock(return_value={'data': data, 'next': next}))


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        api.reset_mock(side_effect=True, return_value=True)
        api.AMP = 'https://amp-api.music.apple.com/v1'
        api.TIMEOUT = 30

    def test_strict_pagination_and_ids(self):
        api.requests.get.side_effect = [response([
            {'id': 'i.rel', 'type': 'library-songs', 'attributes': {
                'name': 'Title', 'playParams': {'id': 'i.lib', 'catalogId': '123'}}}],
            '/v1/me/library/playlists/p.source/tracks?offset=100'), response([
            {'id': 'i.upload', 'type': 'library-songs', 'attributes': {
                'playParams': {'id': 'i.upload'}}}])]
        tracks = ops._tracks('p.source')
        self.assertEqual(tracks[0]['relationship_id'], 'i.rel')
        self.assertEqual(tracks[0]['catalog_id'], '123')
        self.assertIsNone(tracks[1]['catalog_id'])
        self.assertEqual(ops._item(tracks[1]), ('i.upload', 'library-songs'))
        self.assertEqual(api.requests.get.call_count, 2)

    def test_read_errors_never_return_partial_results(self):
        for status in (401, 403, 429, 500):
            api.requests.get.side_effect = [response([], '/v1/me/library/playlists/p.source/tracks?offset=1'),
                                            response([], status=status)]
            api._err.return_value = f'status {status}'
            with self.assertRaisesRegex(RuntimeError, str(status)):
                ops._tracks('p.source')
        api.requests.get.side_effect = [response([], 'https://evil.invalid/tracks')]
        with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
            ops._tracks('p.source')

    def test_search_order_unicode_and_scope(self):
        source = [track(name='Other'), track(name='STRASSE'), track(artist='Straße')]
        with patch.object(ops, '_tracks', return_value=source) as read:
            found = ops.search_playlist_tracks('p.source', 'straße', 1)
        self.assertEqual(found[0]['position'], 2)
        self.assertEqual(found[0]['relationship_id'], 'i.original')
        read.assert_called_once_with('p.source')

    def test_copy_mixed_ids_order_and_batches(self):
        source = [track(str(n), str(n + 1)) for n in range(101)] + [track('i.upload', None)]
        api.create_playlist.return_value = True, 'p.copy'
        api.add_tracks.return_value = True, 'added'
        with patch.object(ops, '_tracks', side_effect=[source, source]):
            result = ops.copy_playlist('p.source', 'Copy', 'Description')
        self.assertTrue(result['ok'])
        self.assertEqual([len(c.args[1]) for c in api.add_tracks.call_args_list], [100, 2])
        self.assertEqual(api.add_tracks.call_args_list[-1].args[1][-1], ('i.upload', 'library-songs'))
        api.delete_playlist.assert_not_called()

    def test_copy_empty_and_partial_failure(self):
        api.create_playlist.return_value = True, 'p.copy'
        with patch.object(ops, '_tracks', side_effect=[[], []]):
            self.assertTrue(ops.copy_playlist('p.source', 'Empty')['ok'])
        api.add_tracks.assert_not_called()
        api.add_tracks.return_value = False, 'status 500'
        with patch.object(ops, '_tracks', return_value=[track()]):
            result = ops.copy_playlist('p.source', 'Partial')
        self.assertFalse(result['ok'])
        self.assertEqual(result['playlist_id'], 'p.copy')
        self.assertIn('500', result['error'])
        api.delete_playlist.assert_not_called()

    def test_copy_read_failure_prevents_creation(self):
        with patch.object(ops, '_tracks', side_effect=RuntimeError('429')):
            self.assertFalse(ops.copy_playlist('p.source', 'Copy')['ok'])
        api.create_playlist.assert_not_called()

    def test_replace_add_verify_remove_sequence(self):
        before = [track(), track('i.other', '456')]
        after = before + [track('i.new', '456')]
        events = []
        def read(_):
            events.append('read')
            return [before, after, after[1:]][events.count('read') - 1]
        api.add_tracks.side_effect = lambda *args: (events.append('add') or True, 'added')
        api.remove_track.side_effect = lambda *args: (events.append('remove') or True, 'removed')
        with patch.object(ops, '_tracks', side_effect=read):
            self.assertTrue(ops.replace_playlist_track('p.source', 'i.original', '456')['ok'])
        self.assertEqual(events, ['read', 'add', 'read', 'remove', 'read'])
        api.remove_track.assert_called_once_with('p.source', 'i.original')

    def test_replace_failures_before_removal_retain_original(self):
        before = [track()]
        for added, verify in [(False, before), (True, before),
                              (True, RuntimeError('429')),
                              (True, [track('i.changed'), track('i.new', '456')])]:
            api.reset_mock()
            api.add_tracks.return_value = added, 'add outcome'
            with patch.object(ops, '_tracks', side_effect=[before, verify]):
                result = ops.replace_playlist_track('p.source', 'i.original', '456')
            self.assertFalse(result['ok'])
            self.assertFalse(result['removal_attempted'])
            api.remove_track.assert_not_called()

    def test_ambiguous_original_rejected(self):
        for source in ([], [track(), track()]):
            with patch.object(ops, '_tracks', return_value=source):
                self.assertFalse(ops.replace_playlist_track('p.source', 'i.original', '456')['ok'])
        api.add_tracks.assert_not_called()
        api.remove_track.assert_not_called()

    def test_remove_failure_reports_uncertainty_without_rollback(self):
        api.add_tracks.return_value = True, 'added'
        api.remove_track.return_value = False, 'timeout'
        with patch.object(ops, '_tracks', side_effect=[[track()], [track(), track('i.new', '456')]]):
            result = ops.replace_playlist_track('p.source', 'i.original', '456')
        self.assertFalse(result['ok'])
        self.assertTrue(result['removal_attempted'])
        self.assertIn('uncertain', result['error'])
        self.assertEqual(api.remove_track.call_count, 1)

    def test_copy_create_and_verification_failures(self):
        api.create_playlist.return_value = False, 'not authorized'
        with patch.object(ops, '_tracks', return_value=[track()]):
            result = ops.copy_playlist('p.source', 'Copy')
        self.assertEqual(result['stage'], 'create')
        api.add_tracks.assert_not_called()
        api.create_playlist.return_value = True, 'p.copy'
        api.add_tracks.return_value = True, 'added'
        with patch.object(ops, '_tracks', side_effect=[[track()], []]):
            result = ops.copy_playlist('p.source', 'Copy')
        self.assertEqual(result['stage'], 'verify')
        self.assertFalse(result['ok'])
        api.delete_playlist.assert_not_called()

    def test_copy_unknown_id_fails_before_creation(self):
        unknown = track()
        unknown.update(catalog_id=None, library_id=None)
        with patch.object(ops, '_tracks', return_value=[unknown]):
            self.assertFalse(ops.copy_playlist('p.source', 'Copy')['ok'])
        api.create_playlist.assert_not_called()

    def test_replace_removal_verification_failure(self):
        api.add_tracks.return_value = True, 'added'
        api.remove_track.return_value = True, 'removed'
        after = [track(), track('i.new', '456')]
        with patch.object(ops, '_tracks', side_effect=[[track()], after, after]):
            result = ops.replace_playlist_track('p.source', 'i.original', '456')
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'verify_removal')
        self.assertEqual(api.remove_track.call_count, 1)

    def test_validation(self):
        for value in ('i.library', '１２３', ''):
            with self.assertRaises(ValueError):
                ops.replace_playlist_track('p.source', 'i.original', value)
        with self.assertRaises(ValueError):
            ops.search_playlist_tracks('p.source', '', 1)
        with self.assertRaises(ValueError):
            ops.copy_playlist('p.source', '')
        api.requests.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
