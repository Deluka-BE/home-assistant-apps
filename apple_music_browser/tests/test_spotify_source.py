"""Offline metadata fixtures and engine lifecycle checks; no browser launch."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spotify_source as source


def snapshot(count=30):
    return dict(identity=source.DEFAULT_PLAYLIST, name='Discover Weekly', auth=False,
                error=False, loading=False, at_end=True,
                tracks=[dict(position=n, id=f'{n:022d}', title=f'Track {n}',
                             artists=['Artist'], album='Album', duration='3:12')
                        for n in range(1, count + 1)])


class SourceTests(unittest.TestCase):
    def test_valid_stable_and_fingerprint(self):
        result = source.finish(snapshot(), snapshot(), source.DEFAULT_PLAYLIST)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['tracks']), 30)
        self.assertEqual(result['tracks'][0]['duration_ms'], 192000)
        self.assertEqual(result['fingerprint'], source.finish(snapshot(), snapshot(), source.DEFAULT_PLAYLIST)['fingerprint'])
        self.assertIn('extracted_at', result)

    def test_invalid(self):
        cases = [snapshot(29), snapshot(31)]
        for key, value in [('position', 2), ('id', ''), ('id', 'bad'), ('title', ''), ('artists', [])]:
            item = snapshot()
            item['tracks'][0][key] = value
            cases.append(item)
        for key, value in [('identity', 'wrong'), ('name', 'Other'), ('error', True), ('loading', True), ('at_end', False)]:
            item = snapshot()
            item[key] = value
            cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                self.assertEqual(source.finish(item, snapshot(), source.DEFAULT_PLAYLIST)['status'], 'source_incomplete')

    def test_auth_and_unstable(self):
        item = snapshot()
        item['auth'] = True
        self.assertEqual(source.finish(item, snapshot(), source.DEFAULT_PLAYLIST)['status'], 'source_auth_required')
        item = snapshot()
        item['tracks'][0]['title'] = 'Changed'
        self.assertEqual(source.finish(snapshot(), item, source.DEFAULT_PLAYLIST)['status'], 'source_unstable')
        self.assertEqual(source.finish(snapshot(), snapshot(29), source.DEFAULT_PLAYLIST)['status'], 'source_incomplete')

    def test_duplicate_occurrences_preserved(self):
        item = snapshot()
        item['tracks'][1] = dict(item['tracks'][0], position=2)
        result = source.finish(item, copy.deepcopy(item), source.DEFAULT_PLAYLIST)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['tracks']), 30)
        self.assertEqual(result['tracks'][0]['id'], result['tracks'][1]['id'])

    def test_explicit_target(self):
        self.assertEqual(source.playlist_id('https://open.spotify.com/playlist/' + source.DEFAULT_PLAYLIST), source.DEFAULT_PLAYLIST)
        for value in ['https://evil.example/playlist/' + source.DEFAULT_PLAYLIST, 'bad', 'spotify:playlist:bad']:
            with self.assertRaises(ValueError):
                source.playlist_id(value)

    def run_reader(self, failure=False, auth=False, virtual=False, cleanup_failure=False):
        primary, tab = Mock(), Mock()
        primary.context.new_page.return_value = tab
        step = 0
        def evaluate(script, *args):
            nonlocal step
            if script == source._READ:
                if virtual:
                    start = min(step, 2) * 10
                    return dict(snapshot(), tracks=snapshot()['tracks'][start:start + 10], at_end=step >= 2)
                return dict(snapshot(), auth=auth)
            if args == (True,):
                step = 0
            else:
                step += 1
            return True
        tab.evaluate.side_effect = evaluate
        if failure:
            tab.goto.side_effect = RuntimeError('private page error')
        if cleanup_failure:
            tab.close.side_effect = RuntimeError('private cleanup error')
        engine = SimpleNamespace(submit=lambda fn: fn(primary))
        package = SimpleNamespace(browser=SimpleNamespace(_engine=engine))
        with patch.dict(sys.modules, {'applemusic_mcp': package}), patch.object(source.time, 'sleep'):
            result = source.read_spotify_discover_weekly()
        primary.context.new_page.assert_called_once_with()
        primary.goto.assert_not_called()
        tab.close.assert_called_once_with()
        return result

    def test_same_context_cleanup_success_error_auth(self):
        self.assertEqual(self.run_reader()['status'], 'ok')
        self.assertEqual(self.run_reader(failure=True)['status'], 'source_incomplete')
        self.assertEqual(self.run_reader(auth=True)['status'], 'source_auth_required')

    def test_cleanup_failure_fails_closed_and_releases_lock(self):
        self.assertEqual(self.run_reader(cleanup_failure=True), {'status': 'source_incomplete'})
        result = self.run_reader()
        expected = source.finish(snapshot(), snapshot(), source.DEFAULT_PLAYLIST)
        self.assertIn('extracted_at', result)
        self.assertEqual({k: v for k, v in result.items() if k != 'extracted_at'},
                         {k: v for k, v in expected.items() if k != 'extracted_at'})

    def test_virtualized_rows_accumulate_by_position_on_both_passes(self):
        result = self.run_reader(virtual=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([t['position'] for t in result['tracks']], list(range(1, 31)))

    def test_missing_fields(self):
        for key in ('position', 'id'):
            item = snapshot()
            del item['tracks'][0][key]
            self.assertEqual(source.validate(item, source.DEFAULT_PLAYLIST)[0], 'source_incomplete')
